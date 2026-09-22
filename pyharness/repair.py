"""pyharness/repair.py — 崩溃修复管线(用户面编排)(specs/repair.py.md 契约;F060/F064)

一句话职责:崩溃恢复管线(F060)——启动/打开会话时自动扫描全会话健康(尾部半行/
中部坏行/seq 空洞/派生索引对账),对损坏会话执行"先备份 `.corrupt-{ts}` → 尾部
半行截断 → 中部坏行隔离到 quarantine 文件(不删,主文件原子重写)→ seq 空洞定位
(对照 compacted/recovered 声明)→ FTS/storage 派生视图整体重建 → 强同步追加
session.recovered 声明"的**幂等**修复,产出 RepairReport;不可修复 → 明确报错且
原文件备份留存;交互模式让用户决策"丢弃尾部 or 保留 / 隔离行删留"。

与 persistence 的分工(本模块 docstring 权威):persistence(核心)提供 JSONL 物理
原语(原子写/严格行读/截断检测偏移);**本模块是 F060 修复策略与编排的唯一归属**
(备份命名、隔离文件、空洞判定、报告、recovered 声明、交互决策)。persistence 草案
同名 repair 仅供存储层内部使用;CLI/启动自检的 repair 一律走本模块(修复策略不并存)。
备份命名以 PRD F060 为唯一权威:`{sid}.corrupt-{ts}.jsonl`(persistence 草案的
`.jsonl.bak-{ts}` 旧命名不改动其自身,但本模块一律不用)。

依赖(单向只读,INV-08):errors.raise_code(PERS-201/202 唯一出口)、events
(Envelope 解析/check_seq_gap/词表校验)、persistence 物理原语(open_store/
detect_truncation/default_sessions_dir);core.session.open_session 惰性导入
(declare_recovered 打开目标会话日志);零 LLM 依赖(崩溃恢复不依赖模型可用性)。

修复的伦理 = 数据优先:隔离不删除(隔离档写失败 → 该行保留原地,绝不丢行)、
任何"修坏删更多"都是失败、修复过程本身可审计(session.recovered 事件 + 备份)。

偏离说明(契约 = specs/repair.py.md,以下为与规格伪码冲突/本仓库既有实现约束下的
取舍,均列理由,与 cli.py/compaction.py 同款先例):
1. recovered 事件载荷按**注册词表**(events/payload.py SessionRecoveredPayload,
   extra=forbid,fixed 非空/lost/backup)构造:spec 伪码 payload 含 quarantined 计数
   与 declared_holes 扩展字段,但真实校验链(EVT-100)拒多余字段 → quarantined 改由
   RepairReport + 隔离档承载,declared_holes 经 fixed "seq-holes:[...]" 词条表达
   (persistence._declared_by_compaction 同款口径);lost 从伪码 int 计数改为
   已注册载荷的 list[int](截断半行的候选 seq;报告侧 lost=len)。
2. truncate_tail 对 offset==0 且文件非空(整文件即一条未完成半行,无任何换行)截到 0:
   spec 伪码 offset<=0 早退会让该类文件永不收敛且破坏幂等;未完成即未发生,备份已留存。
3. declare_recovered 不追加 when fixed 为空:注册载荷 fixed min_length=1,
   "用户保留原地"(keep_quarantine=False)不算修复动作 → 空动作一律不产声明。
4. 派生视图对账/FTS 落后提供者:本仓库 SessionQueryIndex 未暴露 max_seq(sid) 查询面,
   且装配层(cli.assemble_ctx)未挂 ctx.session_query/kv_rebuild → scan_session 的
   index_stale 仅在调用方注入 fts_last_seq(sid) 提供者(或 ctx.session_query.max_seq /
   ctx.fts_last_seq 可调用)时才判定,无视图提供者 = 不算落后(否则全会话被误报不健康);
   rebuild_derived_views 对未装配视图降级为日志并返回 False(派生可弃,装配后自动生效),
   对账(max_seq 可查时)不一致 → PERS-201 上抛(spec 异常表)。
5. scan_session 逐行粒度坏行(编码级坏块=单行隔离):整文件"编码级不可修复 .corrupt"
   路径归 persistence(存储层原语);格式语义不符(如首事件非 session.created)由
   session 回放校验(EVT-100/106)处置,本模块不重复造 PERS-201 分支。
6. rebuild_derived_views/auto_scan 因真实依赖形态改 async / 增可选 provider 参数:
   SessionQueryIndex.rebuild 为 async(spec 伪码 def 与真实依赖冲突,以真实形态为准)。
7. 会话被 ctx.session 持有(sid 匹配)时 repair 直接 PERS-202 拒修:文件级重写会让
   活会话的追加句柄/内存 SeqState 失同步(双开 = 空洞主因,DEP §6 禁双开)。
8. quarantine_lines 不再二次 input 问询(交互问询集中在 interactive_confirm 一次问清,
   spec 伪码对同一批坏行问两遍为残留);消费 policy.keep_quarantine 决策。
9. fsync 目录在 Windows 为尽力而为(NTFS 目录句柄受限),rename 原子替换已保证无半写。
"""
from __future__ import annotations

import inspect
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from pyharness.errors import PyHError, raise_code
from pyharness.events import (DECLARE_TYPES, Envelope, check_seq_gap,
                              declared_ranges)
from pyharness.persistence import (acquire_session_lock, default_sessions_dir,
                                   detect_truncation,
                                   flush_kwargs_of, is_aux_session_file,
                                   open_store, release_session_lock,
                                   rotated_segment_paths, session_lock_path)

log = logging.getLogger("pyharness.repair")

# ===================================================================== 常量
_QUARANTINE_RAW_CAP: int = 8192        # 隔离档单条 raw 截断上限(防爆,spec 结构表)
_TAIL_SEQ_PROBE: int = 2048            # 半行内 seq 探测窗口(信封序列化 seq 最先落)
# 空洞合法化声明事件(PRD §3.4:compacted 折叠区间 / recovered 修复区间)
# 判据唯一来源 events.DECLARE_TYPES(R14-9)
_DECLARE_TYPES: frozenset = DECLARE_TYPES


def now_ts() -> str:
    """修复/隔离文件时间戳:UTC 毫秒(文件系统安全,PRD F060 命名)。"""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")[:-3]


