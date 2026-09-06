"""pyharness/core/plan_mode.py — 先方案后执行编排 (specs/plan_mode.py.md 契约;阶段 4)

功能编号:F045(plan 方案生成)· F046(plan 审批与执行切换)· F023(危险分级联动)·
F043(执行=逐步入队)。一句话:`/plan <目标>` 只调一次 LLM 产出 ≤8 步方案(动作/
工具/预期/风险,危险步标"审批点"),展示待批;approve/reject/单步 edit 裁决后才执行;
执行=逐步入队(F043),单步失败用户三选一(跳过/重试/中止),重试 ≤2 次仍败默认中止;
plan 状态机:草稿→待批→批准→执行中→完成(终止态:拒绝/中止)。

软指导不硬限制:方案批准 ≠ 危险操作批准——批准只定方向,执行中每步仍是普通任务,
工具调用独立走 guard/审批链(F014/F015),plan 层不预授权、不改 scope、不解除 deny;
步骤失败不机械静默重试,一切经用户裁决;执行中用户可随时插话重定向。

本模块为纯编排内核:消费 ctx.session(事件 append)、ctx.llm.json_chat(唯一 LLM
出口,INV-02,注入式——本模块不 import 真实 llm)、ctx.task_queue(submit/wait_for,
F043)、ctx.approval.user_choice(审批/选择通道,headless → APR-501)、ctx.scope.
policy.danger_marks(危险分级表可选覆盖);不 import agent_loop/llm/scope 等外围
能力(INV-08)。plan 注册表由 plan.* 事件回放重建(INV-01):plan_id = plan.proposed
事件的 seq;approved/rejected/exec.step 引用不存在或已拒绝的 plan → EVT-101/BUSY。

偏离说明(契约=specs/plan_mode.py.md;与既有实现/规格冲突处的取舍,均列理由):
1. plan.done/plan.aborted 为词表外扩展(PRD 伪代码用词),须按 EVENT-SCHEMA §7 登记
   → 已在 pyharness/events/{payload,vocab}.py 登记新 payload 模型与事件类型
   (llm.retry 同款先例),并同步 tests/unit/test_events.py 词表清单 57→59。
2. plan.proposed 载荷 steps 按 EVENT-SCHEMA §3.5.2 + events.payload.PlanStep 权威
   只落 {action,tool,expected,risk}(字段级权威,extra=forbid):intent 是内存执行
   意图,事件载荷不带;事件回放重建时由 action/tool/expected 拼装兜底(Step 缺省)。
3. plan.proposed 载荷补可选 expires_at、plan.rejected 载荷补可选 reason(§7 只加
   可选):24h 过期判定(重建后仍需)与修订/过期原因审计(回放可审计)须随事件留痕,
   spec 伪码载荷即含两字段,events.payload 原模型缺口已按 goal.py 先例补齐。
4. 时钟统一 UTC aware(Envelope.ts 口径):伪码 datetime.now() naive 与会话 aware
   时间比较会 TypeError;plan_sweep_expired 的 now 缺省 = datetime.now(timezone.utc)。
5. rebuild 完成态映射为 "completed"(伪码 rebuild 段误写 "done";PlanStatus 词表
   与其余伪码均用 completed,完成事件仍是 plan.done)。
6. plan_propose 前置字段预检(每步 action/tool/expected 非空,缺 → EVT-100):
   伪码靠 selfcheck 软标、但 events.payload.PlanStep(min_length=1,必填)在 append
   时仍会拒写——前置给明确错误,零半成品不落盘(与异常表"精简重提;不落盘"一致)。
7. 执行中被 plan_reject(允许态:仅终态不可拒)→ 执行循环顶守卫停止后续 exec.step/
   入队("拒绝后不再推进"语义,EVENT-SCHEMA §3.5.2),终态已 rejected 不重复落
   plan.aborted;approve 后执行协程开跑前被拒的竞态 → plan_execute 入口对 rejected
   静默退场(不入队不落事件;仅 pending/其余态误调才 BUSY)。
8. 裁决通道 = ctx.approval.user_choice(options)(同步/异步兼容);缺通道/不可调用
   → APR-501(单步失败安全默认中止,已落 plan.aborted 后上抛);通道返回词表外值
   按"中止"安全默认处理(避免机械静默重试)。
9. plan_approve 以 asyncio.create_task 启动执行(伪码同款),句柄登记
   self._exec_tasks(编排/测试可 gather 等待),done 回调自动清理并记异常日志。
10. classify_danger 内置 DANGER_TABLE 与 scope.DEFAULT_DANGER_MARKS 同源
    (fs.delete_file=critical / exec.*,net.*=high,不 import scope,INV-08);
    plan_propose 时若 ctx.scope.policy.danger_marks 存在则优先取用(表变更只紧
    不松由 scope 装配保证),classify_danger 裸调用用内置表。
11. plan_revise 后旧 plan 按伪码弹出注册表(事件留痕 rejected);事件回放重建会
    保留 rejected 旧版——runtime 列表与回放列表在 active_only=False 时存在差异
    (伪码两处行为不一致,以各自伪码为准);active(未完成)集两者恒一致。
12. 执行步骤入队带 meta={"plan_id","step_idx"}(task.enqueued 载荷不支持 extra
    字段 → 事件不带 meta;仅作 Task.meta 内存溯源,供 F044/F058 关联判定;
    submit 的 task_id 由队列自增 t-N,不伪造溯源)。

数据纪律:Plan/Step 为内存编排态;一切状态迁移先 await append 对应 plan.* 事件
(落定成功)再对齐内存字段(INV-01,goal.py 同款纪律);executing 为事件派生中间态
(exec.step 任一步开跑即 executing,rebuild 同口径)。plan 终态只认事件(S-1)。
"""
from __future__ import annotations

