# specs/goal.py.md — 编码规格

> **模块文件**:`pyharness/core/goal.py` | **功能编号**:F047(goal 目标管理)· F044(关联 task_id 溯源)· F058(compaction 不可折叠集联动) | **权威口径**:PRD-Core §5.5 F047(功能与验收伪代码权威)、EVENT-SCHEMA §3.5.2(goal.created/updated/completed 字段级权威,status=active/paused/done/abandoned)、ERR.md §2.2(EVT-1xx)/§2.4(BUSY)、DIS-CORE §0.2(INV-01 历史必由日志派生)、SECURITY.md S-1(完成只认事件不认文本)
> **一句话**:会话级目标管理(PRD F047)——长任务把用户目标拆成可核对子目标(≤8 个活动),create/update/check/abandon 全部事件化(goal.created/updated/completed);目标状态只从事件回放重建(**溯源可回放**,INV-01);汇报"完成"必须先过核对(证据不足→追问,不落 completed,completed 只能由 system 写);活动目标持续渲染进系统提示词并做**防跑偏提醒注入**(软提示,不硬限制行为)。

## 模块职责

1. **目标注册与状态机(F047)**:会话级 Goal 注册——`goal_id`(g-N 单调)、`desc`、可选 `task_id` 关联(F044:目标可绑定产生它的任务)、子目标核对清单 checklist(逐条打勾)。状态四值取自 EVENT-SCHEMA 词表:`active/paused/done/abandoned`(口径裁定:PRD F047 描述态 open/in_progress 在本模块收敛为单一事件态 `active`,避免双词表;区别只体现在 progress 数值)。
2. **事件溯源可回放(INV-01)**:GoalManager 内存态**只由事件派生**——`rebuild_from_events()` 顺序重放 goal.created/updated/completed 重建注册表;任何内存字段不是第二真源;goal.updated/completed 前必有 goal.created,completed 后不可再 updated(EVENT-SCHEMA §3.5.2 校验,违规 BUSY 拒)。
3. **核对闸与防伪造(S-1/F047)**:`goal_check(claim_done=True)` 声称完成 → `verify_completion()` 核对证据(progress≥1.0、checklist 全勾、关联任务无 failed);证据不足 → 返回追问文本,**不落 completed**(未完成却说完成→追问一次,PRD F047 边界);`goal.completed` 事件 actor 恒为 `system`(框架核对后写),agent/用户只有 check 通道——防"伪造已执行"(SECURITY S-1)。
4. **防跑偏提示注入**:`render_goal_segment()` 把活动目标板(desc/进度/待核项)渲染成系统提示词段,由 system-prompt 模块(F010)每次 LLM 调用携带——软锚定不硬限制(agent 可自由偏离,但上下文持续可见);`drift_reminder()` 用确定性事件规则(连续 N 轮无任何 goal 关联活动)生成离题提醒文本注入下一轮。
5. **目标数量上限**:活动(未终态:active+paused)目标 ≤8,超限 create → BUSY 拒(PRD F047 边界);compaction(F058) 不可折叠集包含未完成 goal——done/abandoned 才可随段折叠。
6. **命令分发**:`goal(args, ctx)` 直译 PRD F047 验收伪代码(op=create/update/check/abandon/list),供工具/命令层调用,返回用户可读 summary。

## 依赖

- **依赖方向**(§0.3 拓扑):goal 模块位于编排层(agent-loop 之上);消费 `session.append`(goal.* 事件)、`session.replay/events_between`(溯源重建);被 system-prompt(goal 段装配)、compaction(不可折叠集判定)、命令层(op 分发)消费。
- **消费方**:system-prompt 模块(F010,装配 goal 段)、agent-loop(轮末喂 round_end 事件做漂移判定)、compaction(F058,查未完成 goal)、命令/工具层(goal 命令)。
- **外部依赖**:errors(PyHError,F020)、事件词表 goal.created/updated/completed(EVENT-SCHEMA §3.5.2);不依赖 plan/queue 实现,关联关系经 goal.task_id 字符串引用(弱耦合,INV-08)。

## 数据结构表