def _rotated_segments(path: Path) -> list[Path]:
    """本会话的轮转段 ``{sid}.{n}.jsonl``(n 升序)。

    2026-09-21 R13-5:轮转段是**真源的一部分**(replay 会合并读,见 persistence
    ``replay``),与备份/隔离档(**aux**)不同类 —— 但此前健康扫描与 repair 都按
    "aux 跳过"处理它们 ⇒ 段内整行损坏会**静默丢事件且零告警**(实测:重放少一条、
    `repair`/`auto_scan` 都不报)。故本函数把它们纳入**修复面**(不改扫描面,见 L-25)。

    2026-09-21 R14-3:段枚举判据移交 ``persistence.rotated_segment_paths``(唯一来源,
    与 ``session_data_paths`` / ``replay`` 同序;此前本模块自持一份 glob 判据)。
    """
    return rotated_segment_paths(path)


def _segment_damage(path: Path) -> list[tuple[Path, Optional[int], list[int]]]:
    """各轮转段的损坏面:**只读**扫描 → [(段路径, 尾部半行偏移|None, 中部坏行行号[])]。

    尾部半行与中部坏行分开交付:前者按"未完成即未发生"截断,后者按"隔离不删"处理
    (与主文件同口径)。尾部有半行时不再把它算作坏行(避免同一条被处理两次)。
    """
    out: list[tuple[Path, Optional[int], list[int]]] = []
    for seg in _rotated_segments(path):
        torn = detect_truncation(seg)
        bad: list[int] = []
        if torn is None:
            bad = [no for no, text in _iter_text(seg)
                   if _parse_or_none(text) is None]
        if torn is not None or bad:
            out.append((seg, torn, bad))
    return out


def _segment_scan(path: Path) -> tuple[list[int], int, list[tuple[int, int]]]:
    """轮转段的 (全部 seq, 可索引内容末 seq, 声明区间) —— 供**会话级**seq 连续性判定。

    只读;段文件缺失/不可读时**跳过**(不影响主文件判定)。坏行记跳(与主文件同口径)。
    """
    seqs: list[int] = []
    last = 0
    declared: list[tuple[int, int]] = []
    for seg in _rotated_segments(path):
        try:
            for _no, text in _iter_text(seg):
                if text is None:
                    continue
                env = _parse_or_none(text)
                if env is None:
                    continue
                seqs.append(env.seq)
                if env.type == "session.recovered":
                    declared.extend(_declared_ranges(env))
                    continue
                if _indexable(env):
                    last = max(last, env.seq)
                if env.type in _DECLARE_TYPES:
                    declared.extend(_declared_ranges(env))
        except OSError as e:                         # noqa: BLE001 段不可读不放大
            log.warning("repair: 轮转段不可读,跳过其 seq 连续性判定 file=%s why=%s",
                        seg, e)
    return seqs, last, declared


def _seg_label(seg: Path) -> str:
    """段标签(报告/审计用):``seg{n}``。"""
    m = re.search(r"\.(\d+)\.jsonl$", seg.name)
    return f"seg{m.group(1)}" if m else f"seg:{seg.name}"


def _session_content_last(path: Path) -> int:
    """**会话级**可索引内容末 seq = max(主文件, 轮转段)。

    与 ``scan_session`` 的 ``last`` **同口径**(两者都按 ``_indexable`` 判据 + 轮转段
    合并读)。2026-09-21 R14-1 二阶修:重建源 ``open_store(...).replay`` **合并读轮转段**
    (persistence),故对账水位也必须跨段取 max —— 此前只算主文件 ⇒ 主文件被截空(整文件
    半行)或其尾部全为非内容事件时,段内容使 ``view_last>0`` 而单文件 ``log_last=0``
    ⇒ **假 PERS-201**(repair 已成功却被判失败:CND-06/08 判据多源必然漂移)。
    """
    best = 0
    for _no, text in _iter_text(path):
        if text is None:
            continue
        env = _parse_or_none(text)
        if env is not None and _indexable(env) and env.seq > best:
            best = env.seq
    _seqs, seg_last, _declared = _segment_scan(path)
    return max(best, seg_last)


def _is_aux_file(path: Path) -> bool:
    """辅助文件判定:轮转/备份/隔离(以及旧 .bak)不参与全会话健康扫描。

    2026-09-21 R14-3:判据**唯一来源**移至 ``persistence.is_aux_session_file`` ——
    此前本模块自持一份、其余五处漏判据(见该函数 docstring)。
    """
    return is_aux_session_file(path)


# ================================================================= 数据结构
@dataclass(frozen=True)
class SessionHealth:
    """单会话只读健康报告(扫描产物,不修改文件)。"""

    sid: str
    path: Path
    tail_truncated: bool = False
    tail_offset: Optional[int] = None
    bad_lines: list[int] = field(default_factory=list)
    holes: list[int] = field(default_factory=list)          # 未被声明覆盖的缺失 seq
    holes_declared: list[tuple[int, int]] = field(default_factory=list)
    index_stale: bool = False                               # 视图末 seq ≠ 日志末(落后或幽灵行)
    healthy: bool = True


@dataclass(frozen=True)
class QuarantineEntry:
    """隔离记录(行号 = 主文件原行号;raw 截断 ≤8KB 防爆)。"""

    line_no: int
    raw: str
    reason: str = "parse-fail"
    archived: bool = True


@dataclass(frozen=True)
class RepairReport:
    """修复产出(F060):fixed/quarantined/lost/backup/holes/recovered_seq。

    fixed 词条 ∈ {tail-truncated, quarantine-N, seq-holes:[...], views-rebuilt};
    quarantined: 本次处理的隔离条目(archived=True = 已移隔离档,False = 保留原地);
    lost: 尾部半行含未完成事件数(0/1);
    recovered_seq: session.recovered 声明事件的 seq(无动作/声明跳过 → None)。
    幂等:已修复文件重复 repair → 无动作空报告(不追加事件)。
    """

    sid: str
    fixed: list[str] = field(default_factory=list)
    quarantined: list[QuarantineEntry] = field(default_factory=list)
    lost: int = 0
    backup_path: Optional[Path] = None
    holes_alert: list[int] = field(default_factory=list)
    recovered_seq: Optional[int] = None


