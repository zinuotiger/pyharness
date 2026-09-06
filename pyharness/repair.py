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
from pyharness.events import Envelope, check_seq_gap
from pyharness.persistence import (default_sessions_dir, detect_truncation,
                                   open_store)

log = logging.getLogger("pyharness.repair")

# ===================================================================== 常量
_QUARANTINE_RAW_CAP: int = 8192        # 隔离档单条 raw 截断上限(防爆,spec 结构表)
_TAIL_SEQ_PROBE: int = 2048            # 半行内 seq 探测窗口(信封序列化 seq 最先落)
# 空洞合法化声明事件(PRD §3.4:compacted 折叠区间 / recovered 修复区间)
_DECLARE_TYPES: frozenset = frozenset({"context.compacted", "session.recovered"})
# 轮转/备份/隔离等辅助文件判定:不参与健康扫描
_AUX_PATTERNS = (
    re.compile(r"\.\d+\.jsonl$"),          # 轮转 {sid}.{n}.jsonl
    re.compile(r"\.corrupt-"),             # 修复备份 {sid}.corrupt-{ts}.jsonl
    re.compile(r"\.quarantine-"),          # 隔离档 {sid}.quarantine-{ts}.jsonl
    re.compile(r"\.jsonl\.bak-"),          # persistence 草案旧备份名(只扫跳过)
)


def now_ts() -> str:
    """修复/隔离文件时间戳:UTC 毫秒(文件系统安全,PRD F060 命名)。"""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")[:-3]


def _is_aux_file(path: Path) -> bool:
    """辅助文件判定:轮转/备份/隔离(以及旧 .bak)不参与全会话健康扫描。"""
    return any(p.search(path.name) for p in _AUX_PATTERNS)


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
    index_stale: bool = False                               # 派生视图末 seq < 日志末 seq
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
    """单行信封解析(容错):任何解析/校验失败 → None(坏行语义,PERS-201)。"""
    try:
        return Envelope.model_validate_json(text)
    except Exception:                    # noqa: BLE001 — 坏行记跳不中断
        return None


def _declared_ranges(env: Envelope) -> list[tuple[int, int]]:
    """声明事件 → 合法空洞闭区间列表(§3.4 空洞合法化口径)。

    context.compacted.ranges = [lo, hi] 折叠闭区间;session.recovered 的 lost seq
    与 fixed 中 "seq-holes:[...]" 词条(persistence._declared_by_compaction 同款)
    逐号声明为单点闭区间。
    """
    out: list[tuple[int, int]] = []
    if env.type == "context.compacted":
        for pair in (env.payload.get("ranges") or []):
            lo, hi = int(pair[0]), int(pair[1])
            if lo <= hi:
                out.append((lo, hi))
    elif env.type == "session.recovered":
        for s in (env.payload.get("lost") or []):
            try:
                out.append((int(s), int(s)))
            except (TypeError, ValueError):
                continue
        for item in (env.payload.get("fixed") or []):
            m = re.match(r"^seq-holes:\[(.*)\]$", str(item))
            if m and m.group(1).strip():
                for x in m.group(1).split(","):
                    if x.strip().isdigit():
                        s = int(x)
                        out.append((s, s))
    return out


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
    """逐物理行原始字节(行号 1 起,行尾已剥;quarantine 原子重写用,保字节往返)。"""
    with open(path, "rb") as fh:
        for no, raw in enumerate(fh, 1):
            yield no, raw.rstrip(b"\r\n")


def _sessions_dir(ctx: Any) -> Path:
    """ctx.storage.sessions_dir 解析(缺省回落 L1 锚点,便于脱离装配的离线调用)。"""
    storage = getattr(ctx, "storage", None)
    sd = getattr(storage, "sessions_dir", None) if storage is not None else None
    return Path(sd).expanduser() if sd else default_sessions_dir()


