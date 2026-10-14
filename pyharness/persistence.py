r"""pyharness/persistence.py — 会话日志持久化 (specs/persistence.py.md; F011/F060)

会话真源的物理形态:事件逐行 JSONL append(UTF-8 单行信封+payload)、攒批/强同步
双速 flush、原子写(同目录临时文件 + fsync + rename)、轮转(>50MB)、截断检测与
写通道失败状态机(suspended →(通道恢复)→ normal)。

**F060 修复入口在 ``pyharness/repair.py``**(``repair_session``:备份 → 修复 →
``session.recovered`` 声明;CLI `repair` / 桌面 / 启动自检皆走它)。本模块的
``SessionStore.repair()`` 是**早期面**(阶段 6 之前先行落地):生产**零调用者**、
仅由 ``tests/unit/test_persistence.py`` 使用 —— 见 **L-27**。**不要**在生产路径
调用它。备份命名已按 ``specs/repair.py.md`` 的冲突更正统一为
``{sid}.corrupt-{ts}.jsonl``(以 PRD F060 为准)。

不变式(INV-01 只追加,INV-07 单写者,INV-08 无回边):
- 正常写路径纯 append 句柄,无就地改写;一切重写(repair 截断、轮转 rename)
  走同目录临时文件/rename 原子路径,且修复前强制备份。
- 事件信封只经本模块写盘,不自造信封(seq/ts 只能框架分配,EVENT-SCHEMA §1.1-4):
  本模块产出的 system.error / session.recovered 一律经注入的 on_system_event 回调
  上抛(由装配层/bus 日志订阅者注入),保拓扑无回边、不反向 import session。

损坏标记 / 隔离约定(供阶段 6 pyharness/repair 模块消费,本模块先行落地):
- 隔离区:_quarantine:set[int] 行号(repair 决策/用户查看),经 quarantine_info()
  暴露;坏行只记跳隔离、绝不自动删(中部坏行 = 人类决策)。
- 备份命名(F060 权威):``{sid}.corrupt-{ts}.jsonl``（``repair.backup_file`` 与本模块
  早期面 ``SessionStore.repair`` 现已**同名族**，见 specs/repair.py.md 的命名冲突更正）；
  历史文件名 ``{sid}.jsonl.bak-{ts}`` 仍由 aux 判据 ``\.jsonl\.bak-`` 只扫跳过。
- 不可修复(编码级坏块):原文件 rename 为 {sid}.jsonl.corrupt-{ts} 保留并
  PERS-202 明确报错,不覆盖（与备份名不同名 ⇒ 不会互相覆盖）。
- 轮转命名:{sid}.{n}.jsonl(n 从 1 递增),重放按 {sid}.1..n → {sid}.jsonl 序号合并。
- RepairReport(fixed/quarantined/backup_path) = session.recovered 事件的
  fixed/quarantined/backup 载荷镜像(F060)。

文件布局偏离:spec 头部标注 pyharness/core/persistence.py(8 脊柱归位计划);
当前仓库 spine 模块平铺包根(events/config/errors 同层),故落地
pyharness/persistence.py,待 core/ 归位阶段整体迁移(契约不变)。

错误全走 raise_code(PERS-2xx);外部依赖:events.Envelope、errors.raise_code、
config.DEFAULTS(轮转/攒批参数)、log。
"""
from __future__ import annotations

import logging
import json
import os
import re
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Iterator, Optional

from pyharness.config import DEFAULTS
from pyharness.errors import PyHError, raise_code
from pyharness.events import Envelope, SYNC_TYPES, declared_ranges
from pyharness.events.vocab import validate_payload

log = logging.getLogger("pyharness.persistence")

# ===================================================================== 常量
# L1 权威默认值(config.DEFAULTS 唯一落地;全量 config 解析在装配层完成后再注入)
_ROTATE_BYTES: int = DEFAULTS["storage"]["jsonl"]["rotate_bytes"]     # 50MB
_FLUSH_BATCH: int = DEFAULTS["log"]["jsonl"]["flush_batch"]           # 64 条
_FLUSH_INTERVAL_S: float = DEFAULTS["log"]["jsonl"]["flush_interval_s"]  # 0.5s
_DEFAULT_SESSIONS_DIR: str = DEFAULTS["storage"]["sessions_dir"]      # ~/.pyharness/sessions

# 重试队列上限(PERS-202 暂停判据之二;PARAMETER-ANCHOR 无锁死,沿用 DIS 伪码 192)
_RETRY_Q_LIMIT = 192

# 连续失败上限(PERS-202 暂停判据之一;GAP-2)。**全部写路径共用此单一阈值**——
# 修复前只有 ``_flush_batch``(异步攒批)检查它,而 ``append(sync=True)`` /
# ``flush()`` 只累加计数不判阈值 ⇒ 强同步与公开 flush 路径在磁盘持续故障时
# 永不进入失败状态(会话不暂停、retry_q 无界增长)。
_FAIL_STREAK_LIMIT: int = 3

# 只读检测尾部窗口(半行定位范围)
_TAIL_WINDOW = 4096