import asyncio
import fnmatch
import inspect
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from pyharness.errors import PyHError, raise_code

log = logging.getLogger("pyharness.plan_mode")

# ------------------------------------------------------------------ 常量(F045 边界)
MAX_STEPS = 8                 # 方案步骤上限(超限拒写不落盘)
PLAN_TTL_HOURS = 24           # 方案 24h 未确认作废(过期自动 rejected)
MAX_RETRY = 2                 # 单步失败重试上限(仍败默认中止)

# LLM 提案提示词(json_chat 强制 JSON:返回 {"steps": [...]} 或裸数组;每步含
# action/tool/expected;risk/intent 由本模块统一打标,LLM 不背危险分级)
PLAN_PROMPT = (
    "你是任务拆解器。把用户目标拆成不超过 max_steps 步的可执行方案,每步必须包含:"
    "action(动作,动词短语)、tool(工具名,选可用工具)、expected(预期结果,可核对)。"
    "只输出 JSON,不要任何解释。步骤须按执行顺序排列,依赖前置。"
)

# 危险分级同源表(F023 同源,与 scope.DEFAULT_DANGER_MARKS 一致;INV-08 不 import
# scope——内容复制保同源;critical 本层不可用,high 执行时须独立审批(审批点))
DANGER_TABLE: tuple[dict[str, str], ...] = (
    {"pattern": "fs.delete_file", "level": "critical"},
    {"pattern": "exec.*", "level": "high"},
    {"pattern": "net.*", "level": "high"},
)

# 状态五主态 + 两终止(EVENT-SCHEMA/PRD 词表;draft 为 proposed 落盘前内存态)
PlanStatus = Literal[
    "draft", "pending", "approved", "executing",
    "completed", "rejected", "aborted",
]
# 未完成集(供 UI/compaction F058 判定不可折叠)
_ACTIVE_STATUSES = ("pending", "approved", "executing")

RiskLevel = Literal["none", "low", "high", "critical"]


def _utcnow() -> datetime:
    """当前 UTC aware 时间(会话事件 ts 同口径;避免 naive/aware 比较 TypeError)。"""
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    """datetime → ISO8601 UTC 串(Z 结尾,与 Envelope.ts 同形)。"""
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z")


def _parse_dt(s: Any) -> Optional[datetime]:
    """ISO8601 串(含 Z 结尾)→ aware datetime;空/非法返回 None。"""
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


# ------------------------------------------------------------------ 数据结构
@dataclass
class Step:
    """方案单步:action/tool/expected/risk(intent = 该步执行意图)。

    risk 由 F023 同源表打标:high → 执行时独立审批("审批点"),critical → 本层不可
    执行(selfcheck 软标,展示用);intent 缺省由 action/tool/expected 拼装(事件
    载荷不带 intent,回放重建时兜底走此缺省,Step(**s) 兼容事件派生步骤)。
    """

    action: str
    tool: str
    expected: str
    risk: RiskLevel = "low"
    intent: str = ""

    def __post_init__(self) -> None:
        """intent 缺省拼装:用 <tool> <action>: <expected>(F045 执行意图)。"""
        if not self.intent:
            self.intent = f"用 {self.tool} {self.action}: {self.expected}"

    def to_dict(self) -> dict:
        """值拷贝(plan_revise 单步编辑用;模型转 dict 零共享)。"""
        return asdict(self)


