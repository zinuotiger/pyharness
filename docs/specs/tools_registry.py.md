# specs/tools_registry.py.md — 编码规格

> **模块文件**:`pyharness/core/tools_registry.py` | **功能编号**:F008(工具注册表核心)· F026(schema 编译/先验后跑入口)· F003(与总线注册表联动) | **权威口径**:PRD-Core §5.2 F008/F026、DIS-CORE §7.2/§7.3.1(伪代码级)、DIS-SEAM §2.1(Definition 契约)/§2.4(announce 第③步);冲突以 PRD-Core 为准
> **一句话**:工具唯一登记处——`name → ToolDefinition(不可变契约) + 编译后 pydantic 模型 + Provider(可整体替换)`,负责注册查重(TLB-801/保留名 BUS-002)、给 LLM 的 schemas_for 列表(scope/agent 变体过滤)、以及参数强类型校验的模型供给(F026);本文件不执行 Provider、不过 guard(见 tools_executor.py.md/tools_guard.py.md)。
> **代码目录**:本文件 + tools_guard.py + tools_executor.py 三文件构成脊柱 tools 模块的拆分实现(PRD §2.6「代码目录草案,最终以 specs/ 为准」);本文件为契约侧总闸,执行管道见 tools_executor.py.md。

## 模块职责

1. **Definition 不可变登记(F008)**:注册 `ToolDefinition`(名称/描述/参数 schema/danger/guard_hooks/审批要求/timeout),注册后不可变(frozen);改工具 = 注销重注册留痕(原则 2)。重名 → TLB-801 拒绝,脊柱保留名 → BUS-002 拒绝,两者都不产生脏数据、不发 tool.registered。
2. **schema 编译期校验(F026)**:注册时把 `schema`(JSON-Schema dict)编译成 pydantic v2 模型并缓存——类型错误在注册期暴露(早失败),调用期 `validate_args` 只需 `model_validate`;schema 不可编译 → TLB-803。
3. **给 LLM 的列表(schemas_for)**:每轮 agent-loop 经 `ctx.tools.schemas_for(scope)` 取 OpenAI function 数组;按 **scope 可见性过滤**(deny_tools/strict 沙箱/critical 不可用者不下发,防 LLM 调用注定被拒的工具)+ **agent 变体 toolset 子集过滤**(子 agent F049 tools_subset);输出确定性排序(同输入同输出,可缓存可测)。
4. **Provider 绑定与替换(原则 2)**:注册契约与绑定实现分离——`register_tool(defn)` 入契约,`bind_provider(name, provider)` 装实现;Provider 可整体替换而不触碰 Consumer;执行方(executor)只经 `lookup_provider` 取实现,永不 import Provider 内部符号。
5. **查不到即结构化错误(TLB-802)**:lookup/get_model/validate_args 失败一律抛 `PyHError("TLB-802", …)` 由 executor 转 tool.error 回喂 LLM 自查(幻觉工具名/已卸载),不静默返回 None。
6. **留痕**:register → `tool.registered`(bus)+ registry.updated;unregister → registry.updated(op=del);运行中(有在途调用)不可注销 → BUSY(PyHError("BUSY", advice)),防卸载后仍被消费。

## 依赖

- **单向依赖(INV-08)**:本文件 → `events`(无)/`errors.raise_code`(F020)、`pydantic`(v2 model_validate);经 `bus.emit` 发 tool.registered/registry.updated;不 import tools_guard/tools_executor(避免环)。
- **消费方**:agent-loop(经 ctx.tools.schemas_for 取 tools 参数,F007)、tools_executor(execute 内部 lookup/get_model/validate_args/lookup_provider,DIS-CORE §7.3.2)、CapabilityHost.announce(能力 activate 第③步 `ctx.tools.register(defn)`,DIS-SEAM §2.4)、scope(F026 过滤数据源)。
- **外部依赖**:pydantic v2、`errors.PyHError/raise_code`;RESERVED 名称集合与 bus Registry(F003)共享口径。