# ================================================================= 工具函数
def _resolve_by_seq(rows: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """写前按 seq 归并(保序)并做**一致性校验**(fail-closed)。

    ``seq`` 是事件身份的一部分——框架经 ``SeqState`` 单调分配、``SessionLog``
    另有 EVT-101 失步闸,故同一 seq 只可能代表同一事件。据此:

    - 同 seq + **相同行** ⇒ 去重(同 seq 重放同一事件,幂等);
    - 同 seq + **不同行** ⇒ **数据一致性冲突** → ``PERS-202`` 上抛;
      **不 first-wins、不 last-wins、不静默覆盖**(丢哪一份都是丢事件)。

    返回按 seq 升序的写序列(物理写序 = seq 序)。**本函数无副作用**:在任何
    队列状态变更之前调用,冲突时原队列保持不动。
    """
    seen: dict[int, str] = {}
    for seq, line in rows:
        prev = seen.get(seq)
        if prev is None:
            seen[seq] = line
        elif prev != line:
            raise_code("PERS-202", op="seq_conflict", seq=seq,
                       why="同一 seq 出现不同内容:seq 是事件身份的一部分,"
                           "同一 seq 只能是同一事件",
                       advice="拒绝静默覆盖(不 first/last-wins);查是否绕过"
                              "框架自报 seq 或存在重复写入")
    return [(s, seen[s]) for s in sorted(seen)]


def now_ts() -> str:
    """UTC 时间戳(备份/损坏文件命名用):YYYYMMDDTHHMMSSZ。"""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def default_sessions_dir() -> Path:
    """默认会话日志目录(可配):L3 env 优先(PH_STORAGE_SESSIONS_DIR),否则 L1 锚点。

    L2(config.yaml) 定制由装配层阶段 2 统一解析后经 dir= 注入——本模块不在运行期
    读用户配置文件(避免隐藏副作用),此处只落地 L1/L3 快捷解析。
    """
    raw = os.environ.get("PH_STORAGE_SESSIONS_DIR", _DEFAULT_SESSIONS_DIR)
    return Path(raw).expanduser()


# ================================================================= 跨进程文件锁
def _os_lock_fd(fd: int) -> None:
    """对 fd 取 OS 级**非阻塞**独占锁;已被占 → OSError。Windows msvcrt / POSIX fcntl。

    锁独立于 JSONL 追加句柄(锁文件另开),故 store 内 close/reopen 句柄(轮转/截断
    重写)不会丢锁。msvcrt 锁首字节,fcntl 锁整文件。
    """
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)      # 锁首字节;被占抛 OSError
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _os_unlock_fd(fd: int) -> None:
    """释放 fd 上的 OS 级锁(尽力;失败不阻断 close)。"""
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError as e:                            # noqa: BLE001 释放失败不阻断
        log.warning("PERS-202 域:文件锁释放失败 why=%s", e)


class _FileLock:
    """会话日志跨进程独占锁(INV-07 单写者)。

    在 lock_path 上取 OS 级非阻塞独占锁,防两进程同写一 JSONL 造成 seq 重复 /
    轮转 os.replace 互覆 / 重写时他进程活句柄写的字节静默消失。锁文件独立于 JSONL
    且**不删除**(留 0 字节 .lock 属标准做法;删锁反有 release↔unlink 竞态)。
    """

    def __init__(self, lock_path: Path) -> None:
        self._path = lock_path
        self._fd: Optional[int] = None

    def try_acquire(self) -> bool:
        """非阻塞取锁;成功 True。冲突/IO 失败 → False(不抛,由调用方转 PERS-202)。"""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(str(self._path), os.O_RDWR | os.O_CREAT, 0o600)
        except OSError:
            return False
        try:
            _os_lock_fd(fd)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        return True

    def release(self) -> None:
        """释放锁(幂等);不删锁文件。"""
        if self._fd is None:
            return
        try:
            _os_unlock_fd(self._fd)
        finally:
            fd, self._fd = self._fd, None
            os.close(fd)


# 进程内锁注册表:{resolved lock path -> [_FileLock, refcount]}。同进程多 store 打开
# 同一会话(如桌面 + 进程内 repair)复用同一 OS 锁,不误判为跨进程冲突。
_LOCKS: dict[str, list] = {}
_LOCKS_MUTEX = threading.RLock()


def acquire_session_lock(lock_path: Path) -> None:
    """取会话日志独占锁(进程内引用计数可重入);跨进程冲突 → PERS-202。

    调用方须在 finally 里配对 release_session_lock(lock_path)。
    """
    with _LOCKS_MUTEX:
        key = str(lock_path)
        ent = _LOCKS.get(key)
        if ent is not None:
            ent[1] += 1
            return
        lock = _FileLock(lock_path)
        if not lock.try_acquire():
            raise_code("PERS-202", op="lock", path=str(lock_path),
                       advice="另一进程正在写/修复该会话;先结束该进程再打开")
        _LOCKS[key] = [lock, 1]


def release_session_lock(lock_path: Path) -> None:
    """释放会话日志独占锁(引用计数归零才真正解锁;幂等)。"""
    with _LOCKS_MUTEX:
        key = str(lock_path)
        ent = _LOCKS.get(key)
        if ent is None:
            return
        ent[1] -= 1
        if ent[1] <= 0:
            del _LOCKS[key]
            ent[0].release()


def session_lock_held(lock_path: Path) -> bool:
    """该会话是否**正被其他持有者占用**(只探测,不改语义;R14-2)。

    启动自检不应把**活会话**当作可修对象:被别的进程持有的会话既不该被报为"待修",
    本进程也修不动(``repair_session`` 取锁即 PERS-202)。此前自检会把"索引攒批窗口内
    的正常会话"报为损坏 → ``_ensure_repair`` 撞 PERS-202 并**上抛** ⇒ 第二个 CLI 启动
    直接失败(两进程实测复现)。

    语义:同进程已持有(``_LOCKS`` 命中)→ True;锁文件不存在 → False(**不**新建);
    跨进程 ``try_acquire`` 成功 → 立即归还并返回 False。纯探测,不留副作用。

    **残余**(如实标注):探测在被占用时会**失败取锁**(不持有,无窗口);只有在会话
    **空闲**时才会短暂持有再归还 —— 该微秒级窗口内若有他进程正好 ``open_store`` 同一
    会话,会收到一次 PERS-202(可重试)。量级远小于它修掉的"第二个 CLI 启动即失败",
    且与既有取锁语义同源(无新的锁类型)。
    """
    if str(lock_path) in _LOCKS:
        return True
    if not lock_path.exists():
        return False                     # 从未取过锁:未被占用(不新建锁文件)
    lock = _FileLock(lock_path)
    if lock.try_acquire():
        lock.release()                   # 探针成功:立即归还(锁文件不删,见 _FileLock)
        return False
    return True


def session_lock_path(path: Path) -> Path:
    """会话日志对应的锁文件路径(``{sid}.jsonl.lock``;不匹配 repair 的 *.jsonl 扫描)。"""
    return path.with_name(path.name + ".lock")


