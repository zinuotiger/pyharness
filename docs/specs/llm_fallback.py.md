# specs/llm_fallback.py.md — 编码规格

> **模块文件**:`pyharness/core/llm_fallback.py`(同属脊柱 llm 模块族,职责拆分到编排层文件;模块级依赖仍为 `llm → scope(预算联动)`) | **功能编号**:F013(核心)· F028 · F033 · F032(预算联动前置闸) | **权威口径**:PRD-Core §5.2 F013/F028、§5.3 F032/F033(功能权威)、DIS-CORE §4.3.2/§4.3.3/§4.4(降级链与退避伪代码权威)、ADI §3(触发判据/切换粒度/回切)、§6(超时重试)、ERR.md §2.4/§5.2(模型失败链)、CONSTRAINTS-07 §10(BudgetGuard 预算口径)
> **一句话**:降级链 + 指数退避 + 健康探针 + 预算闸——主模型失败时按链切备用(qwen-max),请求级粒度、降级留痕、探针回切(3 败 down/2 好回),并守住"单任务 <1 元"的预算前置检查;可用性与成本(备用更贵)在编排层平衡。

## 模块职责

1. **降级链编排**(F013):`chat_with_fallback` 按 `chain[idx:]` 依次尝试;适配器内先退避重试(F028),耗尽且满足降级条件才切下一适配器;**LLM-304 业务错不降级**(降级只救"上游坏",不救"我们错");链尾全败 → LLM-310 终态(不无限降级)。
2. **指数退避**(F028):只重试 LLM-301/303(可重试码);基数 1s ×2、上限 4 次、±30% 抖动;每次等待落 `llm.retry(attempt, delay_ms)`;sleep 可被取消(F025);总重试时长计入请求预算。
3. **请求级粒度**(ADI §3.4):当前请求先在本适配器内耗尽,才尝试下一适配器;降级结果 `chain.idx` 前移对**后续请求**生效;每次实际降级落 `llm.request(degraded_from=主模型)`,单会话降级 >5 次告警。
4. **健康探针与回切**(F033):60s 周期对每适配器 `ping()`;连续 3 败 → down(防抖动),down 被 `pick()` 跳过;连续 2 次健康 → healthy → 自动回切主模型;探针不计 F029 用量、失败不落 llm.retry。
5. **单任务预算检查(BudgetGuard)**:发请求前与每次重试前读 `UsageCounters`(report_usage 唯一写入,可重建)与 `BudgetLimits`,返回 ok/warn/paused/exhausted;状态判定与 scope.budget_state 同数据源、无第二份累计——exhausted 由 agent-loop 强制终态(F032)。

## 依赖

- **依赖方向**(§0.3 拓扑):llm_fallback 属 llm 模块族(agent-loop → llm 覆盖);`llm_fallback → llm.py(adapters/chat)`、`→ scope(预算状态/窗口读数)`;经 `ctx.session.append` 落 llm.retry/llm.error 事件、`bus.emit` 发告警。
- **消费方**:agent-loop 与 llm.py 的 `chat`/`chat_stream` 均经 `chat_with_fallback` 出网(INV-02);scope.budget_state 与本文件 BudgetGuard 读同一计数器。
- 外部依赖:`llm.py(LLMAdapter/LLMResponse/adapters 注册表)`、`errors.raise_code`(F020)、`openai` 异常类(经 normalize_exc)、config(llm.retry/llm.degrade/llm.probe/budget.task.*)。

## 数据结构表

| 字段 | 类型 | 规则 |
|---|---|---|
| `FallbackChain` | dataclass | `chain=["deepseek-chat","qwen-max"]`、`idx=0`;降级=idx 前移,对后续请求生效 |
| `AdapterHealth` | dataclass | `state ∈ healthy/degraded/down` + `fail_streak/ok_streak`;F033 判据 |
| `BudgetLimits` | dataclass | `in≤2_000_000 / out≤50_000 tokens / cost≤1.0 元`;warn=80%(F032 默认,全进 config) |
| `BudgetState` | Literal | `ok/warn/paused/exhausted`;exhausted→终态 reason=budget |
| `HealthTable` | dict[str, AdapterHealth] | 探针每 60s 更新;pick() 择优依据 |
| `err_hist` | list[tuple] | 每适配器 (name, code) 失败史;LLM-310 ctx 携带供排查 |