## 数据结构表

### ToolDefinition(冻结 pydantic 模型;五要素 = name/description/schema/danger/执行函数载体 owner+provider)

| 字段 | 类型 | 必填 | 规则 |
|---|---|---|---|
| `name` | str | ✓ | 匹配 `^[a-z][a-z0-9_.]{1,63}$`;全库唯一;脊柱保留名拒绝(BUS-002) |
| `description` | str | ✓ | 给 LLM/人类,≤200 字;guard 判定不读描述文本(防描述注入,SECURITY §3.3) |
| `schema` | dict | ✓ | JSON-Schema(parameters);注册时编译 pydantic 模型(F026);禁止 pydantic 模型对象混入 |
| `output_schema` | dict? | — | 输出校验声明(executor 关4 用);缺省 = 仅做 spill/摘要截断 |
| `danger` | Literal[none,low,high,critical] | ✓ | 工具作者声明、注册后不可变;high→审批、critical→直接拒(不可审批,F014) |
| `guard_hooks` | tuple[str,…] | — | 追加 guard 逻辑名(如 "g-exec"),只加严;announce 时注册到 GuardChain 链尾 |
| `approval` | Literal[never,auto,always] | — | 默认 auto(=danger 分级裁决);never = 永不转审批(仅 low/none 可用,否则注册拒绝) |
| `timeout_s` | int | — | Provider 执行超时,默认 60(F017);executor 关3 使用 |
| `owner` | str | ✓ | 能力/插件 id 或 "builtin"/"spine";uninstall 按 owner 摘除 |
| `ctx_path` | str? | — | 定位器键(如 tools.fs.read);None = 仅注册不挂 ctx(DIS-SEAM §3.2) |
| `version` | str | — | 能力版本,默认 1.0.0 |

**内部索引**(ToolRegistry):`_tools: dict[str, ToolDefinition]`(契约)、`_models: dict[str, type[BaseModel]]`(注册时编译缓存)、`_providers: dict[str, Provider]`(实现)、`_running: set[str]`(在途调用名,注销闸用)、`RESERVED: frozenset` = {"agent-loop","tools","session","llm","system-prompt","scope","agent","persistence"}。

### DangerLevel 处置口径(SECURITY §4.2,权威)

| 级别 | 处置 | 判定原则 | 示例 |
|---|---|---|---|
| none | 直接执行 | 无副作用或 workspace 内可逆 | fs.list/todo/FTS 查询 |
| low | 直接执行 | 副作用在影响域内低风险 | workspace 内新建/读 |
| high | 人类审批 | 不可逆/影响域外/影响他方 | 覆写已有文件、subprocess(g-exec 需 scope 授权) |
| critical | 直接拒绝,不可审批 | 破坏性不可逆或影响全局 | fs.delete_file 类;strict 下 deny_tools 追加项 |

## 类与函数清单

### `def register_tool(defn: ToolDefinition, *, provider: Provider | None = None) -> str` — 工具登记入口(F008)

**功能**:查重(重名/保留名/非法名)→ schema 编译(pydantic,早失败)→ 入表 → 绑 Provider → 事件留痕;返回工具名。注册后 Definition 与模型均不可变。

```python
def register_tool(self, defn, *, provider=None):
    if defn.name in self._tools or defn.name in self.RESERVED:   # 重名/保留名
        raise PyHError("TLB-801", ctx={"tool": defn.name,
            "advice": "重名或脊柱保留名(BUS-002);注销旧定义后重注册"})
    if not NAME_RE.match(defn.name):                              # 非法名
        raise PyHError("TLB-801", ctx={"tool": defn.name, "rule": NAME_RE.pattern})
    if defn.approval == "never" and defn.danger not in ("none", "low"):
        raise PyHError("TLB-801", ctx={"tool": defn.name,          # 矛盾契约:拒
            "why": "approval=never 只允许 none/low"})
    try:
        model = pydantic_model_from_json_schema(defn.schema)      # F026 编译期校验
    except Exception as e:
        raise PyHError("TLB-803", ctx={"tool": defn.name, "why": "schema 不可编译"})
    self._tools[defn.name] = defn          # Definition frozen,入表即不可变
    self._models[defn.name] = model
    if provider is not None: self._providers[defn.name] = provider
    bus.emit("tool.registered", {"name": defn.name,
        "danger": defn.danger, "owner": defn.owner, "hooks": list(defn.guard_hooks)})
    bus.emit("registry.updated", {"op": "add", "kind": "tool", "key": defn.name})
    return defn.name
```

