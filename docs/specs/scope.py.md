# specs/scope.py.md — 编码规格

> **模块文件**:`pyharness/core/scope.py` | **功能编号**:F014(scope 前置)· F032(预算硬闸)· F021(配置)· F054/F055(沙箱/workspace 联动)· F059(fork 快照继承) | **权威口径**:PRD-Core §5.2 F014/F021/F032、§5.6 F054/F055、§5.5 F059(功能权威)、DIS-CORE §6(can_use/budget_state/build_scope 伪代码权威)、ERR.md §2.5(GRD-401)/§2.7(CFG-601)、EVENT-SCHEMA(budget.paused/scope.updated)
> **一句话**:会话级策略作用域——权限边界(deny/危险标记/域名 allowlist)、预算硬闸(F032)、上下文窗口、workspace 根,是 guard 单调拒绝链(原则 3)与 LLM 零信任预算闸的"策略唯一数据源";scope 创建/绑定/遮蔽层/预算联动/注册隔离全部在此单点实现,策略只紧不松(单调)。

## 模块职责

1. **作用域创建与绑定**:`build_scope(cfg, session_id)` 从 config 编译默认策略(**最小权限**);绑定=每会话恰一 Scope(注册进会话作用域表);配置越权(deny 空/域名通配过宽)→ CFG-601 列非法字段,拒绝启动会话。
2. **注册隔离**:scope 属脊柱八模块保留名(F003 RESERVED)——插件不可注册/卸载同名能力(BUS-002);会话作用域表按 session_id 一对一注册,重复 build 同一会话 → 拒绝;仅 `release()` 可解绑(会话关闭/异常)。
3. **遮蔽层(fork/子会话)**:`from_snapshot(parent_snap, session_id)` 生成遮蔽子作用域——继承父策略快照后**独立演进,只紧不松**;子层 `tighten` 只追加本层 deny,不透写父层(F059:主会话后续收紧不影响子会话)。
4. **guard 单调链的 scope 前置**(F014):`can_use(tool_name)` 布尔判定——deny 表/strict 沙箱域外/critical 危险标记任一命中 → False;调用方(工具管道)在 False 时写 `guard.rejected(GRD-401, scope-hidden)` 强同步,Provider 零执行(INV-05)。
5. **预算联动**(F032):`budget_state()` 读 `UsageCounters`(report_usage 唯一写入、事件可重建)判 ok/warn/exhausted;warn(80%)发 budget.warn;paused/exhausted 落 budget.paused 事件并由 agent-loop 拦截强制终态——预算本身不存第二份状态(INV-01)。
6. **单调收紧与快照**:`tighten(deny)` 只追加(写 scope.updated 事件,无 relax/un-tighten API——结构上不可能放宽);`snapshot()` 出不可变只读快照供 fork/审批复核/审计;会话结束 `release()` 清空内存策略引用(凭据永远不落 scope 结构,INV-09)。

## 依赖

- **依赖方向**(§0.3 拓扑):`scope` 无下游脊柱依赖(纯策略容器);被 `agent-loop`(预算拦截)、`agent`(ctx.scope 挂载)、`llm`(预算联动读数)、`tools`(guard 前置)、`system-prompt`(护栏/窗口数据源)只读消费。
- **消费方**:guard 链(F014 evaluate 首查 can_use)、llm_fallback.BudgetGuard(预算前置,同数据源)、agent-loop._must_stop(exhausted 终态)。
- 外部依赖:`errors.raise_code`(F020)、config(budget.task.*/scope 编译项)、事件注册表(budget.paused/scope.updated);计数器对象由 llm 模块注入(ctx.counters),本模块只读。

## 数据结构表

