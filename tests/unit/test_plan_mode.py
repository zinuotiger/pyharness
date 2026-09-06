"""tests/unit/test_plan_mode.py — plan_mode 模块单测(F045/F046/F023/F043 契约)。

契约:docs/specs/plan_mode.py.md(权威)+ EVENT-SCHEMA §3.5.2(plan.proposed/
approved/rejected/exec.step 字段/actor)+ SECURITY S-1(终态只认事件)+ INV-01
(注册表由事件回放重建)。

覆盖面(任务要求全项):
    方案生成(F045):只调一次 LLM(≤8 步/强制 JSON)、显式 steps 零 LLM、超限
        EVT-100 零落盘、危险打标(F023:critical/high/none)、intent 拼装、
        24h TTL、selfcheck 纯函数全分支
    状态机(五主态+两终止):draft→pending(proposed)→approved(冻结)→executing
        →completed;终止 rejected/aborted;非法迁移 BUSY/EVT-101
    审批裁决(F046):approve(仅 pending/重复 approve 拒/已拒绝拒/终态拒/24h
        过期自动 rejected)、reject(who/reason/终态拒)、revise(单步 edit 零
        LLM/旧版作废/批准后改 BUSY/越界 EVT-100)
    执行=逐步入队(F043):approve 自动建任务、逐步 exec.step 事件、失败三选一
        (跳过/重试/中止)、重试 ≤2 仍败默认中止(plan.aborted 带 step)、headless
        APR-501 安全默认中止、未批准执行 BUSY
    过期清理(F045):sweep 自动 rejected(who=system/24h 边界/now 注入)
    事件溯源(INV-01):rebuild_from_events 状态重建(含孤儿防御跳过)、真实
        SessionLog 全链事件校验(新登记 plan.done/aborted 词表外扩展可落盘)

错误断言:EVT-100/EVT-101/APR-501 走 raise_code;BUSY/LLM-304 为命名字面量
(ERR.md §2.11 / raise_code 未登记码兜底先例),断言 ei.value.code。
"""
import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest

from pyharness import events as EV
from pyharness.bus import EventBus
from pyharness.core.plan_mode import (MAX_RETRY, MAX_STEPS, PLAN_TTL_HOURS,
                                      Plan, PlanManager, Step, selfcheck_plan)
from pyharness.core.session import SessionLog
from pyharness.errors import PyHError

SID = "s-plan-test01"

# ------------------------------------------------------------------ 替身
# 每步必带 action/tool/expected;risk/intent 由 plan_mode 打标(LLM 不背)
LLM_STEPS = [
    {"action": "扫描目录", "tool": "list_dir", "expected": "得到文件清单"},
    {"action": "读取配置", "tool": "read_file", "expected": "得到配置内容"},
    {"action": "写入报告", "tool": "write_file", "expected": "报告落盘"},
]


class FakeLLM:
    """json_chat 注入替身:记录调用次数/参数,按预设返回(强制 JSON 产物)。"""

    def __init__(self, result) -> None:
        self.result = result
        self.calls: list[dict] = []

    async def json_chat(self, **kw):
        self.calls.append(kw)
        return self.result


class FakeSession:
    """轻量异步事件落点替身(同步记录 + seq 递增;plan 写路径验证用)。

    带 .events/.of() 供事件断言;ts 仿 Envelope(UTC ISO Z 结尾)供 rebuild。
    """

    def __init__(self) -> None:
        self.events: list = []
        self._seq = 0

    async def append(self, type_, payload, *, actor, **kw):
        self._seq += 1
        env = SimpleNamespace(seq=self._seq, type=type_, actor=actor,
                              payload=dict(payload), session_id=SID,
                              ts="2026-09-07T08:00:00.000000Z")
        self.events.append(env)
        return env

    def of(self, type_) -> list:
        """按类型过滤事件(测试断言用)。"""
        return [e for e in self.events if e.type == type_]


class FakeQueue:
    """TaskQueue 替身(submit/wait_for 面):outcomes 按 submit 序消费。

    outcomes 耗尽后默认成功(ok=True)——三选一/中止路径用显式脚本控制。
    """

    def __init__(self, outcomes=None) -> None:
        self._outcomes = list(outcomes or [])
        self.submitted: list[str] = []       # intent 入队序(F043 串行断言)
        self.metas: list = []
        self._seq = 0
        self._results: dict = {}

    async def submit(self, intent, *, meta=None, task_id=None):
        self._seq += 1
        tid = task_id or f"t-{self._seq}"
        self.submitted.append(intent)
        self.metas.append(meta)
        r = self._outcomes.pop(0) if self._outcomes else SimpleNamespace(
            ok=True, code=None, summary=None)
        self._results[tid] = r
        return tid

    async def wait_for(self, tid, *, timeout=None):
        return self._results[tid]


class FakeApproval:
    """ctx.approval.user_choice 替身:按序吐出预设裁决(耗尽取首项)。"""

    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.calls: list[list] = []

    async def user_choice(self, options):
        self.calls.append(list(options))
        return self.answers.pop(0) if self.answers else options[0]


def _fail(code: str = "TLB-802") -> SimpleNamespace:
    """失败 TaskResult 替身(ok=False)。"""
    return SimpleNamespace(ok=False, code=code, summary=f"{code}: 调用被拒")