| 字段 | 类型 | 规则 |
|---|---|---|
| `GoalStatus` | Literal | `active/paused/done/abandoned`(EVENT-SCHEMA 词表;PRD open/in_progress 收敛为 active) |
| `Goal` | dataclass | `id: str`(g-N)/ `desc: str` / `checklist: list[CheckItem]`(text/done)/ `progress: float 0..1` / `status: GoalStatus` / `task_id: str\|None` / `created_seq: int` / `note: str\|None` |
| `CheckItem` | dataclass | `text: str`(可核对子目标)/ `done: bool=False` |
| `GoalCheck` | dataclass | `goal_id/ok/claim_done/missing: list[str]`(未过核对项)/ `reply: str`(追问或确认文本) |
| `_goals` | dict[str, Goal] | 注册表;**只由事件重建**(INV-01);created_seq 升序 |
| `_active_limit` | int = 8 | 活动(active+paused)目标上限(F047 边界);done/abandoned 不计 |
| `_last_goal_round` | int\|None | 最近一次含 goal 关联活动的轮号(漂移判定用) |
| `_round_no` | int | 当前轮号(agent-loop 每轮 +1 注入,round_end 同步) |
| 事件 | — | goal.created(actor=agent)/ goal.updated(actor=agent)/ goal.completed(actor=**system**) |

## 类与函数清单

### `async def goal(self, args: dict, ctx) -> str` — 命令分发(PRD F047 验收直译)

**功能一句话**:F047 验收伪代码原样落地的 op 分发器——op=create/update/check/abandon/list 各走对应方法并统一返回用户可读 summary 文本;未知 op/不存在 goal_id 显式拒。

```python
async def goal(self, args, ctx):
    op = args["op"]; gid = args.get("id")
    if op == "create":                               # 创建:可关联当前任务 ctx.task_id(F044)
        g = await self.goal_create(args["text"],
                 task_id=args.get("task_id") or ctx.task_id)
        return g.summary()
    if op == "list":
        return "\n".join(g.summary() for g in self.list_active()) or "无活动目标"
    g = self._get(gid)                               # 其余 op 先定位;不存在 → EVT-101
    if op == "update":
        await self.goal_update(gid, note=args.get("note"),
                 status=args.get("status"))          # 支持 status=paused 等
        return g.summary()
    if op == "check":                                # 核对:打勾/报进度/声称完成
        r = await self.goal_check(gid,
                 progress=args.get("progress"), checked=args.get("checked"),
                 claim_done=bool(args.get("claim_done")))
        return r.reply
    if op == "abandon":
        await self.goal_abandon(gid, reason=args.get("reason", ""))
        return g.summary()
    raise PyHError("EVT-100", ctx={"hint": f"未知 goal op: {op}",
                                   "advice": "op ∈ create/update/check/abandon/list"})
```

**参数表**:`args`={"op","text","id","progress","checked","claim_done","status","note","task_id",…};`ctx`=会话实体。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | op 非法 | EVT-100 | 对照 op 枚举修正 |
| `PyHError` | goal_id 不存在 | EVT-101 | 先 create;查 id 拼写 |

**关联测试**:test_f047_goal(状态机/核对/≤8)、GWT-F047-01(goal create→list 可见)。

### `async def goal_create(self, text: str, *, task_id: str | None = None) -> Goal` — 创建目标(F047)

**功能一句话**:新建活动 Goal 并写 goal.created(actor=agent);活动目标已达 ≤8 → BUSY 拒(须先 done/abandoned 旧的);text 空 → EVT-100。

```python
async def goal_create(self, text, *, task_id=None):
    if not text or not text.strip():
        raise PyHError("EVT-100", ctx={"hint": "goal 描述为空"})
    active = self._nondone()                         # active+paused 计数
    if active >= self._active_limit:                 # 活动目标 ≤8(F047 边界)
        raise PyHError("BUSY", ctx={"advice": "活动目标已达 8 个上限;请先完成/放弃旧目标",
                                    "active": active})
    gid = f"g-{self._seq.next()}"
    g = Goal(id=gid, desc=text.strip(), status="active",
             task_id=task_id, created_seq=self._next_seq)
    self._goals[gid] = g
    await session.append("goal.created",             # 事件先落,状态机只认事件(INV-01)
        {"goal_id": gid, "desc": text.strip(), "task_id": task_id}, actor="agent")
    self._touch_activity()
    return g
```

**参数表**:`text`=目标描述(用户原话拆分出的可核对子目标);`task_id`=关联任务(F044,可空)。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 活动目标 ≥8 | BUSY | 完成/放弃旧目标后重试 |
| `PyHError` | 空描述 | EVT-100 | 校验输入 |