| 字段 | 类型 | 规则 |
|---|---|---|
| `ScopePolicy` | dataclass | `role/deny_tools[]/danger_marks[]/allowed_domains[]/workspace_root/sandbox_level`;从 config+会话编译;fork 继承快照(F059) |
| `BudgetLimits` | dataclass | `in≤2_000_000 / out≤50_000 tokens / cost≤1 元`;warn=80%(F032 默认,全进 config) |
| `BudgetState` | Literal | `ok/warn/paused/exhausted`;warn 提醒、paused 等审批、exhausted 终态 |
| `ScopeSnapshot` | frozen dataclass | 全策略只读快照;fork/审批复核/审计用;不可变 |
| `window_tokens` | int = 64_000 | 上下文窗口(system-prompt 预算来源) |
| `SessionScopeTable` | dict[str, Scope] | session_id → Scope 一对一注册;spine 保留名注册/卸载 → BUS-002 |
| `danger 分级` | Literal | `none/low/high/critical`;high→审批、critical 本层直接不可用(不可审批) |

## 类与函数清单

### `def build_scope(cfg: Settings, session_id: str) -> Scope` — 作用域创建与绑定(F021/DIS §6.3.3)

**功能一句话**:编译默认最小权限策略并注册绑定到会话;越权配置(deny 空/域名通配过宽/重复注册)→ CFG-601 拒启动;返回已绑定 Scope。

```python
def build_scope(cfg, session_id):
    if session_id in _scopes:                        # 每会话恰一 scope(注册隔离)
        raise PyHError("BUSY", ctx={"session_id": session_id,
            "advice": "该会话已有活动 scope;重复 create active scope 被拒"})
    p = ScopePolicy(
        role=cfg.scope.role,                         # 会话角色(提示词变量源)
        deny_tools=set(cfg.scope.deny_tools or []),  # 空 deny=越权(默认禁高危组)
        danger_marks=load_danger_marks(cfg),         # 内置危险分级表(F023 同源)
        allowed_domains=set(cfg.scope.allowed_domains or []),
        workspace_root=cfg.scope.workspace_root or fresh_workspace(session_id),  # F055
        sandbox_level=cfg.scope.sandbox_level or "strict")   # 默认 strict(F054)
    _validate_minimal(p)                             # deny 空且非显式豁免→CFG-601
    s = Scope(policy=p, limits=BudgetLimits.from_cfg(cfg), session_id=session_id)
    _scopes[session_id] = s                          # 注册(绑定);scope.updated 留痕
    return s
```

**参数表**:`cfg`=分层配置(scope.* 域);`session_id`=会话 UUID。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | deny 空/域名通配过宽/重复绑定 | CFG-601 | 拒启动,列非法字段;修配置 |
| `PyHError` | 同会话二次 build | BUSY | 用既有 scope;查重复 create 路径 |

**关联测试**:GWT-S6-01 前置(默认最小权限)、test_f032_scope_budget、CFG-601 用例(越权拒绝)。

### `def from_snapshot(snap: ScopeSnapshot, session_id: str) -> Scope` — 遮蔽层创建(F059 fork 继承)

**功能一句话**:子会话遮蔽作用域——从父快照拷贝策略独立演进;子层收紧只影响本层,父会话后续 tighten 不影响子会话;遮蔽=叠加收紧,无透写。

```python
def from_snapshot(snap, session_id):
    if session_id in _scopes:
        raise PyHError("BUSY", ctx={"session_id": session_id})
    p = ScopePolicy(**snap.policy.model_dump())      # 深拷贝策略(COW 起点)
    s = Scope(policy=p, limits=BudgetLimits(**snap.limits.model_dump()),
              session_id=session_id, parent_snap=snap)   # 遮蔽层记父快照
    _scopes[session_id] = s                          # 独立注册(隔离于父表)
    return s
```

**参数表**:`snap`=父会话 snapshot()(fork 时,PRD F059);`session_id`=子会话新 id。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 子会话已存在 scope | BUSY | 查重复 fork 路径 |

**关联测试**:GWT-S6-04(子会话策略=主快照;主会话后续 tighten 不影响子会话)。