def _ctx(sess, *, llm=None, queue=None, approval=None, scope=None) -> SimpleNamespace:
    """会话实体替身:session/llm/task_queue/approval/scope 契约面。"""
    return SimpleNamespace(session=sess, llm=llm, task_queue=queue,
                           approval=approval, scope=scope)


async def _drain(mgr: PlanManager) -> None:
    """等待 approve 自动建的全部执行协程落定(测试确定性收尾)。"""
    tasks = [t for t in list(mgr._exec_tasks) if not t.done()]
    if tasks:
        await asyncio.gather(*tasks)


async def _propose(mgr, sess, goal="归档 D:\\work", *, steps=None, llm=None,
                   ctx_extra=None, risk_steps=None):
    """快速提案(默认走显式 steps 零 LLM,测试专注状态机;LLM 路径另测)。"""
    if risk_steps is not None:
        steps = risk_steps
    steps = steps if steps is not None else [dict(s) for s in LLM_STEPS]
    llm = llm or FakeLLM({"steps": [dict(s) for s in LLM_STEPS]})
    ctx = _ctx(sess, llm=llm, queue=FakeQueue())
    if ctx_extra:
        for k, v in ctx_extra.items():
            setattr(ctx, k, v)
    return await mgr.plan_propose(goal, ctx, steps=steps)


# =====================================================================
# F045 方案生成
# =====================================================================
async def test_propose_llm_path_calls_once_and_marks_steps():
    """LLM 路径:只调一次 json_chat;步骤 ≤8;risk 打标(F023)与 intent 拼装;
    plan.id = proposed 事件 seq;status=pending;TTL=24h;注册入表。"""
    mgr, sess = PlanManager(), FakeSession()
    llm = FakeLLM({"steps": [
        {"action": "删文件", "tool": "fs.delete_file", "expected": "文件已删"},
        {"action": "跑命令", "tool": "exec.bash", "expected": "命令输出"},
        {"action": "扫目录", "tool": "list_dir", "expected": "清单"},
    ]})
    ctx = _ctx(sess, llm=llm, queue=FakeQueue())
    p = await mgr.plan_propose("清理临时文件", ctx)

    assert len(llm.calls) == 1, "F045 边界:只调一次 LLM"
    assert llm.calls[0]["goal"] == "清理临时文件"
    assert llm.calls[0]["max_steps"] == MAX_STEPS
    assert llm.calls[0]["prompt"]

    assert isinstance(p, Plan) and len(p.steps) <= MAX_STEPS
    assert [s.risk for s in p.steps] == ["critical", "high", "none"]
    # intent 缺省由 action/tool/expected 拼装(非空)
    assert all(s.intent for s in p.steps)
    assert p.steps[0].intent == "用 fs.delete_file 删文件: 文件已删"
    # 事件:先落盘(plan.proposed actor=agent)后返回;id = 事件 seq
    proposed = sess.of("plan.proposed")
    assert len(proposed) == 1 and proposed[0].actor == "agent"
    assert p.id == proposed[0].seq
    assert p.status == "pending" and p.goal == "清理临时文件"
    # 24h TTL(created + 24h)
    delta = p.expires_at - p.created_at
    assert timedelta(hours=PLAN_TTL_HOURS - 1) <= delta <= \
        timedelta(hours=PLAN_TTL_HOURS + 1)
    assert p.expires_at > p.created_at
    assert mgr.list_plans() == [p]


async def test_propose_explicit_steps_skips_llm():
    """显式 steps(plan_revise 复用路径):零 LLM 调用;仍打标/自检/落事件。"""
    mgr, sess = PlanManager(), FakeSession()
    llm = FakeLLM(None)
    ctx = _ctx(sess, llm=llm, queue=FakeQueue())
    steps = [{"action": "a", "tool": "exec.bash", "expected": "e"},
             {"action": "b", "tool": "write_file", "expected": "f"}]
    p = await mgr.plan_propose("目标", ctx, steps=steps)
    assert len(llm.calls) == 0, "显式 steps 必须零 LLM 调用"
    assert [s.risk for s in p.steps] == ["high", "none"]
    assert p.selfcheck_ok is True


async def test_propose_step_count_limits():
    """步骤数 0 / >max_steps → EVT-100;不落盘零半成品(LLM 已调但事件零)。"""
    mgr, sess = PlanManager(), FakeSession()
    llm = FakeLLM({"steps": [dict(s) for s in LLM_STEPS] * 4})   # 12 步
    ctx = _ctx(sess, llm=llm, queue=FakeQueue())
    with pytest.raises(PyHError) as ei:
        await mgr.plan_propose("目标", ctx)
    assert ei.value.code == "EVT-100"
    assert "1..8" in ei.value.ctx["hint"]
    assert not sess.events, "超限方案不得落任何事件(零半成品)"

    llm2 = FakeLLM([])                        # 空数组
    ctx2 = _ctx(sess, llm=llm2, queue=FakeQueue())
    with pytest.raises(PyHError) as ei:
        await mgr.plan_propose("目标", ctx2)
    assert ei.value.code == "EVT-100"
    assert not sess.events