**关联测试**:test_f047_goal(≤8 上限)、GWT-F047-02(第 9 个活动目标被拒)。

### `async def goal_update(self, goal_id: str, *, note: str | None = None, status: str | None = None, progress: float | None = None) -> None` — 更新目标(F047)

**功能一句话**:改 note/显式切状态(paused↔active)/报进度,统一写 goal.updated;done 目标不可再 updated(EVENT-SCHEMA 校验);status 只接受词表值。

```python
async def goal_update(self, goal_id, *, note=None, status=None, progress=None):
    g = self._get(goal_id)                           # 不存在 → EVT-101(前必有 created)
    if g.status == "done":                           # completed 后不可再 updated(词表校验)
        raise PyHError("BUSY", ctx={"hint": f"目标 {goal_id} 已完成,不可再更新",
                                    "advice": "完成态不可逆;新需求请新建目标"})
    payload = {"goal_id": goal_id}
    if note is not None: g.note = note; payload["note"] = note
    if status is not None:
        if status not in ("active", "paused", "abandoned"):
            raise PyHError("EVT-100", ctx={"hint": f"非法 goal 状态 {status}"})
        g.status = status                            # paused/resume/abandon 都经此写
        payload["status"] = status
    if progress is not None:
        g.progress = _clamp01(progress); payload["progress"] = g.progress
    await session.append("goal.updated", payload, actor="agent")
    self._touch_activity()
```

**参数表**:`note/status/progress` 均可选(至少一项,否则幂等返回)。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | goal_id 不存在 | EVT-101 | 先 create |
| `PyHError` | done 后再 updated | BUSY | 完成态不可逆;新建目标 |
| `PyHError` | status 非法值 | EVT-100 | 用词表四值 |

**关联测试**:test_f047_goal(状态机转移、done 后更新拒)、GWT-F047-03(completed 后 updated → BUSY)。

### `async def goal_check(self, goal_id: str, *, progress: float | None = None, checked: list[str] | None = None, claim_done: bool = False) -> GoalCheck` — 核对(打勾/进度/完成声明)

**功能一句话**:逐条核对子目标——checked 项置 done、报 progress;claim_done=True 时先 `verify_completion` 验证据:过核→写 goal.completed(actor=system);不过→返回追问文本,状态不变(未完成却说完成→追问一次,F047)。

```python
async def goal_check(self, goal_id, *, progress=None, checked=None, claim_done=False):
    g = self._get(goal_id)
    if g.status == "done":                           # 幂等:已完成的核对直接确认返回
        return GoalCheck(goal_id, ok=True, claim_done=True, missing=[], reply="该目标已完成")
    if checked:
        done_set = set(checked)
        for c in g.checklist:
            if c.text in done_set: c.done = True     # 打勾(子目标核对)
    if progress is not None:
        g.progress = _clamp01(progress)
    if not claim_done:
        await session.append("goal.updated", {"goal_id": goal_id,
            "progress": g.progress}, actor="agent"); self._touch_activity()
        return GoalCheck(goal_id, ok=True, claim_done=False, missing=[],
                         reply=f"已记录进度 {g.progress:.0%}")
    verdict = self.verify_completion(g)              # 声称完成 → 先过核对(纯函数)
    if not verdict.ok:                               # 证据不足:追问一次,不落 completed
        return GoalCheck(goal_id, ok=False, claim_done=True,
                         missing=verdict.missing,
                         reply=f"你说已完成,但核对项未过:{'、'.join(verdict.missing)};"
                               f"逐条核对(goal check)后再报完成。")
    await self.mark_completed(g)                     # 过核 → completed(actor=system)
    return GoalCheck(goal_id, ok=True, claim_done=True, missing=[], reply="核对通过,目标已完成")
```

**参数表**:`checked`=已打勾子目标文本列表;`progress`=0..1;`claim_done`=是否声明完成。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | goal_id 不存在 | EVT-101 | 先 create |

**关联测试**:test_f047_goal(核对/追问一次)、GWT-S-1 配套(伪造完成:claim_done 但 checklist 未全勾 → 追问,无 goal.completed 事件)。

### `def verify_completion(self, goal: Goal) -> CompletionVerdict` — 完成核对(纯函数,S-1 防伪闸)

**功能一句话**:完成证据核对——progress≥1.0 且 checklist 全 done 且关联 task 无 failed 记录;返回 ok 与 missing 明细;纯函数零副作用,供 goal_check 与测试直接调用。

