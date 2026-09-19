from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from pyharness.application import ApplicationService
from pyharness.core import llm as llm_mod

SID = "s-service-0001"


class _FakeAdapter:
    """离线 LLM 适配器(BUG-2 用例:到点任务经 runner 真实走完,零网络)。"""

    model = "deepseek-chat"

    async def chat(self, messages, tools=None, *, ctx):
        usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1,
                                prompt_cache_hit_tokens=0,
                                prompt_cache_miss_tokens=1)
        await llm_mod.report_usage(usage, self.model, ctx=ctx,
                                   counters=getattr(ctx, "counters", None))
        resp = llm_mod.LLMResponse(content="[FAKE] ok", tool_calls=None,
                                   usage=usage, model=self.model,
                                   finish_reason="stop")
        env = await ctx.session.append(
            "llm.response", {"model": self.model, "finish_reason": "stop",
                             "content": resp.content, "tool_calls": []},
            actor="llm")
        resp.seq = env.seq
        return resp

    async def chat_stream(self, messages, tools=None, *, ctx):
        return await self.chat(messages, tools, ctx=ctx)

    async def ping(self) -> float:
        return 0.01


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


def test_approval_for_isolates_sessions_on_shared_ctx():
    """多会话共享 ctx:共享 approval 归属 A 时,请求 B 会话不得复用(修复前串场)。"""
    shared = SimpleNamespace(__shared_approval__=True)      # 冒充 ctx.approval
    ctx = SimpleNamespace(bus=None, storage=None,
                          settings=SimpleNamespace(),
                          approval=shared, _approval_owner_sid="s-A")
    svc = ApplicationService(ctx, channel="desktop")
    # B 先请求(_approvals 尚空):owner(s-A) ≠ s-B → 必须建 per-session,不返回 shared
    got_b = svc.approval_for("s-B", _Log())
    assert got_b is not shared
    assert svc._approvals["s-B"] is got_b
    # A 自己请求:owner 匹配且首个 → 复用注入的共享 provider
    got_a = svc.approval_for("s-A", _Log())
    assert got_a is shared


def test_validate_registry_url_ssrf():
    """registry_url 校验:拒非 http(s) 与云元数据地址;正常 http(s) 放行。"""
    from pyharness.application.service import _validate_registry_url
    from pyharness.errors import PyHError

    assert _validate_registry_url("https://skills.example.com") == \
        "https://skills.example.com"
    with pytest.raises(PyHError) as e1:
        _validate_registry_url("file:///etc/passwd")
    assert e1.value.code == "CFG-601"
    with pytest.raises(PyHError) as e2:
        _validate_registry_url("http://169.254.169.254/latest/meta-data")
    assert e2.value.code == "CFG-601"


@pytest.mark.asyncio
async def test_public_spine_for_injects_task_queue_bug2(tmp_path):
    """BUG-2 回归:public_spine_for 装配的 spine 必须带本会话 task_queue。

    旧实现在此调 activate_orchestration(spine)(**无队列**)→ scheduler 的分钟泵
    以 _SpineCtx.task_queue(=spine.task_queue=None)启动 → 到点 _fire 抛
    CYC-999「未注入 task_queue」:schedule.trigger 已落盘但任务**不入队**(半静默)。
    现委托 queue_for 单例注入。断言:装配后队列非空,且到点直达 task.enqueued。
    """
    from pyharness import cli

    cfg = cli._load_settings(None)
    cfg.storage.root = str(tmp_path)
    cfg.storage.sessions_dir = str(tmp_path / "sessions")
    cfg.storage.workspaces_dir = str(tmp_path / "workspaces")
    cfg.storage.spill_dir = str(tmp_path / "spill")
    cfg.storage.db_path = str(tmp_path / "pyharness.db")

    saved = dict(llm_mod.adapters)
    llm_mod.adapters.clear()
    llm_mod.adapters[cfg.llm.model] = _FakeAdapter()
    try:
        svc = ApplicationService(cli.assemble_ctx(cfg), channel="cli")
        sid = await svc.surface_mgr().create()          # 真实建会话路径
        _, spine = await svc.public_spine_for(sid)      # 被测路径

        assert spine.task_queue is not None, "BUG-2:spine 未拿到 task_queue"
        assert spine.schedule.task_queue is not None, \
            "BUG-2:scheduler 未拿到 task_queue"
        assert spine.schedule._ticker_task is not None  # 泵已起

        sctx = SimpleNamespace(session=spine.session,
                               task_queue=spine.task_queue)
        await spine.schedule.register("morning", "cron", "0 8 * * 1-5",
                                      {"intent": "提醒我开始工作"},
                                      is_risky=False, ctx=sctx)
        job = spine.schedule._jobs["morning"]
        assert job.next_fire_at is not None             # BUG-1 配套:非 None
        await spine.schedule._tick(sctx, now_dt=job.next_fire_at)

        types = [e.type for e in spine.session.events_after(0)]
        assert "task.enqueued" in types, f"BUG-2:未入队,实际事件={types}"
        # 收尾:等到点任务跑完,避免遗留 pending 任务
        st = spine.task_queue.status()
        for tid in list(st.waiting) + ([st.running] if st.running else []):
            await asyncio.wait_for(spine.task_queue.wait_for(tid), timeout=10.0)
        spine.schedule.stop()
    finally:
        llm_mod.adapters.clear()
        llm_mod.adapters.update(saved)
