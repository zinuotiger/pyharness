# specs/plan_mode.py.md — 编码规格

> **模块文件**:`pyharness/core/plan_mode.py` | **功能编号**:F045(plan 方案生成)· F046(plan 审批与执行切换)· F023(危险分级联动)· F043(执行=逐步入队) | **权威口径**:PRD-Core §5.5 F045/F046(功能与验收伪代码权威)、EVENT-SCHEMA §3.5.2(plan.proposed/approved/rejected/exec.step 字段级权威)、ERR.md §2.2(EVT-1xx)/§2.4(BUSY)/§2.10(CYC-999)、DIS-CORE §0.2(INV-01)
> **一句话**:先方案后执行(F045/F046)——`/plan <目标>` 只调一次 LLM 产出 ≤8 步方案(动作/工具/预期/风险,危险步标审批点),展示待批;approve/reject/单步 edit 裁决后才执行,执行=逐步入队(F043)失败三选一(跳过/重试/中止);plan 状态机:草稿→待批→批准→执行中→完成(终止态:拒绝/中止);**软指导不硬限制**——批准只定方向,每步仍走正常 guard/工具审批链(F014/F015,方案批准≠危险操作批准),plan 不改 scope/deny。

## 模块职责

1. **方案生成(F045)**:`plan_propose(goal)` 只调一次 LLM(json_chat 强制 JSON,≤8 步),每步按 F023 危险分级表打标 risk(none/low/high/critical,high 显式标"审批点"),`selfcheck_plan` 做可执行性/依赖自检(纯函数);写 plan.proposed(先落盘后展示,EVENT-SCHEMA §3.5.2)并登记 Plan。
2. **plan 状态机(五态+两终止)**:`draft`(草稿:proposed 落盘前的内存在途态,LLM 失败即失,无审计价值不入事件)→ `pending`(待批:已 proposed 展示,等用户裁决)→ `approved`(批准:方案冻结,steps 不可变)→ `executing`(执行中:逐步入队)→ `completed`(完成:plan.done);终止态 `rejected`(用户拒/24h 过期自动拒 who=system)、`aborted`(执行中中止)。任何已批准方案的"修改"=reject 重提(F046 边界:approve 后方案不可变)。
3. **审批与执行切换(F046)**:`plan_approve`(仅 pending 可批,写 plan.approved 后自动启动执行协程——plan.approved 自动建任务);`plan_reject`(回修订对话);`plan_revise`(单步 edit,仅 pending,内部=旧方案 rejected(reason=revised)+同目标重提新方案,**零 LLM 重调**——编辑是本地操作);方案 24h 未确认 → `plan_sweep_expired` 自动 rejected(who=system)。
4. **执行=逐步入队(F043/F046)**:`plan_execute` 逐 step:写 plan.exec.step → 步骤意图入队 task_queue → `wait_for` 等结果;失败 → 用户三选一(跳过/重试/中止),重试 ≤2 次仍败默认中止汇报;headless 无审批通道 → 直接中止(APR-501 安全默认)。
5. **软指导不硬限制**:方案批准 ≠ 危险操作批准——执行中每步是普通任务,工具调用仍独立过 guard 链/审批(F014/F015),plan 层不预授权、不改 scope、不解除 deny;步骤失败不机械静默重试,一切经用户裁决;执行中用户可随时插话重定向——plan 是方向软约束。
6. **事件溯源(INV-01)**:Plan 注册表由 plan.* 事件回放重建;plan_id = plan.proposed 事件的 seq(EVENT-SCHEMA:plan_id 指向存在的 proposed,否则 EVT-101);approved/rejected/exec.step 引用不存在或已拒绝的 plan → EVT-101/BUSY 显式拒。plan.done/plan.aborted 为 PRD 伪代码使用的词表外扩展,须按 EVENT-SCHEMA §7 登记。

## 依赖