async def test_propose_missing_fields_rejected_early():
    """步骤缺 action/tool/expected → EVT-100 前置拒(events 层必拒,给明确提示)。"""
    mgr, sess = PlanManager(), FakeSession()
    llm = FakeLLM({"steps": [{"action": "", "tool": "list_dir",
                              "expected": "e"}]})
    ctx = _ctx(sess, llm=llm, queue=FakeQueue())
    with pytest.raises(PyHError) as ei:
        await mgr.plan_propose("目标", ctx)
    assert ei.value.code == "EVT-100"
    assert not sess.events


async def test_propose_llm_garbage_structure():
    """LLM 返回非数组/缺 steps 键 → LLM-304(模型业务性失败,不落盘)。"""
    mgr, sess = PlanManager(), FakeSession()
    for bad in ({"foo": 1}, "plain text", 42):
        ctx = _ctx(sess, llm=FakeLLM(bad), queue=FakeQueue())
        with pytest.raises(PyHError) as ei:
            await mgr.plan_propose("目标", ctx)
        assert ei.value.code == "LLM-304"
        assert not sess.events


async def test_propose_scope_danger_marks_override():
    """F023 联动:ctx.scope.policy.danger_marks 存在时优先(表变更只紧不松)。"""
    mgr, sess = PlanManager(), FakeSession()
    scope = SimpleNamespace(policy=SimpleNamespace(danger_marks=[
        # 装配合并序 = 内置表在前、extra 上调在后(先精确后通配)
        {"pattern": "fs.delete_file", "level": "critical"},
        {"pattern": "fs.*", "level": "high"},
        {"pattern": "exec.*", "level": "high"},
        {"pattern": "net.*", "level": "high"},
    ]))
    ctx = _ctx(sess, llm=FakeLLM(None), queue=FakeQueue(), scope=scope)
    p = await mgr.plan_propose("目标", ctx, steps=[
        {"action": "删", "tool": "fs.delete_file", "expected": "删掉"},
        {"action": "复制", "tool": "fs.copy", "expected": "拷好"},
        {"action": "读", "tool": "read_file", "expected": "内容"},
    ])
    assert [s.risk for s in p.steps] == ["critical", "high", "none"]


async def test_selfcheck_plan_pure():
    """selfcheck_plan 纯函数全分支(空/超限/缺 action/tool/expected/critical/过)。"""
    assert selfcheck_plan([]) == (False, "步骤数须为 1..8")
    assert selfcheck_plan([dict(s) for s in LLM_STEPS] * 4)[0] is False  # 12 步
    # 缺 expected(GWT-F045-02)
    ok, why = selfcheck_plan([{"action": "a", "tool": "list_dir"}])
    assert ok is False and "expected" in why
    ok, why = selfcheck_plan([{"action": "", "tool": "list_dir",
                               "expected": "e"}])
    assert ok is False and "action" in why
    ok, why = selfcheck_plan([{"action": "a", "tool": "",
                               "expected": "e"}])
    assert ok is False and "tool" in why
    # critical 工具:本层不可用标 why(展示用不阻断)
    ok, why = selfcheck_plan([{"action": "删", "tool": "fs.delete_file",
                               "expected": "已删", "risk": "critical"}])
    assert ok is False and "critical" in why and "fs.delete_file" in why
    ok, why = selfcheck_plan([{"action": "a", "tool": "list_dir",
                               "expected": "e", "risk": "low"}])
    assert ok is True and why == ""


def test_classify_danger_table():
    """F023 同源表前缀匹配:fs.delete_file=critical/exec.*=high/net.*=high/表外 none。"""
    m = PlanManager()
    assert m.classify_danger("fs.delete_file") == "critical"
    assert m.classify_danger("exec.bash") == "high"
    assert m.classify_danger("net.fetch") == "high"
    assert m.classify_danger("list_dir") == "none"
    assert m.classify_danger("fs.read_file") == "none"   # 精确匹配,不误伤 fs.*
    assert m.classify_danger("") == "none"


async def test_propose_selfcheck_false_still_proposed():
    """risk=critical 步骤:selfcheck_ok=False + why 说明,但方案照常落盘待批(软指导)。"""
    mgr, sess = PlanManager(), FakeSession()
    p = await _propose(mgr, sess, risk_steps=[
        {"action": "删文件", "tool": "fs.delete_file", "expected": "已删"},
        {"action": "扫", "tool": "list_dir", "expected": "清单"},
    ])
    assert p.selfcheck_ok is False and "critical" in p.why
    assert p.status == "pending"
    assert sess.of("plan.proposed")[0].payload["selfcheck_ok"] is False


# =====================================================================
# F046 审批裁决与状态机
# =====================================================================
async def test_approve_missing_plan_evt101():
    """approve/reject 不存在 plan_id → EVT-101(必先 proposed)。"""
    mgr, sess = PlanManager(), FakeSession()
    ctx = _ctx(sess, queue=FakeQueue())
    for call in (mgr.plan_approve(99, ctx=ctx), mgr.plan_reject(99, ctx=ctx),
                 mgr.plan_revise(99, 0, {"action": "x"}, ctx),
                 mgr.plan_execute(99, ctx)):
        with pytest.raises(PyHError) as ei:
            await call
        assert ei.value.code == "EVT-101"


