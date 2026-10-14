"""Repair scan: complete migrated implementation."""
from __future__ import annotations
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional
from pyharness.events import DECLARE_TYPES, Envelope, check_seq_gap, declared_ranges
from pyharness.persistence import default_sessions_dir, detect_truncation, is_aux_session_file, rotated_segment_paths


log = logging.getLogger("pyharness.repair")

_QUARANTINE_RAW_CAP: int = 8192        # 隔离档单条 raw 截断上限(防爆,spec 结构表)

_TAIL_SEQ_PROBE: int = 2048            # 半行内 seq 探测窗口(信封序列化 seq 最先落)

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

# Preserve historical import and pickle identities through the compatibility facade.
SessionHealth.__module__ = 'pyharness.repair'
QuarantineEntry.__module__ = 'pyharness.repair'
RepairReport.__module__ = 'pyharness.repair'
RepairPolicy.__module__ = 'pyharness.repair'