## 类与函数清单

### `class FallbackChain` + `async def chat_with_fallback(messages, tools=None, *, ctx) -> LLMResponse` — 链式降级编排(F013/DIS-CORE §4.3.2)

**功能一句话**:从 `chain[idx:]` 起逐适配器经 `_retry_adapter` 尝试;可重试码耗尽且满足降级条件才前移 idx;业务错上抛;全败 → LLM-310。

```python
class FallbackChain:
    def __init__(self):
        self.chain = ["deepseek-chat", "qwen-max"]   # CFG llm.fallback_models 注入
        self.idx = 0                                  # 降级对后续请求生效
        self.health = {n: AdapterHealth() for n in self.chain}

    async def chat_with_fallback(self, messages, tools=None, *, ctx):
        err_hist = []
        for i, name in enumerate(self.chain[self.idx:]):
            if self.health[name].is_down() and i > 0:  # 探针 down→跳过(主模型 down
                continue                               #  不跳:仍尝试以测恢复)
            BudgetGuard.check(ctx)                      # 每请求前置预算闸(F032)
            try:
                return await self._retry_adapter(name, messages, tools, ctx)
            except PyHError as e:
                err_hist.append((name, e.code))
                if e.code in ("LLM-302", "LLM-303") and self._may_degrade(e, ctx):
                    self._record_degradation(name, self.chain[i+1], ctx)  # 留痕+告警
                    self.idx = i + 1                    # 下一请求从备用起
                    continue                            # 本轮请求继续走下一适配器
                raise                                   # LLM-304 等:不降级直接上抛
        raise PyHError("LLM-310", ctx={"err_hist": err_hist,
            "advice": "全链失败,任务终止(不无限降级)"})
```

**参数表**:同 llm.chat。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 认证错/限流耗尽(302/303) | 链内降级 | idx 前移,备用模型重发同一请求体 |
| `PyHError` | 业务性失败 | LLM-304 | 不重试不降级上抛;agent-loop reason=error |
| `PyHError` | 链尾全败 | LLM-310 | 终态;看 err_hist;降级>5 次/会话告警 |

**关联测试**:GWT-L4-02(deepseek 持续 401 → 落 qwen-max + degraded_from;双败→LLM-310)、test_f013_fallback。

### `async def _retry_adapter(name, messages, tools, ctx)` — 指数退避(F028/DIS-CORE §4.3.3)

**功能一句话**:单适配器内重试循环——只重试 LLM-301/303,上限 4 次,退避 1/2/4/8s ±30% 抖动,每次等待落 llm.retry 事件;耗尽抛 LLM-303(exhausted)交降级决策。

```python
async def _retry_adapter(self, name, messages, tools, ctx):
    delay = 1.0                                     # 基数 1s×2(CFG llm.retry)
    for attempt in range(ctx.config.llm.retry.attempts):    # 默认 4
        BudgetGuard.check(ctx)                      # 每次重试前再查预算(防烧钱)
        try:
            return await adapters[name].chat(messages, tools, ctx=ctx)
        except PyHError as e:
            if e.code not in ("LLM-303", "LLM-301"): raise   # 302/304 不重试
            if attempt == ctx.config.llm.retry.attempts - 1:
                raise PyHError("LLM-303", ctx={"model": name,
                    "exhausted": True})             # 交 chat_with_fallback 降级决策
            await ctx.session.append("llm.retry", {"model": name,
                "attempt": attempt, "delay_ms": int(delay * 1000)}, actor="llm")
            await asyncio.sleep(delay * random.uniform(0.7, 1.3))   # 可被取消(F025)
            delay *= 2
```

**参数表**:`name`=适配器名(chain 元素)。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 退避 4 次耗尽 | LLM-303(exhausted) | 降级链切换下一适配器 |
| `asyncio.CancelledError` | 用户取消 | system.cancelled | 即停不重试;时长计入请求预算 |