- **依赖方向**(§0.3 拓扑):plan_mode 位于编排层;消费 `ctx.llm.json_chat`(唯一 LLM 出口,INV-02)、`session.append`(plan.* 事件)、`task_queue.submit/wait_for`(F043)、危险分级表(F023 同源,经 ctx.scope/registry 读)。
- **消费方**:命令层(/plan、approve/reject/edit 命令)、UI 方案弹窗/审批(F045)、compaction(F058:未完成 plan 属不可折叠集)、agent-loop(expiry sweep 定时驱动)。
- **外部依赖**:errors(PyHError)、审批/选择通道(ctx.approval:user_choice;headless → APR-501 拒)、事件词表 plan.proposed/approved/rejected/exec.step(EVENT-SCHEMA §3.5.2)。

## 数据结构表

| 字段 | 类型 | 规则 |
|---|---|---|
| `PlanStatus` | Literal | `draft/pending/approved/executing/completed/rejected/aborted`(五主态+两终止) |
| `Step` | dataclass | `action: str` / `tool: str` / `expected: str` / `risk: Literal[none,low,high,critical]` / `intent: str`(该步执行意图,缺省由 action/tool 拼装) |
| `Plan` | dataclass | `id: int`(=plan.proposed 的 seq)/ `goal: str` / `steps: list[Step]`(**≤8**) / `selfcheck_ok: bool` / `why: str` / `status: PlanStatus` / `created_at/expires_at`(created+24h) / `who: str\|None`(裁决人) |
| `_plans` | dict[int, Plan] | 注册表;由事件重建(INV-01);plan_id=proposed seq |
| 事件 | — | plan.proposed(actor=agent)/ approved(who)/ rejected(who;过期 who=system)/ exec.step(step_idx,action)/ done/aborted(词表外,§7 登记) |
| 常量 | — | `MAX_STEPS=8`、`PLAN_TTL=24h`(F045 边界)、`MAX_RETRY=2`(失败重试上限) |

## 类与函数清单

### `async def plan_propose(self, goal: str, ctx, *, steps: list | None = None, max_steps: int = 8) -> Plan` — 方案生成(F045)

**功能一句话**:方案提案主函数——默认只调一次 LLM 产出 ≤8 步 JSON 方案;steps 显式传入时跳过 LLM(plan_revise 本地编辑复用,零 LLM 重调);逐步危险打标+自检;写 plan.proposed(先落后展示)返回 Plan(id=事件 seq,status=pending,24h TTL)。

```python
async def plan_propose(self, goal, ctx, *, steps=None, max_steps=8):
    if steps is None:                                # 常规路径:只调一次 LLM(F045 边界)
        raw = await ctx.llm.json_chat(prompt=PLAN_PROMPT, goal=goal,
                                      max_steps=max_steps)     # 强制 JSON;失败→LLM-3xx 上抛
        steps = raw["steps"] if isinstance(raw, dict) else raw
    if not steps or len(steps) > max_steps:          # ≤8 步强校验(超限拒写不落盘)
        raise PyHError("EVT-100", ctx={"hint": f"方案步骤须 1..{max_steps} 步",
                                       "advice": "精简方案后重新提案"})
    for s in steps:                                  # 危险打标(F023 同源表)
        s["risk"] = self.classify_danger(s.get("tool", ""))   # high→执行标"审批点"
        s["intent"] = s.get("intent") or f"用 {s.get('tool')} {s.get('action')}: {s.get('expected')}"
    ok, why = selfcheck_plan(steps)                  # 可执行性/依赖自检(纯函数,展示用)
    ev = await session.append("plan.proposed",      # 先落盘后展示(§3.5.2:proposed 展示前先落)
        {"goal": goal, "steps": steps, "selfcheck_ok": ok,
         "expires_at": iso(now() + timedelta(hours=24))}, actor="agent")   # 可选字段(§7 只加可选)
    p = Plan(id=ev.seq, goal=goal, steps=[Step(**s) for s in steps],
             selfcheck_ok=ok, why=why, status="pending",   # 待批:等用户裁决(F046)
             created_at=now(), expires_at=now() + timedelta(hours=24))   # 24h 未确认作废
    self._plans[p.id] = p                            # 注册(plan_id=proposed seq)
    return p
```

