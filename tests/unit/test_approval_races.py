"""Deterministic approval completion boundaries; Events select the interleaving."""
import asyncio
from types import SimpleNamespace

import pytest

from pyharness.core.approval import ApprovalProvider
from pyharness.core.session import SessionLog
from pyharness.core.task_queue import TaskQueue
from pyharness.errors import PyHError


class Gate:
    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def wait(self):
        self.entered.set()
        await self.release.wait()


async def checkpoint():
    """One explicit event-loop turn, without a timing assumption or polling."""
    future = asyncio.get_running_loop().create_future()
    asyncio.get_running_loop().call_soon(future.set_result, None)
    await future


async def stack(*, pause_gate=None, resume_gate=None, sid="s-approval-races", bus=None):
    log = SessionLog(sid=sid, bus=bus)
    await log.append("session.created", {"title": "", "model": "m"}, actor="system")

    class Queue(TaskQueue):
        paused = None

        async def pause(self, *args, **kwargs):
            if pause_gate:
                await pause_gate.wait()
            await super().pause(*args, **kwargs)
            if args[0] == "approval":
                self.paused.set()

        async def resume(self, *args, **kwargs):
            if resume_gate:
                await resume_gate.wait()
            await super().resume(*args, **kwargs)

    queue = Queue(log)
    queue.paused = asyncio.Event()
    provider = ApprovalProvider(session=log, bus=bus, queue_getter=lambda: queue)
    ctx = SimpleNamespace(session=log, session_id=log.sid, channel="cli")
    call = SimpleNamespace(name="fs.write_file", call_id="race-call", args={})
    return provider, queue, log, ctx, call


async def cleanup(provider, *tasks):
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await provider.aclose()


async def test_grant_cannot_overtake_unfinished_pause():
    gate = Gate()
    provider, queue, log, ctx, call = await stack(pause_gate=gate)
    request = asyncio.create_task(provider.request(call, "test", ctx))
    decision = None
    try:
        async with asyncio.timeout(3):
            await gate.entered.wait()
            aid = next(iter(provider._pending))
            decision = asyncio.create_task(provider.approve_async(aid, by="cli:test"))
            await checkpoint()
            assert not decision.done(), "grant returned before queue.pause acknowledgement"
            gate.release.set()
            await decision
            assert await request == "granted"
            assert provider.pending_count() == 0
            assert not queue.status().paused
            assert queue._resume_evt.is_set()
    finally:
        gate.release.set()
        await cleanup(provider, *([request, decision] if decision else [request]))


async def begin(provider, queue, ctx, call):
    task = asyncio.create_task(provider.request(call, "test", ctx))
    await asyncio.wait_for(queue.paused.wait(), 3)
    return task, next(iter(provider._pending))


@pytest.mark.parametrize("verdict", ["granted", "denied"])
async def test_decision_joins_pending_initialization(verdict):
    from pyharness.application import ApplicationService

    gate = Gate()
    provider, queue, log, ctx, call = await stack()
    append = log.append
    aid = None

    async def held(type_, *args, **kwargs):
        nonlocal aid
        env = await append(type_, *args, **kwargs)
        if type_ == "approval.requested":
            aid = env.seq
            await gate.wait()
        return env

    log.append = held
    service = ApplicationService(SimpleNamespace(bus=None, storage=None), channel="cli")
    service._approvals[log.sid] = provider
    request = asyncio.create_task(provider.request(call, "test", ctx))
    decision = None
    try:
        async with asyncio.timeout(3):
            await gate.entered.wait()
            assert provider.pending_count() == 0
            decision = asyncio.create_task(service.decide_approval(
                aid, "approve" if verdict == "granted" else "deny", sid=log.sid))
            await checkpoint()
            assert not decision.done()
            gate.release.set()
            await decision
            assert await request == verdict
            assert not queue.status().paused
    finally:
        gate.release.set()
        await cleanup(provider, *([request, decision] if decision else [request]))


