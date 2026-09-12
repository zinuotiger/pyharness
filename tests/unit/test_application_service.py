from __future__ import annotations

from types import SimpleNamespace

import pytest

from pyharness.application import ApplicationService

SID = "s-service-0001"


class _Log:
    def __init__(self) -> None:
        self.events = []

    async def append(self, type_, payload, **kw):
        env = SimpleNamespace(seq=len(self.events) + 1, type=type_,
                              payload=dict(payload))
        self.events.append((env, kw))
        return env

    def stats(self):
        return {"seq": len(self.events)}


class _Queue:
    def __init__(self) -> None:
        self.submits = []

    async def submit(self, text, *, meta=None):
        self.submits.append((text, dict(meta or {})))
        return "t-1"


@pytest.mark.asyncio
async def test_service_create_message_and_channel_identity():
    svc = ApplicationService(SimpleNamespace(bus=None, storage=None),
                             channel="cli")
    log_ = _Log()
    queue = _Queue()
    svc._logs[SID] = log_

    async def _queue_for(sid, log_=None):
        assert sid == SID and log_ is log_
        return queue

    svc.queue_for = _queue_for
    out = await svc.create_message(SID, "hello")
    assert out == {"task_id": "t-1", "user_seq": 1}
    env, kw = log_.events[0]
    assert env.type == "user.message"
    assert kw["origin"] == "cli"
    assert queue.submits[0][1]["channel"] == "cli"


@pytest.mark.asyncio
async def test_service_jobs_and_subagents_use_spine_managers():
    svc = ApplicationService(SimpleNamespace(bus=None, storage=None),
                             channel="desktop")
    log_ = _Log()
    svc._logs[SID] = log_

    class _Jobs:
        def list_owned(self, by):
            assert by == f"desktop:{SID}"
            return ["j-1"]

        async def status(self, job_id, *, by):
            return SimpleNamespace(job_id=job_id, state="completed", owner=by,
                                   todo_summary="1/1 项", elapsed_ms=3,
                                   progress=1.0, log_tail=[], error=None)

    class _Subs:
        def status(self):
            return SimpleNamespace(running=1, limit=8,
                                   active_children=[{"sub_id": "s-sub0001",
                                                     "state": "running",
                                                     "age_s": 2}])

    svc._engines[SID] = SimpleNamespace(jobs=_Jobs(), subagent=_Subs())
    jobs = await svc.list_jobs(SID)
    subs = await svc.list_subagents(SID)
    assert jobs["jobs"][0]["job_id"] == "j-1"
    assert subs["status"]["active_children"][0]["sub_id"] == "s-sub0001"


def test_service_skill_root_is_repository_skills():
    svc = ApplicationService(SimpleNamespace(bus=None, storage=None))
    names = {row["name"] for row in svc.skills_mgr().list()}
    assert "interview-pitch" in names