def _fts_last_seq_provider(ctx: Any) -> Optional[Callable[[str], int]]:
    """派生视图末 seq 提供者(对账用,偏离 4):ctx.session_query.max_seq 或 ctx.fts_last_seq。

    本仓库 SessionQueryIndex 未暴露 max_seq → 装配层挂载面(注入后索引落后自动检测)。
    """
    sq = getattr(ctx, "session_query", None)
    if sq is not None:
        mx = getattr(sq, "max_seq", None)
        if callable(mx):
            return mx                      # type: ignore[return-value]
    fl = getattr(ctx, "fts_last_seq", None)
    return fl if callable(fl) else None


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
    - 索引落后:注入 fts_last_seq(sid) 提供者时,日志末 seq > 视图末 seq = stale;
      无提供者不算落后(偏离 4,避免未装配 FTS 时全会话误报)。
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
        last = max(last, env.seq)
        if env.type in _DECLARE_TYPES:
            declared.extend(_declared_ranges(env))
    holes = check_seq_gap(seqs, declared) if seqs else []
    index_stale = False
    if fts_last_seq is not None:
        try:
            view_last = fts_last_seq(sid)
            index_stale = last > 0 and view_last is not None and last > view_last
        except Exception as e:             # noqa: BLE001 — 对账提供者故障不误报
            log.warning("repair: fts_last_seq(%s) 提供者异常,索引落后不判定: %s",
                        sid, e)
    return SessionHealth(
        sid=sid, path=path,
        tail_truncated=tail is not None, tail_offset=tail,
        bad_lines=bad, holes=holes, holes_declared=declared,
        index_stale=index_stale,
        healthy=not (tail is not None or bad or holes or index_stale))


async def auto_scan(sessions_dir: Path, *,
                    fts_last_seq: Optional[Callable[[str], int]] = None
                    ) -> list[SessionHealth]:
    """全会话自检(启动 bootstrap 与 repair 子命令无 sid 共用):只返回需修会话。

    目录不存在(首次运行)→ 零报告;轮转/备份/隔离辅助文件跳过;单文件打开失败
    (权限/竞态)→ 本地异常日志跳过,不崩启动。只读零修改。
    """
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
    health = await scan_session(path, fts_last_seq=_fts_last_seq_provider(ctx))
    if health.healthy:                     # 二次 repair:无动作(幂等)
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
                    out_fh.write(rawb + b"\n")     # 好行全保留(字节往返)
                    continue
                text = rawb.decode("utf-8", "replace")[:_QUARANTINE_RAW_CAP]
                entry = QuarantineEntry(line_no=no, raw=text,
                                        reason="parse-fail", archived=True)
                if _append_quarantine(qpath, {"line_no": no, "raw": text,
                                              "reason": "parse-fail",
                                              "archived": True}):
                    kept.append(entry)
                else:                        # 隔离档写失败:行保留原地(不丢数据)
                    out_fh.write(rawb + b"\n")
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


async def rebuild_derived_views(ctx: Any, sid: str) -> bool:
    """派生视图整体重建(原则 1):FTS 与 storage KV 全部丢弃重建(重放,非增量修补)。

    ctx.session_query.rebuild(session_id=sid)(async,偏离 6)+ ctx.storage.kv_rebuild
    鸭子调用;视图未装配 → 日志降级返回 False(派生可弃,装配后自动生效);重建后
    对账(视图暴露 max_seq(sid) 时):日志末 seq != 视图末 seq → PERS-201(视图落后
    不静默)。索引写失败 → PERS-202(原日志不受影响,索引=派生可再建)。
    """
    sq = getattr(ctx, "session_query", None)
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
    try:
        if rebuild is not None:              # FTS 重放源:修复后的主文件
            store = open_store(sid, dir=_sessions_dir(ctx))
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
    finally:
        if store is not None:
            try:
                store.close()                # 只读回放源句柄收尾
            except Exception:                # noqa: BLE001 — 清理不阻断
                log.debug("repair: FTS 回放源 close 失败", exc_info=True)
    # 对账:日志末 seq == 视图末 seq(视图挂 max_seq 提供者时;不一致 → PERS-201)
    mx = getattr(sq, "max_seq", None) if sq is not None else None
    if callable(mx):
        view_last = None
        try:
            view_last = mx(sid)
        except Exception:                    # noqa: BLE001 — 提供者故障降级
            log.warning("repair: 视图 max_seq(%s) 查询失败,对账跳过", sid)
        if view_last is not None:
            # 对账口径:内容日志末 seq(排除 session.recovered 声明——声明不被
            # FTS 索引,若计入则每次 repair 声明后视图恒落后 → 永不收敛)
            log_last = 0
            for _no, text in _iter_text(_sessions_dir(ctx) / f"{sid}.jsonl"):
                if text is None:
                    continue
                env = _parse_or_none(text)
                if env is not None and env.type != "session.recovered" \
                        and env.seq > log_last:
                    log_last = env.seq
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
    store = open_store(sid, dir=_sessions_dir(ctx))  # 修复先于 open(CLI 序)→ 自开
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
