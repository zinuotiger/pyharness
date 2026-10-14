"""PersistenceWriter operations, separated from facade assembly."""
from __future__ import annotations
import json
import os
from collections import deque
from typing import Optional
from pyharness.errors import raise_code
from pyharness.events import Envelope
from pyharness.events.vocab import validate_payload
from pyharness.persistence_policy import log, _RETRY_Q_LIMIT, _FAIL_STREAK_LIMIT, _resolve_by_seq
from pyharness.persistence_reader import _iter_replay, detect_truncation

class PersistenceWriter:
    """PersistenceWriter responsibility; state is owned by the composing facade."""

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