**参数表**:`defn` = 能力包 definition.py 导出的冻结 Definition;`provider` = 可调用实现(handle(typed_args, ctx),async 或同步,同步由 executor 线程池化)。**返回**:注册成功的工具名。

**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 重名/保留名/非法名/矛盾契约 | TLB-801(保留名语义 BUS-002) | 注销旧定义重注册;换名 |
| `PyHError` | schema 不可编译 | TLB-803 | 修 JSON-Schema 后重注册 |
| `PyHError` | 注册被拒后 | 无脏数据 | 注册表不变、无 tool.registered(测试钉死,GWT-T7-01) |

**关联测试**:GWT-T7-01(重名/非法名/坏 schema → TLB-801/801/803,注册表无脏数据,tool.registered 未发)。

### `def validate_args(name: str, raw_args: dict) -> dict` — 参数强类型校验(F026 调用期入口)

**功能**:`raw_args`(模型原话)→ 编译模型 `model_validate` 强类型转换,失败抛 TLB-803(附 summarize_validation 明细)回喂 LLM 修正;**绝不执行 Provider(INV-06)**——本函数是"先验后跑"的物理闸门,防 `{"path": 123}` 被 `str(123)` 隐式转换执行。

```python
def validate_args(self, name, raw_args):
    model = self._models.get(name)                      # 注册时编译好的模型
    if model is None:                                   # 契约不存在(或已卸载)
        raise PyHError("TLB-802", ctx={"tool": name,
            "advice": "工具不存在或不可用;查注册表与拼写"})
    try:
        typed = model.model_validate(raw_args)          # strict:多余字段默认拒绝
        return typed.model_dump()
    except ValidationError as e:
        raise PyHError("TLB-803", ctx={"tool": name,
            "detail": summarize_validation(e),          # 字段级明细,回喂可行动
            "advice": "按明细修正参数;未执行任何操作"})
```

**参数表**:`name` = 注册工具名;`raw_args` = LLM tool_calls 原始参数 dict(与 args 双份存档,INV-06)。**返回**:强类型化后的参数字典(真实函数收到的 == 日志 args)。**异常表**:TLB-802(未注册)、TLB-803(校验失败,零执行)。**关联测试**:GWT-ERR-03/T-SEC-08(30 例畸形参数每例 TLB-803、真实函数零调用、连败 2 次终止轮)。

### `def schemas_for(scope, *, toolset: set[str] | None = None) -> list[dict]` — 给 LLM 的 tools 数组(F007 每轮取用)

**功能**:遍历注册表,筛掉 `scope.can_use(name) == False`(deny/strict/critical 不可见者不下发)与 toolset 子集外工具(子 agent 变体 F049),转 OpenAI function schema,按名排序后返回——**LLM 拿到的工具列表 = 当前 scope/变体下注定能过 scope 关的集合**,减少无效调用。

```python
def schemas_for(self, scope, *, toolset=None):
    out = []
    for name in sorted(self._tools):                    # 确定性排序(可缓存可测)
        if not scope.can_use(name): continue            # scope 不可见→不下发
        if toolset is not None and name not in toolset: # agent 变体子集(子 agent)
            continue
        out.append(self.to_openai_schema(self._tools[name]))
    return out                                          # [{type:function,function:{...}}]
```