@pytest.mark.parametrize("action", ["deny", "timeout", "cancel", "shutdown"])
async def test_terminal_decision_during_pause_blocks_late_grant(action):
    gate = Gate()
    provider, queue, log, ctx, call = await stack(pause_gate=gate)
    request = asyncio.create_task(provider.request(call, "test", ctx))
    operation = None
    try:
        async with asyncio.timeout(3):
            await gate.entered.wait()
            aid = next(iter(provider._pending))
            req = provider._pending[aid]
            if action == "deny":
                operation = asyncio.create_task(provider.deny_async(aid, by="cli:test"))
            elif action == "timeout":
                operation = asyncio.create_task(provider._on_timeout(req))
            elif action == "cancel":
                request.cancel()
            else:
                operation = asyncio.create_task(provider.aclose())
            await checkpoint()
            gate.release.set()
            if operation:
                await operation
            if action == "cancel":
                with pytest.raises(asyncio.CancelledError):
                    await request
            else:
                assert await request == ("timeout" if action == "timeout" else "denied")
            assert not queue.status().paused
            assert provider.pending_count() == 0
            with pytest.raises(PyHError, match="APR-503"):
                await provider.approve_async(aid, by="cli:late")
            assert not queue.status().paused
            assert call.call_id not in provider._grant_slots
    finally:
        gate.release.set()
        await cleanup(provider, *([request, operation] if operation else [request]))


@pytest.mark.parametrize("winner", ["grant", "deny", "timeout", "cancel", "shutdown"])
async def test_first_claim_wins_and_replays_have_no_effect(winner):
    provider, queue, log, ctx, call = await stack()
    request, aid = await begin(provider, queue, ctx, call)
    try:
        async with asyncio.timeout(3):
            req = provider._pending[aid]
            if winner == "grant":
                await provider.approve_async(aid, by="cli:test")
            elif winner == "deny":
                await provider.deny_async(aid, by="cli:test")
            elif winner == "timeout":
                await provider._on_timeout(req)
            elif winner == "cancel":
                provider.cancel_all()
                provider.cancel_all()
            else:
                await asyncio.gather(provider.aclose(), provider.aclose())
            result = await request
            assert result == {"grant": "granted", "timeout": "timeout"}.get(winner, "denied")
            before = [e.type for e in log.events_after(0)]
            for fn in [provider.approve_async, provider.deny_async]:
                with pytest.raises(PyHError, match="APR-503"):
                    await fn(aid, by="cli:repeat")
            await provider._on_timeout(req)
            assert [e.type for e in log.events_after(0)] == before
            assert queue.status().paused is False
            assert before.count("queue.resumed") == 1
    finally:
        await cleanup(provider, request)


@pytest.mark.parametrize("competitor", ["timeout", "cancel", "shutdown"])
async def test_grant_claim_is_not_overwritten_during_commit(competitor):
    gate = Gate()
    provider, queue, log, ctx, call = await stack(resume_gate=gate)
    request, aid = await begin(provider, queue, ctx, call)
    req = provider._pending[aid]
    decision = asyncio.create_task(provider.approve_async(aid, by="cli:test"))
    closing = None
    try:
        async with asyncio.timeout(3):
            await gate.entered.wait()
            if competitor == "timeout":
                await provider._on_timeout(req)
            elif competitor == "cancel":
                provider.cancel_all()
            else:
                closing = asyncio.create_task(provider.aclose())
                await checkpoint()
            gate.release.set()
            await decision
            assert await request == "granted"
            if closing:
                await closing
            types = [e.type for e in log.events_after(0)]
            assert types.count("approval.granted") == 1
            assert "approval.denied" not in types and "approval.timeout" not in types
            assert not queue.status().paused
    finally:
        gate.release.set()
        await cleanup(provider, request, decision, *([closing] if closing else []))