async def test_approve_pending_starts_execution_to_completed():
    """批准才执行:approve 写 plan.approved(user) 自动建任务;逐步 exec.step 事件
    串行入队(submit 序=方案步骤序);全部成功写 plan.done(system)→ completed。"""
    mgr, sess = PlanManager(), FakeSession()
    p = await _propose(mgr, sess, goal="归档 work")
    q = FakeQueue()
    ctx = _ctx(sess, llm=FakeLLM(None), queue=q)
    await mgr.plan_approve(p.id, ctx=ctx)

    approved = sess.of("plan.approved")
    assert len(approved) == 1
    assert approved[0].actor == "user" and approved[0].payload["who"] == "user"
    assert approved[0].payload["plan_id"] == p.id

    await _drain(mgr)                        # 执行协程落定
    assert mgr.get(p.id).status == "completed"
    # 逐步入队:submit 序 = 步骤 intent 序(F043 串行)
    assert q.submitted == [s.intent for s in p.steps]
    assert [m["step_idx"] for m in q.metas] == [0, 1, 2]
    # 事件序:每步 exec.step(agent)先于入队结果;收尾 plan.done(system)
    types = [e.type for e in sess.events]
    assert types.count("plan.exec.step") == len(p.steps)
    assert all(e.actor == "agent" for e in sess.of("plan.exec.step"))
    assert sess.of("plan.done")[0].actor == "system"
    assert types[-1] == "plan.done"


async def test_approve_guards():
    """重复 approve / 已拒绝 / 终态 / 非待批 → BUSY(状态机非法迁移显式拒)。"""
    mgr, sess = PlanManager(), FakeSession()
    p = await _propose(mgr, sess)
    q = FakeQueue()
    ctx = _ctx(sess, queue=q)
    # 重复 approve(第一次批准后立即二次 → 非待批态,竞态无关:守卫同步先行)
    await mgr.plan_approve(p.id, ctx=ctx)
    with pytest.raises(PyHError) as ei:
        await mgr.plan_approve(p.id, ctx=ctx)
    assert ei.value.code == "BUSY"
    await _drain(mgr)

    # 已拒绝方案不可再批准(F046 边界)
    p2 = await _propose(mgr, sess)
    await mgr.plan_reject(p2.id, ctx=ctx)
    with pytest.raises(PyHError) as ei:
        await mgr.plan_approve(p2.id, ctx=ctx)
    assert ei.value.code == "BUSY"
    assert "已拒绝" in ei.value.ctx["hint"]

    # 终态(completed)不可再裁决
    p3 = await _propose(mgr, sess)
    await mgr.plan_approve(p3.id, ctx=ctx)
    await _drain(mgr)
    assert mgr.get(p3.id).status == "completed"
    with pytest.raises(PyHError) as ei:
        await mgr.plan_approve(p3.id, ctx=ctx)
    assert ei.value.code == "BUSY"


async def test_approve_expired_auto_rejected():
    """24h 未确认后 approve → 先自动 rejected(who=system)再 BUSY(过期不执行)。"""
    mgr, sess = PlanManager(), FakeSession()
    p = await _propose(mgr, sess)
    q = FakeQueue()
    ctx = _ctx(sess, queue=q)
    p.expires_at = p.created_at - timedelta(seconds=1)   # 时钟前拨(过期)
    with pytest.raises(PyHError) as ei:
        await mgr.plan_approve(p.id, ctx=ctx)
    assert ei.value.code == "BUSY"
    assert "过期" in ei.value.ctx["hint"]
    rejected = sess.of("plan.rejected")
    assert len(rejected) == 1
    assert rejected[0].actor == "system"
    assert rejected[0].payload["who"] == "system"
    assert rejected[0].payload["reason"] == "expired"
    assert mgr.get(p.id).status == "rejected"
    assert q.submitted == [], "过期方案不得执行(零入队)"


async def test_reject_writes_event_and_blocks_further_decision():
    """拒绝回修订对话:plan.rejected(who/reason) 落盘;拒绝后 approve/execute 拒。"""
    mgr, sess = PlanManager(), FakeSession()
    p = await _propose(mgr, sess)
    q = FakeQueue()
    ctx = _ctx(sess, queue=q)
    await mgr.plan_reject(p.id, who="user", reason="工具不对,换招", ctx=ctx)
    rejected = sess.of("plan.rejected")
    assert len(rejected) == 1 and rejected[0].actor == "user"
    assert rejected[0].payload["plan_id"] == p.id
    assert rejected[0].payload["reason"] == "工具不对,换招"
    assert mgr.get(p.id).status == "rejected"
    # 拒绝后不再推进
    with pytest.raises(PyHError) as ei:
        await mgr.plan_approve(p.id, ctx=ctx)
    assert ei.value.code == "BUSY"
    # 拒绝后不可执行:直接调 plan_execute → 静默退场(不入队、零新事件,不再推进)
    before = len(sess.events)
    await mgr.plan_execute(p.id, ctx)
    assert len(sess.events) == before and q.submitted == []


async def test_reject_terminal_busy():
    """终态(completed/rejected)不可再拒绝 → BUSY。"""
    mgr, sess = PlanManager(), FakeSession()
    ctx = _ctx(sess, queue=FakeQueue())
    p = await _propose(mgr, sess)
    await mgr.plan_reject(p.id, ctx=ctx)
    with pytest.raises(PyHError) as ei:
        await mgr.plan_reject(p.id, ctx=ctx)
    assert ei.value.code == "BUSY"
    p2 = await _propose(mgr, sess)
    await mgr.plan_approve(p2.id, ctx=ctx)
    await _drain(mgr)
    with pytest.raises(PyHError) as ei:
        await mgr.plan_reject(p2.id, ctx=ctx)
    assert ei.value.code == "BUSY"