**参数表**:`scope` = 会话作用域(§6 scope 模块);`toolset` = 变体允许集(None = 全量可见)。**返回**:OpenAI-compatible function 数组(含 name/description/parameters)。**异常表**:无。**关联测试**:scope 过滤例(GWT-S6-03 联动:strict 下 delete_file 既被 scope 拒也不出现在列表)。

### `def unregister(name: str) -> None` — 注销(卸插件用,F004 detach 第③步)

**功能**:摘除契约/模型/Provider 三索引并留痕;在途调用中的工具拒绝注销(BUSY),防"执行到一半 Provider 没了"。

```python
def unregister(self, name):
    if name not in self._tools:
        raise PyHError("TLB-802", ctx={"tool": name, "advice": "不存在,无需注销"})
    if name in self._running:                           # 在途调用闸
        raise PyHError("BUSY", ctx={"tool": name, "advice": "有在途调用,稍后重试"})
    del self._tools[name]; del self._models[name]
    self._providers.pop(name, None)
    bus.emit("registry.updated", {"op": "del", "kind": "tool", "key": name})
```

**参数表**:`name`。**异常表**:TLB-802(不存在)、BUSY(在途)。**关联测试**:F004 卸载摘净例(卸载后 lookup → TLB-802)。

### 其余函数速览(签名 + 一句话 + 关联)

| 函数 | 功能一句话 | 备注 |
|---|---|---|
| `def lookup(name) -> ToolDefinition` | 查契约;不存在抛 TLB-802 | executor 关1 用(DIS-CORE §7.3.2) |
| `def lookup_provider(name) -> Provider` | 查实现;未绑定/不存在抛 TLB-802 | Provider 可整体替换,Consumer 不 import |
| `def bind_provider(name, provider) -> None` | 契约已注册后补绑/换绑实现;不存在 TLB-802 | 换 Provider 不碰 Consumer(seam 测试) |
| `def get_model(name) -> type[BaseModel]` | 取注册时编译的 pydantic 模型 | validate_args 内部用 |
| `def has(name) -> bool` | 契约是否存在 | 防抖查询,不抛错 |
| `def names() -> list[str]` | 全部已注册工具名(排序) | 健康摘要/审计 |
| `def iter_definitions() -> Iterator[ToolDefinition]` | 只读遍历契约 | 禁修改(frozen) |
| `def count() -> int` | 注册工具数 | 健康摘要 N 能力 |
| `def to_openai_schema(defn) -> dict` | Definition → `{type:function,…}`(描述/参数) | 不泄露 danger/guard_hooks 给 LLM |
| `def snapshot() -> dict` | 名称+版本+danger 只读快照 | fork/审计/自检 F031 用 |
| `def mark_running(name)/mark_idle(name)` | 在途计数(注销闸) | executor 关3 前后调用 |
| `def _validate_contract(defn) -> None` | 内部:名称/approval×danger/owner 一致性 | register_tool 前置 |

**关联测试(模块级)**:GWT-T7-02(合法工具全链执行,Provider 实参 == 日志 args)、换 Provider 不碰 Consumer 例、Definition 不可变例(改 defn.danger → TypeError(frozen))、INV-06(执行 args=日志 args)。

## 关联文档

1. PRD-Core.md §5.2 F008(注册表/不可变)/F026(先验后跑)——功能唯一权威。
2. DIS-CORE.md §7.2/§7.3.1(ToolDefinition 字段与 register_tool 伪代码)、§7.7(GWT-T7-01/02)。
3. DIS-SEAM.md §2.1/§2.4(Definition frozen 契约、announce 第③步 register)、§3.2(registry key 口径)。
4. SECURITY.md §4.2(危险分级处置表,L0-L4 策略分层)、ERR.md §2.9(TLB-801/802/803 语义)、EVENT-SCHEMA.md(tool.registered/registry.updated)。
5. 验收测试:tests/acceptance/test_f008_tool_registry.py、test_f026_arg_validation.py、tests/invariants/(INV-06)。
