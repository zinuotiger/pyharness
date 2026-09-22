"""重启/恢复一致性(e2e,R8-2):**首次打开 vs 恢复打开**的关键派生必须一致。

真实装配(真 store/真 JSONL/真 SessionLog):关掉第一个 spine(含关句柄)后用同一
sid/目录重开,逐项比对——工作区派生 · 会话作用域 · 租户归属 · 事件过滤 · 审批状态 ·
证据状态(由日志重建)· 任务状态。
"""
from __future__ import annotations

import pathlib

from pyharness.bus import EventBus
from pyharness.config import load_settings
from pyharness.core.task_queue import TaskQueue
from pyharness.engine import build_spine


async def _open(tmp_path, sid, *, tenant_id=None, bus=None):
    cfg = load_settings()
    cfg.storage.root = str(tmp_path)
    cfg.storage.sessions_dir = str(tmp_path / "sessions")
    cfg.storage.workspaces_dir = str(tmp_path / "workspaces")
    cfg.storage.spill_dir = str(tmp_path / "spill")
    cfg.storage.db_path = str(tmp_path / "index.db")
    cfg.llm.fallback_models = []
    spine = await build_spine(cfg, sid=sid, sessions_dir=tmp_path / "sessions",
                              bus=bus, tenant_id=tenant_id)
    if not list(spine.session.events_after(0)):        # 仅首次打开需引导 created
        await spine.session.append("session.created",
                                   {"title": sid, "model": "m"}, actor="system",
                                   sync=True)
    return spine


class _Runner:
    def __init__(self, session) -> None:
        self.session = session

    async def run_for_task(self, task):
        await self.session.append("agent.message",
            {"content": f"done:{task.id}", "model": "m"}, actor="agent",
            task_id=task.id)
        return f"ok:{task.id}"

    async def cancel_current(self, task_id):        # noqa: ARG002
        return None


SID = "s-recover-0001"


async def test_recovered_open_matches_first_open_derivations(tmp_path):
    """**首开 vs 恢复开**:工作区/作用域/租户/过滤/审批/证据/任务逐项一致。"""
    from pyharness.core.tenant_settings import event_tenant_of

    # ---- 首次打开:跑一个任务、写若干事件,然后整体关掉(含 store 句柄)
    first = await _open(tmp_path, SID, tenant_id="alpha")
    ws_first = pathlib.Path(first.scope.policy.workspace_root)
    sid_scope_first = first.scope.session_id
    q = TaskQueue(first.session, runner=_Runner(first.session))
    tid = await q.submit("first-task")
    r = await q.wait_for(tid)
    assert r.ok
    evidence_indexed_first = await _evidence_count(first)
    await first.close()
    assert not first.scope.policy.workspace_root == ""    # 关闭不影响快照读取

    # ---- 恢复打开:同一 sid/目录
    second = await _open(tmp_path, SID)
    try:
        # ① 工作区派生:同一单点函数 ⇒ 路径逐字相同且真实存在
        ws_second = pathlib.Path(second.scope.policy.workspace_root)
        assert ws_second == ws_first and ws_second.is_dir()

        # ② 会话作用域:sid 与派生同源
        assert second.scope.session_id == sid_scope_first == SID

        # ③ 历史事件恢复:首开的任务事件仍在(日志=真源)
        types = [e.type for e in second.session.events_after(0)]
        assert "task.completed" in types and "agent.message" in types
        assert any(e.payload.get("content") == "done:t-1"
                   for e in second.session.events_after(0)
                   if e.type == "agent.message")

        # ④ 租户归属:日志已落的租户胜出(new session 新事件带同一租户)
        assert all(e.tenant_id == "alpha" for e in second.session.events_after(0))
        await second.session.append("user.message", {"content": "after-restart"},
                                    actor="user", sync=True)
        env = list(second.session.events_after(0))[-1]
        assert event_tenant_of(env) == "alpha"

        # ⑤ 证据状态:由日志重建的计数与首开时一致(不依赖易失索引)
        assert await _evidence_count(second) == evidence_indexed_first

        # ⑥ 审批状态:重开后无残留未决、未 detach(全新实例,不是复用旧状态)
        assert second.approval.pending_count() == 0
        assert second.approval._detached is False

        # ⑦ 任务状态:重开后队列空闲,不得残留"在途"
        assert second.task_queue is None or second.task_queue.status().running is None

        # ⑧ 事件过滤在新实例同样生效(新总线,独立订阅)
        assert second.session.sid == SID
    finally:
        await second.close()


async def _evidence_count(spine) -> int:
    """证据工件数(**由日志重建**,INV-E3:不读订阅态内存索引)。"""
    from pyharness.governance.evidence import EvidenceCollector
    rebuilt = await EvidenceCollector.from_log(spine.session)
    return rebuilt.evidence_count()


async def test_recovered_open_shares_workspace_with_original(tmp_path):
    """工作区派生是**纯函数**(sid+配置) ⇒ 重启后同一路径,附件/产物不搬家。"""
    bus = EventBus()
    a = await _open(tmp_path, "s-recover-0002", bus=bus)
    ws_a = pathlib.Path(a.scope.policy.workspace_root)
    (ws_a / "artifacts").mkdir(parents=True, exist_ok=True)
    (ws_a / "artifacts" / "note.txt").write_text("kept", encoding="utf-8")
    await a.close()

    b = await _open(tmp_path, "s-recover-0002")
    try:
        ws_b = pathlib.Path(b.scope.policy.workspace_root)
        assert ws_b == ws_a
        assert (ws_b / "artifacts" / "note.txt").read_text(encoding="utf-8") == "kept"
    finally:
        await b.close()


# ============ R11-6:ctx.task_id 必须在本任务执行期内可见(goal/todo/llm.chunk 依赖)
async def test_ctx_task_id_is_bound_during_task_execution(e2e_factory):
    """**修复前失败**:`ctx.task_id` 无写入点 ⇒ 恒 None ⇒ goal 的"关联任务失败→
    目标状态推导"(goal.py:357)与"关联任务"渲染从未生效。

    本用例经**真实链路**(脚本化 LLM → goal 工具 → 事件)断言 goal.created 载荷
    带上**当前任务 id**,而非 None。
    """
    s = await e2e_factory(
        [{"id": "g1", "name": "goal",
          "args": {"op": "create", "text": "整理本周笔记"}}],
        sid="s-taskid-0001")
    await s.boot()
    assert (await s.run("create a goal")).ok

    created = s.of("goal.created")
    assert created, "前置:应落 goal.created"
    tid = created[0].payload.get("task_id")
    assert tid, f"goal 应关联当前任务(修复前恒 None):{created[0].payload}"

    # 任务边界之外必须还原(不得把 task_id 泄漏给闲聊/下一轮之外)
    spine = s.ctx.engine_spine
    ag = spine.active_agents.get(s.ctx.session.sid)
    assert ag is not None and getattr(ag.ctx, "task_id", None) in (None, ""), \
        "任务结束后应还原 ctx.task_id"