async def test_revise_single_step_edit_zero_llm():
    """单步 edit:旧方案 rejected(revised:step:N) 作废;同目标重提新版零 LLM;
    改工具重打危险标;批准后执行的是新版。"""
    mgr, sess = PlanManager(), FakeSession()
    llm = FakeLLM({"steps": [dict(s) for s in LLM_STEPS]})
    ctx = _ctx(sess, llm=llm, queue=FakeQueue())
    p = await mgr.plan_propose("归档 work", ctx)     # LLM 路径:已调 1 次
    assert len(llm.calls) == 1

    # 单步 edit:第 0 步改为 net.fetch(危险高)
    p2 = await mgr.plan_revise(p.id, 0, {
        "action": "拉远程清单", "tool": "net.fetch",
        "expected": "得到远端文件清单"}, ctx)
    assert len(llm.calls) == 1, "revise 是本地编辑,零 LLM 重调(F046 边界)"

    # 旧版作废留痕;新版同目标待批
    rejected = sess.of("plan.rejected")
    assert len(rejected) == 1
    assert rejected[0].payload["plan_id"] == p.id
    assert rejected[0].payload["reason"] == "revised:step:0"
    assert mgr.get(p.id) is None, "旧 plan 出注册表(事件留痕)"
    assert p2.id != p.id and p2.goal == "归档 work" and p2.status == "pending"
    assert p2.steps[0].tool == "net.fetch" and p2.steps[0].risk == "high"
    assert p2.steps[0].action == "拉远程清单"
    assert [s.tool for s in p2.steps[1:]] == [s.tool for s in p.steps[1:]]

    # 批准后执行的是新版(入队序 = 新版步骤)
    q = FakeQueue()
    ctx2 = _ctx(sess, llm=llm, queue=q)
    await mgr.plan_approve(p2.id, ctx=ctx2)
    await _drain(mgr)
    assert mgr.get(p2.id).status == "completed"
    assert q.submitted == [s.intent for s in p2.steps]
    assert q.submitted[0] == "用 net.fetch 拉远程清单: 得到远端文件清单"


async def test_revise_guards():
    """非 pending(已批准)改 → BUSY(改=reject 重提);step_idx 越界 → EVT-100;
    脏 patch 字段 → EVT-100。"""
    mgr, sess = PlanManager(), FakeSession()
    ctx = _ctx(sess, queue=FakeQueue())
    p = await _propose(mgr, sess, steps=[dict(LLM_STEPS[0])])
    await mgr.plan_approve(p.id, ctx=ctx)
    with pytest.raises(PyHError) as ei:
        await mgr.plan_revise(p.id, 0, {"action": "x"}, ctx)
    assert ei.value.code == "BUSY"
    assert "已批准方案不可变" in ei.value.ctx["advice"]
    await _drain(mgr)

    p2 = await _propose(mgr, sess)
    with pytest.raises(PyHError) as ei:
        await mgr.plan_revise(p2.id, 5, {"action": "x"}, ctx)
    assert ei.value.code == "EVT-100"
    assert "越界" in ei.value.ctx["hint"]
    with pytest.raises(PyHError) as ei:
        await mgr.plan_revise(p2.id, 0, {"aciton": "x"}, ctx)
    assert ei.value.code == "EVT-100"


async def test_execute_requires_approved():
    """未批准(仅 pending)直接执行 → BUSY(批准才执行,F046 硬条件)。"""
    mgr, sess = PlanManager(), FakeSession()
    p = await _propose(mgr, sess)
    ctx = _ctx(sess, queue=FakeQueue())
    with pytest.raises(PyHError) as ei:
        await mgr.plan_execute(p.id, ctx)
    assert ei.value.code == "BUSY"
    assert "未批准" in ei.value.ctx["hint"]


# =====================================================================
# F043 执行=逐步入队:失败三选一
# =====================================================================
async def test_execute_skip_and_retry_choices():
    """失败三选一:重试(同一步重入队)成功继续;跳过则下一步;全部成功 → done。"""
    mgr, sess = PlanManager(), FakeSession()
    p = await _propose(mgr, sess)
    # 脚本:step0 首败(重试后成);step1 首败(跳过);step2 默认成功
    q = FakeQueue(outcomes=[_fail(), _fail()])
    ap = FakeApproval("重试", "跳过")
    ctx = _ctx(sess, queue=q, approval=ap)
    await mgr.plan_approve(p.id, ctx=ctx)
    await _drain(mgr)

    plan = mgr.get(p.id)
    assert plan.status == "completed"
    # step0 重试 = 同一步重新入队(重走 guard/审批);跳过继续下一步
    assert q.submitted == [p.steps[0].intent, p.steps[0].intent,
                           p.steps[1].intent, p.steps[2].intent]
    assert ap.calls == [["跳过", "重试", "中止"], ["跳过", "重试", "中止"]]
    assert sess.of("plan.done"), "跳过/重试后全部成功 → plan.done"
    assert not sess.of("plan.aborted")