**关联测试**:GWT-L4-03(429×3 后成功 → llm.retry 3 条且 delay 递增 1/2/4s±30%)、test_f028_retry。

### `def _may_degrade(e: PyHError, ctx) -> bool` — 降级条件判定(ADI §3.3 状态表)

**功能一句话**:按码判定——LLM-302 无条件降;LLM-303 已达 `rate_limit_consecutive`(默认 2)或 exhausted 降;LLM-304 永不降级。

```python
def _may_degrade(self, e, ctx):
    if e.code == "LLM-302":                         # 认证错:不重试直接降级
        return True
    if e.code == "LLM-303":                         # 429/5xx/断网/超时
        if "exhausted" in e.ctx: return True        # 退避耗尽(4 次)后降
        consec = ctx.counters.rate_limit_streak()   # 连续 429 计数(事件可重建)
        return consec >= ctx.config.llm.degrade.rate_limit_consecutive  # 默认 2
    return False                                    # LLM-301/304:301 已在退避内消化
```

**参数表**:`e`=适配器上抛的 PyHError。**异常表**:无(纯决策布尔)。**关联测试**:test_f013_fallback(2 次限流阈值触发降级,1 次不触发)、GWT-L4-02。

### `def _record_degradation(failed: str, to: str, ctx) -> None` — 降级留痕与告警(ADI §3.4)

**功能一句话**:每次实际降级计数;`chain.idx` 前移已在调用方完成,此处落告警与留痕——超过 `max_per_session`(默认 5)发 system.error 告警事件。

```python
def _record_degradation(self, failed, to, ctx):
    n = ctx.counters.degrade_add(failed, to)        # 会话级降级计数(可重建)
    if n > ctx.config.llm.degrade.max_per_session:  # 默认 5 次/会话
        bus.emit("system.error", {"code": "LLM-310",
            "advice": f"单会话降级 {n} 次,查备用模型配额/上游状态"})
    # llm.request 的 degraded_from 由下一次 chat() 落(见 llm.py);此处仅计数
```

**参数表**:`failed/to`=源/目标模型名。**异常表**:无(告警事件化不抛)。**关联测试**:test_f013_fallback(>5 次降级告警)、GWT-ERR-05。

### `class AdapterHealth` — 健康状态机(F033)

**功能一句话**:单适配器健康判定——`mark(ok)` 清 fail_streak 并累计 ok_streak(2 好回切);`mark(fail)` 累计 fail_streak(3 败 down);down 者被 pick 跳过。

```python
class AdapterHealth:
    def __init__(self):
        self.state = "healthy"; self.fail_streak = 0; self.ok_streak = 0
    def mark(self, ok: bool):
        if ok:
            self.fail_streak = 0; self.ok_streak += 1
            if self.state == "down" and self.ok_streak >= 2:   # 连 2 好→回切
                self.state = "healthy"; bus.emit("llm.recovered", {})
        else:
            self.ok_streak = 0; self.fail_streak += 1
            if self.state == "healthy" and self.fail_streak == 1: self.state = "degraded"
            if self.fail_streak >= 3: self.state = "down"      # 连 3 败,防抖动
    def is_down(self): return self.state == "down"
```

**参数表**:`mark(ok)` 输入=探针 ping 成功与否。**异常表**:无。**关联测试**:test_f033_probe(3 败 down/2 健回切/1 败仅 degraded)。

### `async def probe_loop(adapters, health, interval_s: float = 60.0)` — 周期健康探针(F033)

**功能一句话**:每 60s 对全部适配器 `ping()`(轻量延迟探测),失败 → `mark(False)`;探针成功不计 F029、失败不落 llm.retry;探针只判可达性,真实请求仍按错误码走降级。

```python
async def probe_loop(self, adapters, interval_s=60.0):
    while True:                                    # 进程生命周期协程(F051 job 内可停)
        for name, adp in adapters.items():
            try:
                lat = await asyncio.wait_for(adp.ping(), timeout=5.0)
                self.health[name].mark(True)       # 成功:2 好回切判定
            except PyHError:
                self.health[name].mark(False)      # 失败:streak 累计,不计 usage
        await asyncio.sleep(interval_s)            # 周期可配(CFG llm.probe.interval_s)
```

