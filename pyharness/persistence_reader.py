"""Persistence reader: complete migrated implementation."""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional
from pyharness.events import Envelope
from pyharness.events.vocab import validate_payload

from pyharness.persistence_policy import _TAIL_WINDOW, default_sessions_dir, log




def rotated_segment_paths(main_path: Any) -> list:
    """该会话的轮转段 ``{sid}.{n}.jsonl``(n 升序)—— 真源的一部分,非独立会话。"""
    main_path = Path(main_path)
    pat = re.compile(rf"^{re.escape(main_path.stem)}\.(\d+)\.jsonl$")
    segs = [p for p in main_path.parent.glob(f"{main_path.stem}.*.jsonl")
            if pat.match(p.name)]
    return sorted(segs, key=lambda p: int(pat.match(p.name).group(1)))

def session_data_paths(main_path: Any) -> list:
    """该会话**真源**的全部文件:轮转段(序号升序)+ 主文件(与 ``replay`` 同序)。

    唯一"会话数据面"来源(R14-3):按会话聚合的调用方(replay / 用量统计)必须用它,
    否则要么漏段(欠计)要么把段当独立会话(重计)。
    """
    main_path = Path(main_path)
    return rotated_segment_paths(main_path) + [main_path]

def _iter_replay(paths: list, quarantine: set, *,
                 tolerate_unreadable: bool = False) -> Iterator[Envelope]:
    """会话真源(按 ``paths`` 给定顺序的多文件)逐事件回放(**唯一回放实现**)。

    空行跳过;坏行(解码失败/JSON 非法/信封校验失败)行号记入 ``quarantine`` 并**继续**
    —— 回放 = 恢复 = 审计同一路径,绝不因单行损坏中断(§3.8)。行号按文件从 1 起
    (多文件时可能重号;隔离判定以 repair 主文件扫描为准)。

    ``tolerate_unreadable``:整个文件打不开(权限/被占/是目录)时,``False`` = 上抛
    (``SessionStore`` 恢复语义:读不到主文件就是读不到基线),``True`` = 告警跳过
    (``SessionReader`` 只读派生语义:离线命令不该因单个会话不可读而整体失败)。

    2026-09-21 R14-5:从 ``SessionStore.replay`` 抽出,使**只读**读取方
    (``SessionReader``)复用同一实现 —— 此前只读命令只能借 ``open_store`` 读,而它会取
    INV-07 写锁 ⇒ 把所有会话锁住。
    """
    for p in paths:
        try:
            fh = open(p, "rb")
        except FileNotFoundError:
            continue                             # 段/主文件尚未存在:跳过(原语义)
        except OSError as e:
            if not tolerate_unreadable:
                raise
            log.warning("PERS-201 域:回放源不可读,跳过 file=%s why=%s", p, e)
            continue
        with fh:
            for no, raw in enumerate(fh, 1):
                line = raw.rstrip(b"\r\n")       # 容忍 \r\n 遗留(Windows 兼容)
                if not line:
                    continue                     # 空行跳过(轮转残留容忍)
                try:
                    text = line.decode("utf-8")
                    env = Envelope.model_validate_json(text)  # 信封一次校验
                    # 读侧 payload 强校验(P1-4):写侧拒坏 payload、读侧曾漏网——
                    # 缺字段/错类型的行"合法"通过回放,到 reducer/repair 深处才
                    # KeyError 崩。校验失败落入下方 PERS-201 隔离(与写侧同口径)。
                    canonical = validate_payload(env.type, env.payload)
                    if canonical is not env.payload:
                        env = env.model_copy(update={"payload": canonical})
                    yield env
                except Exception:                # noqa: BLE001
                    # 解码坏块/JSON 非法/信封校验失败统一记 PERS-201 隔离
                    quarantine.add(no)           # 中部坏行隔离(记跳不中断)
                    log.warning("PERS-201 域:坏行隔离(记跳不中断,删否经 repair) "
                                "file=%s line_no=%s", p, no)