@dataclass(frozen=True)
class RepairPolicy:
    """修复决策入参:auto = headless 安全默认(截断+隔离);interactive = 问询产物。"""

    mode: str = "auto"                  # "auto" | "interactive"
    drop_tail: bool = True              # 截断续跑(尾部半行未完成不假装发生)
    keep_quarantine: bool = True        # True=隔离;False=用户选择保留原地(不删)


# ================================================================= 基础工具
def _parse_or_none(text: str) -> Optional[Envelope]:
    """单行信封解析(容错):任何解析/校验失败 → None(坏行语义,PERS-201)。

    含 payload 按 type 强校验(P1-4,读侧与写侧同口径)——缺字段/错类型的行
    视为坏行隔离,不再漏进 reducer(此前只验信封、payload 坏数据延迟崩溃)。
    """
    try:
        from pyharness.events.vocab import validate_payload
        env = Envelope.model_validate_json(text)
        canonical = validate_payload(env.type, env.payload)
        if canonical is not env.payload:
            env = env.model_copy(update={"payload": canonical})
        return env
    except Exception:                    # noqa: BLE001 — 坏行记跳不中断
        return None


def _indexable(env: Any) -> bool:
    """信封是否在 FTS 索引**新增行**(repair 水位对账用;口径单源于 session_query)。

    懒导入避免 repair 顶层耦合 sqlite 模块;未装配/异常 → 保守按"可索引"计
    (退化旧口径,不改变安全方向)。CND-06/08:使日志水位与索引 max_seq 同口径
    ——非内容事件(session.created/finished、llm.request、guard.* 等)不产索引行,
    不应计入水位,否则尾部非内容即误判 stale / 误抛 PERS-201。
    """
    try:
        from pyharness.core.session_query import is_indexable
        return bool(is_indexable(getattr(env, "type", ""), env))
    except Exception:                               # noqa: BLE001 未装配:保守
        return True


def _declared_ranges(env: Envelope) -> list[tuple[int, int]]:
    """声明事件 → 合法空洞闭区间列表(§3.4 空洞合法化口径)。

    2026-09-21 R14-9:判据**唯一来源**移至 ``events.declared_ranges`` —— 此前
    ``repair`` / ``persistence`` / ``session`` / ``governance.audit`` 各写一份,其中
    ``session`` 那份**只认 compacted** ⇒ 已声明的修复空洞在回放时被重报为"未声明空洞"。
    """
    return declared_ranges(env)


def _iter_text(path: Path) -> Iterator[tuple[int, Optional[str]]]:
    """逐物理行读(行号从 1 起;PERS-201 语义):非 UTF-8 段 → (no, None) 记跳。

    \r\n 遗留容忍(rstrip 行尾);空行以 "" 交付(JSON 解析必败 = 坏行,与坏块同路)。
    """
    with open(path, "rb") as fh:
        for no, raw in enumerate(fh, 1):
            line = raw.rstrip(b"\r\n")
            if not line:
                yield no, ""
                continue
            try:
                yield no, line.decode("utf-8")
            except UnicodeDecodeError:
                yield no, None


def _iter_bytes(path: Path) -> Iterator[tuple[int, bytes]]:
    """逐物理行原始字节(行号 1 起,行尾**原样保留**;quarantine 原子重写用)。

    行尾不剥:遗留 `\\r\\n` 须随好行原样回写,否则重写把 CRLF 归一为 LF(丢 \\r,
    P3)。隔离条目展示时单独 rstrip。
    """
    with open(path, "rb") as fh:
        for no, raw in enumerate(fh, 1):
            yield no, raw


def _sessions_dir(ctx: Any) -> Path:
    """ctx.storage.sessions_dir 解析(缺省回落 L1 锚点,便于脱离装配的离线调用)。"""
    storage = getattr(ctx, "storage", None)
    sd = getattr(storage, "sessions_dir", None) if storage is not None else None
    return Path(sd).expanduser() if sd else default_sessions_dir()


def _fts_last_seq_provider(ctx: Any) -> Optional[Callable[[str], int]]:
    """派生视图末 seq 提供者(对账用,偏离 4):ctx.session_query.max_seq / ctx.fts_last_seq。

    2026-09-21 R11-2 修:此前只认前两者,而**没有任何生产调用方注入它们**(CLI 的
    ``auto_scan`` 连 ctx 都不收)⇒ 索引落后对账**从未真正启用**。现补**第三兜底**:
    由 ``cfg.storage.db_path`` 构造只读读数口(``session_query.read_max_seq``)——
    repair 只需要"db 在哪",不需要 live 索引句柄。
    """
    sq = getattr(ctx, "session_query", None)
    if sq is not None:
        mx = getattr(sq, "max_seq", None)
        if callable(mx):
            return mx                      # type: ignore[return-value]
    fl = getattr(ctx, "fts_last_seq", None)
    if callable(fl):
        return fl
    db_path = _db_path_of(ctx)
    if db_path:
        from pyharness.core.session_query import read_max_seq
        return lambda sid: read_max_seq(db_path, sid)
    return None


def _db_path_of(ctx: Any) -> Optional[str]:
    """FTS 库路径读取(鸭子类型:ctx.settings/ctx.config → storage.db_path)。"""
    cfg = (getattr(ctx, "settings", None) or getattr(ctx, "config", None)
           or ctx)
    for holder in (cfg, getattr(cfg, "storage", None)):
        p = getattr(holder, "db_path", None)
        if p:
            return str(p)
    return None


def _log_last_seq(path: Path) -> int:
    """日志末完整事件 seq(坏行记跳,只数可解析信封;0 = 无完整事件)。"""
    best = 0
    for _no, text in _iter_text(path):
        if text is None:
            continue
        env = _parse_or_none(text)
        if env is not None and env.seq > best:
            best = env.seq
    return best


def _fsync_dir(d: Path) -> None:
    """目录 fsync(rename 持久化保险;Windows 目录句柄受限 → 尽力而为,偏离 9)。"""
    if os.name == "nt":                    # NTFS 目录 fsync 需特殊句柄,rename 原子已够
        return
    try:
        fd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