### `def can_use(self, tool_name: str) -> bool` — scope 前置查权(F014/DIS §6.3.1)

**功能一句话**:guard 单调链第一个检查点——deny 表/strict 沙箱域外/critical 任一命中 → False;False 时调用方写 guard.rejected(GRD-401,scope-hidden) 强同步,Provider 零执行。

```python
def can_use(self, tool_name):
    if tool_name in self.policy.deny_tools:          # 显式禁止(strict 下 fs.delete_file)
        return False
    if (self.policy.sandbox_level == "strict"
            and not self._inside_workspace_domain(tool_name)):
        return False                                 # strict:文件类仅限 workspace 域
    if self._danger_mark(tool_name) == "critical":
        return False                                 # critical 不可审批,直接不可用
    return True                                      # 其余交 guard 链继续判定(F014)
```

**参数表**:`tool_name`=注册工具名(registry 查得到才进此处)。**异常表**:无(纯策略布尔;拒绝由调用方以 GRD-401 事件化)。**边界**:high 危险工具此处放行,**转审批**(F015);critical 本层即终局(不可审批,§4.3)。**关联测试**:GWT-S6-03(can_use=False → guard.rejected 强同步 + Provider 零执行 INV-05)。

### `def tighten(self, deny: set[str], *, reason: str) -> None` — 单调收紧(原则 3 作用域来源)

**功能一句话**:策略只紧不松——追加 deny(或把危险标记降级),写 scope.updated 事件;**无 relax/un-tighten API**,放宽在结构上不可能。

```python
def tighten(self, deny, *, reason):
    if deny <= self.policy.deny_tools:               # 无新增→幂等返回(仍留痕?)
        return                                       # 不产生事件(防刷日志)
    self.policy.deny_tools |= deny                   # 只追加(单调)
    ctx.session.append("scope.updated",              # 收紧留痕可审计
        {"op": "tighten", "added": sorted(deny), "reason": reason}, actor="system")
    # 注意:不存在 self.relax(...)/un_tighten(...) 方法——测试断言 AttributeError
```

**参数表**:`deny`=新增禁止工具集合;`reason`=收紧原因(sandbox strict/guard 拒绝等)。**异常表**:无(状态迁移事件化;非法放宽=无 API,结构免疫)。**关联测试**:GWT-S6-01(尝试去掉 a → AttributeError;deny 只增不减)、INV-05 配套。

### `def budget_state(self) -> BudgetState` — 预算硬闸(F032/DIS §6.3.2)

**功能一句话**:读计数器判 ok/warn/exhausted;硬闸只用 token 数(out/in/cost 任一超即 exhausted);状态迁移事件化,agent-loop 对 exhausted 强制终态。

```python
def budget_state(self):
    used = ctx.counters.task_total()                 # llm.usage 事件聚合(可重建 INV-01)
    lim = self.limits
    if (used.out_tokens >= lim.out_tokens or used.in_tokens >= lim.in_tokens
            or used.cost_est >= lim.cost_yuan):      # 硬闸:token 数为主(F032)
        if self._budget_state != "exhausted":        # 首达才落事件(防刷)
            ctx.session.append("budget.paused",
                {"state": "exhausted", "used": used.model_dump()}, actor="system")
        self._budget_state = "exhausted"; return "exhausted"
    if used.out_tokens >= 0.8 * lim.out_tokens:      # warn=80%(只提醒不拦)
        if self._budget_state != "warn":
            bus.emit("budget.warn", {"used_out": used.out_tokens})
        self._budget_state = "warn"; return "warn"
    self._budget_state = "ok"; return "ok"
```

**参数表**:无;返回 ok/warn/paused/exhausted(调用方对 exhausted/paused 强制终态 reason=budget)。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| 无(状态化) | out/in/cost 超限 | budget.paused(exhausted) | agent-loop 强制终态;人工续额(F032 paused) |
| 无 | 达 80% | budget.warn | 提醒;后台 job 超预算直接杀死 |