**参数表**:`goal`=用户目标;`ctx`=会话实体;`steps`=显式步骤(编辑复用,None 走 LLM);`max_steps`=8 上限。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 步骤 0 或 >8 | EVT-100 | 精简重提;不落盘零半成品 |
| LLM 异常 | json_chat 失败(唯一一次调用) | LLM-301~310 | 无 plan.proposed 事件;重试提案 |

**关联测试**:test_f045_plan_propose(≤8 步/自检/危险打标/只调一次 LLM)、GWT-F045-01(proposed 落盘先于展示)。

### `def selfcheck_plan(steps: list[dict]) -> tuple[bool, str]` — 方案自检(纯函数,F045)

**功能一句话**:可执行性/依赖自检——步骤 1..8、每步 action/tool 齐全、risk=critical 的工具本层不可用(F014)则标 why;结果只展示不阻断(软指导,批准权在用户)。

```python
def selfcheck_plan(steps):
    if not steps or len(steps) > 8:                  # 空方案/超上限
        return False, "步骤数须为 1..8"
    for i, s in enumerate(steps):                    # 逐步字段完整性
        if not s.get("action") or not s.get("tool"):
            return False, f"步骤 {i+1} 缺 action 或 tool(不可执行)"
        if not s.get("expected"):
            return False, f"步骤 {i+1} 缺预期结果 expected(不可核对)"
        if s.get("risk") == "critical":              # critical 工具 guard 层直接不可用
            return False, f"步骤 {i+1} 工具 {s['tool']} 为 critical 不可执行,需换招"
    return True, ""                                  # 通过(展示 selfcheck_ok=True)
```

**参数表**:`steps`=LLM 原始步骤列表。**异常表**:无(纯函数)。**关联测试**:test_f045_plan_propose(自检分支全覆盖)、GWT-F045-02(缺 expected → False)。

### `def classify_danger(self, tool_name: str) -> str` — 危险分级查表(F023 同源)

**功能一句话**:按 F023 内置分级表(与 scope._danger_mark/guard 同源加载)前缀匹配工具名返回 none/low/high/critical——high 步执行时须独立审批,表变更只紧不松。

```python
def classify_danger(self, tool_name):
    for rule in DANGER_TABLE:                        # 同源表:fs.*/exec.*/net.* 前缀规则
        if fnmatch(tool_name, rule["pattern"]):
            return rule["level"]                     # high→审批点;critical→不可执行
    return "none"                                    # 表外默认无危险标记
```

**参数表**:`tool_name`=步骤声明的工具名。**异常表**:无。**关联测试**:test_f045_plan_propose(fs.delete_file 类命中 high/critical)、GWT-F023 联动。

### `async def plan_approve(self, plan_id: int, *, who: str = "user", ctx) -> None` — 审批:批准(F046)

**功能一句话**:仅 pending 可批——写 plan.approved(who) 并置 approved(方案冻结),随即启动执行协程(plan.approved 自动建任务,逐步入队);已拒绝/已终态/不存在显式拒;批准前先查 24h 过期(过期自动转 rejected)。