async def test_resume_failure_is_not_success_and_retains_pending(monkeypatch):
    provider, queue, log, ctx, call = await stack()
    request, aid = await begin(provider, queue, ctx, call)
    real_resume = queue.resume

    async def fail(*args, **kwargs):
        raise OSError("controlled resume failure")

    monkeypatch.setattr(queue, "resume", fail)
    try:
        with pytest.raises(OSError, match="controlled resume failure"):
            await provider.approve_async(aid, by="cli:test")
        assert await request == "denied"
        assert queue.status().paused
        assert provider.pending_count() == 1
        assert call.call_id not in provider._grant_slots
        with pytest.raises(PyHError, match="APR-503"):
            await provider.approve_async(aid, by="cli:late")
    finally:
        monkeypatch.setattr(queue, "resume", real_resume)
        await cleanup(provider, request)
    assert not queue.status().paused
    assert provider.pending_count() == 0


async def test_audit_failure_and_cleanup_failure_preserve_primary(monkeypatch):
    provider, queue, log, ctx, call = await stack()
    request, aid = await begin(provider, queue, ctx, call)
    append, resume = log.append, queue.resume

    async def fail_append(type_, *args, **kwargs):
        if type_ == "approval.granted":
            raise OSError("primary audit failure")
        return await append(type_, *args, **kwargs)

    async def fail_resume(*args, **kwargs):
        raise ValueError("secondary cleanup failure")

    monkeypatch.setattr(log, "append", fail_append)
    monkeypatch.setattr(queue, "resume", fail_resume)
    try:
        with pytest.raises(OSError, match="primary audit failure"):
            await provider.approve_async(aid, by="cli:test")
        assert await request == "denied"
        assert provider.pending_count() == 1
    finally:
        monkeypatch.setattr(log, "append", append)
        monkeypatch.setattr(queue, "resume", resume)
        await cleanup(provider, request)


async def test_delayed_observer_cannot_settle_waiter_before_flush():
    from pyharness.bus import EventBus

    gate, bus = Gate(), EventBus()
    provider, queue, log, ctx, call = await stack(bus=bus)

    async def observer(type_, envelope):
        await gate.wait()

    bus.subscribe("approval.granted", observer, owner="delayed-test")
    request, aid = await begin(provider, queue, ctx, call)
    decision = asyncio.create_task(provider.approve_async(aid, by="cli:test"))
    try:
        async with asyncio.timeout(3):
            await gate.entered.wait()
            assert provider.pending_count() == 1
            assert not request.done() and not decision.done()
            assert queue.status().paused
            gate.release.set()
            await decision
            assert await request == "granted"
            assert not queue.status().paused
            types = [e.type for e in log.events_after(0)]
            assert types == ["session.created", "approval.requested", "queue.suspended",
                             "approval.granted", "queue.resumed"]
    finally:
        gate.release.set()
        await cleanup(provider, request, decision)


async def test_two_ids_share_pause_without_sharing_verdict():
    provider, queue, log, ctx, call = await stack()
    first, aid = await begin(provider, queue, ctx, call)
    second_call = SimpleNamespace(name="fs.delete", call_id="second", args={"path": "b"})
    registered = asyncio.Event()
    register = provider._register

    async def notified(*args):
        req = await register(*args)
        registered.set()
        return req

    provider._register = notified
    second = asyncio.create_task(provider.request(second_call, "second", ctx))
    try:
        async with asyncio.timeout(3):
            await registered.wait()
            bid = next(i for i in provider._pending if i != aid)
            await provider.approve_async(aid, by="cli:a")
            assert await first == "granted"
            assert queue.status().paused and provider.pending_count() == 1
            assert not second.done()
            await provider.deny_async(bid, by="cli:b")
            assert await second == "denied"
            assert not queue.status().paused
            assert call.call_id in provider._grant_slots
            assert second_call.call_id not in provider._grant_slots
    finally:
        await cleanup(provider, first, second)