# ================================================================= 健康扫描
async def scan_session(path: Path, *,
                       fts_last_seq: Optional[Callable[[str], int]] = None
                       ) -> SessionHealth:
    """单会话健康扫描(截断/坏行/空洞/索引对账),只读零修改。

    - 尾部 4KB 判半行(detect_truncation 偏移,复用 persistence 原语);
    - 严格行迭代:解码失败/JSON 信封解析失败记 bad_lines(不中断,PERS-201 语义);
    - 收集 seq 序列与声明区间(compacted/recovered),check_seq_gap 求未声明空洞;
    - 索引对账:注入 fts_last_seq(sid) 提供者时,视图末 seq ≠ 日志末 seq = stale
      (落后=索引漏事件;超前=幽灵行,见 index_stale 注释);无提供者不算 stale
      (偏离 4,避免未装配 FTS 时全会话误报)。
    损坏全部落报告字段,不抛(打开失败 OSError 上抛,由 auto_scan/repair_session
    各自按 PERS-202 语义处置)。
    """
    sid = path.stem
    if not path.exists():
        return SessionHealth(sid=sid, path=path, healthy=True)
    tail = detect_truncation(path)         # 非空且末字节 ≠ \\n → 半行起始偏移;None=完整
    bad: list[int] = []
    seqs: list[int] = []
    declared: list[tuple[int, int]] = []
    last = 0
    for no, text in _iter_text(path):
        if text is None:                   # 编码级坏块:按行记跳(PERS-201 语义)
            bad.append(no)
            continue
        env = _parse_or_none(text)
        if env is None:                    # JSON/信封非法:坏行
            bad.append(no)
            continue
        seqs.append(env.seq)
        if env.type == "session.recovered":   # 声明不产生 FTS 内容:不计入对账末 seq
            declared.extend(_declared_ranges(env))  # 但其 lost 声明仍合法化空洞
            continue
        if _indexable(env):                   # 水位与索引 max_seq 同口径(CND-06/08)
            last = max(last, env.seq)
        if env.type in _DECLARE_TYPES:
            declared.extend(_declared_ranges(env))
    # 空洞是**会话级**属性(R13-6):轮转段与主文件同属一个会话(replay 合并读),
    # 故 seq 连续性必须**跨段**判定 —— 否则主文件从 seq N+1 起会被误判为"空洞 1..N"
    # (实测:凡轮转过一次的会话恒判不健康;每次 repair 都报假空洞并追加一条
    # session.recovered ⇒ 稳定态噪声 + 重复写入)。段级坏行/半行仍按**各文件**口径
    # 分别处置(见 `_segment_damage`)。
    seg_seqs, seg_last, seg_declared = _segment_scan(path)
    if seg_seqs:
        seqs = sorted(set(seqs) | set(seg_seqs))
        declared = declared + seg_declared
        last = max(last, seg_last)
    holes = check_seq_gap(seqs, declared) if seqs else []
    index_stale = False
    if fts_last_seq is not None:
        try:
            view_last = fts_last_seq(sid)
            # 对账口径 = 视图末 seq 与日志末 seq **必须一致**(!=,非仅落后 <):
            # 落后(视图<日志)= 索引漏事件;超前(视图>日志)= 幽灵行——dispatch 先于
            # flush,session.append 落盘失败时事件已入总线被 FTS 订阅者索引,日志却
            # 没有该 seq。两者都判 stale → 触发 rebuild 整体对账(幽灵行随之清除)。
            index_stale = view_last is not None and view_last != last
        except Exception as e:             # noqa: BLE001 — 对账提供者故障不误报
            log.warning("repair: fts_last_seq(%s) 提供者异常,索引对账不判定: %s",
                        sid, e)
    return SessionHealth(
        sid=sid, path=path,
        tail_truncated=tail is not None, tail_offset=tail,
        bad_lines=bad, holes=holes, holes_declared=declared,
        index_stale=index_stale,
        healthy=not (tail is not None or bad or holes or index_stale))


async def auto_scan(sessions_dir: Path, *,
                    fts_last_seq: Optional[Callable[[str], int]] = None,
                    db_path: Optional[str] = None
                    ) -> list[SessionHealth]:
    """全会话自检(启动 bootstrap 与 repair 子命令无 sid 共用):只返回需修会话。

    目录不存在(首次运行)→ 零报告;轮转/备份/隔离辅助文件跳过;单文件打开失败
    (权限/竞态)→ 本地异常日志跳过,不崩启动。只读零修改。

    ``db_path``(2026-09-21 R11-2):ctx-less 调用方(如 CLI 启动自检)只给库路径,
    即可启用**索引落后对账**;``fts_last_seq`` 优先(显式注入时胜出)。
    """
    if fts_last_seq is None and db_path:
        from pyharness.core.session_query import read_max_seq
        fts_last_seq = lambda sid: read_max_seq(db_path, sid)   # noqa: E731
    d = Path(sessions_dir) if sessions_dir is not None else default_sessions_dir()
    if not d.exists():
        return []
    out: list[SessionHealth] = []
    for path in sorted(d.glob("*.jsonl")):
        if _is_aux_file(path):
            continue
        try:
            h = await scan_session(path, fts_last_seq=fts_last_seq)
        except PermissionError as e:
            log.warning("repair: 会话不可读跳过(目录不可读/被占用) file=%s why=%s",
                        path, e)
            continue
        except OSError as e:               # glob/读竞态:跳过单文件
            log.warning("repair: 会话扫描失败跳过 file=%s why=%s", path, e)
            continue
        if not h.healthy:
            out.append(h)
    return out