```python
async def plan_approve(self, plan_id, *, who="user", ctx):
    p = self._get(plan_id)                           # 不存在 → EVT-101(必先 proposed)
    if p.status == "rejected":                       # 拒绝后不再推进(F046 边界)
        raise PyHError("BUSY", ctx={"hint": f"方案 {plan_id} 已拒绝,不可批准",
                                    "advice": "修订后重新提案(reject 重提)"})
    if p.status in ("completed", "aborted"):         # 终态不可再裁决
        raise PyHError("BUSY", ctx={"hint": f"方案 {plan_id} 已{ p.status }"})
    if p.status != "pending":                        # 只待批可批(防重复 approve)
        raise PyHError("BUSY", ctx={"hint": f"方案 {plan_id} 非待批态({p.status})"})
    if await self._expire_if_stale(p, ctx):          # 24h 未确认 → 自动 rejected(system)
        raise PyHError("BUSY", ctx={"hint": "方案已过期(24h 未确认),请重新提案"})
    p.status = "approved"                            # 批准后 steps 冻结不可变(改=reject 重提)
    await session.append("plan.approved", {"plan_id": plan_id, "who": who}, actor="user")
    asyncio.create_task(self.plan_execute(plan_id, ctx))   # approved 自动建任务(F046 I/O)
```

**参数表**:`plan_id`=proposed 事件 seq;`who`=裁决人。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | plan_id 不存在 | EVT-101 | 先 plan_propose |
| `PyHError` | 非 pending/已拒绝/已终态/已过期 | BUSY | 按 advice 重新提案 |

**关联测试**:test_f046_plan_exec(批准才执行、重复 approve 拒、拒绝后批准拒)、GWT-F046-01(plan.approved 后方案不可变)。

### `async def plan_reject(self, plan_id: int, *, who: str = "user", reason: str = "user-rejected", ctx) -> None` — 审批:拒绝(F046)

**功能一句话**:拒绝方案回修订对话——写 plan.rejected(who,reason);拒绝后不可再 approve/revise/execute;终态(completed/aborted)不可再拒绝。

```python
async def plan_reject(self, plan_id, *, who="user", reason="user-rejected", ctx):
    p = self._get(plan_id)                           # 不存在 → EVT-101
    if p.status in ("completed", "aborted", "rejected"):
        raise PyHError("BUSY", ctx={"hint": f"方案 {plan_id} 已终态({p.status})"})
    p.status = "rejected"
    await session.append("plan.rejected",
        {"plan_id": plan_id, "who": who, "reason": reason}, actor="user")
    # I/O:rejected 回修订对话(F046)——用户可带反馈重新 plan_propose
```

**参数表**:`reason`=拒绝原因(修订意见载体)。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | plan_id 不存在 | EVT-101 | 先 propose |
| `PyHError` | 已终态 | BUSY | 终态不可逆 |

**关联测试**:GWT-F046-02(rejected 后 exec.step 引用 → 拒)、test_f046_plan_exec。

### `async def plan_revise(self, plan_id: int, step_idx: int, patch: dict, ctx) -> Plan` — 单步编辑(F046)

**功能一句话**:仅 pending 方案可单步 edit(F046:approve/reject/单步 edit 后才执行)——旧方案 rejected(reason=revised)+同目标重提新方案,零 LLM 重调;批准后不可改(改=reject 重提,F046 边界)。

```python
async def plan_revise(self, plan_id, step_idx, patch, ctx):
    p = self._get(plan_id)
    if p.status != "pending":                        # 只待批可改(批准后冻结)
        raise PyHError("BUSY", ctx={"hint": "仅待批方案可单步修改",
                                    "advice": "已批准方案不可变;请 reject 后重新提案"})
    if not (0 <= step_idx < len(p.steps)):           # 步下标越界
        raise PyHError("EVT-100", ctx={"hint": f"step_idx {step_idx} 越界(共 {len(p.steps)} 步)"})
    new_steps = [s.model_dump() for s in p.steps]    # 值拷贝后单步字段替换
    new_steps[step_idx] = {**new_steps[step_idx], **patch}
    await session.append("plan.rejected", {"plan_id": plan_id, "who": "user",
        "reason": f"revised:step:{step_idx}"}, actor="user")   # 旧版作废(回放可审计)
    self._plans.pop(plan_id, None)                   # 旧 plan 出注册表(事件留痕)
    return await self.plan_propose(p.goal, ctx,      # 同目标重提新版,显式 steps 零 LLM 调用
                                   steps=new_steps)
```