**参数表**:`adapters`=F030 注册表;`interval_s`=探针周期。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| 探针 ping 失败 | 网络/认证不可达 | 不落码(仅健康标记) | 3 败 down;恢复后 2 好回切 |

**关联测试**:test_f033_probe、GWT-S6-04 配套(回切后主模型恢复)。**边界**:探针 healthy 但真实请求仍 401 → 按 §3.3 正常降级路径,不因探针通过而跳过(ADI §3.5)。

### `def pick(self) -> str` — 适配器择优(F033)

**功能一句话**:返回 chain 中从 idx 起第一个非 down 适配器;主模型 down 不跳过(保底仍尝试,以测恢复),全部 down → 返回 idx 当前元素并交由降级流程暴露 LLM-310。

```python
def pick(self):
    for m in self.chain[self.idx:]:
        if not self.health[m].is_down(): return m  # 健康择优
    return self.chain[self.idx]                    # 全 down:仍走主,失败→LLM-310
```

**参数表**:无。**异常表**:无(不抛;决策留给 chat_with_fallback)。**关联测试**:test_f033_probe(pick 跳过 down)。

### `class BudgetGuard` — 单任务预算闸(CONSTRAINTS-07 §10 + F032 口径)

**功能一句话**:静态前置检查——读 `ctx.counters.task_total()`(report_usage 唯一写入、事件可重建)与 `ctx.config.budget.task.*`,返回 BudgetState;exhausted/paused 由 agent-loop 拦截强制终态。

```python
class BudgetGuard:
    @staticmethod
    def check(ctx) -> str:
        used = ctx.counters.task_total()           # 与 scope.budget_state 同一数据源
        lim = ctx.config.budget.task               # in≤200万/out≤5万/cost≤1元(F032)
        if (used.out_tokens >= lim.max_out_tokens
                or used.in_tokens >= lim.max_in_tokens
                or used.cost_est >= lim.max_cost_yuan):
            ctx.session.append("budget.paused", {"state": "exhausted",
                "used": used.model_dump()}, actor="system")
            raise PyHError("BUDGET-EXHAUSTED", ctx={"used": used.model_dump()})
        if used.out_tokens >= 0.8 * lim.max_out_tokens:   # warn=80% 提醒
            bus.emit("budget.warn", {"used_out": used.out_tokens}); return "warn"
        return "ok"
    @staticmethod
    def report(ctx) -> dict:                       # /cost 命令数据源(CONSTRAINTS-07 §9)
        used = ctx.counters.task_total(); lim = ctx.config.budget.task
        return {"used": used.model_dump(), "limit": lim.model_dump(),
                "pct_out": used.out_tokens / lim.max_out_tokens * 100}
```

**参数表**:`ctx`=会话实体(含 counters/config/session)。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError(BUDGET-EXHAUSTED)` | 输出/输入/成本任一超限 | budget.paused(exhausted 事件) | agent-loop 强制终态 reason=budget;人工续额后才可继续(F032) |
| — | warn(80%) | budget.warn 事件 | 提醒不拦截;后台 job 超预算直接杀死 |

**关联测试**:GWT-S6-02(90/100 → warn;+20 → exhausted + budget.paused 落盘)、test_f032_budget、CONSTRAINTS-07 §11 验收(超限中止)。

## 关联文档

| 文档 | 章节 | 关系 |
|---|---|---|
| PRD-Core.md | §5.2 F013、§5.3 F028/F032/F033 | 功能与验收权威 |
| DIS-CORE.md | §4.3.2/§4.3.3/§4.4/§4.6 | 降级/退避/状态机伪代码权威 |
| ADI.md | §3(触发/粒度/回切)、§6(重试幂等) | 协议侧判据唯一来源 |
| ERR.md | §2.4 LLM-310、§5.2 模型失败链 | 错误码权威(决策只看码) |
| CONSTRAINTS-07 | §1/§10/§11 | 成本上限与 BudgetGuard 验收 |
| llm.py.md | 本规格同族 | 请求/解析/计量实现单元 |