async def test_closing_one_session_does_not_affect_other_same_id():
    from pyharness.bus import EventBus

    bus = EventBus()
    a, qa, la, ca, calla = await stack(sid="s-race-session-a", bus=bus)
    b, qb, lb, cb, callb = await stack(sid="s-race-session-b", bus=bus)
    ta, aid = await begin(a, qa, ca, calla)
    tb, bid = await begin(b, qb, cb, callb)
    assert aid == bid
    try:
        async with asyncio.timeout(3):
            await a.aclose()
            assert await ta == "denied"
            assert not tb.done() and qb.status().paused
            await b.approve_async(bid, by="cli:b")
            assert await tb == "granted"
            assert not qb.status().paused
            await a.aclose()
    finally:
        await cleanup(a, ta)
        await cleanup(b, tb)


@pytest.mark.parametrize("eager", [False, True])
async def test_bounded_fast_scheduling_has_no_lost_wakeup(eager):
    loop = asyncio.get_running_loop()
    old_factory = loop.get_task_factory()
    # Python 3.11 exercises explicit checkpoints; 3.12+ additionally exercises
    # eager starts, the fast scheduling pattern that exposes initialization races.
    if eager and hasattr(asyncio, "eager_task_factory"):
        loop.set_task_factory(asyncio.eager_task_factory)
    try:
        for index in range(8):
            gate = Gate()
            provider, queue, log, ctx, call = await stack(pause_gate=gate)
            task = asyncio.create_task(provider.request(call, str(index), ctx))
            decision = None
            try:
                async with asyncio.timeout(3):
                    await gate.entered.wait()
                    aid = next(iter(provider._pending))
                    decision = asyncio.create_task(provider.approve_async(aid, by="cli:test"))
                    gate.release.set()
                    await decision
                    assert await task == "granted"
                    assert not queue.status().paused and provider.pending_count() == 0
            finally:
                gate.release.set()
                await cleanup(provider, task, *([decision] if decision else []))
    finally:
        loop.set_task_factory(old_factory)


async def test_cancelled_api_does_not_abandon_owned_completion():
    gate = Gate()
    provider, queue, log, ctx, call = await stack(resume_gate=gate)
    request, aid = await begin(provider, queue, ctx, call)
    decision = asyncio.create_task(provider.approve_async(aid, by="cli:test"))
    try:
        async with asyncio.timeout(3):
            await gate.entered.wait()
            decision.cancel()
            with pytest.raises(asyncio.CancelledError):
                await decision
            assert provider.pending_count() == 1
            gate.release.set()
            assert await request == "granted"
            assert not queue.status().paused
    finally:
        gate.release.set()
        await cleanup(provider, request, decision)


async def test_task_queue_pause_resume_interleaving_and_failed_resume(monkeypatch):
    provider, queue, log, ctx, call = await stack()
    gate = Gate()
    append = log.append

    async def held(type_, *args, **kwargs):
        if type_ == "queue.suspended":
            await gate.wait()
        return await append(type_, *args, **kwargs)

    monkeypatch.setattr(log, "append", held)
    pause = asyncio.create_task(queue.pause("approval"))
    resume = None
    try:
        async with asyncio.timeout(3):
            await gate.entered.wait()
            resume = asyncio.create_task(queue.resume("approval"))
            await checkpoint()
            assert not resume.done()
            gate.release.set()
            await asyncio.gather(pause, resume)
            assert not queue.status().paused and queue._resume_evt.is_set()
            await queue.pause("approval")

            async def failed(type_, *args, **kwargs):
                if type_ == "queue.resumed":
                    raise OSError("resume append failed")
                return await append(type_, *args, **kwargs)

            monkeypatch.setattr(log, "append", failed)
            with pytest.raises(OSError, match="resume append failed"):
                await queue.resume("approval")
            assert queue.status().paused and not queue._resume_evt.is_set()
            monkeypatch.setattr(log, "append", append)
            await queue.resume("approval")
            assert not queue.status().paused and queue._resume_evt.is_set()
    finally:
        gate.release.set()
        monkeypatch.setattr(log, "append", append)
        await asyncio.gather(pause, *([resume] if resume else []), return_exceptions=True)
        await cleanup(provider)