**参数表**:`step_idx`=0 起;`patch`=该步字段增量(action/tool/expected/intent…)。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 非 pending(含已批准) | BUSY | reject 重提(边界:改=reject 重提) |
| `PyHError` | step_idx 越界 | EVT-100 | 核对步数 |

**关联测试**:test_f046_plan_exec(单步 edit 后执行的是新版;edit 不重调 LLM)、GWT-F046-03(批准后 revise → BUSY)。

### `async def plan_execute(self, plan_id: int, ctx) -> None` — 执行切换:逐步入队(F046/F043)

**功能一句话**:approve 后执行器——逐 step:写 plan.exec.step → 步骤意图 submit 入队(F043)并 wait_for;失败用户三选一(跳过/重试/中止),重试 ≤2 次仍败默认中止汇报;全部成功写 plan.done;中止写 plan.aborted(step=i);headless 无通道自动中止(APR-501 安全默认)。

```python
async def plan_execute(self, plan_id, ctx):
    p = self._get(plan_id)
    if p.status not in ("approved", "executing"):    # 硬条件:批准后才执行(F046)
        raise PyHError("BUSY", ctx={"hint": f"方案 {plan_id} 未批准,禁止执行"})
    p.status = "executing"                           # 批准 → 执行中
    for i, step in enumerate(p.steps):
        await session.append("plan.exec.step", {"plan_id": plan_id, "step_idx": i,
            "action": step.action}, actor="agent")
        tries = 0
        while True:                                  # 逐步入队:队列单飞,天然串行(F043)
            r = await task_queue.wait_for(task_queue.submit(step.intent))
            if r.ok: break                           # 成功 → 下一步
            if tries >= 2:                           # 重试耗尽 → 默认中止(汇报,F046 边界)
                p.status = "aborted"
                await session.append("plan.aborted",
                    {"plan_id": plan_id, "step": i}, actor="system"); return
            choice = await self._user_choice(["跳过", "重试", "中止"])  # 失败三选一
            if choice == "跳过": break               # 跳过:继续下一步
            if choice == "中止":
                p.status = "aborted"
                await session.append("plan.aborted",
                    {"plan_id": plan_id, "step": i}, actor="system"); return
            tries += 1                               # 重试:同一步重新入队(重走 guard/审批)
    if p.status == "executing":                      # 全部成功(未被中止打断)
        p.status = "completed"
        await session.append("plan.done", {"plan_id": plan_id}, actor="system")
```

**参数表**:`plan_id`=已批准方案。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 未批准执行 | BUSY | 先 plan_approve |
| headless 无通道 | _user_choice 不可用 | APR-501 | 单步失败默认中止(安全默认) |

**关联测试**:test_f046_plan_exec(批准才执行/三选一/危险步骤执行仍独立审批)、GWT-F046-04(单步失败重试 2 次后中止、plan.aborted 带 step)、test_f043_queue(逐步串行入队)。

### `async def plan_sweep_expired(self, ctx, *, now: datetime | None = None) -> int` — 过期清理(F045:24h 作废)

**功能一句话**:扫全部 pending 方案,超 24h 未确认自动 rejected(who=system,reason=expired)并返回清理数;由 agent-loop 周期驱动与 approve 前即时检查双路调用。

```python
async def plan_sweep_expired(self, ctx, *, now=None):
    now = now or datetime.now()
    n = 0
    for p in list(self._plans.values()):             # 快照遍历(清理中可安全变更)
        if p.status == "pending" and p.expires_at <= now:   # 24h 未确认(F045 边界)
            p.status = "rejected"                    # 自动作废,不执行
            await session.append("plan.rejected", {"plan_id": p.id,
                "who": "system", "reason": "expired"}, actor="system")   # who=system 自动态
            n += 1
    return n
```

