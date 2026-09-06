# specs/compaction.py.md — 编码规格

> **模块文件**:`pyharness/core/compaction.py` | **功能编号**:F058(上下文压缩 compaction,核心)· 联动 F010(窗口裁决:assemble 触发 compact_hint)/F007(agent-loop 请求前先压缩再调用)/F044(折叠单位=任务段整段)/F032(压缩后窗口回落)/F060(repair 对账:compacted 声明是合法空洞)/F042(标题/关键事实入摘要)/F009(session.mark_folded 派生遮蔽) | **权威口径**:PRD-Core §5.6 F058(功能与验收伪代码权威:触发=派生≥75% 窗口且上次压缩后新增≥10 轮、保留最近 12 轮、不可折叠集、整段不切半、摘要失败降级、空洞不回填、可逆审计)、EVENT-SCHEMA §3.5.5(context.compacted 字段级权威:ranges[[lo,hi]]/summary/tokens_before/tokens_after;强同步落盘)+§4.2(reducer 以摘要代区间→system 消息\"[已压缩 {ranges}] {summary}\")、DIS-CORE §5(系统提示词状态机:COMPACT_HINT→agent-loop 先 compact 再重试)、SECURITY.md §3.1(compaction 后指令属性丢失风险:guard.rejected 与不可折叠内容不进摘要)、ERR.md §2.2/§2.4;冲突以 PRD-Core 为准
> **一句话**:接近窗口上限时把**早期完整任务段**折叠为摘要(F058)——物理日志**仍只追加**:折叠区间原事件一行不删不改,只追加一条强同步 `context.compacted(ranges/summary/tokens_before/tokens_after)` 声明,由它把该区间在**派生层遮蔽**(reducer 遇 compacted 以摘要代区间、seq 空洞合法化,§3.4);触发=派生历史 ≥75% 上下文窗口**且**上次压缩后新增 ≥10 轮;折叠候选=最近 12 轮以前的**完整任务段**,含未完成 plan/goal/todo、待审批请求、guard 拒绝记录的段一律不折叠;摘要失败自动降级(\"只丢工具原始结果,保留决策轮\")不阻塞对话;ranges+摘要同留事件,随时可展开核对(压缩但不撒谎);折叠块放在历史头部稳定位置,让 LLM 侧 **KV 缓存前缀在两次压缩之间可复用**(cache_hit 入 llm.usage)。

## 模块职责

1. **触发判定(F058+DIS-CORE §5)**:`should_compact`:派生历史 token 数 ≥75%×window_tokens(默认 64k)且距上次 context.compacted 新增轮数 ≥10(轮=自上次压缩以来 user.message 触发的人机轮);在 agent-loop 每次 LLM 请求前检查(F007 边界:只在请求前、会话空闲时),system-prompt.assemble 撞条件时发 `sysprompt.compact_hint` 总线事件→agent-loop 先调 `run_if_needed` 压缩再重试装配(见 关联文档 DIS-CORE §5)。
2. **候选选择(不可折叠集+整段纪律)**:候选=**最近 12 轮之前的完整任务段**(segment.start/end 配对齐全、end_seq ≤ 保留锚);四类不可折叠内容所在段整体排除:①未完成 plan(proposed 无 approved/rejected)/goal(未 done/abandoned)/todo(有未 done 项,F040/F047 联动);②待审批 approval.requested 无裁决;③guard.rejected 拒绝记录(单调审计链永不进摘要,SECURITY §3.1);④已在折叠区间的段(防重复)。折叠单位=**整段不切半**(F044 段是审计最小单元)。
3. **压缩执行与声明**:对每个候选段 `_summarize_segment`(独立 LLM 调用,摘要预算 400 token,失败重试 1 次仍败→`_degrade_summary` 规则降级:只拼 user.message/agent.message/session 决策类文本,丢 tool 原始结果,degraded=True 标记)→`session.mark_folded(ranges)`(派生层遮蔽标记)→追加 `context.compacted`(ranges/summary/tokens_before/tokens_after,**强同步落盘**:空洞合法化声明不持久则重放误报空洞,EVENT-SCHEMA §8.1)。
4. **遮蔽语义(物理只追加)**:压缩**绝不改写 JSONL**:无删除/无回填/无重排;folded 区间原事件永久在日志(可逆审计:ranges+摘要同留,随时按 seq 展开核对原文);\"压缩\"只发生在**派生历史层**——reducer 跳过 folded 事件并在区间起点插入 `{role:system, content:"[已压缩 {ranges}] {summary}"}`(EVENT-SCHEMA §4.2);append 永远 max+1 续写,空洞=已被摘要代表(§3.4)。FTS 索引不受影响(旧事件仍可全文搜到,派生视图各自独立)。
5. **KV 缓存前缀复用**:压缩把\"一大段将滚出窗口的原文\"替换为\"固定位置的一条摘要\"——派生态历史结构稳定为 [system][历次 compacted 摘要(按 seq 序)][最近 12 轮+新轮尾部];两次压缩之间**头部前缀零变动**,每轮只在尾部追加新内容→LLM 服务端前缀缓存整段命中;`kv_prefix_plan()` 返回稳定前缀边界供 llm 层标注预期 cache 命中区(llm.request 注解/llm.usage.cache_hit 计量,F029)。纪律:compaction 只在请求边界执行、从不重写旧 compacted 摘要位置、窗口裁剪永不触碰折叠块。
6. **压缩报告**:返回 `CompactReport(folds=[{range,summary,degraded,tokens}], tokens_before/after, kept_recent_rounds, reason)`;agent-loop/UI 展示前后 token 对比(F058 输出要求);无可折叠候选→空报告 no-op(不写事件)。

## 依赖

- **依赖方向**:compaction 位于会话周边层(ctx.session 命名空间);消费 `session.derive_history`(token 估算/触发判定,只读)、`session.segments_before`/`events_between`(F044 段切片)、`session.mark_folded`+`session.append`(F009 门面;append context.compacted 由 session 强同步落盘)、`scope.window_tokens`、`ctx.llm.summarize`(F012 出口;框架内派生小调用,同 F042 auto-title 先例);被 agent-loop(F007 请求前闸)、system-prompt(F010 compact_hint 信号源)、repair(F060 空洞声明核对)消费。
- **消费方**:agent-loop(请求前 run_if_needed)、UI/CLI(压缩报告展示)、session.derive_history(reducer 读 folded 标记与 compacted 事件)、F031 自检(空洞有声明=合法)。
- **外部依赖**:errors(PyHError);无独立存储(折叠标记 `_folded` 属 SessionLog 内存态,可由日志重建,INV-01)。

## 数据结构表

| 字段 | 类型 | 规则 |
|---|---|---|
| `FoldCandidate` | dataclass | `task_id: str` / `range: tuple[int,int]`(段 [start_seq,end_seq],整段)/ `start_ts: str` / `n_events: int` |
| `SegmentSummary` | dataclass | `range: tuple[int,int]` / `summary: str`(≤~800 字)/ `degraded: bool`(True=规则降级产物)/ `tokens: int` |
| `CompactReport` | dataclass | `folds: list[SegmentSummary]` / `tokens_before/after: int` / `kept_recent_rounds: int` / `reason: str`(window+rounds)/ `noop: bool` |
| `PrefixPlan` | dataclass | `stable_upto_seq: int`(稳定前缀终点=最新 compacted 后)/ `prefix_tokens: int` / `cacheable: bool` / `blocks: list[tuple[int,int]]`(历次折叠块) |
| `_keep_recent_rounds` | int = 12 | F058:保留最近 12 轮原文 |
| `_window_ratio` / `_min_new_rounds` | float/int | 0.75 / 10(触发条件,读 config 可调) |
| `_summary_budget` | int = 400 | 每段摘要 token 预算(F058) |
| 事件 | — | context.compacted{ranges:[[lo,hi]],summary,tokens_before,tokens_after};actor=system;**强同步落盘** |

## 类与函数清单

### `def should_compact(self, ctx) -> bool` — 触发判定(F058 条件+轮闸)

**功能一句话**:派生历史 ≥75% 窗口 **且** 距上次压缩新增 ≥10 轮 才返回 True(双条件缺一不触发,防窗口抖动频繁压缩);会话不在请求边界(BUSY)时调用方自行保证。

```python
def should_compact(self, ctx):
    hist_tokens = estimate_tokens(ctx.session.derive_history())
    window = ctx.scope.window_tokens               # 默认 64k(scope 联动)
    usage = hist_tokens / window if window else 1.0
    if usage < self._window_ratio:                 # ① 未到 75% → 不压
        return False
    if self._turns_since_last_compact(ctx) < self._min_new_rounds:  # ② <10 轮
        return False
    return True                                    # 双条件齐 → agent-loop 先压再调
```

**参数表**:`ctx`=会话上下文。**异常表**:无(纯判定,不抛)。**关联测试**:test_f058_compaction(触发条件:GWT 窗口 75%+10 轮边界)。

### `def candidates(self, ctx) -> list[FoldCandidate]` — 折叠候选选择(F058 不可折叠集)

**功能一句话**:取最近 12 轮以前的**完整任务段**;含未完成 plan/goal/todo、待审批、guard.rejected、已折叠区间的段整体剔除(整段不切半);返回按 seq 升序的候选列表。

```python
def candidates(self, ctx):
    anchor = self._recent_anchor_seq(ctx)          # 第 12 轮(从尾数 user.message)起点
    out = []
    for seg in ctx.session.segments_before(anchor):   # F044 段切片,仅完整段
        events = list(ctx.session.events_between(*seg.range))
        if self._protected(events):                # 不可折叠集命中 → 整段排除
            continue
        if ctx.session.folded_overlaps(seg.range): # 已折叠区间 → 防重复
            continue
        out.append(FoldCandidate(task_id=seg.task_id, range=seg.range,
                                 start_ts=events[0].ts, n_events=len(events)))
    return out                                     # 升序,不切半
```

**参数表**:`ctx`。**异常表**:无(读侧纯函数)。**关联测试**:test_f058(不可折叠:段含 guard.rejected 不进候选;未完成 plan 段不进;段边界不切半)。

### `async def compact(self, ctx) -> CompactReport` — 压缩主流程(F058 验收直译)

**功能一句话**:候选逐个摘要(失败降级)→统计前后 token→mark_folded→强同步追加 `context.compacted`;无可折叠候选返回 noop 不写事件;全程不碰 JSONL 原文(只追加声明事件)。

```python
async def compact(self, ctx):
    cands = self.candidates(ctx)
    if not cands:                                  # 无候选 → noop,不写事件
        return CompactReport(folds=[], tokens_before=0, tokens_after=0,
                             kept_recent_rounds=12, reason="no-candidates", noop=True)
    before = estimate_tokens(ctx.session.derive_history())
    folds, failures = [], 0
    for cand in cands:                             # 逐段独立摘要(LLM 调用预算受限)
        try:
            folds.append(await self._summarize_segment(ctx, cand))
        except PyHError:
            failures += 1                          # 摘要失败→降级(见 _degrade),
            evs = list(ctx.session.events_between(*cand.range))
            txt, _ = self._degrade_summary(evs)    # 只丢工具原文,保留决策轮
            folds.append(SegmentSummary(range=cand.range, summary=txt,
                                        degraded=True, tokens=estimate_tokens(txt)))
    for f in folds:                                # 派生遮蔽标记(物理日志不动)
        ctx.session.mark_folded(f.range)
    after = estimate_tokens(ctx.session.derive_history())  # 摘要代区间后估算
    await ctx.session.append("context.compacted",          # 声明=空洞合法化
        {"ranges": [list(f.range) for f in folds],
         "summary": "\n".join(f.summary for f in folds),
         "tokens_before": before, "tokens_after": after},
        actor="system", sync=True)                 # 强同步:声明不丢(§8.1)
    return CompactReport(folds=folds, tokens_before=before, tokens_after=after,
                         kept_recent_rounds=self._keep_recent_rounds,
                         reason=f"window:{failures} degraded", noop=False)
```

**参数表**:`ctx`。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | append 强同步失败(声明没落盘) | PERS-202 | 压缩视为未发生;修存储重试(F060 后) |
| `PyHError` | 折叠区间与既有折叠重叠 | EVT-100 | candidates 已过滤;重叠即内部 bug,拒写防双摘要 |
| `PyHError` | 会话 running 中调用(非请求边界) | BUSY | agent-loop 只在空闲边界调用 |

**关联测试**:test_f058(触发/不可折叠/空洞/可逆)、GWT-F058-01(压缩后 seq 空洞被 compacted 声明覆盖、append 继续 max+1)。

### `async def _summarize_segment(self, ctx, cand: FoldCandidate) -> SegmentSummary` — 段摘要(独立 LLM 调用)

**功能一句话**:把段事件渲染为可摘要文本→`ctx.llm.summarize(budget=400)` 独立调用产摘要;重试 1 次仍败抛 PyHError 交 compact() 降级;摘要提示词要求含关键事实与目标/待办结果,不含 guard/审批细节。

```python
async def _summarize_segment(self, ctx, cand):
    events = list(ctx.session.events_between(*cand.range))
    text = render_segment(events)                  # 决策轮文本化(tool 结果截断)
    prompt = ("压缩以下任务段为 ≤400 token 摘要:保留用户意图、最终结论、"
              "关键事实、目标/待办结果;省略工具原始输出与安全审计细节。\n" + text)
    try:
        summ = await ctx.llm.summarize(prompt, budget=self._summary_budget)
    except PyHError:
        summ = await ctx.llm.summarize(prompt,     # 重试 1 次(可重试码)
                    budget=self._summary_budget)   # 仍败→上抛走降级
    return SegmentSummary(range=cand.range, summary=summ.strip(),
                          degraded=False, tokens=estimate_tokens(summ))
```

**参数表**:`ctx`;`cand`。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 两次摘要失败(LLM 不可用) | LLM-3xx 透传 | compact() 捕获→`_degrade_summary` 规则降级,不阻塞对话 |

**关联测试**:test_f058(摘要含关键事实;mock 摘要失败→degraded 路径)。

### `def _degrade_summary(self, events: list[Envelope]) -> tuple[str, bool]` — 规则降级摘要(摘要失败分支)

**功能一句话**:纯规则降级——只抽取段内 user.message/agent.message/session.renamed/plan.*/goal.*/todo.updated(决策类)文本拼摘要,**丢弃全部 tool.* 原始结果**,截断 ≤600 字;返回 (文本, degraded=True)。

```python
_KEEP = {"user.message", "agent.message", "session.renamed",
         "plan.proposed", "plan.approved", "goal.updated",
         "todo.updated", "context.compacted"}
def _degrade_summary(self, events):
    parts = []
    for ev in events:                              # 决策轮保留,工具原文丢弃
        if ev.type not in _KEEP:
            continue
        text = ev.payload.get("content") or ev.payload.get("new_title") \
             or ev.payload.get("desc") or ev.payload.get("summary") or ""
        if text: parts.append(text.strip())
    merged = "；".join(parts)
    return merged[:600], True                      # 截断保底;degraded 如实标记
```

**参数表**:`events`=段内事件列表。**异常表**:无(纯函数)。**关联测试**:test_f058(降级摘要零 tool.* 原文、长度 ≤600、degraded=True)。

### `async def _apply(self, ctx, folds) -> Envelope` — 遮蔽+声明(被 compact 调用;语义见 compact)

**功能一句话**:与 compact 内联逻辑同——mark_folded 全部区间→强同步 append context.compacted;独立成方法供 repair/测试直接复用(幂等:全空 folds 直接返回 None)。

```python
async def _apply(self, ctx, folds):
    if not folds:
        return None                                # 幂等 no-op
    before = estimate_tokens(ctx.session.derive_history())
    for f in folds:
        ctx.session.mark_folded(f.range)           # 派生遮蔽(物理只追加不变)
    after = estimate_tokens(ctx.session.derive_history())
    return await ctx.session.append("context.compacted",
        {"ranges": [list(f.range) for f in folds],
         "summary": "\n".join(f.summary for f in folds),
         "tokens_before": before, "tokens_after": after},
        actor="system", sync=True)
```

**参数表**:`ctx`;`folds`=SegmentSummary 列表。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 强同步 append 失败 | PERS-202 | 压缩未生效;repair 后重试 |

**关联测试**:test_f060_repair(compacted 声明=合法空洞;无声明空洞才告警)。

### `def render_compacted_message(self, env: Envelope) -> str` — reducer 钩子文本(EVENT-SCHEMA §4.2)

**功能一句话**:给 session.derive_history 用的纯函数:把 context.compacted 事件渲染为插桩 system 消息 `[已压缩 {ranges}] {summary}`——空洞语义的派生锚(reducer 在区间起点插入本条,其余 folded 事件跳过)。

```python
def render_compacted_message(self, env):
    p = env.payload
    rng = ",".join(f"[{lo}-{hi}]" for lo, hi in p["ranges"])
    return f"[已压缩 {rng}] {p.get('summary', '')}"
```

**参数表**:`env`=context.compacted 事件。**异常表**:无。**关联测试**:EVENT-SCHEMA §4.2 映射测试(compacted→system 消息且旧轮不出现)。

### `def kv_prefix_plan(self, ctx) -> PrefixPlan` — KV 缓存前缀复用规划

**功能一句话**:计算\"两次压缩间稳定、可整段复用\"的派生历史前缀:截止最新 compacted 事件后第一条(其后只有尾部长),返回边界与预计前缀 token,供 llm 层标注 cache 命中区(llm.usage.cache_hit 计量,F029)。

```python
def kv_prefix_plan(self, ctx):
    comp = ctx.session.last_event_of("context.compacted")   # 最新压缩声明
    if comp is None:
        return PrefixPlan(stable_upto_seq=0, prefix_tokens=0,
                          cacheable=False, blocks=[])        # 从未压缩:无可复用锚
    head = ctx.session.events_until(comp.seq)                # [0..compacted.seq]
    blocks = [(lo, hi) for lo, hi in comp.payload["ranges"]]
    return PrefixPlan(stable_upto_seq=comp.seq,              # 稳定前缀终点
                      prefix_tokens=estimate_tokens(
                          [self.render_compacted_message(comp)] + head),
                      cacheable=True, blocks=blocks)
```

**参数表**:`ctx`。**异常表**:无(只读派生计算)。**关联测试**:test_f058(KV:两次压缩间两轮请求前缀哈希一致;新轮只改尾部)、test_f029(cache_hit 计量联动)。

### `async def run_if_needed(self, ctx) -> CompactReport | None` — 请求前门面(DIS-CORE §5 消费点)

**功能一句话**:agent-loop 在每次 LLM 请求前(收到 compact_hint 或主动)调用:should_compact 命中→compact 并返回报告;未命中→None;保证\"先压后调\"装配可继续(DIS-CORE §5:COMPACT_HINT→先 compact 再重试)。

```python
async def run_if_needed(self, ctx):
    if ctx.agent.state != "idle":                  # 仅请求边界可压(BUSY 防竞争)
        return None
    if not self.should_compact(ctx):               # 双条件未齐 → 不压
        return None
    report = await self.compact(ctx)               # 强同步声明后返回
    if not report.noop:
        bus.emit("sysprompt.compacted",            # 通知装配层重试(缓存已失效)
                 {"tokens_before": report.tokens_before,
                  "tokens_after": report.tokens_after})
    return report
```

**参数表**:`ctx`。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 状态非空闲调压缩 | BUSY | 调用方等 running 结束 |

**关联测试**:test_f058(压缩后继续对话正常;test_f007 集成:窗口撞线→先压缩后请求)。

### `def _turns_since_last_compact(self, ctx) -> int` / `def _recent_anchor_seq(self, ctx) -> int` — 轮计数与保留锚(内部)

**功能一句话**:纯读辅助——自上次 context.compacted 以来新增 user.message 轮数(触发闸②);从日志尾部倒数第 12 个 user.message 的 seq(保留锚:锚之后的整段事件一律不折叠)。

```python
def _turns_since_last_compact(self, ctx):
    last = ctx.session.last_event_of("context.compacted")
    return sum(1 for e in ctx.session.events_after(last.seq if last else 0)
               if e.type == "user.message")        # 轮=人机轮(user.message 计数)
def _recent_anchor_seq(self, ctx):
    um = [e.seq for e in ctx.session.all_events() if e.type == "user.message"]
    if len(um) <= self._keep_recent_rounds:
        return 0                                   # 总轮 ≤12 → 无折叠空间
    return um[-self._keep_recent_rounds]           # 倒数第 12 轮起点=保留锚
```

**参数表**:`ctx`。**异常表**:无。**关联测试**:test_f058(轮闸边界:新增 9 轮不触发、第 10 轮触发;保留 12 轮原文不动)。

## 关联文档

- PRD-Core.md §5.6 F058(功能与验收伪代码权威)、§3.4(空洞语义:压缩段以 compacted 代替,seq 不回填)、§4.1(原则 1:派生折叠、日志不可变)
- EVENT-SCHEMA.md §3.5.5(context.compacted 字段级权威/校验:ranges 不重叠、折叠单位整段、触发条件)+§4.2(reducer:摘要代区间→system 消息)+§8.1(强同步落盘矩阵)
- DIS-CORE.md §5(系统提示词窗口状态机:COMPACT_HINT→agent-loop 先 compact 再重试;GWT-S5-04)
- SECURITY.md §3.1(compaction 后指令属性丢失风险:guard 拒绝记录与不可折叠内容不进摘要;摘要失真由工具闸口兜底)
- ERR.md §2.3(PERS-202 强同步声明失败)、§2.2(EVT-1xx)、§2.4(BUSY/LLM-3xx)
- CFG.md(`scope.window_tokens=64k`、F058 数字约束)、N4/N3(上下文卫生)
- specs/session.py.md(SessionLog `_folded` 遮蔽/compacted 事件写入口)、specs/system_prompt.py.md(压缩触发信号与装配重试)、specs/goal.py.md/plan_mode.py.md(不可折叠集联动)、specs/session_query.py.md(FTS 独立于折叠:旧事件仍可搜)