@pytest.mark.parametrize("decision", ["approve", "deny"])
async def test_application_service_returns_only_after_queue_ack(decision):
    from pyharness.application import ApplicationService

    gate = Gate()
    provider, queue, log, ctx, call = await stack(resume_gate=gate)
    service = ApplicationService(SimpleNamespace(bus=None, storage=None), channel="cli")
    service._approvals[log.sid] = provider
    request, aid = await begin(provider, queue, ctx, call)
    api = asyncio.create_task(service.decide_approval(aid, decision, sid=log.sid))
    try:
        async with asyncio.timeout(3):
            await gate.entered.wait()
            assert not api.done() and not request.done()
            assert (await service.pending_approvals())["count"] == 1
            assert queue.status().paused
            gate.release.set()
            assert (await api)["ok"] is True
            assert await request == ("granted" if decision == "approve" else "denied")
            assert queue._resume_evt.is_set() and not queue.status().paused
            assert (await service.pending_approvals())["count"] == 0
    finally:
        gate.release.set()
        await cleanup(provider, request, api)


async def test_shutdown_during_initialization_denies_without_late_pause():
    gate = Gate()
    provider, queue, log, ctx, call = await stack()
    append = log.append

    async def held(type_, *args, **kwargs):
        env = await append(type_, *args, **kwargs)
        if type_ == "approval.requested":
            await gate.wait()
        return env

    log.append = held
    request = asyncio.create_task(provider.request(call, "test", ctx))
    closing = None
    try:
        async with asyncio.timeout(3):
            await gate.entered.wait()
            closing = asyncio.create_task(provider.aclose())
            await checkpoint()
            gate.release.set()
            await closing
            assert await request == "denied"
            assert not queue.status().paused and provider.pending_count() == 0
            with pytest.raises(PyHError, match="APR-503"):
                await provider.request(call, "late", ctx)
            await provider.aclose()
    finally:
        gate.release.set()
        await cleanup(provider, request, *([closing] if closing else []))


async def test_grant_preserves_unrelated_budget_pause():
    provider, queue, log, ctx, call = await stack()
    await queue.pause("budget")
    request, aid = await begin(provider, queue, ctx, call)
    try:
        await provider.approve_async(aid, by="cli:test")
        assert await request == "granted"
        assert queue.status().pause_reasons == ["budget"]
        assert provider.pending_count() == 0
    finally:
        await queue.resume("budget")
        await cleanup(provider, request)


async def test_cancel_merged_waiter_settles_whole_batch():
    provider, queue, log, ctx, call = await stack()
    first, aid = await begin(provider, queue, ctx, call)
    entered = asyncio.Event()
    register = provider._register

    async def notified(*args):
        req = await register(*args)
        entered.set()
        return req

    provider._register = notified
    second_call = SimpleNamespace(name=call.name, call_id="merged-call", args=call.args)
    second = asyncio.create_task(provider.request(second_call, "merged", ctx))
    try:
        async with asyncio.timeout(3):
            await entered.wait()
            second.cancel()
            with pytest.raises(asyncio.CancelledError):
                await second
            assert await first == "denied"
            assert provider.pending_count() == 0 and not queue.status().paused
            with pytest.raises(PyHError, match="APR-503"):
                await provider.approve_async(aid, by="cli:late")
    finally:
        await cleanup(provider, first, second)


@pytest.mark.parametrize("decision", ["approve", "deny"])
async def test_sync_cli_decision_joins_published_request_initialization(decision):
    gate = Gate()
    provider, queue, log, ctx, call = await stack()
    append = log.append
    aid = None

    async def held(type_, *args, **kwargs):
        nonlocal aid
        env = await append(type_, *args, **kwargs)
        if type_ == "approval.requested":
            aid = env.seq
            await gate.wait()
        return env

    log.append = held
    request = asyncio.create_task(provider.request(call, "test", ctx))
    try:
        async with asyncio.timeout(3):
            await gate.entered.wait()
            getattr(provider, decision)(aid, by="cli:test")
            assert not request.done()
            gate.release.set()
            assert await request == ("granted" if decision == "approve" else "denied")
            assert not queue.status().paused and provider.pending_count() == 0
    finally:
        gate.release.set()
        await cleanup(provider, request)