**参数表**:`now`=测试注入时钟。**异常表**:无。**关联测试**:GWT-F045-03(24h 后 approve → 先自动 rejected)、test_f046_plan_exec(过期方案不执行)。

### `def list_plans(self, *, active_only: bool = True) -> list[Plan]` — 方案查询

**功能一句话**:按状态过滤列出方案(默认非终态),供 UI/命令层/compaction 判定未完成 plan 不可折叠(F058)。

```python
def list_plans(self, *, active_only=True):
    plans = sorted(self._plans.values(), key=lambda p: p.id)
    if not active_only:
        return plans                                 # 全量(含 rejected/aborted/completed)
    return [p for p in plans if p.status in
            ("pending", "approved", "executing")]    # 未完成集 → F058 不可折叠
```

**参数表**:`active_only`=是否仅未完成。**异常表**:无。**关联测试**:GWT-F046-05(list 与事件状态一致)、test_f058_compaction 联动。

### `@classmethod def rebuild_from_events(cls, events: list[Envelope]) -> PlanManager` — 事件溯源重建(INV-01)

**功能一句话**:顺序重放 plan.proposed/approved/rejected/exec.step/done/aborted 重建方案注册表与状态——崩溃恢复/会话恢复后内存态 ≡ 日志;孤儿引用(approved 前无 proposed)由写入层 EVT-101 拦截,此处防御跳过。

```python
@classmethod
def rebuild_from_events(cls, events):
    m = cls()
    for e in events:                                 # seq 升序重放(只读不写回)
        p = e.payload
        if e.type == "plan.proposed":
            pid = e.seq                              # plan_id = proposed 事件 seq(§3.5.2)
            m._plans[pid] = Plan(id=pid, goal=p["goal"],
                steps=[Step(**s) for s in p["steps"]],
                selfcheck_ok=p.get("selfcheck_ok", True), why="",
                status="pending", created_at=now(),
                expires_at=parse_iso(p["expires_at"]) if p.get("expires_at") else None)
        elif e.type == "plan.approved":
            g = m._plans.get(p["plan_id"])
            if g: g.status = "approved"
        elif e.type == "plan.rejected":
            g = m._plans.get(p["plan_id"])
            if g: g.status = "rejected"              # reason=expired/revised 等不细分
        elif e.type == "plan.exec.step":
            g = m._plans.get(p["plan_id"])
            if g: g.status = "executing"             # 任一步开跑即执行中
        elif e.type in ("plan.done", "plan.aborted"):
            g = m._plans.get(p["plan_id"])
            if g: g.status = "done" if e.type == "plan.done" else "aborted"
    return m
```

**参数表**:`events`=session.replay()/events_between 输出。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| 防御跳过+告警 | 孤儿引用(坏日志) | EVT-101 已拒于写入层 | repair 查隔离;重放不中断 |

**关联测试**:GWT-F046-06(事件流→状态重建)、崩溃恢复配套。

## 关联文档

| 文档 | 章节 | 关系 |
|---|---|---|
| PRD-Core.md | §5.5 F045/F046(方案生成/审批执行切换,验收伪代码) | 功能与验收权威 |
| EVENT-SCHEMA.md | §3.5.2(plan.* 字段/校验 plan_id=seq/actor)、§8.1 落盘矩阵、§7 词表外登记(plan.done/aborted) | 事件权威 |
| ERR.md | §2.2 EVT-100/EVT-101、§2.4 BUSY、§2.6 APR-501、§2.4 LLM-3xx | 错误码契约 |
| task_queue.py.md | 本规格同族 | 执行=逐步入队 + wait_for(F043) |
| goal.py.md | 本规格同族 | 目标→plan 提案;未完成 plan/goal 同属 F058 不可折叠集 |
| scope.py.md / DIS-CORE.md | F014 危险分级同源表、§0.2 INV-01 | 软指导边界与回放纪律 |
| SECURITY.md | S-1(终态只认事件)、T-3(方案批准≠危险操作批准) | 安全边界 |