# 会话**辅助档**命名(轮转段/修复备份/隔离档/旧备份名):它们**属于**某会话的字节
# 历史,但**不是独立会话**。这是判定该事实的**唯一来源**(R14-3)—— 此前只有
# ``repair`` 用真判据,其余五处各自用 ``stem.startswith("s-")``,而
# ``{sid}.1.jsonl`` / ``{sid}.corrupt-*`` / ``{sid}.quarantine-*`` 的 stem 同样以
# ``s-`` 开头 ⇒ 辅助档被当成独立会话(实测:``search`` 撞 fts_rows 主键冲突崩成
# PERS-202、桌面会话列表混入修复备份、``budget/stats`` 重复计数)。
AUX_SESSION_PATTERNS: tuple = (
    re.compile(r"\.\d+\.jsonl$"),          # 轮转段 {sid}.{n}.jsonl
    re.compile(r"\.corrupt-"),             # 修复备份 {sid}.corrupt-{ts}.jsonl
    re.compile(r"\.quarantine-"),          # 隔离档 {sid}.quarantine-{ts}.jsonl
    re.compile(r"\.jsonl\.bak-"),          # persistence 草案旧备份名
)


def is_aux_session_file(path: Any) -> bool:
    """该文件是否会话**辅助档**(轮转段/修复备份/隔离档)—— 属于某会话但非独立会话。"""
    return any(p.search(Path(path).name) for p in AUX_SESSION_PATTERNS)


def session_log_paths(d: Any) -> list:
    """枚举目录下的会话**主文件**(每会话恰一个;排序稳定;目录不存在 → [])。

    **唯一枚举点**(R14-3):一并排除非会话命名与全部辅助档。凡"把目录里的 ``*.jsonl``
    当作会话"的调用方都该走这里,而不是各自 ``glob("*.jsonl")`` +
    ``stem.startswith("s-")`` —— 后者对辅助档**恒为真**,是五处同源的漏判据。
    """
    d = Path(d)
    if not d.is_dir():
        return []
    return [p for p in sorted(d.glob("*.jsonl"))
            if p.stem.startswith("s-") and not is_aux_session_file(p)]


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


# ================================================================= RepairReport
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