```python
def verify_completion(self, goal):
    missing = []
    if goal.progress < 1.0:                          # 进度未满
        missing.append(f"进度仅 {goal.progress:.0%}")
    for c in goal.checklist:                         # 子目标逐条核对(PRD F047)
        if not c.done: missing.append(f"未打勾: {c.text}")
    if goal.task_id and self._task_failed(goal.task_id):
        missing.append(f"关联任务 {goal.task_id} 未成功结束")
    return CompletionVerdict(ok=not missing, missing=missing)   # ok=False → 追问(不落 completed)
```

**参数表**:`goal`=待核 Goal。**异常表**:无(纯函数)。**关联测试**:test_f047_goal(核对判定)、SECURITY S-1(伪造完成被拦:完成只认事件不认文本)。

### `async def mark_completed(self, goal: Goal) -> None` — 框架侧完成(仅供核对过核后调用)

**功能一句话**:写 goal.completed(actor 恒 system)——完成事实只能由框架核对后落事件;agent/用户路径只能经 goal_check(claim_done) 间接触发,无直接公开调用口。

```python
async def mark_completed(self, goal):
    if goal.status == "done": return                 # 幂等(重复过核无害)
    goal.status = "done"
    await session.append("goal.completed",
        {"goal_id": goal.id, "status": "done"}, actor="system")   # actor=system 硬编码(S-1)
    self._touch_activity()
```

**参数表**:`goal`=已过 verify_completion 的 Goal。**异常表**:无。**关联测试**:GWT-F047-04(goal.completed 的 actor 恒 system;agent 无法直写)。

### `async def goal_abandon(self, goal_id: str, *, reason: str = "") -> None` — 放弃目标

**功能一句话**:目标不再追求——写 goal.updated{status:abandoned}(词表事件,非新类型);abandoned 目标释放活动名额并进入 compaction 可折叠集。

```python
async def goal_abandon(self, goal_id, *, reason=""):
    g = self._get(goal_id)
    if g.status in ("done", "abandoned"):            # 终态幂等返回
        return
    g.status = "abandoned"
    await session.append("goal.updated",
        {"goal_id": goal_id, "status": "abandoned", "note": reason or None},
        actor="agent")
    self._touch_activity()
```

**参数表**:`reason`=放弃说明(可空)。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | goal_id 不存在 | EVT-101 | 先 create |

**关联测试**:GWT-F047-05(abandon 后 create 名额恢复)、test_f058_compaction(未完成 goal 不可折叠联动)。

### `def list_active(self) -> list[Goal]` — 活动目标看板源(F047)

**功能一句话**:返回全部未终态(active+paused)Goal,按 created_seq 升序——供看板/渲染/名额计数;≤8 由 create 保证。

```python
def list_active(self):
    return [g for g in sorted(self._goals.values(), key=lambda x: x.created_seq)
            if g.status in ("active", "paused")]     # done/abandoned 已终态不入板
```

**参数表**:无。**异常表**:无。**关联测试**:test_f047_goal(list 顺序)、GWT-F047-06。

### `def render_goal_segment(self, *, max_tokens: int = 800) -> str` — 防跑偏提示注入(goal 板渲染)

**功能一句话**:把活动目标板渲染为系统提示词段——每次 LLM 调用由 system-prompt(F010) 携带:目标 id/描述/进度/待核项;**软锚定不硬限制**(仅提示,不改 scope/不拦工具);超长按 max_tokens 截断。

```python
def render_goal_segment(self, *, max_tokens=800):
    acts = self.list_active()
    if not acts:
        return ""                                    # 无活动目标 → 零开销不注入
    lines = ["<goals> 当前活动目标(软提醒:汇报完成必须先过核对):"]
    for g in acts:
        pend = [c.text for c in g.checklist if not c.done]   # 待核子目标
        lines.append(f"- [{g.id}] {g.desc} | 进度 {g.progress:.0%}"
                     f" | 待核:{','.join(pend) if pend else '无'}"
                     + (f" | 关联任务 {g.task_id}" if g.task_id else ""))
    return "\n".join(lines)[:max_tokens]             # 截断防超窗(余量 ≥25% 由 F058 控)
```

**参数表**:`max_tokens`=注入段预算上限。**异常表**:无。**关联测试**:GWT-F047-07(渲染含待核项;无目标返回空串)、test_f010_sysprompt(goal 段装配)。

