"""tests/e2e/test_persistence_failure_e2e.py — 持久化失败语义（GAP-2）。

口径：真实 ``open_store``（真文件、真锁、真队列），只在**写通道**注入 OSError。
验证失败语义三要件:

  Bounded Retry（有界重试） → Failure State（失败状态） → Observable Evidence（可观察证据）

以及四条禁令:
  无无限重试 · 无静默丢行 · 无队列无界增长 · 无关闭挂死
"""
from __future__ import annotations

import pytest

from pyharness.errors import PyHError
from pyharness.persistence import _FAIL_STREAK_LIMIT, _RETRY_Q_LIMIT, open_store


class _Env:
    """store.append 需要的最小信封面(seq + model_dump_json)。"""

    def __init__(self, seq: int, text: str) -> None:
        self.seq = seq
        self._text = text

    def model_dump_json(self) -> str:
        return self._text


class _BrokenFH:
    """写通道代理:第 N 次之后一直失败(模拟磁盘持续故障)。"""

    def __init__(self, real, *, fail_after: int = 0) -> None:
        self._real = real
        self._fail_after = fail_after
        self.writes = 0
        self.closed = False

    def write(self, line: str) -> int:
        self.writes += 1
        if self.writes > self._fail_after:
            raise OSError("simulated disk failure")
        return self._real.write(line)

    def flush(self) -> None:
        self._real.flush()

    def close(self) -> None:
        self.closed = True
        self._real.close()


@pytest.fixture
def broken_store(tmp_path):
    """真实 store + 可注入故障的写句柄 + system.error 事件收集器。"""
    seen: list[tuple[str, dict]] = []
    store = open_store("s-pers-fail-0001", dir=tmp_path)
    store.on_system_event = _collector(seen)
    real = store._fh
    store._fh = _BrokenFH(real, fail_after=0)
    try:
        yield store, seen, store._fh
    finally:
        try:
            store._fh = real
            store.close()
        except Exception:                       # noqa: BLE001 收尾尽力
            pass


def _collector(seen):
    async def _cb(type_: str, payload: dict) -> None:
        seen.append((type_, dict(payload)))
    return _cb


# =================================================== 有界重试 → 失败状态
async def test_sync_append_reaches_failure_state_and_leaves_evidence(broken_store):
    """强同步路径(GAP-2 修复点):连续失败达阈值 ⇒ 暂停 + system.error。"""
    store, seen, fh = broken_store
    assert _FAIL_STREAK_LIMIT == 3

    codes = []
    for i in range(1, _FAIL_STREAK_LIMIT + 1):
        with pytest.raises(PyHError) as ei:
            await store.append(_Env(i, f'{{"seq":{i}}}'), sync=True)
        codes.append(ei.value.code)

    assert codes == ["PERS-202"] * _FAIL_STREAK_LIMIT
    # Failure State(失败状态)
    assert store._suspended is True
    # Observable Evidence(可观察证据)
    assert [t for t, _ in seen] == ["system.error"]
    assert seen[0][1]["code"] == "PERS-202"
    assert seen[0][1]["fail_streak"] >= _FAIL_STREAK_LIMIT
    # 无静默丢行:三行都在重试队列里(拒新不丢旧)
    assert len(store._retry_q) == _FAIL_STREAK_LIMIT


async def test_failure_state_refuses_new_writes_without_retrying(broken_store):
    """失败状态后**新写入快速拒绝**,不进入重试循环(无无限重试)。"""
    store, _seen, fh = broken_store
    for i in range(1, _FAIL_STREAK_LIMIT + 1):
        with pytest.raises(PyHError):
            await store.append(_Env(i, f'{{"seq":{i}}}'), sync=True)
    assert store._suspended is True
    before = fh.writes
    with pytest.raises(PyHError) as ei:
        await store.append(_Env(99, '{"seq":99}'), sync=True)
    assert ei.value.code == "PERS-202"
    assert fh.writes == before, "暂停后不得再触碰写通道(快速失败,不重试)"
    assert len(store._retry_q) == _FAIL_STREAK_LIMIT, "不得因拒新而丢旧行"


# =================================================== 公开 flush 同样有界
async def test_public_flush_path_is_bounded(broken_store):
    """``flush()`` 此前只累加计数、不判阈值(GAP-2)⇒ 现同样进入失败状态。"""
    store, seen, _fh = broken_store
    for i in range(1, _FAIL_STREAK_LIMIT + 1):
        store._pending.append((i, f'{{"seq":{i}}}'))   # 攒批(非 sync 入队)
        with pytest.raises(PyHError) as ei:
            await store.flush()
        assert ei.value.code == "PERS-202"
    assert store._suspended is True
    assert len(seen) == 1 and seen[0][0] == "system.error"
    # 行仍在(不丢),且队列未越界
    assert len(store._retry_q) <= _RETRY_Q_LIMIT


# =================================================== 成功会复位失败状态机
async def test_success_resets_fail_streak(tmp_path):
    """恢复语义:写成功后 ``_fail_streak`` 归零(阈值按**连续**失败计,非累计)。"""
    store = open_store("s-pers-fail-0002", dir=tmp_path)
    real = store._fh
    try:
        store._fh = _BrokenFH(real, fail_after=0)
        with pytest.raises(PyHError):
            await store.append(_Env(1, '{"seq":1}'), sync=True)
        assert store._fail_streak == 1
        store._fh = real                        # 磁盘恢复
        await store.append(_Env(2, '{"seq":2}'), sync=True)
        assert store._fail_streak == 0
        assert store._suspended is False
        assert not store._retry_q, "成功写必须把重试队列清空(不重复落盘)"
    finally:
        store._fh = real
        store.close()


# =================================================== 关闭不挂死
async def test_close_after_failure_state_does_not_hang(tmp_path):
    """无关闭挂死:故障后 close() 仍能返回(句柄坏但 close 尽力而为)。"""
    import asyncio

    store = open_store("s-pers-fail-0003", dir=tmp_path)
    await store.append(_Env(1, '{"seq":1}'), sync=True)
    store._fh = _BrokenFH(store._fh, fail_after=0)
    for i in range(2, 2 + _FAIL_STREAK_LIMIT):
        with pytest.raises(PyHError):
            await store.append(_Env(i, f'{{"seq":{i}}}'), sync=True)
    assert store._suspended is True
    await asyncio.wait_for(asyncio.to_thread(store.close), timeout=5.0)