@dataclass
class Plan:
    """会话级方案(id = plan.proposed 事件 seq;状态只由事件回放重建,INV-01)。

    who = 裁决人(approve/reject 后置位;24h 过期自动 rejected 时 who=system)。
    """

    id: int
    goal: str
    steps: list[Step]                    # ≤8 步(propose 强校验)
    selfcheck_ok: bool = True            # 可执行性/依赖自检结论(展示用不阻断)
    why: str = ""                        # selfcheck 未过原因(空 = 通过)
    status: PlanStatus = "pending"       # draft 仅在 proposed 落盘前在途
    created_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None  # created + 24h;过期自动作废
    who: Optional[str] = None

    def summary(self) -> str:
        """用户可读一行摘要(UI/命令层展示待批方案用)。"""
        steps_txt = " → ".join(f"{i}.{s.action}({s.tool})" for i, s in
                               enumerate(self.steps))
        check = "自检通过" if self.selfcheck_ok else f"自检未过:{self.why}"
        return (f"[plan {self.id}] {self.goal} | {self.status} | {check}\n"
                f"  步骤({len(self.steps)}):{steps_txt}")


# ------------------------------------------------------------------ PlanManager
class PlanManager:
    """会话级 plan 注册表与状态机(F045/F046 编排内核)。

    消费 ctx.session(append 唯一写口)/ctx.llm.json_chat(F045 唯一 LLM 出口)/
    ctx.task_queue(F043 submit/wait_for)/ctx.approval.user_choice(F046 三选一)/
    ctx.scope.policy.danger_marks(F023 危险分级可选覆盖);被命令层(/plan、approve/
    reject/edit)、UI 方案弹窗、compaction(F058 不可折叠集)、agent-loop(expiry
    sweep 周期驱动)消费。内存态只由事件派生:直接构造(空表)或 rebuild_from_events。

    私有字段:_plans(注册表,plan_id=proposed seq)、_danger_marks(内置 F023 表,
    可被 ctx.scope 运行时覆盖)、_exec_tasks(approve 自动建的执行协程句柄集)。
    """

    def __init__(self, *, danger_marks: Optional[list[dict]] = None) -> None:
        self._plans: dict[int, Plan] = {}
        self._danger_marks: list[dict] = list(
            danger_marks if danger_marks is not None else DANGER_TABLE)
        self._exec_tasks: set[asyncio.Task] = set()

    # ============================================================ 内部辅助
    def _require_session(self, ctx: Any) -> Any:
        """写事件前置:ctx.session 未接线 → EVT-100(编排内核不背第二落点)。"""
        sess = getattr(ctx, "session", None)
        if sess is None or not callable(getattr(sess, "append", None)):
            raise_code("EVT-100", hint="ctx.session 未接线:plan.* 事件无落点",
                       advice="装配层注入 SessionLog 或同型 append 协议对象")
        return sess

    def _get(self, plan_id: Optional[int]) -> Plan:
        """定位方案;不存在 → EVT-101(必先 plan.proposed,事件引用校验)。"""
        p = self._plans.get(plan_id) if plan_id is not None else None
        if p is None:
            raise_code("EVT-101", plan_id=plan_id,
                       hint=f"方案 {plan_id} 不存在:先 plan_propose 再裁决",
                       advice="先 /plan 提案;核对 plan_id")
        return p

    def _scope_marks(self, ctx: Any) -> list[dict]:
        """危险分级表:ctx.scope.policy.danger_marks(装配期已并入 extra 上调,
        只紧不松)优先;无 scope/空表回落内置同源表。"""
        policy = getattr(getattr(ctx, "scope", None), "policy", None)
        marks = getattr(policy, "danger_marks", None)
        if isinstance(marks, list) and marks:
            return list(marks)
        return list(self._danger_marks)

    def _spawn_exec(self, plan_id: int, ctx: Any) -> None:
        """approve 自动建任务(F046 I/O):登记句柄 + done 回调清理/记异常。"""
        task = asyncio.create_task(self.plan_execute(plan_id, ctx))
        self._exec_tasks.add(task)

        def _done(t: asyncio.Task) -> None:
            self._exec_tasks.discard(t)
            if not t.cancelled() and t.exception() is not None:
                # 后台执行协程异常不静默:堆栈仅本地(事件已由 _abort 落 plan.aborted)
                log.error("plan %s 执行协程异常: %r", plan_id, t.exception())

        task.add_done_callback(_done)

    async def _abort(self, p: Plan, ctx: Any, step_idx: int) -> None:
        """中止落账:写 plan.aborted(actor=system,step=中止步)后对齐内存。"""
        sess = self._require_session(ctx)
        await sess.append("plan.aborted",
                          {"plan_id": p.id, "step": step_idx}, actor="system")
        p.status = "aborted"               # 事件落定 → 内存对齐(INV-01)
        p.who = "system"

    async def _user_choice(self, options: list[str], ctx: Any) -> str:
        """用户三选一裁决通道(ctx.approval.user_choice;同步/异步兼容)。

        无通道/不可调用 → APR-501(安全默认:由调用方直接中止);通道返回词表外
        值按"中止"安全默认处理——失败不机械静默重试,一切经用户裁决。
        """
        approval = getattr(ctx, "approval", None)
        fn = getattr(approval, "user_choice", None) if approval is not None else None
        if not callable(fn):
            raise_code("APR-501",
                       hint="无用户裁决通道(ctx.approval.user_choice 未接线)",
                       advice="headless 无审批通道:单步失败直接中止(安全默认)")
        res = fn(options)
        if inspect.isawaitable(res):
            res = await res
        if res not in options:
            log.warning("plan 裁决通道返回词表外值 %r,按中止安全默认处理", res)
            return "中止"
        return res

    # ============================================================ 方案生成(F045)
    async def plan_propose(self, goal: str, ctx: Any, *, steps: Optional[list] = None,
                           max_steps: int = MAX_STEPS) -> Plan:
        """方案提案主函数:默认只调一次 LLM 产出 ≤max_steps 步 JSON 方案。

        steps 显式传入时跳过 LLM(plan_revise 单步编辑复用,零 LLM 重调);逐步
        危险打标(F023 同源表)+ 字段预检 + 自检;写 plan.proposed(先落后展示,
        EVENT-SCHEMA §3.5.2)并登记 Plan(id=事件 seq,status=pending,24h TTL)。
        """
        sess = self._require_session(ctx)
        if not goal or not str(goal).strip():
            raise_code("EVT-100", hint="plan 目标为空",
                       advice="提供 <目标> 后重新提案")
        if steps is None:                    # 常规路径:只调一次 LLM(F045 边界)
            raw = await self._json_chat(ctx, goal, max_steps)
            if isinstance(raw, dict):
                steps = raw.get("steps")     # 兼容 {"steps": [...]} 包装
            else:
                steps = raw
            if not isinstance(steps, list):  # LLM 结构不达标(非步骤数组)
                raise_code("LLM-304",
                           hint="json_chat 未返回 steps 数组,方案不可执行",
                           advice="让模型重出(提示词强制 JSON),或人工给出步骤")
        if not steps:                        # 空方案(显式 steps 空同样拒)
            raise PyHError("EVT-100", ctx={
                "hint": f"方案步骤须 1..{max_steps} 步",
                "advice": "精简方案后重新提案"})
        if len(steps) > max_steps:           # ≤8 步强校验(超限拒写不落盘)
            raise PyHError("EVT-100", ctx={
                "hint": f"方案步骤须 1..{max_steps} 步(实际 {len(steps)})",
                "advice": "精简方案后重新提案"})
        marks = self._scope_marks(ctx)       # F023 表(F045 提案时刻定格)
        # 规范化 + 危险打标 + 执行意图拼装(每步只认五键,杜绝脏键入事件)
        ann: list[dict[str, str]] = []
        for i, s in enumerate(steps):
            if not isinstance(s, dict):
                raise PyHError("EVT-100", ctx={
                    "hint": f"步骤 {i + 1} 不是对象", "advice": "检查方案结构"})
            action = str(s.get("action") or "").strip()
            tool = str(s.get("tool") or "").strip()
            expected = str(s.get("expected") or "").strip()
            # 字段预检:缺 action/tool/expected 的步骤 events.payload.PlanStep 必拒
            # (min_length=1),此处前置给明确错误——零半成品不落盘(见偏离 6)
            if not action or not tool or not expected:
                miss = [k for k, v in (("action", action), ("tool", tool),
                                       ("expected", expected)) if not v]
                raise PyHError("EVT-100", ctx={
                    "hint": f"步骤 {i + 1} 缺字段: {','.join(miss)}(不可执行)",
                    "advice": "补齐 action/tool/expected 后重新提案"})
            risk = self._classify(tool, marks)      # F023 同源表打标
            intent = str(s.get("intent") or "").strip() or \
                f"用 {tool} {action}: {expected}"   # 缺省由 action/tool 拼装
            ann.append({"action": action, "tool": tool, "expected": expected,
                        "risk": risk, "intent": intent})
        ok, why = selfcheck_plan(ann)        # 可执行性/依赖自检(纯函数,展示用)
        created_at = _utcnow()
        expires_at = created_at + timedelta(hours=PLAN_TTL_HOURS)  # 24h 未确认作废
        # 事件载荷:steps 只落 events.payload.PlanStep 五字段子集(action/tool/
        # expected/risk),intent 不随事件(见偏离 2);先落盘后展示(§3.5.2)
        step_payloads = [{k: d[k] for k in ("action", "tool", "expected", "risk")}
                         for d in ann]
        ev = await sess.append(
            "plan.proposed",
            {"goal": str(goal).strip(), "steps": step_payloads,
             "selfcheck_ok": ok, "expires_at": _iso(expires_at)},
            actor="agent")
        p = Plan(id=ev.seq, goal=str(goal).strip(),
                 steps=[Step(**d) for d in ann],
                 selfcheck_ok=ok, why=why, status="pending",   # 待批:等用户裁决
                 created_at=created_at, expires_at=expires_at)
        self._plans[p.id] = p                # 注册(plan_id = proposed 事件 seq)
        return p

    async def _json_chat(self, ctx: Any, goal: str, max_steps: int) -> Any:
        """LLM 单次调用(注入式):ctx.llm.json_chat 未接线 → CYC-999 装配错误。

        只调一次(F045 边界);LLM-3xx 失败由 llm 层上抛,本层零捕获零重试。
        """
        llm = getattr(ctx, "llm", None)
        fn = getattr(llm, "json_chat", None) if llm is not None else None
        if not callable(fn):
            raise_code("CYC-999",
                       hint="ctx.llm.json_chat 未接线(plan 提案需要 LLM)",
                       advice="装配层注入带 json_chat 的 LLM 门面后再提案")
        res = fn(prompt=PLAN_PROMPT, goal=goal, max_steps=max_steps)
        if inspect.isawaitable(res):
            res = await res
        return res

    def _classify(self, tool_name: str, marks: list[dict]) -> str:
        """按给定分级表前缀匹配工具名(打标核心,分类结果由调用方落内存)。"""
        for rule in marks:
            if fnmatch.fnmatch(str(tool_name or ""), str(rule.get("pattern") or "")):
                level = str(rule.get("level") or "none")
                return level if level in ("none", "low", "high", "critical") else "none"
        return "none"

    def classify_danger(self, tool_name: str) -> str:
        """危险分级查表(F023 同源):内置表前缀匹配,返回 none/low/high/critical。

        high → 执行时独立审批("审批点");critical → guard 本层不可执行(selfcheck
        软标,展示不阻断);表外默认 none。表变更只紧不松(scope 装配纪律)。
        """
        return self._classify(tool_name, self._danger_marks)

    # ============================================================ 审批裁决(F046)
    async def plan_approve(self, plan_id: int, *, who: str = "user",
                           ctx: Any) -> None:
        """审批:批准(F046)。仅 pending 可批——写 plan.approved(who) 并置 approved
        (方案冻结,steps 不可变),随即启动执行协程(approved 自动建任务,逐步入队);
        已拒绝/已终态/不存在显式拒;批准前先查 24h 过期(过期自动转 rejected)。
        """
        p = self._get(plan_id)               # 不存在 → EVT-101(必先 proposed)
        if p.status == "rejected":           # 拒绝后不再推进(F046 边界)
            raise PyHError("BUSY", ctx={
                "hint": f"方案 {plan_id} 已拒绝,不可批准",
                "advice": "修订后重新提案(reject 重提)"})
        if p.status in ("completed", "aborted"):   # 终态不可再裁决
            raise PyHError("BUSY", ctx={
                "hint": f"方案 {plan_id} 已{p.status}",
                "advice": "终态不可逆;新需求请重新提案"})
        if p.status != "pending":            # 只待批可批(防重复 approve)
            raise PyHError("BUSY", ctx={
                "hint": f"方案 {plan_id} 非待批态({p.status})",
                "advice": "已裁决方案不可重复批准"})
        if await self._expire_if_stale(p, ctx):    # 24h 未确认 → 自动 rejected
            raise PyHError("BUSY", ctx={
                "hint": "方案已过期(24h 未确认),请重新提案",
                "advice": "重新 /plan 提案后再批准"})
        sess = self._require_session(ctx)
        await sess.append("plan.approved",
                          {"plan_id": plan_id, "who": who}, actor="user")
        p.status = "approved"                # 事件落定 → 内存对齐(INV-01)
        p.who = who                          # 批准后 steps 冻结不可变(改=reject 重提)
        self._spawn_exec(plan_id, ctx)       # approved 自动建任务(F046 I/O)

    async def _expire_if_stale(self, p: Plan, ctx: Any) -> bool:
        """24h 过期即时检查(approve 前置 + sweep 双路复用):过期 → 自动 rejected。

        写 plan.rejected(who=system,reason=expired) 后置状态;返回是否已过期。
        """
        if p.status != "pending" or p.expires_at is None:
            return False
        if p.expires_at > _utcnow():
            return False
        sess = self._require_session(ctx)
        await sess.append("plan.rejected",
                          {"plan_id": p.id, "who": "system", "reason": "expired"},
                          actor="system")    # who=system 自动态(24h 未确认)
        p.status = "rejected"                # 自动作废,不执行
        p.who = "system"
        return True

    async def plan_reject(self, plan_id: int, *, who: str = "user",
                          reason: str = "user-rejected", ctx: Any) -> None:
        """审批:拒绝(F046)。拒绝方案回修订对话——写 plan.rejected(who,reason);
        拒绝后不可再 approve/revise/execute;终态(completed/aborted/rejected)
        不可再拒绝。执行中被拒 → 执行循环顶守卫停止后续步骤(见偏离 7)。
        """
        p = self._get(plan_id)               # 不存在 → EVT-101
        if p.status in ("completed", "aborted", "rejected"):
            raise PyHError("BUSY", ctx={
                "hint": f"方案 {plan_id} 已终态({p.status})",
                "advice": "终态不可逆;新需求请重新提案"})
        sess = self._require_session(ctx)
        await sess.append("plan.rejected",
                          {"plan_id": plan_id, "who": who, "reason": reason},
                          actor="user")
        p.status = "rejected"                # 事件落定 → 内存对齐(INV-01)
        p.who = who
        # I/O:rejected 回修订对话(F046)——用户可带反馈重新 plan_propose

    async def plan_revise(self, plan_id: int, step_idx: int, patch: dict,
                          ctx: Any) -> Plan:
        """单步编辑(F046):仅 pending 可改——旧方案 rejected(reason=revised)+
        同目标重提新方案,零 LLM 重调(编辑是本地操作);批准后不可改(改=reject
        重提,F046 边界);改动工具时按新工具重打危险标(F023 联动)。
        """
        p = self._get(plan_id)
        if p.status != "pending":            # 只待批可改(批准后冻结)
            raise PyHError("BUSY", ctx={
                "hint": "仅待批方案可单步修改",
                "advice": "已批准方案不可变;请 reject 后重新提案"})
        if not (0 <= step_idx < len(p.steps)):   # 步下标越界
            raise_code("EVT-100", plan_id=plan_id, step_idx=step_idx,
                       hint=f"step_idx {step_idx} 越界(共 {len(p.steps)} 步)",
                       advice="核对方案步数(0 起)")
        unknown = set(patch or {}) - {"action", "tool", "expected",
                                      "risk", "intent"}
        if unknown:                          # 脏键拒改(防打字错误静默丢字段)
            raise_code("EVT-100", plan_id=plan_id, step_idx=step_idx,
                       hint=f"patch 含未知字段: {','.join(sorted(unknown))}",
                       advice="patch 字段 ∈ action/tool/expected/intent")
        new_steps = [s.to_dict() for s in p.steps]  # 值拷贝后单步字段替换
        merged = {**new_steps[step_idx], **(patch or {})}
        # intent 是 action/tool/expected 的派生态:改了源字段而未显式给 intent →
        # 丢弃旧 intent 让重提时按新字段拼装(防修订后执行意图残留旧文案)
        if "intent" not in (patch or {}) and any(k in (patch or {})
                                                  for k in ("action", "tool", "expected")):
            merged.pop("intent", None)
        new_steps[step_idx] = merged
        sess = self._require_session(ctx)
        await sess.append("plan.rejected", {
            "plan_id": plan_id, "who": "user",
            "reason": f"revised:step:{step_idx}"}, actor="user")  # 旧版作废
        self._plans.pop(plan_id, None)       # 旧 plan 出注册表(事件留痕,偏离 11)
        return await self.plan_propose(p.goal, ctx,   # 同目标重提新版,显式 steps
                                       steps=new_steps)     # 零 LLM 调用

    # ============================================================ 执行(F043/F046)
    async def plan_execute(self, plan_id: int, ctx: Any) -> None:
        """执行切换:逐步入队。逐 step:写 plan.exec.step → 步骤意图 submit 入队
        (F043)并 wait_for;失败用户三选一(跳过/重试/中止),重试 ≤2 次仍败默认
        中止;全部成功写 plan.done;中止写 plan.aborted(step=i);headless 无通道
        自动中止(APR-501 安全默认)。批准只定方向,每步仍走正常 guard/审批链。
        """
        p = self._get(plan_id)
        if p.status not in ("approved", "executing"):  # 硬条件:批准后才执行
            if p.status == "rejected":
                # 拒绝后不再推进(EVENT-SCHEMA §3.5.2):已裁决方案不可执行——
                # approve 后执行协程开跑前被 plan_reject 的竞态同样落此静默退场
                return
            raise PyHError("BUSY", ctx={
                "hint": f"方案 {plan_id} 未批准,禁止执行",
                "advice": "先 plan_approve 批准方案后再执行"})
        sess = self._require_session(ctx)
        tq = getattr(ctx, "task_queue", None)
        if (tq is None or not callable(getattr(tq, "submit", None))
                or not callable(getattr(tq, "wait_for", None))):
            raise_code("CYC-999",
                       hint="ctx.task_queue 未接线:执行=逐步入队(F043)需要队列",
                       advice="装配层注入 TaskQueue(runner=agent_loop)后再批准")
        p.status = "executing"               # 批准 → 执行中(事件态由 exec.step 派生)
        for i, step in enumerate(p.steps):
            if p.status == "rejected":       # 执行中被拒:停止后续步(偏离 7)
                return
            await sess.append("plan.exec.step", {
                "plan_id": plan_id, "step_idx": i, "action": step.action},
                actor="agent")
            tries = 0
            while True:                      # 逐步入队:队列单飞,天然串行(F043)
                meta = {"plan_id": plan_id, "step_idx": i}  # Task.meta 溯源(偏离 12)
                tid = await tq.submit(step.intent, meta=meta)
                r = await tq.wait_for(tid)
                if getattr(r, "ok", False):
                    break                    # 成功 → 下一步
                if tries >= MAX_RETRY:       # 重试耗尽 → 默认中止(汇报,F046 边界)
                    await self._abort(p, ctx, i)
                    return
                try:
                    choice = await self._user_choice(["跳过", "重试", "中止"], ctx)
                except PyHError as e:
                    if e.code == "APR-501":  # headless 无通道:直接中止(安全默认)
                        await self._abort(p, ctx, i)
                        raise
                    raise
                if choice == "跳过":
                    break                    # 跳过:继续下一步
                if choice == "中止":
                    await self._abort(p, ctx, i)
                    return
                tries += 1                   # 重试:同一步重新入队(重走 guard/审批)
        if p.status == "executing":          # 全部成功(未被中止/拒绝打断)
            await sess.append("plan.done", {"plan_id": plan_id}, actor="system")
            p.status = "completed"           # 事件落定 → 内存对齐(INV-01)

    # ============================================================ 过期清理(F045)
    async def plan_sweep_expired(self, ctx: Any, *,
                                 now: Optional[datetime] = None) -> int:
        """过期清理:扫全部 pending 方案,超 24h 未确认自动 rejected(who=system,
        reason=expired)并返回清理数;由 agent-loop 周期驱动与 approve 前即时检查
        双路调用。now = 测试注入时钟(aware UTC,偏离 4)。
        """
        sess = self._require_session(ctx)
        now = now or _utcnow()
        n = 0
        for p in list(self._plans.values()):     # 快照遍历(清理中可安全变更)
            if (p.status == "pending" and p.expires_at is not None
                    and p.expires_at <= now):    # 24h 未确认(F045 边界)
                await sess.append("plan.rejected", {
                    "plan_id": p.id, "who": "system", "reason": "expired"},
                    actor="system")              # who=system 自动态
                p.status = "rejected"            # 自动作废,不执行
                p.who = "system"
                n += 1
        return n

    # ============================================================ 查询/重建
    def list_plans(self, *, active_only: bool = True) -> list[Plan]:
        """按状态过滤列出方案(默认非终态),供 UI/命令层/compaction 判定未完成
        plan 不可折叠(F058)。返回按 plan_id(proposed seq)升序。"""
        plans = sorted(self._plans.values(), key=lambda p: p.id)
        if not active_only:
            return plans                      # 全量(含 rejected/aborted/completed)
        return [p for p in plans
                if p.status in _ACTIVE_STATUSES]   # 未完成集 → F058 不可折叠

    def get(self, plan_id: int) -> Optional[Plan]:
        """按 id 取方案(查询口;不存在返回 None,供 UI/命令层读,不抛)。"""
        return self._plans.get(plan_id)

    @classmethod
    def rebuild_from_events(cls, events: list[Any]) -> "PlanManager":
        """事件溯源重建(INV-01):顺序重放 plan.proposed/approved/rejected/
        exec.step/done/aborted 重建注册表与状态——崩溃恢复/会话恢复后内存态 ≡
        日志;孤儿引用(approved 前无 proposed)由写入层 EVT-101 拦截,此处防御
        跳过。完成态映射为 completed(偏离 5);steps 从事件载荷重建,缺省 intent
        由 Step.__post_init__ 拼装(偏离 2)。
        """
        m = cls()
        for e in events:                     # seq 升序重放(只读不写回)
            t = getattr(e, "type", "")
            p = getattr(e, "payload", {}) or {}
            if t == "plan.proposed":
                pid = getattr(e, "seq", 0)   # plan_id = proposed 事件 seq(§3.5.2)
                created_at = _parse_dt(getattr(e, "ts", None)) or _utcnow()
                expires = _parse_dt(p.get("expires_at")) or \
                    (created_at + timedelta(hours=PLAN_TTL_HOURS)
                     if p.get("expires_at") is None else None)
                if expires is None:          # 旧日志无 ts/expires:按创建时刻兜底
                    expires = created_at + timedelta(hours=PLAN_TTL_HOURS)
                steps = [Step(**dict(s)) for s in p.get("steps", []) or []]
                m._plans[pid] = Plan(
                    id=pid, goal=p.get("goal", ""), steps=steps,
                    selfcheck_ok=bool(p.get("selfcheck_ok", True)), why="",
                    status="pending", created_at=created_at, expires_at=expires)
            else:
                g = m._plans.get(p.get("plan_id"))
                if g is None:
                    log.warning("orphan plan event seq=%s type=%s(防御跳过)",
                                getattr(e, "seq", "?"), t)
                    continue                 # 孤儿引用:重放不中断
                if t == "plan.approved":
                    g.status = "approved"
                    g.who = p.get("who")
                elif t == "plan.rejected":
                    g.status = "rejected"    # reason=expired/revised 等不细分
                    g.who = p.get("who")
                elif t == "plan.exec.step":
                    g.status = "executing"   # 任一步开跑即执行中
                elif t == "plan.done":
                    g.status = "completed"
                    g.who = "system"
                elif t == "plan.aborted":
                    g.status = "aborted"
                    g.who = "system"
        return m                              # 内存态 ≡ 日志派生态(INV-01)