class SessionReader:
    """**只读**会话回放源:**不取**会话写锁、不开写句柄、不建文件(R14-5)。

    与 ``SessionStore`` 的差价只在"要不要单写者锁":``SessionStore`` 可能写(append/
    flush/repair),故必须持 INV-07 锁;只读取真源的命令(如 CLI ``search`` 建索引回放源)
    却因此把所有会话一并锁住 ⇒ 其他进程开会话撞 PERS-202(实测:`search` 运行期间
    并发 open 交替得到 OPEN_OK / BLOCKED PERS-202)。

    只暴露 ``replay()``(与 ``SessionStore`` 同形,可直接作 session_query 的注入源);
    **无任何写面**。并发写者正在追加时可能读到半行 ⇒ 按坏行跳过(读侧不变式)。
    """

    def __init__(self, session_id: str, *, dir: Optional[Path] = None) -> None:
        d = Path(dir) if dir is not None else default_sessions_dir()
        self.session_id = session_id
        self.path = d / f"{session_id}.jsonl"
        self._quarantine: set = set()

    def replay(self) -> Iterator[Envelope]:
        """同 ``SessionStore.replay``(轮转段升序 + 主文件;坏行记跳不中断)。

        不可读的**单个**源文件告警跳过(``tolerate_unreadable``):只读派生用途,
        离线命令不该因某会话不可读而整体失败(与 ``_scan_usage`` / ``_segment_scan``
        同款容错)。
        """
        return _iter_replay(session_data_paths(self.path), self._quarantine,
                            tolerate_unreadable=True)

    def quarantine_info(self) -> dict:
        """坏行隔离区只读暴露(与 ``SessionStore`` 同形):{path, line_nos, count}。"""
        nos = sorted(self._quarantine)
        return {"path": str(self.path), "line_nos": nos, "count": len(nos)}

def _last_newline_before(f, end: int) -> Optional[int]:
    """在 [0, end) 内从后向前分块查找最近 \\n 的字节偏移;无换行 → None。

    detect_truncation 的半行超窗兜底:尾部窗口内无换行不代表文件前部无完整行
    ——>4096B 半行曾致返回 0 触发 repair 把含完整数据的文件整段截断(数据事故),
    故需扩大到窗口前区域定位最后完整行的切点。分块读取,避免整文件载入内存。
    """
    pos = end
    while pos > 0:
        step = min(pos, _TAIL_WINDOW)
        pos -= step
        f.seek(pos)
        i = f.read(step).rfind(b"\n")
        if i >= 0:
            return pos + i
    return None

def detect_truncation(path: Path) -> Optional[int]:
    """截断检测(只读):文件非空且末字节不是 \\n → 返回半行起始字节偏移;否则 None。

    崩溃遗留的尾部半行 = 未完成写的事实,不假装发生:open/回放不修,repair 先备份
    再截断(F060)。实现按 spec 注释意图读取尾部窗口——伪码 seek(0,2) 后未回退
    read 为空,实际以 seek(-window, 2) 读窗口再定位最后一个换行;窗口内无换行
    (半行超窗)时扩大到窗口前区域,仅当整文件都无换行(单条半行文件)才返回 0。
    """
    try:
        if not path.exists() or path.stat().st_size == 0:
            return None
    except OSError:
        return None
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        window = min(size, _TAIL_WINDOW)
        f.seek(-window, 2)                       # 回退读尾部窗口(修正伪码遗漏)
        tail = f.read(window)
        if tail.endswith(b"\n"):
            return None                          # 完整行结尾:无截断
        last_nl = tail.rfind(b"\n")
        if last_nl >= 0:
            # 偏移 = 文件大小 - 窗口内最后一个换行之后的字节数(字节级切点,保完整行)
            return size - (len(tail) - last_nl - 1)
        # 窗口内无换行:半行超窗。扩大到窗口前区域定位最后完整行;整文件仍无
        # 换行 = 真·单条半行文件,保留 offset=0 语义;否则禁止误判为 0。
        prev = _last_newline_before(f, size - window)
        return (prev + 1) if prev is not None else 0

def _read_clean(path: Path) -> list[tuple[int, str]]:
    """整文件解码为 (行号, 文本) 列表;编码级坏块 → 抛 UnicodeDecodeError。

    repair 中部坏行扫描专用:先整读保证\"中部编码坏块 = 不可修复\"可整体暴露
    (逐行容错会把编码坏块降级成单行隔离,违背 spec PERS-202 处置表)。
    """
    out: list[tuple[int, str]] = []
    with open(path, "rb") as f:
        for no, raw in enumerate(f, 1):
            line = raw.rstrip(b"\r\n")
            if not line:
                continue
            out.append((no, line.decode("utf-8")))   # 坏块在此处整体抛出
    return out

@dataclass(frozen=True)
class RepairReport:
    """repair 产出(F060):fixed/quarantined/backup_path —— session.recovered 载荷镜像。

    fixed: 修复动作清单(如 "tail-truncated" / "quarantined:[2]" / "seq-holes:[3]");
    quarantined: 坏行行号(隔离不删,人类决策);
    backup_path: 修复前强制备份(`{sid}.corrupt-{ts}.jsonl`);文件不存在 → None(空会话)。
    幂等:对已修复文件重复 repair,结果一致(无修复动作)。
    """

    fixed: list[str] = field(default_factory=list)
    quarantined: list[int] = field(default_factory=list)
    backup_path: Optional[Path] = None

# Preserve historical import and pickle identities through the compatibility facade.
SessionReader.__module__ = 'pyharness.persistence'
RepairReport.__module__ = 'pyharness.persistence'