async def test_cancel_before_registration_cannot_cancel_another_caller():
    gate = Gate()
    provider, queue, log, ctx, call = await stack(pause_gate=gate)
    first = asyncio.create_task(provider.request(call, "first", ctx))
    second = None
    try:
        async with asyncio.timeout(3):
            await gate.entered.wait()
            aid = next(iter(provider._pending))
            second = asyncio.create_task(provider.request(call, "second", ctx))
            await checkpoint()
            second.cancel()
            await checkpoint()
            gate.release.set()
            with pytest.raises(asyncio.CancelledError):
                await second
            await provider.approve_async(aid, by="cli:first")
            assert await first == "granted"
            assert not queue.status().paused
    finally:
        gate.release.set()
        await cleanup(provider, first, *([second] if second else []))


async def test_real_ttl_callback_can_claim_while_pause_is_inflight():
    gate = Gate()
    provider, queue, log, ctx, call = await stack(pause_gate=gate)
    request = asyncio.create_task(provider.request(call, "test", ctx))
    try:
        async with asyncio.timeout(3):
            await gate.entered.wait()
            aid = next(iter(provider._pending))
            req = provider._pending[aid]
            assert req.timer is not None, "TTL must be armed before queue.pause finishes"
            req.timer.cancel()
            provider._ttl_tick(req)  # controlled timer delivery; no clock sleep
            await checkpoint()
            gate.release.set()
            assert await request == "timeout"
            with pytest.raises(PyHError, match="APR-503"):
                await provider.approve_async(aid, by="cli:late")
            assert provider.pending_count() == 0 and not queue.status().paused
    finally:
        gate.release.set()
        await cleanup(provider, request)


async def test_sync_replay_still_fails_immediately_during_other_initialization():
    provider, queue, log, ctx, call = await stack()
    first, old_id = await begin(provider, queue, ctx, call)
    await provider.approve_async(old_id, by="cli:first")
    assert await first == "granted"
    gate, append = Gate(), log.append
    new_id = None

    async def held(type_, *args, **kwargs):
        nonlocal new_id
        env = await append(type_, *args, **kwargs)
        if type_ == "approval.requested":
            new_id = env.seq
            await gate.wait()
        return env

    log.append = held
    second = asyncio.create_task(provider.request(call, "second", ctx))
    try:
        async with asyncio.timeout(3):
            await gate.entered.wait()
            with pytest.raises(PyHError, match="APR-503"):
                provider.approve(old_id, by="cli:replay")
            with pytest.raises(PyHError, match="APR-503"):
                provider.deny(old_id, by="cli:replay")
            provider.approve(new_id, by="cli:second")
            gate.release.set()
            assert await second == "granted"
            assert not queue.status().paused
    finally:
        gate.release.set()
        await cleanup(provider, first, second)


async def test_waiter_and_pending_remain_until_resume_acknowledgement():
    gate = Gate()
    provider, queue, log, ctx, call = await stack(resume_gate=gate)
    request = asyncio.create_task(provider.request(call, "test", ctx))
    decision = None
    try:
        async with asyncio.timeout(3):
            await queue.paused.wait()
            aid = next(iter(provider._pending))
            decision = asyncio.create_task(provider.approve_async(aid, by="cli:test"))
            await gate.entered.wait()
            assert provider.pending_count() == 1, "pending removed before resume completed"
            assert not request.done(), "approval waiter woke before resume completed"
            assert not decision.done()
            assert queue.status().paused
            gate.release.set()
            await decision
            assert await request == "granted"
            assert not queue.status().paused
            assert queue._resume_evt.is_set()
            assert provider.pending_count() == 0
    finally:
        gate.release.set()
        await cleanup(provider, *([request, decision] if decision else [request]))