### `def round_end(self, events: list) -> None` / `def drift_reminder(self, *, quiet_rounds: int = 3) -> str | None` — 离题提醒(防跑偏确定性规则)

**功能一句话**:agent-loop 每轮末喂本轮事件给 round_end;drift_reminder 判定"存在活动目标但连续 quiet_rounds(≥3)轮无任何 goal 关联活动"(goal.* 事件或携带 goal 的任务入队),是→返回提醒文本(注入下一轮系统提示,软提示不阻断),否→None。

```python
def round_end(self, events):
    for e in events:                                 # 轮内事件扫:goal.* 或任务带 goal 溯源
        if e.type.startswith("goal.") or (e.type == "task.enqueued"
                and e.payload.get("meta", {}).get("goal_id")):
            self._last_goal_round = self._round_no   # 本轮有目标活动 → 静默轮计数清零
    return None

def drift_reminder(self, *, quiet_rounds=3):
    if not self.list_active():                       # 无活动目标:不判定不提醒
        return None
    if self._last_goal_round is None:                # 会话从未有目标活动:不判定
        return None
    if self._round_no - self._last_goal_round >= quiet_rounds:   # 连续 N 轮零目标活动
        return ("提示:已连续数轮未推进任何活动目标(goal 板见上)。若已偏离原目标,"
                "请说明理由或更新/放弃目标,避免跑偏。")   # 软提醒:不硬限制行动
    return None
```

**参数表**:`events`=该轮全部 Envelope;`quiet_rounds`=静默轮阈值(默认 3)。**异常表**:无。**关联测试**:GWT-F047-08(连续 3 轮无目标活动出提醒;有 goal 事件不出;无活动目标不出)。

### `@classmethod def rebuild_from_events(cls, events: list[Envelope]) -> GoalManager` — 事件溯源重建(INV-01)

**功能一句话**:顺序重放 goal.created/updated/completed 重建注册表——崩溃恢复/会话恢复后内存态与日志一致;孤儿 updated/completed(前无 created)属日志损坏,由 EVENT-SCHEMA 校验层拦截,此处防御性跳过并告警。

```python
@classmethod
def rebuild_from_events(cls, events):
    m = cls()                                        # 全新空注册表
    for e in events:                                 # 顺序重放(seq 升序,只读不写回)
        p = e.payload
        if e.type == "goal.created":
            m._goals[p["goal_id"]] = Goal(id=p["goal_id"], desc=p["desc"],
                task_id=p.get("task_id"), status="active", created_seq=e.seq)
        elif e.type == "goal.updated":
            g = m._goals.get(p["goal_id"])
            if g is None: log.warning("orphan goal.updated seq=%s", e.seq); continue
            g.progress = p.get("progress", g.progress)
            g.status = p.get("status", g.status)     # paused/abandoned 等迁移
            g.note = p.get("note", g.note)
        elif e.type == "goal.completed":             # actor=system;终态
            g = m._goals.get(p["goal_id"])
            if g is not None: g.status = "done"
    return m                                         # 内存态 ≡ 日志派生态(INV-01)
```

**参数表**:`events`=session.replay()/events_between 输出。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| 防御跳过+告警 | updated 前无 created(坏日志) | EVT-101 已拒于写入层 | repair 查隔离区;重放不中断 |

**关联测试**:test_f047_goal(事件流→状态重建一致)、test_f009_session_log(replay 后重建)、崩溃恢复配套(GWT-F009)。

## 关联文档

| 文档 | 章节 | 关系 |
|---|---|---|
| PRD-Core.md | §5.5 F047(验收伪代码/状态机/≤8/追问一次) | 功能与验收权威 |
| EVENT-SCHEMA.md | §3.5.2(goal.created/updated/completed 字段/校验/actor)、§8.1 落盘矩阵 | 事件字段与 actor 权威 |
| ERR.md | §2.2 EVT-100/EVT-101、§2.4 BUSY | 错误码契约 |
| SECURITY.md | S-1 伪造"已执行" | 完成只认事件不认文本(本模块 completed 恒 system) |
| task_queue.py.md / plan_mode.py.md | 本规格同族 | task_id 关联溯源(F044)、目标→plan 提案联动 |
| DIS-CORE.md | §0.2(INV-01)、§5 system-prompt(goal 段装配) | 回放纪律与注入装配 |
