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


# ===================================================================== 常量
# L1 权威默认值(config.DEFAULTS 唯一落地;全量 config 解析在装配层完成后再注入)

# 重试队列上限(PERS-202 暂停判据之二;PARAMETER-ANCHOR 无锁死,沿用 DIS 伪码 192)

# 连续失败上限(PERS-202 暂停判据之一;GAP-2)。**全部写路径共用此单一阈值**——
# 修复前只有 ``_flush_batch``(异步攒批)检查它,而 ``append(sync=True)`` /
# ``flush()`` 只累加计数不判阈值 ⇒ 强同步与公开 flush 路径在磁盘持续故障时
# 永不进入失败状态(会话不暂停、retry_q 无界增长)。

# 只读检测尾部窗口(半行定位范围)

# ================================================================= 工具函数


# ================================================================= 跨进程文件锁


# 进程内锁注册表:{resolved lock path -> [_FileLock, refcount]}。同进程多 store 打开
# 同一会话(如桌面 + 进程内 repair)复用同一 OS 锁,不误判为跨进程冲突。


# 会话**辅助档**命名(轮转段/修复备份/隔离档/旧备份名):它们**属于**某会话的字节
# 历史,但**不是独立会话**。这是判定该事实的**唯一来源**(R14-3)—— 此前只有
# ``repair`` 用真判据,其余五处各自用 ``stem.startswith("s-")``,而
# ``{sid}.1.jsonl`` / ``{sid}.corrupt-*`` / ``{sid}.quarantine-*`` 的 stem 同样以
# ``s-`` 开头 ⇒ 辅助档被当成独立会话(实测:``search`` 撞 fts_rows 主键冲突崩成
# PERS-202、桌面会话列表混入修复备份、``budget/stats`` 重复计数)。


# ================================================================= RepairReport


# ================================================================= SessionStore
from pyharness.persistence_policy import (log, _ROTATE_BYTES, _FLUSH_BATCH, _FLUSH_INTERVAL_S, _DEFAULT_SESSIONS_DIR, _RETRY_Q_LIMIT, _FAIL_STREAK_LIMIT, _TAIL_WINDOW, _resolve_by_seq, now_ts, default_sessions_dir)
from pyharness.persistence_reader import (rotated_segment_paths, session_data_paths, _iter_replay, SessionReader, _last_newline_before, detect_truncation, _read_clean, RepairReport)

from pyharness.persistence_writer import PersistenceWriter
from pyharness.persistence_recovery import PersistenceRecovery

AUX_SESSION_PATTERNS: tuple = (
    re.compile(r"\.\d+\.jsonl$"),          # 轮转段 {sid}.{n}.jsonl
    re.compile(r"\.corrupt-"),             # 修复备份 {sid}.corrupt-{ts}.jsonl
    re.compile(r"\.quarantine-"),          # 隔离档 {sid}.quarantine-{ts}.jsonl
    re.compile(r"\.jsonl\.bak-"),          # persistence 草案旧备份名
)


def is_aux_session_file(path: Any) -> bool:
    """该文件是否会话**辅助档**(轮转段/修复备份/隔离档)—— 属于某会话但非独立会话。"""
    return any(p.search(Path(path).name) for p in AUX_SESSION_PATTERNS)


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


class SessionStore(PersistenceWriter, PersistenceRecovery):
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


    # ------------------------------------------------------ 失败状态(GAP-2)


    # ------------------------------------------------------- 写入(残片自愈,R13-2)


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




    # ------------------------------------------------------------ 截断检测(只读)

    # ------------------------------------------------------------ repair 崩溃恢复(F060)

    # ------------------------------------------------------------ 原子截断重写(repair 专用)

    # ------------------------------------------------------------ 轮转(内部)


    # ------------------------------------------------------------ seq 空洞


    # ------------------------------------------------------------ 隔离区查询

    # ------------------------------------------------------------ 系统事件上抛(注入回调)


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