async def test_execute_user_abort_midway():
    """单步失败用户选中止 → plan.aborted(step=i, actor=system),不再入队后续步。"""
    mgr, sess = PlanManager(), FakeSession()
    p = await _propose(mgr, sess)
    q = FakeQueue(outcomes=[_fail()])
    ap = FakeApproval("中止")
    ctx = _ctx(sess, queue=q, approval=ap)
    await mgr.plan_approve(p.id, ctx=ctx)
    await _drain(mgr)

    plan = mgr.get(p.id)
    assert plan.status == "aborted"
    assert q.submitted == [p.steps[0].intent], "中止后不得再入队"
    aborted = sess.of("plan.aborted")
    assert len(aborted) == 1 and aborted[0].actor == "system"
    assert aborted[0].payload == {"plan_id": p.id, "step": 0}
    assert not sess.of("plan.done")


async def test_execute_retry_exhausted_default_abort():
    """重试 ≤2 次仍败 → 不再问用户,默认中止(GWT-F046-04:aborted 带 step)。"""
    mgr, sess = PlanManager(), FakeSession()
    p = await _propose(mgr, sess)
    q = FakeQueue(outcomes=[_fail(), _fail(), _fail()])   # 初败 + 两次重试全败
    ap = FakeApproval("重试", "重试")
    ctx = _ctx(sess, queue=q, approval=ap)
    await mgr.plan_approve(p.id, ctx=ctx)
    await _drain(mgr)

    plan = mgr.get(p.id)
    assert plan.status == "aborted"
    # 3 次尝试 = 初败 + 重试 2 次(MAX_RETRY=2),耗尽默认中止不询问
    assert q.submitted == [p.steps[0].intent] * (1 + MAX_RETRY)
    assert len(ap.calls) == MAX_RETRY, "耗尽那次不再问用户(默认中止)"
    assert sess.of("plan.aborted")[0].payload == {"plan_id": p.id, "step": 0}
    assert not sess.of("plan.done")


async def test_execute_headless_no_channel_aborts_apr501():
    """headless 无审批通道(ctx.approval 缺失):单步失败 → APR-501 安全默认中止,
    plan.aborted 照落(汇报),执行协程上抛 APR-501。"""
    mgr, sess = PlanManager(), FakeSession()
    p = await _propose(mgr, sess)
    q = FakeQueue(outcomes=[_fail()])
    ctx = _ctx(sess, queue=q)                # 无 approval 通道
    await mgr.plan_approve(p.id, ctx=ctx)
    with pytest.raises(PyHError) as ei:
        await _drain(mgr)
    assert ei.value.code == "APR-501"
    assert mgr.get(p.id).status == "aborted"
    assert sess.of("plan.aborted")[0].payload == {"plan_id": p.id, "step": 0}


async def test_execute_unknown_choice_safe_default_abort():
    """裁决通道返回词表外值 → 按"中止"安全默认处理(不机械静默重试)。"""
    mgr, sess = PlanManager(), FakeSession()
    p = await _propose(mgr, sess)

    class WeirdApproval:
        async def user_choice(self, options):
            return "随便"

    ctx = _ctx(sess, queue=FakeQueue(outcomes=[_fail()]),
               approval=WeirdApproval())
    await mgr.plan_approve(p.id, ctx=ctx)
    await _drain(mgr)
    assert mgr.get(p.id).status == "aborted"


async def test_execute_sync_choice_channel():
    """裁决通道可为同步可调用(异步兼容:inspect 判定)。"""
    mgr, sess = PlanManager(), FakeSession()
    p = await _propose(mgr, sess)

    class SyncApproval:
        def user_choice(self, options):
            return "中止"

    ctx = _ctx(sess, queue=FakeQueue(outcomes=[_fail()]),
               approval=SyncApproval())
    await mgr.plan_approve(p.id, ctx=ctx)
    await _drain(mgr)
    assert mgr.get(p.id).status == "aborted"


async def test_execute_success_multiple_plans_sequential_registry():
    """两方案先后批准:注册表各自独立完成;事件流按序不串扰。"""
    mgr, sess = PlanManager(), FakeSession()
    ctx = _ctx(sess, queue=FakeQueue())
    pa = await _propose(mgr, sess, goal="甲")
    pb = await _propose(mgr, sess, goal="乙")
    await mgr.plan_approve(pa.id, ctx=ctx)
    await mgr.plan_reject(pb.id, ctx=ctx)
    await _drain(mgr)
    assert mgr.get(pa.id).status == "completed"
    assert mgr.get(pb.id).status == "rejected"
    # active_only=False 全量;active 集只剩未完成
    assert len(mgr.list_plans(active_only=False)) == 2
    assert mgr.list_plans(active_only=True) == []