# ================================================================= SessionStore
class SessionStore:
    """单个会话的 JSONL 存储(单写者纪律 INV-07)。

    字段(spec 关键数据结构):session_id / path / _fh(append 句柄)/ _pending
    (攒批缓冲)/ _retry_q(失败重试,拒新不丢旧)/ _fail_streak / _quarantine /
    SYNC_TYPES。写通道状态机:攒批 →(满 batch/间隔定时器)→ flush;强同步三类
    立即 write+flush;OSError 重试 ≤3,3 败 → system.error(PERS-202)+ 会话暂停;
    repair → recovering → normal。间隔定时器的**持有者**是 ``EngineSpine``
    (``start_flush_ticker``;本类只暴露 ``flush_interval_s`` 读面,见 GAP-13)。
    """

    # 强同步事件族(与 events.vocab 同一 frozenset,单一真源在词表)
    SYNC_TYPES: frozenset = SYNC_TYPES

    def __init__(self, session_id: str, path: Path, fh, *,
                 rotate_bytes: Optional[int] = None,
                 flush_batch: Optional[int] = None,
                 flush_interval_s: Optional[float] = None,
                 lock_path: Optional[Path] = None,
                 on_system_event: Optional[Callable[[str, dict], Awaitable]] = None):
        self.session_id = session_id
        self.path = path
        self._fh = fh                             # 追加句柄(append,UTF-8,单写者)
        self._lock_path = lock_path               # 跨进程独占锁文件(open_store 持有)
        self._close_mutex = threading.RLock()
        self._scan_offset = 0
        self._committed_rows: dict[int, dict] = {}
        self._segments_scanned = False
        self._closing = False
        self.rotate_bytes = rotate_bytes or _ROTATE_BYTES
        self.flush_batch = flush_batch or _FLUSH_BATCH
        self.flush_interval_s = flush_interval_s or _FLUSH_INTERVAL_S
        self.on_system_event = on_system_event    # 注入回调(装配层/bus 订阅者)
        self._pending: deque[tuple[int, str]] = deque()   # 攒批缓冲(seq, line)
        # 打开时的"崩溃残片"标记(R13-2):文件非空且末字节非 \n ⇒ 首写前补行尾分隔,
        # 否则新事件会与残片拼在同一物理行而不可解析(静默丢事件)。
        self._torn_tail: bool = detect_truncation(path) is not None
        self._retry_q: deque[tuple[int, str]] = deque()   # 写失败重试队列
        self._fail_streak = 0                     # 连续失败计数(≥3 暂停)
        self._quarantine: set[int] = set()        # 坏行行号隔离区(repair/查看)
        self._suspended = False                   # 暂停中:拒新不丢旧

    # ------------------------------------------------------------ 关闭
    def close(self) -> None:
        """收尾:flush + 关追加句柄 + 释放跨进程锁(会话终态/进程退出前调用)。"""
        with self._close_mutex:
            self._closing = True
            errors = []
            lock_path, self._lock_path = self._lock_path, None
            if not self._fh.closed:
                try:
                    rows = _resolve_by_seq(list(self._retry_q) + list(self._pending))
                    if rows:
                        self._write_batch(rows)
                        self._retry_q.clear()
                        self._pending.clear()
                    self._fh.flush()
                except BaseException as exc:
                    errors.append(exc)
                try:
                    self._fh.close()
                except BaseException as exc:
                    errors.append(exc)
            if lock_path is not None:
                try:
                    release_session_lock(lock_path)
                except BaseException as exc:
                    errors.append(exc)
            if errors:
                for exc in errors[1:]:
                    errors[0].add_note(f'additional cleanup failure: {type(exc).__name__}')
                raise errors[0]

    def ensure_writable(self) -> None:
        """写入前置检查；调用方在分配事件前调用，避免总线隔离拒写异常。"""
        if self._closing or self._suspended or self._fh.closed:
            raise_code("PERS-202", hint="会话写通道暂停或已关闭;恢复后再提交")

    # ------------------------------------------------------------ 落盘主路径
    async def append(self, env: Envelope, sync: bool = False) -> None:
        """Envelope → 单行落盘(F011 双速写)。

        sync=True(强同步三类:user.message/guard.rejected/approval.* 等):立即
        write+flush,成功才返回(崩溃一致性锚点);否则入 _pending 攒批(满 batch
        即刷,间隔定时器兜底 —— **定时器不在本类**:由 ``EngineSpine`` 持有
        (``start_flush_ticker``,间隔读本实例 ``flush_interval_s``),见 GAP-13)。
        异常:强同步 OSError → PERS-202 直接抛;异步 3 次重试仍败 → PERS-202 +
        会话暂停;暂停中(拒新不丢旧)→ PERS-202。
        """
        self.ensure_writable()                   # 拒新不丢旧:暂停期拒绝新事件
        line = env.model_dump_json() + "\n"       # 信封+payload 拍平单行(§3.6)
        if sync:                                  # 强同步点:成功才返回
            # 入 pending + 立即 flush:物理序 = 入队序 = seq 序。若直接 write,
            # 会与非 sync 攒批行交错 → 文件物理序 ≠ seq 序(5 在 3/4 前),
            # 回放遇 seq 倒退即弃 → 重启后 0 事件(实测 bug)。
            try:
                self._pending.append((env.seq, line))
                await self._flush_pending_all()
            except OSError as e:
                self._fail_streak += 1
                if self._failure_state_reached():   # GAP-2:强同步同样受阈值约束
                    await self._enter_failure_state(why=str(e))
                    raise_code("PERS-202", seq=env.seq,
                               advice="会话暂停(fail-streak/重试队列达阈值),"
                                      "repair 后恢复;本行仍在重试队列")
                raise_code("PERS-202", seq=env.seq, why=str(e),
                           advice="落盘通道故障;跑 repair(F060)")
        else:                                     # 普通事件:攒批(内存即对订阅者可见)
            self._pending.append((env.seq, line))
            if len(self._pending) >= self.flush_batch:   # ≥64 条 → 立即批量 flush
                await self._flush_batch()
            # 间隔定时器兜底:由 EngineSpine.start_flush_ticker 周期性调 flush()
            # (append 内不 sleep;GAP-13 前该定时器不存在,注释与实现不符)
        self._maybe_rotate()                      # >50MB → 轮转(§8.3.4)

    async def _flush_pending_all(self) -> None:
        """内部:全量 pending + 重试队列写盘(强同步路径用;失败 → OSError 上抛)。

        失败语义(**F-SYNC-1 修复**):**本批一行不丢**——写失败时把
        ``retry_old + pending_now`` **完整回填** ``_retry_q``(保序)后上抛
        ``OSError``;不丢任何一条、不重复任何一条。

        为什么这样能恢复 caller-visible failure:本函数由装配层适配器(总线订阅者)
        调用,其异常被总线按 EVT-103 隔离——若此处**丢弃**本批,``session.append``
        步骤 9 的 ``flush(seq)`` 会面对空队列而**静默返回成功**(调用方以为已落盘,
        实际丢行)。回填后,步骤 9 成为对同一批的**第二次真实尝试**:

        - 成功 ⇒ 事件真正持久,``append`` 成功返回(且 replay 可读出);
        - 仍失败 ⇒ ``PERS-202`` 上抛至调用方(**不再 silent success**),行仍留在
          ``_retry_q`` 待 repair 后恢复。

        排序:重试行恒先写(旧行 seq 更小),与 ``flush`` / ``_flush_batch`` 三处
        写入路径语义一致——**seq 不会倒退**,replay 顺序正常。
        """
        if not self._pending and not self._retry_q:
            return
        retry_old = list(self._retry_q)
        pending_now = list(self._pending)
        # 一致性校验先行(无副作用):同 seq 异内容 → PERS-202 fail-closed,
        # 此时尚未改动任何队列 → 两份数据都保留,绝不静默覆盖。
        to_write = _resolve_by_seq(retry_old + pending_now)
        self._pending = deque()
        try:
            self._write_batch(to_write)
            self._fh.flush()
            self._fail_streak = 0
            self._retry_q.clear()
        except BaseException:
            self._retry_q = deque(retry_old)
            self._retry_q.extend(pending_now)     # F-SYNC-1:本批完整回填(保序)
            raise

    async def flush(self, up_to_seq: Optional[int] = None) -> None:
        """公开 flush:把 _pending 与 _retry_q 中 seq ≤ up_to_seq 的行写盘+flush。

        up_to_seq=None = 全量(定时器/关闭前);成功才返回(强同步契约);OSError →
        行回重试队列(PERS-202,拒新不丢旧)。调用方:session 强同步点、
        ``EngineSpine.start_flush_ticker`` 的间隔定时器(GAP-13)、进程收尾。
        两队列同受 up_to_seq 过滤(P3:此前 retry_q 不过滤,>up_to_seq 的旧重试行会被
        提前写出、破坏 seq 升序)。
        """
        if not self._pending and not self._retry_q:
            return                                # 无积压:空操作
        def _within(seq: int) -> bool:
            return up_to_seq is None or seq <= up_to_seq
        take = [it for it in self._pending if _within(it[0])]
        keep = [it for it in self._pending if not _within(it[0])]
        self._pending = deque(keep)
        retry_original = list(self._retry_q)      # 保序(失败回填需与写序一致)
        retry_take = [it for it in retry_original if _within(it[0])]
        retry_keep = [it for it in retry_original if not _within(it[0])]
        to_write = retry_take + take              # 先重试旧行再新取(seq 升序)
        if not to_write:
            self._retry_q = deque(retry_keep)     # 全在 up_to_seq 之后:原位保留
            return
        try:
            self._write_batch(to_write)
            self._fh.flush()
            self._fail_streak = 0                 # 成功:复位连续失败计数
            self._retry_q = deque(retry_keep)     # 仅剩 >up_to_seq 的旧重试行待刷
            # R14-15:写通道**确已恢复** ⇒ 解除暂停(recovering→normal)。此前解除点只有
            # ``SessionStore.repair()``(旧面,生产零调用者)⇒ 一旦因磁盘故障达阈值暂停,
            # 本进程内**永不恢复**:``append`` 恒被 "拒新不丢旧" 挡回 PERS-202,而重试队列
            # 只靠定时器重写、永不解除暂停(实测)。现由**既有** flush 定时器
            # (``EngineSpine.start_flush_ticker``)在通道恢复后自动解除,不需新调用方。
            if self._suspended:
                self._suspended = False
                log.warning("会话写通道恢复:解除暂停(重试队列已落盘)path=%s",
                            self.path)
        except BaseException as e:
            self._retry_q = deque(retry_original)  # 旧行原样保序保留
            self._retry_q.extend(take)            # 新取入队:不重不漏
            if not isinstance(e, OSError):
                raise
            self._fail_streak += 1
            if self._failure_state_reached():     # GAP-2:公开 flush 同样受阈值约束
                await self._enter_failure_state(why=str(e))
                raise_code("PERS-202", n=len(take),
                           advice="会话暂停(fail-streak/重试队列达阈值),"
                                  "repair 后恢复;行不丢")
            raise_code("PERS-202", n=len(take), why=str(e),
                       advice="行已入重试队列不丢;repair 后恢复")

    # ------------------------------------------------------ 失败状态(GAP-2)
    def _failure_state_reached(self) -> bool:
        """失败状态判据(**单一真源**;GAP-2):连续失败 ≥ N 或重试队列越界。

        全部写路径(强同步 ``append`` / 公开 ``flush`` / 异步 ``_flush_batch``)
        共用本判据——修复前只有异步路径检查,强同步路径的 ``_fail_streak``
        只增不判 ⇒ 磁盘持续故障时永不暂停、队列无界增长。
        """
        return (self._fail_streak >= _FAIL_STREAK_LIMIT
                or len(self._retry_q) > _RETRY_Q_LIMIT)

    async def _enter_failure_state(self, *, why: str = "") -> None:
        """进入失败状态:发**可观察证据**(system.error)并暂停会话(拒新不丢旧)。

        ``_suspended=True`` 后的新 ``append`` 一律 ``PERS-202``(拒新),已入
        ``_retry_q`` 的行**一行不丢**(repair → recovering → normal 后恢复)。
        """
        await self._on_system_event("system.error", {
            "code": "PERS-202",
            "advice": "落盘通道故障,会话暂停(拒新不丢旧);已入重试队列的行不会丢,"
                      "通道恢复后由定时 flush 自动解除暂停(R14-15);"
                      "若进程先退出,队列内事件需跑 repair(F060) 复核",
            "fail_streak": self._fail_streak,
            "retry_q": len(self._retry_q),
            **({"why": why} if why else {})})
        self._suspended = True

    # ------------------------------------------------------- 写入(残片自愈,R13-2)
    def _heal_torn_tail(self) -> None:
        """首写前给崩溃残片补一个行尾分隔(2026-09-21 R13-2)。

        崩溃可能留下**无换行的尾部半行**;此后任何 append 都会与该残片拼在**同一物理
        行** ⇒ 新事件不可解析(内存以为已落盘、磁盘读不回 = **静默丢事件**,实测:
        `…,"type":"agent.mess{"seq":3,…`)。此处**只补一个 `\\n`**:残片自成一(坏)行、
        新事件保持完整可解析;**不删任何字节**(残片留待 repair 按"中部坏行"口径隔离),
        保持"open/回放不修"的既有口径(§repair:先备份再截断)。
        """
        if not self._torn_tail:
            return
        self._fh.write("\n")
        self._fh.flush()
        self._torn_tail = False

    def _write_batch(self, rows: list) -> None:
        """Commit actual missing rows, reconciling uncertain writes before retry.

        The cache is derived from complete physical rows, never from write()'s
        return value. Drain uncertain TextIO buffers before reading the file;
        flush/fsync failure cannot cause a visible prefix to be appended twice.
        """
        rows = _resolve_by_seq(rows)
        self._fh.flush()
        self._reconcile_rows()
        if self._torn_tail:
            self._heal_torn_tail()
            # A short write may contain the entire JSON object except its newline.
            # Reconcile the now complete row before deciding which suffix to retry.
            self._reconcile_rows()
        missing = []
        for seq, line in rows:
            expected = json.loads(line)
            actual = self._committed_rows.get(seq)
            if actual is not None:
                if actual != expected:
                    raise_code('PERS-202', op='seq_conflict', seq=seq)
            else:
                if self._committed_rows and seq <= max(self._committed_rows):
                    raise_code('PERS-202', op='out_of_order', seq=seq)
                missing.append((seq, line))
        # An fsync failure is an unknown durability result, never business success.
        os.fsync(self._fh.fileno())
        for _seq, line in missing:
            if self._fh.write(line) != len(line):
                raise OSError('short JSONL write')
        self._fh.flush()
        os.fsync(self._fh.fileno())
        self._reconcile_rows()

    def _reconcile_rows(self) -> None:
        if not self._segments_scanned:
            for path in self._rotated_paths():
                for env in _iter_replay([path], self._quarantine):
                    self._remember_committed(env.model_dump(mode='json'))
            self._segments_scanned = True
        with self.path.open('rb') as source:
            if source.seek(0, os.SEEK_END) < self._scan_offset:
                raise_code('PERS-202', op='unexpected_truncation')
            source.seek(self._scan_offset)
            while True:
                start = source.tell()
                line = source.readline()
                if not line:
                    self._torn_tail = False
                    break
                if not line.endswith(b'\n'):
                    self._torn_tail = True
                    self._scan_offset = start
                    break
                try:
                    env = Envelope.model_validate_json(line)
                    validate_payload(env.type, env.payload)
                    value = env.model_dump(mode='json')
                except Exception:
                    self._scan_offset = source.tell()
                    continue  # damaged bytes remain for explicit repair
                self._remember_committed(value)
                self._scan_offset = source.tell()

    def _remember_committed(self, value):
        seq = value['seq']
        if seq in self._committed_rows:
            raise_code('PERS-202', op='duplicate_physical_seq', seq=seq)
        if self._committed_rows and seq <= next(reversed(self._committed_rows)):
            raise_code('PERS-202', op='out_of_order', seq=seq)
        self._committed_rows[seq] = value

    def _invalidate_scan(self):
        self._scan_offset = 0
        self._committed_rows.clear()
        self._segments_scanned = False

    def _tail_looks_torn(self) -> bool:
        """写失败后判定"文件是否停在行中"(R13-3)。

        句柄是**缓冲**的 ⇒ 半行可能还在缓冲里:先尽力 ``flush`` 把它推到盘上,再按
        事实读尾部(``detect_truncation``)。推不出去(仍 OSError)⇒ 无法判定,保守
        按"可能半行"处理(下轮写前补分隔,最坏多一个空行 —— 空行被 replay 跳过,
        代价远小于丢事件)。
        """
        try:
            self._fh.flush()
        except OSError:
            return True
        return detect_truncation(self.path) is not None

    async def _flush_batch(self) -> None:
        """内部:攒批写 + 失败重试(摘批先行:并发 append 不阻塞)。

        失败语义:行全部回填 ``_retry_q``(拒新不丢旧),``_fail_streak += 1``;
        达 ``_failure_state_reached`` ⇒ 暂停会话 + ``PERS-202``;未达阈值 ⇒
        **静默保留**(等下一次 flush 定时器重试,不在批量路径上抛——批量由
        ``append`` 的非 sync 分支调用,抛会打断事件发射)。
        """
        batch, self._pending = self._pending, deque()
        retry = list(self._retry_q)
        to_write = retry + list(batch)
        if not to_write:
            return
        try:
            self._write_batch(to_write)
            self._fh.flush()
            self._fail_streak = 0                 # 成功:复位失败计数
            self._retry_q.clear()
        except BaseException as e:
            self._retry_q = deque(retry)
            self._retry_q.extend(batch)           # 拒新不丢旧:全部回重试队列
            if not isinstance(e, OSError):
                raise
            self._fail_streak += 1
            if self._failure_state_reached():
                await self._enter_failure_state(why=str(e))
                raise_code("PERS-202", advice="会话暂停,repair 后恢复")
            # 未达阈值:保留重试队列,等下一次 flush 定时器/强同步点重试

    # ------------------------------------------------------------ 读取(隔离,不中断)
    def replay(self) -> Iterator[Envelope]:
        """行迭代器(轮转文件按序号合并:先 {sid}.1..n 后主文件)。

        空行跳过;坏行(解码失败/JSON 非法/信封校验失败)记 PERS-201 进 _quarantine
        隔离区,继续下一行——回放 = 恢复 = 审计同一路径,绝不因单行损坏中断
        (§3.8)。行号按文件从 1 起(多文件时可能重号,隔离判定以 repair 主文件
        扫描为准)。
        """
        return _iter_replay(self._rotated_paths() + [self.path], self._quarantine)

    # ------------------------------------------------------------ 截断检测(只读)
    @staticmethod
    def detect_truncation(path: Path) -> Optional[int]:
        """同模块函数(静态门面,兼容既有调用形态)。"""
        return detect_truncation(path)

    # ------------------------------------------------------------ repair 崩溃恢复(F060)
    async def repair(self, session_id: Optional[str] = None) -> RepairReport:
        """**委托** F060 权威实现（`pyharness.repair.repair_session`）；本面是早期适配器。

        契约（`specs/repair.py.md` §0）：「**本模块是 F060 修复策略与编排的唯一归属**…
        core/persistence 早期草案中同名 repair 若已实现，**应改为委托本模块，禁两套
        修复策略并存**」。R24 依此把本方法由「第二套流水线」改为**薄适配器**（此前两套
        并存：备份命名不同、隔离表示不同、派生视图重建缺失）。

        安全委托序（**关键**）：本对象正持有自己的**追加句柄**，而 `repair_session` 会
        整体重写主文件（它自己以 `ctx.session` 守卫**拒绝**活会话修复，正是防句柄失
        同步）⇒ 委托前必须 flush + **关本句柄**，委托后**重开**：

          ① `flush()`（攒批落盘）→ ② 关 `_fh` → ③ 委托 `repair_session`（同进程
          会话锁可重入，不冲突）→ ④ 重开追加句柄 + 按事实重建 `_torn_tail`
          → ⑤ 解除暂停（写通道已随修复恢复）。

        返回**本模块的** `RepairReport`（`fixed`/`quarantined: list[int]`/`backup_path`），
        由权威报告投影而来，保持既有的声明返回类型不变。
        """
        sid = session_id or self.session_id
        path = self.path
        if not path.exists():
            return RepairReport()                 # 空会话:空报告(新会话)
        await self.flush()                        # ① 攒批先落盘
        close = getattr(self._fh, "close", None)
        if callable(close):
            close()  # Do not rewrite while ownership of the old handle is uncertain.
        from pyharness.repair import repair_session     # 惰性:避免模块级回边
        ctx = SimpleNamespace(
            storage=SimpleNamespace(sessions_dir=path.parent))
        primary = None
        try:
            rep = await repair_session(ctx, sid, interactive=False)
        except BaseException as exc:
            primary = exc
            raise
        finally:
            self._invalidate_scan()
            try:
                self._fh = open(path, "a", encoding="utf-8", newline="\n")
                self._torn_tail = detect_truncation(path) is not None
                if primary is None: self._suspended = False
            except BaseException as exc:
                if primary is None: raise
                primary.add_note(f'repair reopen failed: {type(exc).__name__}')
        kept = [int(getattr(e, "line_no", 0)) for e in
                (getattr(rep, "quarantined", None) or [])]
        self._quarantine.clear()
        self._quarantine.update(kept)
        return RepairReport(fixed=list(getattr(rep, "fixed", None) or []),
                            quarantined=sorted(self._quarantine),
                            backup_path=getattr(rep, "backup_path", None))

    # ------------------------------------------------------------ 原子截断重写(repair 专用)
    def _rewrite_without_tail(self, cut_offset: int) -> None:
        """把主文件截至 cut_offset 的内容原子重写(临时文件 + fsync + rename)。

        同目录 tmp → fsync → os.replace 原子替换 → 重开追加句柄;rename 原子性
        保证任何时刻磁盘上要么旧完整文件要么新完整文件,杜绝半写。Windows 先关
        句柄再 rename(持开句柄 replace 会 PermissionError),失败不留临时残留。
        """
        tmp = self.path.with_name(self.path.name + f".tmp-{os.getpid()}")
        primary = None
        cleanup_errors = []
        try:
            try:
                self._fh.close()
                with open(self.path, "rb") as src, open(tmp, "wb") as dst:
                    dst.write(src.read(cut_offset))
                    dst.flush()
                    os.fsync(dst.fileno())
                os.replace(tmp, self.path)
            except OSError as exc:
                raise_code("PERS-202", op="rewrite_without_tail", why=str(exc),
                           advice="原子截断失败；保留原文件及故障证据")
        except BaseException as exc:
            primary = exc
            raise
        finally:
            try: tmp.unlink(missing_ok=True)
            except BaseException as exc: cleanup_errors.append(exc)
            if self._fh.closed:
                try: self._fh = open(self.path, "a", encoding="utf-8", newline="\n")
                except BaseException as exc: cleanup_errors.append(exc)
            self._invalidate_scan()
            if primary is not None:
                for exc in cleanup_errors: primary.add_note(f'cleanup failed: {exc!r}')
            elif cleanup_errors:
                raise BaseExceptionGroup('rewrite cleanup failed', cleanup_errors)

    # ------------------------------------------------------------ 轮转(内部)
    def _rotated_paths(self) -> list[Path]:
        """主目录下 {sid}.{n}.jsonl(n 从 1)按序号升序(重放合并顺序)。"""
        pat = re.compile(r"^" + re.escape(self.session_id) + r"\.(\d+)\.jsonl$")
        found: list[tuple[int, Path]] = []
        try:
            children = list(self.path.parent.iterdir())
        except OSError:
            return []
        for child in children:
            m = pat.match(child.name)
            if m:
                found.append((int(m.group(1)), child))
        return [p for _n, p in sorted(found)]

    def _maybe_rotate(self) -> None:
        """轮转检查(append 写入后调用):>rotate_bytes → _rotate。

        大小取 OS 文件字节数(fstat),不用 `_fh.tell()`——文本模式 tell() 返回的是
        不透明游标(cookie)而非字节偏移,与 rotate_bytes(字节)不可比(P3:_rotate 判定)。
        轮转失败只记日志不抛(数据无损:事件已先落主文件;rename 原子性保证主文件
        仍在),下次 append 再触发——避免在 append 成功路径上叠加二次异常。
        """
        if self._retry_q or self._pending:
            return
        try:
            size = os.fstat(self._fh.fileno()).st_size
            if size > self.rotate_bytes:
                self._rotate()
        except (OSError, ValueError) as e:
            log.error("PERS-202 域:轮转失败(数据无损,下次重试) why=%s", e)

    def _rotate(self) -> None:
        """主文件 >50MB → 原子 rename 为 {sid}.{n}.jsonl → 开新主句柄。"""
        self._fh.flush()
        n = max((int(p.stem.rsplit('.', 1)[1]) for p in self._rotated_paths()), default=0) + 1
        target = self.path.with_name(f"{self.session_id}.{n}.jsonl")
        self._fh.close()                          # Windows:先关句柄再 rename
        try:
            os.replace(self.path, target)         # 原子 rename(同文件系统)
        except OSError:
            self._fh = open(self.path, "a", encoding="utf-8", newline="\n")
            raise                                # 主文件未动,恢复句柄
        self._fh = open(self.path, "a", encoding="utf-8", newline="\n")
        self._torn_tail = False                   # 新文件无残片(R13-2)
        self._invalidate_scan()
        log.info("jsonl rotated: %s (size_mb=%d)",
                 target, self.rotate_bytes // 1_048_576)

    # ------------------------------------------------------------ seq 空洞
    def seq_holes(self, path: Path) -> list[int]:
        """对照连续序列找缺失号(含轮转合并回放;path 参数保留 spec 形态)。

        只找相邻差 >1 的缺口;seq 只前进,空洞只解释不回填(§3.4)。重复/倒退
        (range 空)不入洞——append 保证单调,此处只定位缺失,声明判定在 repair 内。
        """
        seqs = [env.seq for env in self.replay()]     # 复用坏行隔离读取
        holes: list[int] = []
        if seqs and seqs[0] > 1:
            # 前缀段连续缺失(轮转文件丢失/坏文件):seq 1..(seqs[0]-1) 整体缺失,
            # 旧实现只查相邻差、对首段失明(P1-5,与 repair.check_seq_gap 口径对齐)
            holes.extend(range(1, seqs[0]))
        for expect, got in zip(seqs, seqs[1:]):
            if got != expect + 1:
                holes.extend(range(expect + 1, got))  # 相邻差 >1 = 空洞
        return holes

    def _declared_by_compaction(self, holes: list[int]) -> bool:
        """空洞合法性判定:对照 context.compacted.ranges / session.recovered 声明。

        覆盖来源:compacted.ranges 闭区间、recovered.lost 截断 seq 清单、既往
        recovered.fixed 中 "seq-holes:[…]" 声明(重复 repair 不重复告警)。
        全部被声明覆盖 → 合法空洞(压缩/截断/修复事实),不告警不回填。

        2026-09-21 R14-9:判据**唯一来源** ``events.declared_ranges``(此前本处、
        ``repair``、``session``、``governance.audit`` 各写一份 ⇒ 漂移)。
        """
        covered: set[int] = set()
        for env in self.replay():
            for lo, hi in declared_ranges(env):
                covered.update(range(lo, hi + 1))
        return all(h in covered for h in holes)

    # ------------------------------------------------------------ 隔离区查询
    def quarantine_info(self) -> dict:
        """坏行隔离区只读暴露(repair 决策/用户查看):{path, line_nos, count}。"""
        nos = sorted(self._quarantine)
        return {"path": str(self.path), "line_nos": nos, "count": len(nos)}

    # ------------------------------------------------------------ 系统事件上抛(注入回调)
    async def _on_system_event(self, type_: str, payload: dict) -> None:
        """向上写 system.error / session.recovered(INV-08 无回边)。

        回调签名:async cb(type_: str, payload: dict) -> None,由装配层/bus 日志
        订阅者注入;装配层负责打 seq/ts 成信封并 append(sync=True)——本模块不自造
        信封(seq/ts 只能框架分配,EVENT-SCHEMA §1.1-4)。未注入 → 降级日志
        (阶段 2 装配后必有);回调失败不掩盖主路径错误。
        """
        cb = self.on_system_event
        if cb is None:
            log.warning("on_system_event 未注入,type=%s 仅日志留痕(装配层阶段注入)",
                        type_)
            return
        try:
            await cb(type_, payload)
        except Exception as exc:                 # noqa: BLE001 — 不掩盖主错误
            log.error("on_system_event(type=%s) 失败:%s", type_, exc)


# ================================================================= 工厂
def session_recorder(store: Any, session_id: str) -> Callable:
    """总线 → 存储订阅工厂(**唯一实现**;engine 与桌面壳共用,禁各自实现)。

    **sid 过滤是必须的**:多会话**共用一条总线**时,订阅者会收到**所有**会话的信封;
    不过滤则 B 的事件会串写进 A 的 JSONL(实测:两个 ``session.created`` 落同一文件,
    回放错序并让 repair 误判空洞/误隔离)。``store.append`` 侧只写字节、**不做** sid
    校验,故过滤责任**只在此单点**(桌面壳曾各自实现过一份,engine 侧长期缺失 ——
    2026-09-21 R8 补)。

    另:瞬时类型(``llm.chunk`` 等以 dict 上总线)不进 append-only JSONL。
    强同步清单**唯一真源** = ``events.vocab.SYNC_TYPES``(ADR-019 P-3),不本地维护副本。
    """
    async def _record(type_: str, payload: Any) -> None:
        if not hasattr(payload, "model_dump_json"):
            return
        if session_id and getattr(payload, "session_id", None) != session_id:
            return                     # 跨会话事件:不落本会话文件
        await store.append(payload, sync=type_ in SYNC_TYPES)
    return _record


def flush_kwargs_of(cfg: Any) -> dict:
    """由 Settings **鸭子取值**解析攒批/定时落盘标量(N1)。

    本模块**不 import** ``pyharness.config``(依赖方向纪律):只按属性读取。
    缺键 ⇒ 返回 ``{}``,``open_store`` 回落模块常量(DEFAULTS),行为与修复前一致
    —— 即"未配置"与"配置为默认值"不可区分但**结果相同**,不引入新语义。
    """
    j = getattr(getattr(cfg, "log", None), "jsonl", None)
    out: dict = {}
    fis = getattr(j, "flush_interval_s", None)
    fb = getattr(j, "flush_batch", None)
    if fis is not None:
        out["flush_interval_s"] = fis
    if fb is not None:
        out["flush_batch"] = fb
    return out


def open_store(session_id: str, *, dir: Optional[Path] = None,
               flush_interval_s: Optional[float] = None,
               flush_batch: Optional[int] = None) -> SessionStore:
    """创建/打开会话存储(工厂):目录 mkdir(700) → 主文件 append 句柄。

    文件已存在(重启恢复):由 session.open_session 重放取 _seq 基线;检测到尾部
    半行 → 告警提示先跑 repair(open 不自动截断——修复前强制备份,F060)。
    异常:目录/文件不可写 → PERS-202。

    ``flush_interval_s`` / ``flush_batch``(N1):**已解析的标量**,由**装配层**从
    ``Settings`` 取值后传入(见 ``flush_kwargs_of``);缺省 ``None`` ⇒ 用模块常量
    (DEFAULTS)。修复前本工厂**不接收也不传递**这两个值 ⇒ ``config.log.jsonl.*``
    是**死配置**(实测:配置 3.0/7,store 实为 0.5/64)。
    """
    d = dir or default_sessions_dir()
    try:
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as e:
        raise_code("PERS-202", op="mkdir", path=str(d), why=str(e),
                   advice="查磁盘/权限;repair 后恢复")
    path = d / f"{session_id}.jsonl"              # 会话日志锚点 ~/.pyharness/sessions
    lock_path = session_lock_path(path)           # 跨进程独占锁(INV-07 单写者)
    try:
        acquire_session_lock(lock_path)           # 冲突 → PERS-202(不建第二写者)
    except PyHError:
        raise
    except Exception as e:                        # noqa: BLE001 非预期锁故障:拒开
        raise_code("PERS-202", op="lock", path=str(lock_path), why=str(e),
                   advice="会话锁获取异常;查磁盘/权限")
    fh = None
    try:
        trunc = detect_truncation(path)
        if trunc is not None:
            log.warning("PERS-201:尾部半行待 repair file=%s offset=%s", path, trunc)
        # newline="\n":JSONL 统一 \n 行尾,Windows 下禁 \r\n 翻译(半行检测按字节)
        fh = open(path, "a", encoding="utf-8", newline="\n")
        return SessionStore(session_id=session_id, path=path, fh=fh,
                            lock_path=lock_path, flush_interval_s=flush_interval_s,
                            flush_batch=flush_batch)
    except BaseException as primary:
        for cleanup in ([fh.close] if fh is not None else []) + [lambda: release_session_lock(lock_path)]:
            try:
                cleanup()
            except BaseException as secondary:
                primary.add_note(f'initialization cleanup failed: {type(secondary).__name__}')
        if isinstance(primary, OSError):
            raise_code('PERS-202', op='open', path=str(path), why=str(primary),
                       advice='查磁盘/权限;repair 后恢复')
        raise


__all__ = [
    "SessionStore", "RepairReport", "open_store", "detect_truncation",
    "default_sessions_dir", "now_ts",
    "acquire_session_lock", "release_session_lock", "session_lock_path",
    "session_lock_held", "is_aux_session_file", "session_log_paths",
    "rotated_segment_paths", "session_data_paths", "SessionReader",
]