# ================================================================= 修复管线
async def repair_session(ctx: Any, sid: str, *, interactive: bool = False,
                         policy: Optional[RepairPolicy] = None) -> RepairReport:
    """F060 主入口(PRD 验收伪码直译):扫描 → 无损坏空报告(幂等)→ 先备份
    `.corrupt-{ts}` → 尾部半行截断 → 中部坏行隔离(删留决策按 policy)→ seq 空洞
    定位告警 → 派生视图整体重建 → 强同步追加 session.recovered → 返回报告。

    异常表:备份/重写落盘失败 → PERS-202(原文件未动或备份留存);会话被 ctx.session
    持有(sid 匹配,双开)→ PERS-202 拒修;不可修复编码整块由 persistence 层处置。
    """
    sdir = _sessions_dir(ctx)
    path = sdir / f"{sid}.jsonl"
    if not path.exists():                  # 不存在 = 空报告(幂等,新会话前身)
        return RepairReport(sid=sid)
    sess = getattr(ctx, "session", None)   # 禁双开:活会话文件级重写 = 句柄失同步
    if sess is not None and getattr(sess, "sid", None) == sid:
        raise_code("PERS-202", sid=sid,
                   advice="会话正被 ctx.session 持有,禁双开修复(DEP §6);"
                          "先退出该会话进程再 repair")
    lock_path = session_lock_path(path)    # 跨进程独占锁:活会话占用 → PERS-202
    acquire_session_lock(lock_path)
    try:
        return await _repair_locked(ctx, sid, path,
                                    interactive=interactive, policy=policy)
    finally:
        release_session_lock(lock_path)


async def _repair_locked(ctx: Any, sid: str, path: Path, *,
                         interactive: bool,
                         policy: Optional[RepairPolicy]) -> RepairReport:
    """repair_session 的持锁突变段(扫描 → 备份 → 截断/隔离 → 重建 → 声明)。

    调用方(repair_session)已持会话独占锁并经 ctx.session 双开守卫,故本段对目标
    文件的全部读-改-写处于单写者保护下(跨进程活写者已被锁挡在 repair 之外,不再
    出现"repair 关句柄整写时他进程活句柄写的字节静默消失")。
    """
    health = await scan_session(path, fts_last_seq=_fts_last_seq_provider(ctx))
    seg_damage = _segment_damage(path)          # 轮转段损坏面(只读;R13-5)
    if health.healthy and not seg_damage:       # 二次 repair:无动作(幂等)
        return RepairReport(sid=sid)
    pol = policy
    if pol is None:
        pol = (interactive_confirm(health) if interactive else RepairPolicy())
    backup = backup_file(path)             # ① 修复前强制备份 .corrupt-{ts}
    fixed: list[str] = []
    lost_seqs: list[int] = []
    kept: list[QuarantineEntry] = []
    if health.tail_truncated and pol.drop_tail:      # ② 尾部半行:截断续跑
        lost_seqs = _dropped_seqs(path, health.tail_offset)
        truncate_tail(path, health.tail_offset or 0)  # offset=0 = 整文件半行(偏离 2)
        fixed.append("tail-truncated")
    if health.bad_lines:                   # ③ 中部坏行:隔离不自动删
        kept = await quarantine_lines(path, health.bad_lines, pol)
        fixed += [f"quarantine-{e.line_no}" for e in kept if e.archived]
    # ⑤' 轮转段同口径修复(R13-5):它们是真源的一部分,损坏必须可见且可修 ——
    #     段级同样"修复前强制备份";尾部半行截断、中部坏行隔离(不删、字节日志保真)。
    for seg, torn_off, bad_lines in seg_damage:
        label = _seg_label(seg)
        backup_file(seg)                             # 段级备份(失败即 PERS-202 中止)
        if torn_off is not None and pol.drop_tail:
            lost_seqs += _dropped_seqs(seg, torn_off)
            truncate_tail(seg, torn_off or 0)
            fixed.append(f"{label}-tail-truncated")
        if bad_lines:
            entries = await quarantine_lines(seg, bad_lines, pol)
            kept += entries
            fixed += [f"{label}-quarantine-{e.line_no}"
                      for e in entries if e.archived]
    holes_alert = locate_holes(health)     # ④ seq 空洞:未声明 → 告警 F031 深查
    if holes_alert and not interactive:
        fixed.append("seq-holes:" + repr(holes_alert))
    rebuilt = False
    if health.index_stale or fixed:        # ⑤ 派生视图整体重建(原则 1)
        rebuilt = await rebuild_derived_views(ctx, sid)
    if rebuilt and "views-rebuilt" not in fixed:
        fixed.append("views-rebuilt")      # 结构表词条:重建动作入审计
    seq = await declare_recovered(ctx, sid, fixed, lost_seqs, kept, backup)
    return RepairReport(sid=sid, fixed=fixed, quarantined=kept,
                        lost=len(lost_seqs), backup_path=backup,
                        holes_alert=holes_alert, recovered_seq=seq)


def backup_file(path: Path) -> Path:
    """修复前强制备份:copy2 整文件 → `{sid}.corrupt-{utc_ms}.jsonl`(PRD F060 命名)。

    同 ts 备份已存在(重试/并发)→ 直接返回(幂等,不重复备份);备份权限 600
    (Windows NTFS chmod 尽力而为,失败只记日志不阻断——权限收紧非修复动作)。
    异常:OSError → PERS-202(备份优先于一切修复动作,备份失败 = 修复中止)。
    """
    dst = path.with_name(f"{path.stem}.corrupt-{now_ts()}.jsonl")
    if dst.exists():
        return dst
    try:
        shutil.copy2(path, dst)            # 元数据一并保留
    except OSError as e:
        raise_code("PERS-202", op="backup", path=str(path), why=str(e),
                   advice="备份失败,修复中止(备份优先于一切修复动作);查磁盘/权限")
    try:
        os.chmod(dst, 0o600)
    except OSError as e:
        log.warning("repair: 备份文件权限 600 收紧失败(尽力而为) dst=%s why=%s",
                    dst, e)
    log.info("repair: 修复前备份 %s", dst)
    return dst


def truncate_tail(path: Path, offset: int) -> None:
    """尾部半行原子截断:截断到 offset(最后完整行末尾)+ fsync。

    半行内未完成事件按 lost 声明(declare_recovered),绝不尝试补全;offset==0 且
    文件非空(整文件即半行,无任何换行)→ 截到 0(偏离 2:未完成即未发生)。
    异常:OSError → PERS-202(备份已存在,报错中止,禁止继续)。
    """
    if offset is None:
        return
    if offset <= 0 and path.stat().st_size == 0:
        return                              # 空文件:无半行不动
    try:
        with open(path, "r+b") as f:
            f.truncate(offset)              # 截断点 = 最后完整 \\n 后(字节级)
            f.flush()
            os.fsync(f.fileno())
    except OSError as e:
        raise_code("PERS-202", op="truncate_tail", path=str(path), why=str(e),
                   advice="截断失败(备份已在 .corrupt-{ts});文件可能半截,禁止继续")
    log.warning("repair: 尾部半行截断于 offset=%d path=%s", offset, path)