# =====================================================================
# F045 过期清理
# =====================================================================
async def test_sweep_expired_plans():
    """过期清理:仅 pending 且超 24h 的自动 rejected(who=system/actor=system);
    now 注入时钟;幂等(二次清理为 0)。"""
    mgr, sess = PlanManager(), FakeSession()
    ctx = _ctx(sess, queue=FakeQueue())
    p1 = await _propose(mgr, sess)           # 过期:时钟拨到 24h 后
    p2 = await _propose(mgr, sess)           # 过期
    p3 = await _propose(mgr, sess)           # 未过期:expires_at 拨远
    p3.expires_at = p3.expires_at + timedelta(hours=2)
    now = _deadline_plus(p1, hours=25)
    n = await mgr.plan_sweep_expired(ctx, now=now)
    assert n == 2
    assert mgr.get(p1.id).status == "rejected"
    assert mgr.get(p2.id).status == "rejected"
    assert mgr.get(p3.id).status == "pending"
    rejected = sess.of("plan.rejected")
    assert len(rejected) == 2
    assert all(e.actor == "system" for e in rejected)
    assert all(e.payload["who"] == "system" for e in rejected)
    assert all(e.payload["reason"] == "expired" for e in rejected)
    # 幂等:已 rejected 不再计
    assert await mgr.plan_sweep_expired(ctx, now=now) == 0


def _deadline_plus(p: Plan, *, hours: int):
    """测试时钟:方案过期截止后 hours 时刻(aware UTC)。"""
    from datetime import datetime, timezone
    base = p.created_at or p.expires_at
    return (base + timedelta(hours=hours)).astimezone(timezone.utc)


async def test_sweep_skips_non_pending():
    """sweep 只动 pending:已批准/已拒绝的方案不自动作废。"""
    mgr, sess = PlanManager(), FakeSession()
    ctx = _ctx(sess, queue=FakeQueue())
    p = await _propose(mgr, sess)
    await mgr.plan_approve(p.id, ctx=ctx)    # approved → 执行中(不受 sweep 影响)
    await _drain(mgr)
    p2 = await _propose(mgr, sess)
    await mgr.plan_reject(p2.id, ctx=ctx)
    p3 = await _propose(mgr, sess)
    p3.expires_at = p3.created_at - timedelta(seconds=1)
    now = _deadline_plus(p3, hours=0)
    assert await mgr.plan_sweep_expired(ctx, now=now) == 1
    assert mgr.get(p.id).status == "completed"
    assert mgr.get(p2.id).status == "rejected"
    assert mgr.get(p3.id).status == "rejected"


# =====================================================================
# INV-01 事件溯源重建
# =====================================================================
def _env(seq: int, type_, payload: dict, *, ts: str = None) -> SimpleNamespace:
    return SimpleNamespace(seq=seq, type=type_, payload=payload,
                           ts=ts or "2026-09-07T08:00:00.000000Z")


def test_rebuild_from_events_lifecycle_completed():
    """事件流 → 状态重建:proposed→approved→exec.step→done 重建为 completed;
    步骤从事件载荷还原(缺省 intent 拼装);expires_at 由载荷还原。"""
    events = [
        _env(10, "plan.proposed", {
            "goal": "归档 work",
            "steps": [{"action": "扫", "tool": "list_dir", "expected": "清单",
                       "risk": "none"},
                      {"action": "读", "tool": "read_file", "expected": "内容",
                       "risk": "low"}],
            "selfcheck_ok": True,
            "expires_at": "2026-09-08T08:00:00.000000Z"}),
        _env(11, "plan.approved", {"plan_id": 10, "who": "user"}),
        _env(12, "plan.exec.step", {"plan_id": 10, "step_idx": 0, "action": "扫"}),
        _env(13, "plan.done", {"plan_id": 10}),
    ]
    m = PlanManager.rebuild_from_events(events)
    p = m.get(10)
    assert p is not None
    assert p.status == "completed"           # done → completed(PlanStatus 词表)
    assert p.goal == "归档 work" and p.selfcheck_ok is True
    assert len(p.steps) == 2 and p.steps[0].risk == "none"
    assert p.steps[0].intent == "用 list_dir 扫: 清单"   # 事件无 intent → 拼装
    assert p.expires_at is not None          # 从载荷还原 24h 截止
    assert m.list_plans() == []              # completed 不在未完成集(F058)


def test_rebuild_from_events_terminal_states():
    """aborted/rejected/pending 各态重建;孤儿引用防御跳过不中断。"""
    events = [
        _env(20, "plan.proposed", {"goal": "g1", "steps": [
            {"action": "a", "tool": "list_dir", "expected": "e"}]}),
        _env(21, "plan.approved", {"plan_id": 20, "who": "user"}),
        _env(22, "plan.exec.step", {"plan_id": 20, "step_idx": 0, "action": "a"}),
        _env(23, "plan.aborted", {"plan_id": 20, "step": 0}),
        _env(24, "plan.proposed", {"goal": "g2", "steps": [
            {"action": "b", "tool": "write_file", "expected": "f"}]}),
        _env(25, "plan.rejected", {"plan_id": 24, "who": "system",
                                   "reason": "expired"}),
        _env(26, "plan.proposed", {"goal": "g3", "steps": [
            {"action": "c", "tool": "read_file", "expected": "r"}]}),
        _env(27, "plan.approved", {"plan_id": 999}),   # 孤儿:无 proposed 前身
    ]
    m = PlanManager.rebuild_from_events(events)
    assert m.get(20).status == "aborted"
    assert m.get(24).status == "rejected" and m.get(24).who == "system"
    assert m.get(26).status == "pending"
    assert m.get(999) is None                # 孤儿防御跳过
    # 未完成集:仅 pending 26
    assert [p.id for p in m.list_plans()] == [26]
    assert len(m.list_plans(active_only=False)) == 3