**关联测试**:GWT-S6-02(90/100 → warn;+20 → exhausted + budget.paused 落盘;agent-loop 下一轮前拦截)。

### `def snapshot(self) -> ScopeSnapshot` — 只读快照

**功能一句话**:全策略不可变快照;fork/审批复核/审计读取;快照不随原 scope 后续 tighten 变化。

```python
def snapshot(self):
    return ScopeSnapshot(
        policy=ScopePolicy(**self.policy.model_dump()),   # 值拷贝
        limits=BudgetLimits(**self.limits.model_dump()),
        window_tokens=self.window_tokens,
        taken_at=now_iso())                          # 审计时间戳
    # ScopeSnapshot frozen=True:任何字段赋值→FrozenInstanceError(结构不可变)
```

**参数表**:无。**异常表**:无。**关联测试**:GWT-S6-04(fork 时快照继承)、test_f059_fork(COW 独立演进)。

### `def within_window(self, hist_tokens: int) -> bool` — 窗口余量判定(system-prompt 联动)

**功能一句话**:供 F058 压缩触发与 system-prompt 装配复用——返回历史是否在窗口内(余量 ≥25% 为 True);与 window_tokens 单一数据源联动。

```python
def within_window(self, hist_tokens):
    return hist_tokens < 0.75 * self.window_tokens   # 阈值 75% 与 F058 同源(可配)
```

**参数表**:`hist_tokens`=派生历史 token 估算累计。**异常表**:无。**关联测试**:test_f058_compaction(触发条件与本函数一致)、GWT-S5-04 配套。

### `def release(self) -> None` — 作用域解绑(会话收尾)

**功能一句话**:会话关闭/异常时解绑:清内存策略引用、从会话作用域表摘除;凭据不落 scope 结构(INV-09),进程退出即失。

```python
def release(self):
    sid = self.session_id
    if sid in _scopes and _scopes[sid] is self:      # 防误释放他 scope
        del _scopes[sid]                             # 解绑(注册隔离收尾)
    self.policy = None; self.limits = None           # 引用清空(GC 可回收)
    # 凭据从未进入 ScopePolicy(读取走 F016 单口);此处无脱敏负担(INV-09)
```

**参数表**:无。**异常表**:无(幂等:重复 release 无害)。**关联测试**:test_f032_scope_budget(释放后查询拒)、INV-09(日志无凭据)。

### `def _danger_mark(self, tool_name: str) -> str` — 危险分级查表(内部)

**功能一句话**:查内置分级表 none/low/high/critical;表与 F023 guard 同源加载;high→审批、critical→can_use False。

```python
def _danger_mark(self, tool_name):
    for rule in self.policy.danger_marks:            # 前缀规则:fs.*/exec.*/net.* …
        if fnmatch(tool_name, rule["pattern"]):
            return rule["level"]                     # critical 直接不可用(6.3.1)
    return "none"
```

**参数表**:`tool_name`=工具名。**异常表**:无。**关联测试**:test_f023_builtin_guards(fs.delete_file=critical 命中例)、GWT-S6-03 配套。

## 关联文档

| 文档 | 章节 | 关系 |
|---|---|---|
| PRD-Core.md | §5.2 F014/F021/F032、§5.5 F059、§5.6 F054/F055 | 功能与验收权威 |
| DIS-CORE.md | §6 全节(6.3.1-6.3.3/状态机) | 伪代码级权威(本文件落地) |
| ERR.md | §2.5 GRD-401、§2.7 CFG-601 | 错误码(scope-hidden/POL-* 引用) |
| system_prompt.py.md | 本规格同族 | 护栏段/窗口数据源消费方 |
| llm_fallback.py.md | 本规格同族 | BudgetGuard 与 budget_state 同数据源 |
| EVENT-SCHEMA.md | budget.paused/scope.updated | 事件字段权威 |