# ------------------------------------------------------------------ 纯函数
def selfcheck_plan(steps: list[dict]) -> tuple[bool, str]:
    """方案自检(纯函数,F045):步骤 1..8、每步 action/tool/expected 齐全、
    risk=critical 的工具本层不可用(F014)则标 why;结果只展示不阻断(软指导,
    批准权在用户)。零写副作用,propose 与测试直接调用。"""
    if not steps or len(steps) > MAX_STEPS:  # 空方案/超上限
        return False, "步骤数须为 1..8"
    for i, s in enumerate(steps):            # 逐步字段完整性
        if not s.get("action") or not s.get("tool"):
            return False, f"步骤 {i + 1} 缺 action 或 tool(不可执行)"
        if not s.get("expected"):
            return False, f"步骤 {i + 1} 缺预期结果 expected(不可核对)"
        if s.get("risk") == "critical":      # critical 工具 guard 层直接不可用
            return False, f"步骤 {i + 1} 工具 {s['tool']} 为 critical 不可执行,需换招"
    return True, ""                          # 通过(展示 selfcheck_ok=True)


__all__ = [
    "PlanManager", "Plan", "Step", "PlanStatus", "RiskLevel",
    "selfcheck_plan", "MAX_STEPS", "PLAN_TTL_HOURS", "MAX_RETRY",
    "PLAN_PROMPT", "DANGER_TABLE",
]