def test_rebuild_missing_expires_at_falls_back_ts_plus_ttl():
    """旧日志 proposed 无 expires_at → 按事件 ts + 24h 兜底(过期判定不失效)。"""
    events = [_env(30, "plan.proposed", {
        "goal": "g", "steps": [{"action": "a", "tool": "list_dir",
                                "expected": "e"}],
        "selfcheck_ok": True},
        ts="2026-09-07T08:00:00.000000Z")]
    m = PlanManager.rebuild_from_events(events)
    p = m.get(30)
    assert p.expires_at is not None
    assert (p.expires_at - p.created_at) == timedelta(hours=PLAN_TTL_HOURS)


# =====================================================================
# 真实 SessionLog 全链(EVENT-SCHEMA §3.5.2 字段级 + 词表外扩展落盘)
# =====================================================================
class FakeStore:
    """SessionStore 内存替身(总线日志订阅者 record;replay/flush 真源语义)。"""

    def __init__(self) -> None:
        self._rows: list = []
        self.flushed: list[int] = []

    def record(self, type_, payload) -> None:
        if isinstance(payload, EV.Envelope):
            self._rows.append(payload)

    def replay(self):
        return list(self._rows)

    async def flush(self, seq: int) -> None:
        self.flushed.append(seq)


async def _wired() -> tuple[SessionLog, FakeStore]:
    """真实 SessionLog + 总线 → 存储订阅者(append 过五步校验链/payload 强校验)。"""
    store = FakeStore()
    bus = EventBus()
    for t in EV.EVENT_TYPES:                 # 词表全量订阅(记录即真源)
        bus.subscribe(t, store.record, owner="persistence")
    log = SessionLog(sid=SID, persistence=store, bus=bus)
    await log.append("session.created", {"title": "", "model": "deepseek-chat"},
                     actor="system")
    return log, store


async def test_real_session_full_lifecycle_events_valid():
    """真实会话全链:propose→approve→执行→done 全部事件过校验链可落盘;
    proposed 载荷按 PlanStep 权威(仅 action/tool/expected/risk)+ expires_at;
    回放重建后内存态 ≡ 日志(completed)。"""
    log, _store = await _wired()
    mgr = PlanManager()
    llm = FakeLLM({"steps": [dict(s) for s in LLM_STEPS]})
    q = FakeQueue()
    ctx = _ctx(log, llm=llm, queue=q, approval=FakeApproval())
    p = await mgr.plan_propose("归档 D:\\work", ctx)

    assert p.id == 2                        # session.created(1) 后的首事件
    proposed = [e for e in log.events_after(0) if e.type == "plan.proposed"][0]
    payload = proposed.payload
    assert payload["goal"] == "归档 D:\\work"
    assert payload["selfcheck_ok"] is True
    assert payload["expires_at"].endswith("Z")
    # 事件载荷字段权威:PlanStep 五字段子集(无 intent 等内存字段)
    assert set(payload["steps"][0]) == {"action", "tool", "expected", "risk"}

    await mgr.plan_approve(p.id, ctx=ctx)
    await _drain(mgr)
    assert mgr.get(p.id).status == "completed"

    # 词表外扩展 plan.done 已登记可落盘(未登记会 EVT-102 拒写)
    types = [e.type for e in log.events_after(0)]
    assert "plan.approved" in types and "plan.done" in types
    assert types.count("plan.exec.step") == len(p.steps)
    approved = [e for e in log.events_after(0) if e.type == "plan.approved"][0]
    assert approved.actor == "user" and approved.payload["plan_id"] == p.id

    # 回放重建:内存态 ≡ 日志(INV-01)
    m2 = PlanManager.rebuild_from_events(list(log.events_after(0)))
    p2 = m2.get(p.id)
    assert p2 is not None
    assert p2.status == "completed"
    assert [s.tool for s in p2.steps] == [s.tool for s in p.steps]
    assert p2.expires_at is not None


async def test_real_session_aborted_event_valid():
    """真实会话中止路径:plan.aborted(step) 过校验链可落盘;actor=system。"""
    log, _store = await _wired()
    mgr = PlanManager()
    ctx = _ctx(log, llm=FakeLLM(None), queue=FakeQueue(outcomes=[_fail()]),
               approval=FakeApproval("中止"))
    p = await mgr.plan_propose("目标", ctx, steps=[dict(LLM_STEPS[0])])
    await mgr.plan_approve(p.id, ctx=ctx)
    await _drain(mgr)
    assert mgr.get(p.id).status == "aborted"
    aborted = [e for e in log.events_after(0) if e.type == "plan.aborted"][0]
    assert aborted.actor == "system"
    assert aborted.payload == {"plan_id": p.id, "step": 0}
    # 过期拒绝(reason 可选字段)同样可落盘
    p2 = await mgr.plan_propose("目标2", ctx, steps=[dict(LLM_STEPS[0])])
    p2.expires_at = p2.created_at - timedelta(seconds=1)
    with pytest.raises(PyHError) as ei:
        await mgr.plan_approve(p2.id, ctx=ctx)
    assert ei.value.code == "BUSY"
    rej = [e for e in log.events_after(0) if e.type == "plan.rejected"][0]
    assert rej.payload.get("reason") == "expired" and rej.payload["who"] == "system"