def _append_quarantine(qpath: Path, record: dict) -> bool:
    """隔离档逐条追加写(自持结构,非 Envelope;≤8KB 防爆已在调用方截断)。

    失败(磁盘/权限)→ 记日志返回 False:调用方把该行保留在主文件(隔离≠丢数据)。
    """
    try:
        with open(qpath, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            fh.flush()
        return True
    except OSError as e:
        log.warning("repair: 隔离档写失败(行保留原地) qpath=%s why=%s", qpath, e)
        return False


async def quarantine_lines(path: Path, bad: list[int],
                           pol: RepairPolicy) -> list[QuarantineEntry]:
    """中部坏行隔离(不删,删留用户定):坏行抽到 `{sid}.quarantine-{ts}.jsonl`
    (行号/原文/原因,≤8KB/条),好行写临时文件,fsync+rename 原子替换主文件。

    pol.keep_quarantine=False(交互问询选"保留原地")→ 不重写主文件,只返回
    reason="user-keep" 的只读记录(报警留痕);隔离档写失败 → 该行保留原地且
    archived=False(数据优先:隔离≠删除,任何丢行都是失败)。
    异常:临时文件/rename 失败 → PERS-202(已备份存在)。
    """
    if not bad:
        return []
    badset = set(bad)
    if not pol.keep_quarantine:            # 用户保留原地(不删不隔离)
        out: list[QuarantineEntry] = []
        for no, text in _iter_text(path):
            if no in badset:
                out.append(QuarantineEntry(
                    line_no=no, raw=(text or "")[:_QUARANTINE_RAW_CAP],
                    reason="user-keep", archived=False))
        return out
    qpath = path.with_name(f"{path.stem}.quarantine-{now_ts()}.jsonl")
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    kept: list[QuarantineEntry] = []
    try:
        with open(tmp, "wb") as out_fh:
            for no, rawb in _iter_bytes(path):
                if no not in badset:
                    out_fh.write(rawb)             # 好行原样保留(含原行尾,字节往返)
                    continue
                text = rawb.rstrip(b"\r\n").decode("utf-8", "replace")[:_QUARANTINE_RAW_CAP]
                entry = QuarantineEntry(line_no=no, raw=text,
                                        reason="parse-fail", archived=True)
                if _append_quarantine(qpath, {"line_no": no, "raw": text,
                                              "reason": "parse-fail",
                                              "archived": True}):
                    kept.append(entry)
                else:                        # 隔离档写失败:行保留原地(不丢数据)
                    out_fh.write(rawb)       # 原样(含原行尾)
                    kept.append(QuarantineEntry(line_no=no, raw=text,
                                                reason="parse-fail",
                                                archived=False))
            out_fh.flush()
            os.fsync(out_fh.fileno())        # 数据落盘再换名(无半写,DIS §8)
        os.replace(tmp, path)                # rename 原子替换:旧完整或新完整
        _fsync_dir(path.parent)
    except OSError as e:
        if tmp.exists():
            try:
                tmp.unlink()                 # 失败清理,不留临时残留
            except OSError:
                pass
        raise_code("PERS-202", op="quarantine", path=str(path), why=str(e),
                   advice="隔离重写失败(备份已在 .corrupt-{ts});重跑 repair")
    if not qpath.exists():                   # 全部隔离失败且行已保留:无隔离档
        log.warning("repair: 坏行全部保留原地(隔离档写失败),见报告 archived=False")
    log.info("repair: 中部坏行隔离 path=%s archived=%d kept=%d",
             qpath, sum(1 for e in kept if e.archived), len(kept))
    return kept


def locate_holes(health: SessionHealth) -> list[int]:
    """seq 空洞定位(声明合法化,纯函数):holes 已被 compacted/recovered 声明
    覆盖 = 合法(压缩/修复事实)不告警;未声明空洞 → 告警并触发 F031 深查线索
    (不自动补 seq,append 永远 max+1)。scan 已滤声明,此处按 spec 语义再防御一遍。
    """
    if not health.holes:
        return []
    undeclared = []
    for h in health.holes:
        if any(lo <= h <= hi for lo, hi in health.holes_declared):
            continue
        undeclared.append(h)
    if undeclared:
        log.warning("repair: 未声明 seq 空洞 %s → F031 深查线索(不回填,append "
                    "恒 max+1) sid=%s", undeclared, health.sid)
    return undeclared


def _index_handle(ctx: Any) -> tuple[Any, bool]:
    """FTS 派生视图句柄解析(**唯一入口**):返回 (句柄, 是否本函数自查构造)。

    解析序(鸭子类型,逐级兜底):
      ① ``ctx.session_query``(装配注入面;引擎**只注入在 agent ctx** 上);
      ② 兄弟形态 ``ctx.fts`` / ``ctx.engine_spine.fts``(engine 的 spine 自带);
      ③ 由 ``cfg.storage.db_path`` **惰性构造**(repair 只需"库在哪" —— 与 R11-2
         的 `read_max_seq` 同款思路,自有句柄由调用方 detach 收尾)。

    2026-09-21 R14-1 修:此前只认 ①,而 repair 的调用方(CLI 命令 / 桌面)传的是
    **外壳 ctx**(无 `session_query`)⇒ "⑤派生视图整体重建"步骤**不可达** ⇒ repair
    修完仍 `index_stale=True`(不收敛)。同 PIT-19("约定被读 ≠ 被注入")。
    """
    sq = getattr(ctx, "session_query", None)
    if sq is not None and callable(getattr(sq, "rebuild", None)):
        return sq, False
    for holder in (ctx, getattr(ctx, "engine_spine", None)):
        cand = getattr(holder, "fts", None) if holder is not None else None
        if cand is not None and callable(getattr(cand, "rebuild", None)):
            return cand, False
    db_path = _db_path_of(ctx)
    if not db_path:
        return None, False
    try:
        from pyharness.core.session_query import SessionQueryIndex
        return SessionQueryIndex(db_path=db_path), True
    except Exception as e:                           # noqa: BLE001 构造失败=未装配
        log.warning("repair: 构造 FTS 句柄失败(%s):视图重建跳过 sid=?", e)
        return None, False


async def rebuild_derived_views(ctx: Any, sid: str) -> bool:
    """派生视图整体重建(原则 1):FTS 与 storage KV 全部丢弃重建(重放,非增量修补)。

    句柄经 ``_index_handle`` 解析(装配注入面 → 兄弟形态 → cfg 惰性构造;R14-1);
    视图未装配 → 日志降级返回 False(派生可弃,装配后自动生效);重建后对账(视图
    暴露 max_seq(sid) 时):日志末 seq != 视图末 seq → PERS-201(视图落后不静默)。
    索引写失败 → PERS-202(原日志不受影响,索引=派生可再建)。
    """
    sq, owned = _index_handle(ctx)
    if owned and sq is not None:                 # 自建句柄须先装载(R14-1)
        try:
            await sq.enter(ctx)
        except Exception as e:                   # noqa: BLE001 装载失败=视图不可用
            log.warning("repair: 自建 FTS 句柄装载失败(%s):视图重建跳过 sid=%s",
                        type(e).__name__, sid)
            sq, owned = None, False
    rebuild = getattr(sq, "rebuild", None) if sq is not None else None
    storage = getattr(ctx, "storage", None)
    kv = None
    if storage is not None:
        cand = getattr(storage, "kv_rebuild", None)
        kv = cand if callable(cand) else None
    if rebuild is None and kv is None:
        log.info("repair: 派生视图(FTS/storage)未装配,整体重建跳过 sid=%s", sid)
        return False
    store = None
    view_last: Optional[int] = None
    try:
        if rebuild is not None:              # FTS 重放源:修复后的主文件
            store = open_store(
                sid, dir=_sessions_dir(ctx),
                **flush_kwargs_of(getattr(ctx, "config", None)
                                  or getattr(ctx, "settings", None)))
            attach = getattr(sq, "attach_source", None)
            if callable(attach):
                attach(sid, store)
            try:
                await rebuild(session_id=sid)
            except OSError as e:
                raise_code("PERS-202", op="views-rebuild", sid=sid, why=str(e),
                           advice="索引为派生副本可再建;真源不受影响,重跑 repair")
            log.info("repair: FTS 派生视图重建完成 sid=%s", sid)
        if kv is not None:                   # storage KV 派生缓存重建(鸭子面)
            try:
                kv(sid)
            except OSError as e:
                raise_code("PERS-202", op="kv_rebuild", sid=sid, why=str(e),
                           advice="KV 派生缓存可再建;真源不受影响")
        # 对账读数**必须在句柄有效期内**取(R14-1 二阶):自建句柄在 finally 里
        # detach 关库(``_db=None``),此后 ``max_seq`` 恒返 0 ⇒ 与日志水位不等 ⇒
        # **假 PERS-201**(修复自身引出的缺陷:重建明明成功却被判不一致)。
        mx = getattr(sq, "max_seq", None) if sq is not None else None
        if callable(mx):
            try:
                view_last = mx(sid)
            except Exception:                # noqa: BLE001 — 提供者故障降级
                log.warning("repair: 视图 max_seq(%s) 查询失败,对账跳过", sid)
    finally:
        if store is not None:
            try:
                store.close()                # 只读回放源句柄收尾
            except Exception:                # noqa: BLE001 — 清理不阻断
                log.debug("repair: FTS 回放源 close 失败", exc_info=True)
        if owned and sq is not None:         # 本函数自建的句柄:摘除并关库(R14-1)
            try:
                det = getattr(sq, "detach", None)
                if callable(det):
                    r = det(None)
                    if inspect.isawaitable(r):
                        await r
            except Exception:                # noqa: BLE001 — 清理不阻断
                log.debug("repair: 自建 FTS 句柄 detach 失败", exc_info=True)
    # 对账判定:日志末 seq == 视图末 seq(读数已在句柄有效期内取得;不一致 → PERS-201)
    if view_last is not None:
        # 对账口径:与索引 max_seq 同口径 = **可索引事件**(内容型 insert)末 seq——
        # 排除 session.recovered 声明(不索引)与一切非内容事件(created/finished/
        # llm.request/guard.* 等,不产索引行);否则尾部非内容即误抛 PERS-201。
        # 水位是**会话级**的(R14-1 二阶):重建源 replay 合并读轮转段 ⇒ 对账也须跨段
        # 取 max(与 scan_session 单源),否则主文件被截空时假 PERS-201。
        log_last = _session_content_last(_sessions_dir(ctx) / f"{sid}.jsonl")
        if log_last != view_last:
            raise_code("PERS-201", op="view-reconcile", sid=sid,
                       log_last=log_last, view_last=view_last,
                       advice="索引对账不一致(视图落后不静默);重跑 repair "
                              "或手动重建派生视图")
    return True


async def declare_recovered(ctx: Any, sid: str, fixed: list[str],
                            lost: Optional[list[int]] = None,
                            kept: Optional[list[QuarantineEntry]] = None,
                            backup: Optional[Path] = None) -> Optional[int]:
    """recovered 事件声明(修复是事件不是抹除):actor=system 强同步 append 到目标
    会话日志;声明是修复的审计落点与"空洞合法化"依据(persistence._declared_by_
    compaction 消费 fixed seq-holes/lost,后续 replay 不再告警)。

    载荷按注册词表 SessionRecoveredPayload(fixed 非空/backup/lost,偏离 1):
    fixed 为空(无修复动作,含"用户保留原地")→ 不追加(幂等,注册载荷 min_length=1);
    目标会话已终态(finished)/空文件未 created → append 语义 EVT-104/106 不允许,
    声明跳过(日志 + 报告 recovered_seq=None;审计由备份 + RepairReport 承担);
    其余 PyHError(PERS-202 落盘失败)→ 上抛(文件已修,声明可下次 repair 补,幂等
    保证不重复动作)。返回 recovered 事件 seq。
    """
    if not fixed:                            # 无修复动作:不声明(偏离 3)
        return None
    payload: dict[str, Any] = {"fixed": fixed}
    if backup is not None:
        payload["backup"] = str(backup)
    if lost:
        payload["lost"] = [int(x) for x in lost]
    from pyharness.core.session import open_session   # 惰性:避免装配期硬依赖
    store = open_store(  # 修复先于 open(CLI 序)→ 自开
        sid, dir=_sessions_dir(ctx),
        **flush_kwargs_of(getattr(ctx, "config", None)
                          or getattr(ctx, "settings", None)))
    log_ = await open_session(sid, store)
    log_._bus = _DirectBus(store)            # 直连落盘(无全局总线时声明仍强同步落盘)
    try:
        env = await log_.append("session.recovered", payload,
                                actor="system", sync=True)
    except PyHError as e:
        if e.code in ("EVT-104", "EVT-106"):  # 终态/未 created:声明不适用
            log.warning("repair: recovered 声明跳过(session=%s %s,审计以备份+"
                        "报告为准) fixed=%s", sid, e.code, fixed)
            return None
        raise                                  # PERS-202 等:上抛(声明可下次补)
    finally:
        store.close()
    log.info("repair: session.recovered 声明 sid=%s seq=%d fixed=%s lost=%s",
             sid, env.seq, fixed, lost or [])
    return env.seq


class _DirectBus:
    """SessionLog 直连落盘总线:等价 cli._attach_log_persistence 的 bus→store 订阅
    (INV-08 无回边:事件经注入 store.append 物理落盘,不自造信封)。仅服务本模块
    recovered 声明的单次落盘,不依赖全局装配。"""

    def __init__(self, store: Any) -> None:
        self._store = store

    async def emit(self, type_: str, payload: Any, mode: str = "sequential"
                   ) -> None:
        env = payload                        # SessionLog._dispatch 以 Envelope 为载荷
        await self._store.append(env, sync=True)


def _dropped_seqs(path: Path, offset: Optional[int]) -> list[int]:
    """尾部半行含未完成事件候选 seq(0/1 个):信封序列化 seq 字段最先落,半行内
    常可探测;探测不到则取"最后完整 seq + 1"(append 恒 max+1 分配的事实口径)。
    offset 为 None/文件空/剩余为空 → []。
    """
    if offset is None:
        return []
    try:
        data = path.read_bytes()[offset:]
    except OSError:
        return []
    if not data.strip():
        return []
    probe = data[:_TAIL_SEQ_PROBE]
    m = re.search(rb'"seq"\s*:\s*(\d+)', probe)
    if m:
        return [int(m.group(1))]
    last = _log_last_seq(path)               # 整文件半行且无 seq 可探:无完整事件
    return [last + 1] if last else []


# ================================================================= 交互决策
def _ask(prompt: str, default: bool = True) -> bool:
    """交互问询([Y/n] 语义);无输入(EOF/非 tty)→ 安全默认。
    特殊:prompt 含 '[K]保留' 时按 k 视同 n(用户显式保留 = 拒绝默认动作)。
    """
    try:
        ans = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return default
    if not ans:
        return default
    if "[k]" in prompt.lower() and ans in ("k", "keep"):
        return False                       # 用户选保留(拒绝截断/隔离默认动作)
    return not ans.startswith("n")


def interactive_confirm(health: SessionHealth) -> RepairPolicy:
    """交互决策(tty):尾部半行 → [T]截断续跑(默认)/[K]保留退出;中部坏行 → 隔离
    默认(保留原地可选);空洞 → F031 深查提示(不回填)。headless(auto)= 全部安全
    默认,不悬挂等待。EOFError → 回退 auto 默认(安全侧:截断+隔离,偏离 8 单次问询)。
    """
    print(f"[repair] {health.sid} 检测:尾部截断={health.tail_truncated} "
          f"坏行={len(health.bad_lines)} 空洞={health.holes}")
    drop_tail = True
    if health.tail_truncated:
        ans = _ask("尾部半行未完成事实,已备份。[T]截断续跑(默认) [K]保留退出: ",
                   default=True)
        if not ans:                          # 用户选择保留:会话不可续跑
            drop_tail = False
    keep_q = True
    if health.bad_lines:
        keep_q = _ask(f"隔离 {len(health.bad_lines)} 行坏数据? [Y/n](n=保留原地): ",
                      default=True)
    if health.holes:
        print(f"[repair] 存在未声明 seq 空洞 {health.holes} → F031 深查线索;"
              "空洞不回填,append 恒 max+1")
    return RepairPolicy(mode="interactive", drop_tail=drop_tail,
                        keep_quarantine=keep_q)


# ================================================================= 隔离区查看
def view_quarantine(sid: str, sessions_dir: Path) -> list[QuarantineEntry]:
    """隔离区查看(删留决策数据源):列出 `{sid}.quarantine-*.jsonl` 全部隔离条目
    (行号/原文/原因/归档)。repair 本身永不删除;隔离档自持结构(非 Envelope,不强
    校验),隔离档自身损坏 → 跳过该行并日志(隔离档非真源,损坏无害)。
    """
    out: list[QuarantineEntry] = []
    d = Path(sessions_dir)
    for q in sorted(d.glob(f"{sid}.quarantine-*.jsonl")):
        try:
            lines = q.read_text(encoding="utf-8").splitlines()
        except OSError as e:
            log.warning("repair: 隔离档读失败跳过 %s why=%s", q, e)
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError as exc:
                log.warning("repair: 隔离档行损坏跳过 %s:%s", q, exc)
                continue
            out.append(QuarantineEntry(
                line_no=int(e.get("line_no", 0)),
                raw=str(e.get("raw", ""))[:_QUARANTINE_RAW_CAP],
                reason=str(e.get("reason", "parse-fail")),
                archived=bool(e.get("archived", True))))
    return out


__all__ = [
    # 数据结构
    "SessionHealth", "QuarantineEntry", "RepairReport", "RepairPolicy",
    # 函数清单(spec 契约面)
    "auto_scan", "scan_session", "repair_session", "backup_file",
    "truncate_tail", "quarantine_lines", "locate_holes",
    "rebuild_derived_views", "declare_recovered", "interactive_confirm",
    "view_quarantine", "now_ts",
]
