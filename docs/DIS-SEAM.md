# DIS-SEAM.md — 能力 seam × 插件总线 × ctx 服务定位器:伪代码级实现规格

> **定位**:PyHarness(Python 3.11 复刻 DSH 架构思想)的**能力 seam 三件套 + 自研插件总线 + ctx.\* 服务定位器**实现规格——把 PRD-Core §2.3(能力 seam 与 ctx.\*)、§4.2/§4.3(原则 2/3)、§5.1(F001-F006 总线)、F008/F014/F015/F016/F039 从"要什么"展开到"代码长什么样"。**本文可被 AI 编码 Agent 直接照着实现,不读其他文档也能写对 seam 相关代码。**
> **权威声明**:本文是**规格展开**,不引入新架构。凡功能编号(F001-F066)、事件词汇、错误码前缀、模块职责、依赖方向与 PRD-Core.md 冲突,**一律以 PRD-Core.md 为准**;ADD.md 的 ADR 若冲突亦同。本文新增的实现级错误码(GRD-4xx/APR-5xx 细目)与 guard 逻辑名(g1-g7)是对既有域代码的**细化建议**,正式注册进 F019 错误码表时不得改前缀域与含义。
> **关联文档**:PRD-Core.md(权威)· ADD.md(ADR-002/003/004)· MAP.md(四关图/依赖图)· TECH-ANCHOR.md(禁止表)
> **读者**:写 `pyharness/bus/`、`core/tools.py`、`capabilities/` 的开发者;面试讲"可插拔能力"用 §2+§4、"安全单调"用 §5、"40+ 服务"用 §3。

**8 章**:0 阅读约定 · 1 全景:两条正交的缝 · 2 seam 三件套通用实现模式 · 3 ctx.\* 服务定位器 · 4 自研插件总线与事件分发 · 5 guard 单调拒绝链 · 6 三个代表性 seam 深挖(spill/approval/credentials)· 7 模式复用盘点与验证门禁

---

# 0 阅读约定

- **对象注入**:与 PRD §5.0 一致,伪代码里 `ctx/bus/session/log/registry` 是全局注入对象:`ctx`=会话实体门面(脊柱 agent 模块创建,§3 展开);`bus`=进程内唯一事件通道(F001);`session`=事件日志门面(唯一写入口 append,F009);`registry`=三类索引(F003);`log`=本地调试日志(不进会话真源)。
- **伪代码纪律**:Python 风格规格语言,中文注释;类型标注与 ABC/Protocol 仅为说明意图,实现允许等价写法;`async def` 表示可挂起;同步 handler 由总线层用 `asyncio.to_thread` 隔离(F017 超时同源)。**禁止 TS**:任何示例不得出现 `interface/type X =/=>` 等 TS 语法。
- **术语先记**:
  | 词 | 含义 | 出处 |
  |---|---|---|
  | seam(能力接缝) | Definition+Provider+Consumer 三件套的接缝结构,换实现不碰消费方 | PRD §2.3/§4.2 |
  | 脊柱 8 模块 | agent-loop/agent/session/llm/system-prompt/scope/tools/persistence,注册表保留名 | PRD §2.2 |
  | 外围能力 | 挂 ctx.\* 的可插拔能力,每能力一包,Definition 自描述 | PRD §2.3 |
  | 脊柱子系统 | guard/approval/credentials 等阶段1 核心服务:启动硬接线、不注册为可卸载插件,但内部仍按 seam 三件套组织 | 本文 §2.1 |
  | 单调拒绝 | guard 只收严:任一 reject 即终局,无 allow 开关、审批后重入链起点 | PRD 原则 3 |
  | announce | 能力进入 active 前的"对外宣布"阶段:注册事件类型/挂订阅/发布 schema 到 tools 注册表 | 本文 §2.4(DSH capability 生命周期等价物) |
  | 强同步 | user.message/guard.rejected/approval.\* 三类事件立即落盘+flush,成功才返回 | PRD §3.6 |
  | 投影 | 由事件日志派生的视图(消息历史/UI/FTS),禁存第二份 | PRD §3.1 |

---

# 1 全景:两条正交的缝

## 1.1 一句话

**插件总线是"事件怎么在模块间流动"的通信地基(阶段0,自研 Cordis 等价物);能力 seam 是"每个可选能力以什么形状被声明/实现/消费"的接缝协议(ADR-002);ctx.\* 是"40+ 服务以什么名字、何时被找到"的消费端门面(PRD §2.3)。总线管流动,seam 管形状,ctx 管寻址——三者正交,拼起来就是"一切皆插件"。**

```
消费端:agent-loop / 结果检查器 / UI(只认 Definition,经 ctx.* 取服务,永不 import Provider)
  ↑ ctx.* 服务定位器(§3):统一命名空间门面;脊柱服务 eager,能力服务 lazy
  ↑ seam 三件套(§2):Definition ↔ Provider ↔ Consumer;生命周期 enter→announce→detach
  ↑ 自研插件总线(§4):emit/subscribe(sequential/waterfall/parallel)+ Registry + 热插拔
只中转不落盘;落盘 = 日志订阅者(强同步三类)
```

## 1.2 三件事分别解决什么问题

| 机制 | 不做的后果(场景) | 对应裁决 |
|---|---|---|
| seam 三件套 | 揉成单一 Tool 类:加工具改核心、换实现改全部调用点、契约不可审计 | ADR-002 |
| 自研总线 | 用 pluggy:机制黑盒讲不深;硬编码装配:无热卸载、新能力改 core | ADR-004 |
| ctx.\* 定位器 | 各自全局单例:依赖隐式、难替换、脊柱被外围 import 污染 | PRD §2.3 |
| guard 单调 | 存在 allow 开关:安全强度=最松一环,审批即放行 | ADR-003 |

## 1.3 与兄弟文档的分工

| 文档 | 管什么 | 本文补什么 |
|---|---|---|
| PRD §2.3 / §4.2 | seam 概念 + ctx 六命名空间 | 基类/注册/发现/生命周期的代码形状 |
| PRD §5.1(F001-F006) | 总线功能项 | dispatch 三模式表、装载顺序细节 |
| PRD F014/F015/F016/F039 | 单项规格 | 按 seam 解剖,九段式模板 |
| MAP §4 | 四关链路图 | g1-g7 各 guard 的 match/check |
| ADD ADR-002/004 | 为什么 | 怎么(冲突以 PRD 为准) |

## 1.4 最小类图(单进程,无网络边界)

代码落点:`bus/capability.py`(Definition/Provider 协议/CapabilityHost)、`bus/event_bus.py`(EventBus+三种分发)、`bus/registry.py`、`bus/lifecycle.py`(五态机);`core/tools.py`(ToolExecutor 统一 Consumer)、`core/{guard,scope,approval,credentials}.py`、`core/session.py`(append 唯一写入口);`capabilities/<能力包>/{definition.py,provider.py,__init__.py(export DEFINITION,PROVIDER)}`。类关系见图 §1.1 与各章代码。

---

# 2 seam 三件套通用实现模式(模式层,所有能力共用)

> 本章给"三件套长什么样"的**唯一答案**;§6 用三个深例证明可复用,§7 给其余能力的映射。代码位置 `pyharness/bus/capability.py`(模式)+ 各能力包(实例)。

## 2.1 Definition:不可变契约(是什么)

**职责一句话**:Definition 是能力对外的唯一事实来源——给 LLM 看的 schema、给校验器用的类型、给 guard 判危险的标记、给审计对比的基准,**是同一份对象**,注册后不可变。

```python
# pyharness/bus/capability.py
@dataclass(frozen=True)@dataclass(frozen=True)                       # frozen = 注册后不可变(PRD §4.2)
class CapabilityDefinition:
    name: str                                 # 唯一 ID;匹配 ^[a-z][a-z0-9_.]{1,63}$
    namespace: Literal["tools","agent","session","storage","sys","ui","system"]
    description: str                          # 给 LLM/人类 ≤200 字
    schema: type[BaseModel]                   # pydantic 模型,注册时编译
    danger: Literal["none","low","high","critical"] = "none"
    expose_to_llm: bool = False               # True → announce 进 tools 注册表
    guard_hooks: tuple[str, ...] = ()         # 追加 guard,只加严(§5.2)
    approval: Literal["never","auto","always"] = "auto"
    owner: str = "builtin"                    # 插件 id / "builtin" / "spine"
    version: str = "1.0.0"
    ctx_path: Optional[str] = None            # 定位器键,如 "tools.fs.read"
    subscriptions: tuple[tuple[str, str], ...] = ()   # (事件类型, 处理方法)
    cost_hint: Literal["free","cheap","expensive"] = "free"
```
```

**字段表(实现核对用)**:name 全库唯一`name` 全库唯一(保留名→BUS-002,重名→TLB-801);`namespace`∈六命名空间(system 仅供脊柱子系统);`schema` 注册时编译、调用时强校验(F026);`danger`: high→审批、critical→直接拒;`guard_hooks` 只加严;`ctx_path`=定位器注册键(None=仅注册不挂 ctx);`subscriptions` 随 announce/detach 挂摘(按 owner)。

## 2.2 Provider:实现与装载生命周期(怎么实现)

**职责一句话**:Provider 是能力的真实实现与装载钩子——只做"被调用时干活、enter/announce/detach 时准备与收拾",**绝不携带**校验/guard/审批/计量逻辑(那些在消费协议里,§2.3)。

```python
# pyharness/bus/capability.py(续)
from typing import Protocol, runtime_checkable

@runtime_checkable
class CapabilityProvider(Protocol):
    """能力实现的装载协议。enter→announce→detach 由 CapabilityHost 驱动(§2.4)。"""
    async def enter(self, ctx) -> None:        # 装载:打开资源、构造内部状态(可空实现)
        ...
    async def announce(self, ctx) -> None:     # 对外宣布:见 §2.4;多数能力空实现
        ...
    async def detach(self, ctx) -> None:       # 卸载:释放资源、摘订阅;必须幂等
        ...

# —— 工具型 Provider 的推荐形状(实现模板)——
class FsReadProvider:
    """cap:fs.read 的实现。消费协议由 ToolExecutor 统一执行,这里只写 IO。"""
    async def enter(self, ctx): ...                        # 预检:workspace 根已就绪?
    async def announce(self, ctx): ...                     # 空:工具注册由 host 代做
    async def handle(self, args: FsReadArgs, ctx) -> dict: # 真实逻辑,参数已强类型化
        path = ctx.sys.workspace.resolve_in(args.path)     # 单点边界(F055,防逃逸)
        return {"content": path.read_text(encoding="utf-8"),
                "chars": path.stat().st_size}
    async def detach(self, ctx): ...                       # 无状态,空
```

**实现纪律(违反 = 评审不过)**:①Provider 禁止 import 上层 Consumer(脊柱 tools/agent-loop),只能经注入 ctx 反向调用(grep `capabilities/` 无 `from pyharness.core... import`);②禁止自校验参数/自判危险/自请求审批——重复实现=guard 漏挂(INV-04:无 guard.evaluated 的执行非法);③同步 handler 由执行层 `asyncio.to_thread` 跑线程池(§2.5.2),Provider 不开线程;④Provider 内部调低层危险动作仍须显式 `ctx.guard.evaluate` 复查(F052 样例)——guard 复查是义务,不是替 Consumer 做决定。

## 2.3 Consumer:统一消费协议(谁来调)

**职责一句话**:Consumer 是"校验→guard→审批→计量→执行→结果检查→事件"这条管道的唯一实现点——不关心实现是谁,只认 Definition;新能力**自动**获得全部纪律,因为纪律在协议里不在实现里。

工具型能力的 Consumer = 脊柱 tools 模块的 `ToolExecutor.execute`(F007 里 `ctx.tools.execute(call)`,纪律全在内部):

```python
# pyharness/core/tools.py —— 统一 Consumer(工具类)
class ToolExecutor:# pyharness/core/tools.py —— 统一 Consumer(工具类):纪律全在管道里,不在 Provider
class ToolExecutor:
    async def execute(self, call: ToolCall) -> ToolResult:   # call 来自 llm.response 或内部
        defn = self._r.lookup("tool", call.name)             # 1 查契约;查不到→TLB-802
        args = validate_args(defn, call.raw_args)            # 2 先验后跑(F026);失败→TLB-803 零执行
        d = await self._g.evaluate(call, self._scope)        # 3 guard 链(F014/§5);reject→终局
        if d == "approval":                                  # 4 审批(F015/§6.2)
            v = await self._ap.request(defn, args, ctx=self)
            if v != "granted": return ToolResult(ok=False, reason=f"approval:{v}")
            d = await self._g.evaluate(call, self._scope)    # granted≠放行:重入链起点
        if d != "allow": return ToolResult(ok=False, reason=d)
        self._meter.bill(defn, stage="pre")                  # 5 计量(F029/F032)
        prov = self._r.lookup_provider("tool", call.name)    # 6 Provider 可整体替换
        try:
            out = await self._run(prov.handle, args)         # 超时/取消三档(F017/F025)
            out = self._check_output(defn, out)              # 7 结果检查:契约+spill(F039)
        except PyHError as e:
            await self._session.append("tool.error", code=e.code, ...); return ...
        await self._session.append("tool.result", name=call.name, call_id=call.call_id,
            ok=True, summary=summarize(out), truncated=out.truncated, spill_ref=out.spill_ref)
        return out
```

非工具能力的 Consumer 无 ToolExecutor 但**消费语义同构**:Definition → 校验入参 → scope 查权 →(危险则)guard/审批 → Provider → 事件(如 agent-loop 的 F049 调 `ctx.agent.subagent.spawn` 同样先 lookup 契约再经 guard)。**规则:凡"LLM 或外部输入能触发的动作",Consumer 必须走同一管道;脊柱内部只读派生除外。**

## 2.4 生命周期状态机:enter → announce → detach

**职责一句话**:一个能力从"装上"到"干活"到"摘掉"只有三个动作——enter(装载资源)、announce(对外宣布就绪)、detach(逆操作),状态机保证迁移合法、detach 幂等、失败可回滚。

**能力状态机**(对象级;与插件容器 F006 五态的关系见映射表):

```text
`detached ─enter→ entering ─announce成功→ active ─detach开始→ detaching ─完成→ detached`
(失败语义:enter 抛错→回 detached 不 announce;announce 任一步失败→回滚已做步骤;detach 抛错→仍继续摘除、持续失败标记 broken 报警——不留半装载;重复 detach 幂等)

```

| 迁移 | 关键动作 | 失败处置 |
|---|---|---|
| detached→entering | 实例化 Provider;`provider.enter(ctx)` | 抛错→回 detached,不 announce |
| entering→active | announce 五步(见代码):①注册事件类型 schema ②挂订阅 ③tools.register_tool ④locator.mount ⑤registry.updated 留痕 | ②③④失败→回滚已做步骤→回 detached |
| active→detaching | `provider.detach(ctx)`;摘订阅;注销工具;unmount | 抛错仍继续摘除(半卸>僵尸);重复 detach 幂等 |
| detaching→detached | registry.updated(op=del) | — |

**与插件五态(F006)的映射**:插件是能力**容器**(一插件可含多能力):activating 时逐个驱动 enter→announce;deactivating 时逆序驱动 detach;uninstalled = 全部 detached。能力状态机**不独立对外**,由 CapabilityHost 按容器驱动——热插拔(F004)对用户只暴露容器 API,能力迁移是内部实现。

```python
# pyharness/bus/capability.py(续)—— 生命周期驱动器
class CapabilityHost:class CapabilityHost:
    def __init__(self, registry, bus, locator): ...
    async def activate(self, cap_id, ctx) -> None:
        rec = self.registry.lookup("capability", cap_id)     # 不存在→KeyNotFound
        if rec.state != "installed": raise IllegalTransition(cap_id, "activate")  # BUS-003
        rec.state = "entering"
        try:
            prov = rec.provider_factory()                    # 工厂而非实例:enter 前不构造
            await prov.enter(ctx)                            # 1 装载
            await self._announce(rec, prov, ctx)             # 2 宣布
        except PyHError:
            await self._rollback(rec, ctx); rec.state = "detached"; raise
        rec.provider, rec.state = prov, "active"
    async def _announce(self, rec, prov, ctx) -> None:       # 五步,顺序固定
        for etype, _ in rec.defn.subscriptions:              # ①类型 schema 先注册(否则 EVT-102)
            self.bus.register_type(etype, model_for(etype))
        for etype, method in rec.defn.subscriptions:         # ②挂订阅(owner=cap_id)
            self.bus.subscribe(etype, getattr(prov, method), owner=rec.owner)
        if rec.defn.expose_to_llm: ctx.tools.register(rec.defn)  # ③发布给 LLM(tool.registered)
        if rec.defn.ctx_path: ctx.locator.mount(rec.defn.ctx_path, rec)  # ④挂 ctx(首访才 new Provider)
        await self._session.append("registry.updated", op="add", kind="capability",
            key=rec.defn.ctx_path or rec.defn.name)          # ⑤留痕(既有词汇)
    async def detach(self, cap_id, ctx) -> None:             # 幂等;容器 deactivate 逆序调用
        rec = self.registry.lookup("capability", cap_id)
        if rec.state != "active": return                     # 已 detached → 幂等返回
        if self.bus.inflight(owner=rec.owner): raise BusyDetach(cap_id)  # 投递中不可卸(F004)
        rec.state = "detaching"
        try:    await rec.provider.detach(ctx)
        finally:                                             # 摘除四步,顺序固定
            self.bus.unsubscribe_all(owner=rec.owner)        # 摘订阅(按 owner)
            if rec.defn.expose_to_llm: ctx.tools.unregister(rec.defn.name)
            ctx.locator.unmount(rec.defn.ctx_path)           # 摘 ctx 挂载
            rec.state = "detached"
            await self._session.append("registry.updated", op="del", kind="capability", key=...)
```

## 2.5 注册与发现

- **注册三入口**:①内置能力——bootstrap 第 5 步扫描 `capabilities/*/` 的 `definition.py`(`DEFINITION`/`PROVIDER`)逐个注册再 activate;②第三方插件——`install(manifest)`(F006)注册其声明能力;③脊柱子系统(guard/approval/credentials)——硬接线时以 `namespace="system"`,`owner="spine"` 注册,**不进可卸载插件表**(spine 不可 uninstall,撞 BUS-002)。
- **发现两通道**:Consumer 经 `registry.lookup("capability"|"tool", key)`(O(1));LLM 经 `tools.schemas()`(announce 时把 expose_to_llm 的 Definition 编译为 function schema,agent-loop 每请求带上,F007)。
- **查不到即结构化错误,不静默**:工具查不到→TLB-802 回喂;能力查不到→`KeyNotFound` 转 `system.error` 事件。
- **启动顺序**(PRD §2.5 细化):①CFG → ②Registry 建索引+事件类型 schema 预注册 → ③总线+日志订阅者挂载 → ④脊柱硬接线(guard 链 g1-g7 按序装配,§5.2)→ ⑤内置能力注册:先无依赖基础能力(spill/kv),后编排类(plan/jobs 依赖 queue;**依赖序复用 manifest.requires 拓扑排序器,F006**)→ ⑥外壳接入,打印健康摘要(8 模块/N 能力/M 插件)。- **启动顺序**(PRD §2.5 细化):①CFG → ②Registry 索引+类型 schema 预注册 → ③总线+日志订阅者 → ④脊柱硬接线(guard 链 g1-g7 按序装配,§5.2)→ ⑤内置能力注册:先基础(spill/kv)后编排类(plan/jobs 依赖 queue;依赖序复用 manifest.requires 拓扑排序器,F006)→ ⑥外壳接入,打印健康摘要。

## 2.6 新能力接入清单(写一个能力的 8 步)

1. `definition.py`:Args pydantic 模型 + `DEFINITION`(danger/guard_hooks 据实声明);2. `provider.py`:`Provider(CapabilityProvider)` + `handle(args, ctx)` + `PROVIDER`;3. expose_to_llm → 测 schema 进 `tools.schemas()`;4. 新事件类型 → 总线登记 payload 模型(§3.8);5. 新 guard → `guards/` 写 match/check 并声明 guard_hooks(§5.4);6. 验收测试 `test_f{xxx}_*.py`:校验/guard/审批/计量各一 + 生命周期 + 换 Provider 不碰 Consumer;7. bootstrap 清单或 manifest 声明,跑 INV-04/05/06。

## 2.7 seam 通用异常与错误码(注册进 F019 对应域)

| 码 | 名称 | 场景 | 处置 |
|---|---|---|---|
| BUS-002 | 保留名冲突 | 注册/卸载脊柱模块或 spine 子系统 | 拒绝,不改注册表 |
| BUS-003 | 非法状态迁移 | activate/detach 时状态不在合法前驱 | 拒绝,状态机层抛(F006) |
| TLB-801 | 重复注册 | 同名 Definition 二次注册 | 拒绝并提示注销重注册 |
| TLB-802 | 工具未注册 | Consumer 查不到工具 | 回喂 LLM(TLB-802) |
| TLB-803 | 参数校验失败 | raw_args 强校验不过 | 回喂 LLM,零执行(INV-06) |
| EVT-102 | 事件类型未注册 | announce 前 emit | 拒投;先 register_type |
| EVT-103 | 订阅者异常 | handler 抛错 | 封装隔离,不中断他人(F001) |

抛错唯一入口 `raise_code(code, **ctx)`(F019/F020),禁止裸 `raise str`。

## 2.8 通用 seam 测试(模式层;每能力深例 GWT 见 §6 ⑧)

| 场景 | 断言(落 tests/acceptance/) |
|---|---|
| 换 Provider 不碰 Consumer | ProviderA→B 注销重注册后 ToolExecutor 零改动照常执行;tool.result 的 origin 恒为 cap 名;新旧均可审计 |
| Definition 不可变 | 改 defn.danger → TypeError(frozen);注册表内引用不变 |
| 生命周期回滚 | announce 中途注入失败 → 回 detached;无残留订阅/无 tool.registered/无 registry.updated(add) |
| 脊柱子系统不可卸 | uninstall("guard") → BUS-002;guard 链仍按 g1-g7 求值 |

# 3 ctx.* 服务定位器(等价 DSH ctx:40+ 服务怎么挂)

## 3.1 设计原则

**职责一句话**:ctx 是会话级服务定位器门面——脊柱 agent 模块创建的普通对象,不做任何业务,只回答"给我 `tools.fs_read`,并保证它是活的(惰性装载好、状态正确)"。

1. **统一寻址**:所有可注入服务只有一个根 `ctx`;能力间**禁止互相 import 实现**,只能经 ctx 取(ADR-002/原则 5)——grep 规则:`capabilities/` 内不允许出现 `import` 另一个能力包。
2. **按需惰性**:脊柱服务(§2.5 启动第④步)eager 构造;外围能力服务首访才实例化 Provider——`import pyharness` 不装载任何能力,`ctx.tools.fs_read` 第一次被触碰才 enter。
3. **无第二份状态**:ctx 上**不缓存会话事实**(历史/用量/预算都在日志投影里,PRD 原则 1);ctx 缓存的是"服务实例"(可丢重建,重建=重新 locate)。
4. **只读门面**:ctx 属性是服务句柄;写会话事实一律 `session.append`,禁止 `ctx.xxx = 事实`。

## 3.2 服务盘点(内置 41 个对象,与 MAP"40+ 能力"同口径;插件可追加,无上限)

**A. 根系统服务 11 项(eager,owner=spine/脊柱模块)**:

| ctx 属性 | 提供方 | 说明(F 号) |
|---|---|---|
| ctx.bus / ctx.registry | bus 模块 | 事件通道 / 三类索引(F001/F003) |
| ctx.session | 脊柱 session | append/回放/derive_history(F009) |
| ctx.llm | 脊柱 llm | 降级链客户端(F012/F013) |
| ctx.sysprompt | 脊柱 system-prompt | 提示词组装+护栏段(F010/F024) |
| ctx.scope | 脊柱 scope | 权限/预算/窗口/危险标记集 |
| ctx.guard | spine 子系统 | 单调链求值(F014,§5) |
| ctx.approval | spine 子系统 | 人类审批(F015,§6.2) |
| ctx.credentials | spine 子系统 | 凭据读取+脱敏(F016,§6.3) |
| ctx.config | core config | 分层配置只读(F021) |
| ctx.metrics | spine 子系统 | 用量/预算记账(F029/F032) |

**B. 能力命名空间 30 项(lazy,owner=builtin/插件)**:

| 命名空间 | 服务(attr → Definition name) | F 号 |
|---|---|---|
| ctx.tools | fs_read→fs.read · fs_write→fs.write · fs_list→fs.list · web_search→web.search · web_fetch→web.fetch · subprocess→exec.subprocess · pty→exec.pty · todo→todo | F034-038/F052/F053/F040 |
| ctx.agent | queue→task.queue · segments→task.segments · plan→plan · goal→goal · schedule→schedule · subagent→subagent · workflow→workflow · jobs→jobs | F043-051 |
| ctx.session | history→session.history(派生) · fork→session.fork · compact→session.compact · fts→session.fts · repair→session.repair | F009/F057-060 |
| ctx.storage | kv→storage.kv · spill→storage.spill · index→storage.index | F056/F039/F057 |
| ctx.sys | sandbox→sys.sandbox · workspace→sys.workspace · proc→sys.proc(进程树管理底座) | F054/F055/F052-053 |
| ctx.ui | attachment→ui.attachment · feedback→ui.feedback · edit→ui.edit | F061-063 |

合计 41 个内置服务对象(≥40,与 MAP 外围能力层同口径);其中 guard/approval/credentials/metrics/proc/spill/index 7 项为脊柱子系统/支撑服务(owner=spine,不可卸载);可热插拔外围能力 24 项(bootstrap 内置)+ 插件任意追加。

## 3.3 实现:惰性装载的 ServiceLocator

```python
# pyharness/core/agent.py(agent 模块创建 ctx 门面;落点以 specs/ 为准)
class _LazyService:                 # 首访属性访问才实例化的占位句柄
    def __init__(self, host, rec): self._h, self._r = host, rec
    def _get(self):
        if self._r.instance is None:
            self._r.instance = self._r.provider_factory()  # 此刻才 new Provider
            asyncio.get_event_loop().run_until_complete(
                self._r.instance.enter(self._h.ctx))       # 或由 host 预激活(见下)
        return self._r.instance
    def __getattr__(self, item):    # ctx.tools.fs_read.read → 透传 Provider
        return getattr(self._get(), item)
    def __call__(self, *a, **kw):   # ctx.tools.fs_read(args=...) 直调 handle
        return self._get().handle(*a, **kw)

class Ctx:
    """会话实体门面:根服务 eager + 命名空间 + 能力惰性句柄。普通对象,无全局单例。"""
    def __init__(self, session, llm, scope, bus, registry, ...):
        self.session, self.llm, self.scope = session, llm, scope
        self.bus, self.registry = bus, registry
        self.guard = GuardChain(...); self.approval = ApprovalService(...)
        self.credentials = Credentials(...); self.config = cfg; self.metrics = Metrics(...)
        self._ns = {n: SimpleNamespace() for n in
                    ("tools","agent","session","storage","sys","ui")}
    def mount(self, ctx_path, rec):   # announce 第④步(§2.4)
        ns, _, attr = ctx_path.partition(".")
        setattr(self._ns[ns], attr, _LazyService(self, rec))
    def unmount(self, ctx_path):      # detach 调用;已实例化则先 detach 再摘
        ns, _, attr = ctx_path.partition(".")
        svc = getattr(self._ns[ns], attr, None)
        if svc is not None and svc._r.instance is not None:
            loop.run_until_complete(svc._r.instance.detach(self))
        delattr(self._ns[ns], attr)
    def __getattr__(self, ns):        # ctx.tools / ctx.agent / ...
        if ns in self._ns: return self._ns[ns]
        raise ServiceNotFound(ns)     # → 记 system.error
    async def close(self):            # 会话结束:逆序 detach 全部已装载能力
        for ns in reversed(list(self._ns)):
            for attr, svc in list(vars(self._ns[ns]).items()):
                await self._detach_one(ns, attr, svc)
```

**装载时机两档**(配置 `ctx.lazy=true|false`,默认 true):惰性档=首访 enter(如上);预激活档=bootstrap 第⑤步对无依赖基础能力先行 enter+announce(如 spill/credentials,防首访延迟被审计为"慢")。**active 能力的 Provider 实例与容器状态绑定**:插件 deactivate 时 host.detach → locator.unmount,已取走的句柄再访问抛 `ServiceUnavailable(cap_id)`(结构化,调用方转 tool.error/system.error,LLM 可见建议:"该能力已卸载")。

## 3.4 ctx 生命周期(与会话绑定,非进程全局)

```text
session.created(seq=1) → Ctx(session,llm,scope,...) 构造(eager 11 项就绪)
  → bootstrap activate 基础能力(spill/credentials/kv)
  → agent-loop 消费:ctx.tools.fs_read(...) 首访惰性 enter+announce 已由 host 完成(实例在此刻创建)
  → 会话结束 session.finished → ctx.close():逆序 detach → 实例与订阅全部释放
  → fork(F059)派生新会话 = 新 Ctx;共享日志区只读,服务实例不共享(各自 enter)
```

**与 DSH ctx 等价对照(复刻思想非 TS 翻译)**:DSH 把会话内句柄塞进 `ctx:*` 注入插件;等价物 = Ctx 门面 + 六命名空间 + registry 寻址。差异:①DSH 上下文框架魔法装配,我们显式构造+mount,逐行可讲;②DSH 多急切装载,我们默认惰性(省启动、可演示"按需加载");③**ctx 路径 = capability 注册键**,两套索引由 CapabilityHost 同步驱动,永不打架。

## 3.5 查找失败语义与测试

| 情形 | 行为 | 码/事件 |
|---|---|---|
| 命名空间不存在(ctx.foo) | AttributeError → 上层转 system.error | EVT-100 族外,记 code=CFG-601(hint) |
| 能力已卸载仍被调用 | ServiceUnavailable | tool.error(code=TLB-802,hint=已卸载) |
| 惰性 enter 失败 | 抛结构化错误,实例不缓存,下次重试 | 原码透传 + system.error |
| 脊柱服务缺失(启动顺序错) | 构造期即抛 | CFG-601(装配顺序违反 §2.5) |

```text
G1 惰性装载
Given bootstrap 完成、无能力被触碰
When  首次访问 ctx.tools.fs_read
Then  Provider 此刻才实例化并 enter;日志出现 lazy enter;二次访问复用同实例(id 相同)

G2 卸载后访问
Given 插件 P 含 cap:web.search,已 active
When  P deactivate → locator.unmount;再调 ctx.tools.web_search(...)
Then  抛 ServiceUnavailable;无 Provider 执行;无 tool.result;有 system.error 事件

G3 能力间无直接 import
Given capabilities/ 全树
When  grep "import" 各能力包
Then  零命中其他能力包模块路径(只允许 import bus 契约层与 stdlib/第三方库)
```

# 4 自研插件总线:事件分发 × 注册表 × 装载顺序 × 卸载

## 4.1 总览(ADR-004 落地)

**职责一句话**:总线 = 进程内唯一通信通道,三条路径——注册(Registry)、分发(EventBus,三种模式)、热插拔(CapabilityHost+Lifecycle)——自研数百行,机制全透明。**总线不落盘**(F001/ADR-001):emit 后由日志订阅者追加进 JSONL,追加成功才算生效。

```python
# pyharness/bus/event_bus.py —— 核心数据结构
@dataclass
class Subscription:
    pattern: str            # "user.message" 或通配 "tool.*"
    handler: Callable       # async/同步均可
    owner: str              # 插件/能力 id;uninstall 按此精确摘除(F004)
    when: Callable | None   # payload 谓词,只读(F002)
    wildcard: bool = False
    seq: int = 0            # 注册序,同命中按序执行

class EventBus:
    def __init__(self, backpressure_limit: int = 1000):     # F005 默认 1000 在途
        self._subs: list[Subscription] = []
        self._by_type: dict[str, list[Subscription]] = {}   # 精确索引
        self._wild: list[Subscription] = []                 # 通配索引(tool.*)
        self._types: dict[str, type[BaseModel]] = {}        # 事件类型 schema 注册表(EVT-102)
        self._queues: dict[str, deque] = {}                 # per-sender FIFO(F005)
        self.dropped: dict[str, int] = {}; self._inflight: set[str] = set()
```

## 4.2 订阅与匹配(F001/F002)

```python
    def register_type(self, type_: str, model: type[BaseModel]) -> None:
        """类型 schema 预注册;未注册类型 emit → EVT-102(先注册才能发)"""
        if type_ in self._types: raise DuplicateType(type_)          # 事件类型只增不改(§3.8)
        self._types[type_] = model
    def subscribe(self, pattern, handler, *, owner="anonymous",
                  when=None) -> Subscription:
        sub = Subscription(pattern, handler, owner, when,
                           wildcard=pattern.endswith(".*"))
        (self._wild if sub.wildcard else self._by_type.setdefault(pattern, [])).append(sub)
        self._subs.append(sub); return sub
    def _match(self, type_: str) -> list[Subscription]:   # 精确→通配,各按注册序(F002)
        return [*self._by_type.get(type_, []), *[s for s in self._wild if _wild_match(s.pattern, type_)]]
    def unsubscribe_all(self, owner: str) -> int:        # 卸载唯一摘除口(F004)
        before = len(self._subs)
        self._subs = [s for s in self._subs if s.owner != owner]
        self._by_type = {t: [s for s in ss if s.owner != owner] for t, ss in self._by_type.items()}
        self._wild = [s for s in self._wild if s.owner != owner]
        return before - len(self._subs)
```

谓词纪律:when 只读 payload,抛异常 = 该订阅跳过并记 EVT-103,不影响他人、不改事件(F002 边界)。

## 4.3 分发模式 dispatch 表:sequential / waterfall / parallel

**三种模式的语义**(本文档对 F001 的展开,模式选择由事件族决定,见下表):

| 模式 | 语义 | 顺序保证 | 错误隔离 | 典型用途 |
|---|---|---|---|---|
| sequential(默认) | 命中订阅按注册序逐个 await | 全序=注册序;同 sender FIFO(F005) | handler 异常→EVT-103 封装后继续 | 一切会话事件(日志订阅者必须见序) |
| waterfall | 链式:前一个返回值=后一个入参;STOP 即短路 | 全序;短路后订阅**不执行** | 异常=短路+EVT-103;结果取最后返回值 | guard 链(§5)、tool.call 前置管线 |
| parallel | asyncio.gather 并发执行并聚合 | 无跨订阅序 | 单个失败仅该订阅记 EVT-103 | 只读投影:UI 渲染、FTS 写、用量统计 |

```python
    async def emit(self, type_: str, payload: dict, mode: str = "sequential") -> dict:    async def emit(self, type_, payload, mode="sequential") -> dict:
        """总线入口:类型校验 → 按模式分发 → 投递统计。不落盘。"""
        if type_ not in self._types: raise UnknownEventType(type_)      # EVT-102
        subs = self._match(type_); stats = {"delivered": 0, "errored": 0}
        if mode == "sequential":                          # 注册序逐条 await
            for s_ in subs:
                if s_.when and not safe_call(s_.when, payload): continue
                ok = await self._run(s_, type_, payload)
                stats["delivered" if ok else "errored"] += 1
        elif mode == "waterfall":                         # 链式:前值=后参,STOP 短路
            acc = payload
            for s_ in subs:
                if s_.when and not safe_call(s_.when, acc): continue
                r = await self._run(s_, type_, acc)
                if r is STOP: break
                if r is not None: acc = r; stats["delivered"] += 1
            return {"result": acc, **stats}
        else:                                             # parallel:只读投影 gather
            res = await asyncio.gather(*(self._run(s_, type_, payload) for s_ in subs
                if not (s_.when and not safe_call(s_.when, payload))), return_exceptions=True)
            stats["delivered"] = sum(1 for r in res if not isinstance(r, Exception))
            stats["errored"] = sum(1 for r in res if isinstance(r, Exception))
        return stats
    async def _run(self, s_: Subscription, type_, payload):   # 单订阅:异常→EVT-103 不扩散
        self._inflight.add(s_.owner)
        try:
            fn = s_.handler(type_, payload) if _needs_two_args(s_.handler) else s_.handler(payload)
            return await fn if asyncio.iscoroutine(fn) else await asyncio.to_thread(fn)
        except Exception as e:
            log.error(struct_error("EVT-103", type_=type_, owner=s_.owner, exc=e)); return None
        finally:
            self._inflight.discard(s_.owner)
```

**事件族 → 默认模式表**(emit 不带 mode 时的选择;强同步三类恒为 sequential):

| 事件族 | 模式 | 理由 |
|---|---|---|
| user.message / guard.rejected / approval.\* / tool.call / tool.result / llm.\* / session.\* | sequential | 事实顺序即语义;日志订阅者强同步(F009/F011) |
| guard 求值内部(scope→g1→…→g7) | waterfall | 任一 reject 短路终局(§5.3) |
| llm.chunk → UI / FTS 索引写 / 用量统计 | parallel | 只读投影,可乱序到达各自消费者(§3.3 聚合规则仍守) |
| registry.updated / plugin.installed 等元事件 | sequential | 元事实同样要落盘 |

## 4.4 事件系统:全流程与保证

```text
session.append(event)                          # 唯一写入口(F009)
  → validate_envelope:信封字段/类型注册/seq 连续(§3.2)  违规→EVT-100/101/102/106 拒写
  → 分配 seq(框架唯一打点,防伪造乱序,§3.1.5)
  → bus.emit(type,payload,mode=sequential)     # 总线只中转;handler 异常 EVT-103 隔离
  → 日志订阅者追加 JSONL;强同步三类(见下)立即 flush,成功才返回
  → 派生投影订阅者(UI/FTS/统计)可走 parallel 模式各自刷新
```

- **顺序保证**:①seq 由 session.append 单调分配(§3.4 空洞语义);②同 sender 事件 FIFO,未完成不取下一件(F005);③订阅者同类型按注册序(精确先、通配后)。
- **背压**:在途超阈值(默认 1000)→ **拒新不丢旧**:丢弃计数 + `bus.backpressure` 事件,禁静默(F005)。
- **错误隔离**:订阅者异常 = EVT-103 封装+本地日志,不影响会话与他人——敢热插拔的前提(F001)。
- **强同步三类**(§3.6):user.message / guard.rejected / approval.\* 立即写+flush;崩溃最多丢其后 ≤0.5s 普通事件,由 repair 声明。

## 4.5 注册表(插件/工具/能力三类索引,F003)

```python
# pyharness/bus/registry.py
class Registry:# pyharness/bus/registry.py
class Registry:
    RESERVED = {"agent-loop","tools","session","llm","system-prompt",
                "scope","agent","persistence"}                # 脊柱 8 名(BUS-002)
    def __init__(self): self._index = {"plugin": {}, "tool": {}, "capability": {}}
    def register(self, kind, key, obj):
        if kind == "plugin" and key in self.RESERVED: raise BusReserved(key)      # BUS-002
        if key in self._index[kind]: raise DuplicateKey(kind, key)                # TLB-801
        self._index[kind][key] = obj
        self._bus.emit("registry.updated", {"op": "add", "kind": kind, "key": key})  # 留痕(F003)
    def lookup(self, kind, key):
        v = self._index[kind].get(key)
        if v is None: raise KeyNotFound(kind, key)            # 不静默
        return v
    def unregister(self, kind, key): ...                      # 摘除+registry.updated(op=del)
```

三类的 key:plugin=插件 id;tool=Definition.name(fs.read,LLM 可见);capability=ctx 路径(tools.fs.read,定位器寻址)。**同一能力的两个键由 CapabilityHost 同步维护**(§2.4 announce ①②),保证"LLM 调得到 = ctx 取得到"。

## 4.6 插件装载顺序与卸载

- **装载**:`install(manifest)` → 依赖拓扑排序(F006,循环依赖检出失败)→ 依序 activate 依赖 → 逐能力 enter+announce。顺序规则:**脊柱先于内置能力,内置能力先于第三方插件;同层按 manifest.requires 拓扑序,无依赖关系按注册序**(确定性可复现)。
- **卸载(F004 语义全量)**:

```python
async def uninstall(self, plugin_id: str, ctx) -> None:
    p = self.registry.lookup("plugin", plugin_id)              # 不存在→KeyNotFound
    if p.state == "running": raise BusyUninstall(plugin_id)    # 投递中不可卸
    for cap in reversed(p.capabilities):                       # 逆序 detach 各能力(后装先卸)
        await self.host.detach(cap, ctx)                       # 摘订阅+注销工具+unmount(§2.4)
    self.registry.unregister("plugin", plugin_id)
    await self._session.append("plugin.uninstalled", plugin_id=plugin_id)   # 留痕(F004)
```

- **不回溯**:新装插件不接收已发生事件(F004 边界,保住原则 1:事实不可被后来者改变);脊柱模块 uninstall → BUS-002。

## 4.7 总线错误码与测试

| 码 | 场景 | 处置 |
|---|---|---|
| EVT-102 | emit 未注册类型 | 拒投;提示先 register_type |
| EVT-103 | 订阅者异常 | 封装隔离;delivered/errored 统计可见 |
| EVT-101 | seq 不连续 | 拒写;怀疑丢事件跑 repair(F060) |
| BUS-003 | 非法状态迁移 | 状态机层拒绝(F006) |
| BUS-002 | 卸脊柱/保留名 | 拒绝并留事件 |

```text
G1 双插件互发(F004 里程碑)
Given 插件 A 订阅 greeting,B 订阅 echo
When  A emit("hello") → B emit("echo.hello")
Then  B 的 handler 收到;顺序=A→B;热卸载 B 后 A 再发 echo 类事件,B 零接收

G2 handler 崩溃不扩散
Given 两个订阅者,前者抛 RuntimeError
When  emit("user.message", ...)
Then  前者记 EVT-103;后者照常收到;emit 返回 delivered=1/errored=1

G3 waterfall 短路
Given tool.call 链:[g1 schema, g2 danger, g3 fs]
When  g2 返回 REJECT
Then  g3 不执行;分发返回 result=REJECT;无 tool.result

G4 背压拒新不丢旧
Given 在途队列满(limit=1000),sender=S
When  S 再 emit 一件
Then  新件被拒并计数;bus.backpressure 事件发出;队列内旧件全部按序投递完
```

# 5 guard 单调拒绝链(只拒绝,不放行)

## 5.1 决策模型(原则 3)

**职责一句话**:guard 链 = tool.call 的单调安全求值器——任一 guard 可 reject,无机制能把 reject 翻回 allow;审批不是放行而是"决策权移交人类,批准后请求**重入链起点**"。决策三值 `allow/reject/approval`,无 bypass。

形式语义:任一 `G_i=reject` ⇒ `decision=reject`(终局,其后零副作用 INV-05);approval 仅当 danger=high 且有通道;critical 的 approval 强制转 reject。拒绝必有强同步 guard.rejected(guard_id+policy_ref);执行前必有 guard.evaluated(INV-04,缺失=非法执行)。

## 5.2 Guard 契约与内置链(g1-g7)

```python
# pyharness/guards/base.py
class Guard(ABC):
    id: str                       # 事件里出现的 guard_id,如 "g-fs-path"
    @abstractmethod
    def match(self, call) -> bool:        # 管不管这个工具(按名前缀/Definition 声明)
        ...
    @abstractmethod
    async def check(self, call, scope) -> Decision:   # allow/reject/approval + policy_ref
        ...
# GuardChain 内置七节(注册序=求值序;PRD §4.3 的 g1-g5 为固定首五节)
```

| 位 | id(逻辑名) | match 依据 | 判定要点 | policy_ref 例 |
|---|---|---|---|---|
| g1 | g-schema | 所有工具+内部调用 | **schema 复查**(MAP 注:链内对内部调用复查契约;入口校验 F026 先行) | TLB-803 转 reject |
| g2 | g-danger | danger≠none 的工具 | high→approval;critical→reject(不可审批) | POL-DGR-1 |
| g3 | g-fs-path | `fs.*`/`workspace.*` 前缀 | 绝对路径越界拒;`..` 逃逸拒;symlink 解析后仍须在 workspace 内(F055) | POL-FS-1/2/3 |
| g4 | g-credential-read | 读路径类工具 | 目标命中凭据文件(credentials.yaml/环境变量清单)→拒 | POL-CRED-1 |
| g5 | g-net-outbound | web.*/网络工具 | 目标域不在 scope.allowed_domains →拒 | POL-NET-1 |
| g6 | g-exec | exec.subprocess/exec.pty | 无 shell;cwd 限 workspace;需 scope 显式授权(F052) | POL-EXEC-1 |
| g7 | g-overwrite | fs.write 等写工具 | 目标已存在 → approval(覆写转审批,F023) | POL-OVW-1 |

g1-g5 恒在;g6/g7 与插件 guard(经 Definition.guard_hooks 声明)按注册序**追加链尾**——单调性只允许加拒绝面:新 guard 只新增拒绝条件,不许移除/弱化既有 guard(F014 边界:卸 guard 须先停用其工具;guard.disabled 须 config 显式声明并留事件,F023 边界)。g3 的 match 示例见 PRD F023(按 `fs.`/`workspace.` 前缀),g4 的凭据路径清单来自 ctx.credentials(§6.3),g5 域名表来自 scope.policy.allowed_domains。

## 5.3 链求值实现(waterfall 模式;单调核心)

```python
# pyharness/core/guard.py
class GuardChain:
    def __init__(self, chain: list[Guard]): self.chain = chain      # 启动装配,运行期只读

    async def evaluate(self, call, scope) -> Decision:
        """单调求值:return (decision, reasons)。reject=终局,无续跑 API。"""
        if not scope.can_use(call.name):                            # scope 前置(不可见→终局)
            await self._audit(call, "reject", "scope-hidden", "GRD-401")
            return Decision.REJECT
        for g in self.chain:                                        # 按注册序 waterfall
            if not g.match(call): continue                          # 非本 guard 管辖 → 放行看下一个
            d, policy = await g.check(call, scope)                  # allow/reject/approval
            if d == "approval" and call.defn.danger == "critical":  # critical 不可审批(F014)
                d, policy = "reject", "POL-DGR-1"
            if d != "allow":                                        # 首个非 allow 即短路(waterfall)
                await self._audit(call, d, g.id, policy)
                if d == "reject":
                    await self._session.append("guard.rejected", tool=call.name,
                        guard_id=g.id, reason=policy, call_id=call.call_id,
                        policy_ref=policy, sync=True)               # 强同步三类之一(§3.6)
                return d                                            # reject/approval 都到此为止
        await self._audit(call, "allow", [g.id for g in self.chain], None)
        return Decision.ALLOW                                       # 全链 allow → Provider 执行

    async def _audit(self, call, decision, guard_ids, policy):
        await self._session.append("guard.evaluated", tool=call.name,
            decision=decision, guard_ids=guard_ids, policy_ref=policy)   # 单调性审计点(INV-04)
```

**审批后重入**(R2/F014 边界,§6.2 深挖):ToolExecutor 收到 approval 并获 granted 后,**不得直接执行**,而是再次调用 `evaluate`——期间策略收紧(guard 新增/scope 变更/凭据清单变化)则批准作废,单调性高于人类即时意志(PRD §6.7)。

**单调用全时序**(§6.3 威胁场景同款):

```text
llm.response(tool_calls=[fs.delete_file(...)]) → F022 解析 → F026 契约校验(失败→TLB-803 回喂零执行)
→ guard.evaluate:scope 可见? → g1 schema 复查 → g2 danger=critical → REJECT → guard.evaluated → guard.rejected 强同步落盘
→ 其后零副作用;LLM 收拒绝原因可换招,下一轮同管道;审计=guard.rejected 序列 + 无对应 tool.result("拦了且没执行",INV-05)

```

## 5.4 新增 guard 模板(插件只能加严)+ 错误码 + GWT

```python
# capabilities/my_plugin/guards.py —— 例:禁读某敏感目录
class GDenyDir(Guard):
    id = "g-deny-dir"
    def match(self, call): return call.name in ("fs.read", "fs.list")
    async def check(self, call, scope):
        p = resolve(call.args.get("path", ""))
        if str(p).startswith(scope.policy.deny_dirs): return ("reject", "POL-DIR-1")
        return ("allow", None)
# Definition 声明:CapabilityDefinition(..., guard_hooks=("g-deny-dir",)) → announce 时追加链尾
```

| 码 | 场景 | 处置 |
|---|---|---|
| GRD-401 | scope 前置不可见 / 调用被拒 | 终局;事件含 guard_id+policy_ref |
| GRD-402 | 对已拒绝 call_id 的重执行尝试 | 拒绝并记 system.error(防重放) |
| GRD-403 | approval 后重入链被(新)拒 | 以新决策为准,非错误;事件链可证 |

```text
G1 单调终局(INV-05)
Given 链=[g-schema,g-danger,g-fs-path],delete_file danger=critical,路径越界
When  evaluate
Then  decision=reject;guard.rejected 强同步;Provider.handle 零调用(副作用为零)

G2 critical 不可审批
Given 同上但 danger=critical 且审批通道在线
When  g-danger 返回 approval
Then  强制转 reject;不发 approval.requested;人类无批准入口

G3 审批后重入
Given 调用 approval 获 granted;期间插件注册了新 guard g-deny-dir 命中该调用
When  重入 evaluate
Then  第二次 decision=reject;无 tool.result;审计见两次 guard.evaluated

G4 插件 guard 只加严
Given 内置链 g1-g5;插件注册 guard_hooks=("g-deny-dir",)
When  断言语义
Then  链长=6 且新 guard 恒在链尾;无任何 API 可移除/重排 g1-g5
```


# 6 三个代表性 seam 深挖(证明模式可复用)

> 三类形态各取一个:**spill**=基础设施型(Consumer=脊柱结果检查器,LLM 不直调 put);**approval**=决策型(横切 guard 链,脊柱子系统);**credentials**=资源型(被任意 Provider 消费)。每例九段式(①职责 ②Definition ③Provider ④Consumer ⑤生命周期 ⑥注册发现 ⑦错误码 ⑧GWT ⑨关联),与 §2 通用模式一一对应;"建议码"注册进 F019 对应前缀域、含义不变。

## 6.1 seam A:spill 输出溢出(基础设施型,F039)

**①职责一句话**:超限工具输出落会话私有 spill 文件,上下文只放 ≤2KB 摘要+引用——截断不丢信息、按行可取回,防上下文/预算爆炸。
**②Definition 契约**(namespace=storage,owner=spine,expose_to_llm=True;Args=`SpillReadArgs(ref:str,start:int=0,limit:int=200)` 按行读回;内部面 put 只以 Provider 方法存在、不进 tools):
```python
DEFINITION = CapabilityDefinition(name="storage.spill", namespace="storage",
    schema=SpillReadArgs, danger="none", ctx_path="storage.spill",
    owner="spine", expose_to_llm=True)
```
**③Provider 接口(签名;实现模板见 §2.2 与 PRD F039)**:
```python
class SpillProvider:
    async def enter(self, ctx) -> None                    # 首访:建会话 spill 目录(workspace 外)
    async def put(self, text: str, kind: str) -> dict     # ≤10MB;返回 {spilled,ref,chars,lines,preview};超限→PERS-223
    async def read(self, ref: str, start: int, limit: int) -> dict  # 按行读回+more;越权/逃逸→PERS-222
    async def detach(self, ctx) -> None                   # 幂等;文件留待归档/repair(F060),不删
```
**④Consumer**:脊柱结果检查器 `ToolExecutor._check_output`(F039):输出>2KB→`ctx.storage.spill.put(...)`→`tool.result` 只带 summary+truncated+spill_ref(§3.3 D 表);LLM 要全文→调 spill.read,已读量计入防循环烧预算。
**⑤生命周期 enter→announce→detach**:enter=建目录(惰性);announce=spill.read 挂 tools(tool.registered)+locator.mount("storage.spill");detach=摘工具+unmount。owner=spine 不可热卸(BUS-002),随 ctx.close() detach。
**⑥注册/发现**:bootstrap 第⑤步最先 activate(编排类依赖它截断);registry key="storage.spill";Consumer 经 `ctx.storage.spill` 惰性取。
**⑦异常与错误码**(建议 PERS-2xx):PERS-221 写失败;PERS-222 越权读;PERS-223 超 10MB 拒写——结构化错误+事件回喂 LLM(含 advice,F020)。
**⑧关联测试(GWT≥3)**:
```text
G1 Given 工具输出 5KB(>2KB) → When 结果检查 → Then tool.result 带 truncated+spill_ref;文件=原文;上下文≤2KB 摘要
G2 Given spill_ref 存在 → When spill.read(ref,start=0,limit=100) → Then 前 100 行+more=true;已读量递增
G3 Given ref="../../s-other/x.jsonl" → When read → Then PERS-222;零读取;tool.error 回喂
```
**⑨关联文档**:PRD F039、§3.3 D 表、§4.4;ADD ADR-001;落点 core/tools.py + capabilities/storage/。

## 6.2 seam B:approval 人类审批(决策型,脊柱子系统,F015)

**①职责一句话**:danger≥high 调用把决策权移交人类——请求摘要化、TTL 超时=拒绝(安全默认)、granted≠放行(重入 guard 链),全程留痕可审计。
**②Definition 契约**(namespace=system,owner=spine,ctx_path=None 非 expose_to_llm——横切服务不是工具):
```python
DEFINITION = CapabilityDefinition(name="system.approval", namespace="system",
    schema=ApprovalRequest, danger="none", ctx_path=None, owner="spine",
    subscriptions=(("approval.granted","on_verdict"), ("approval.denied","on_verdict"),
                   ("approval.timeout","on_verdict")))   # 订阅裁决事件驱动等待者
```
**③Provider 接口(签名;实现模板见 §2.2 与 PRD F015)**:
```python
class ApprovalProvider:
    async def enter(self, ctx) -> None                  # 构造等待表 {approval_id: Future}
    async def request(self, defn, args, ctx) -> str     # granted/denied;headless→APR-501;approval_id=请求 seq
                                                        # 超时→denied+approval.timeout;结果强同步落盘(§3.6)
    async def on_verdict(self, type_, payload) -> None  # 唤醒等待者;重放/未知 id→APR-503
    async def detach(self, ctx) -> None                 # 未决 waiters 全置 denied
```
**④Consumer**:ToolExecutor 第 4 步(§2.3)——guard 返回 approval→`ctx.approval.request(...)`;granted 后**不直接执行**,重入 guard.evaluate(策略可能已收紧,PRD §6.7);60s 同工具同参合并防轰炸(F015);审批期队列暂停。
**⑤生命周期**:enter=构造等待表;announce=注册 approval.granted/denied/timeout 类型+挂订阅+挂 ctx.approval;detach=未决全置 denied(不留悬挂 Future)。启动第④步硬接线(guard 链后),随 ctx.close() 收尾。
**⑥注册/发现**:硬接线直接构造挂 ctx(不进 capability 注册表,§3.2 根服务);全会话共享同一对象,裁决按 approval_id 配对,防跨会话串扰。
**⑦异常与错误码**(建议 APR-5xx):APR-501 headless 无通道(直接拒,R8);APR-502 等待被取消;APR-503 裁决重放/未知 id(记 system.error 防重放)。
**⑧关联测试(GWT≥3)**:
```text
G1 Given danger=high 进入审批、无人类响应 → When 120s 到 → Then approval.timeout;request=denied;Provider 零执行
G2 Given channel=None → When request → Then APR-501;不发 approval.requested;无等待
G3 Given approval_id=42 已消费(granted) → When 再投 granted(42) → Then APR-503;不重复执行;无第二个 tool.result
G4 Given 批准期间新增 g-deny-dir 命中 → When granted→重入链 → Then 二次 decision=reject;审计见两条 guard.evaluated
```
**⑨关联文档**:PRD F015/F014、§3.6、§6.7;ADD ADR-003;MAP §4 关2。

## 6.3 seam C:credentials 凭据管理(资源型,脊柱子系统,F016)

**①职责一句话**:凭据单一读取口+进程内持有+全出口脱敏+进程退出即失——把 key 泄漏面压到最小(INV-09)。
**②Definition 契约**(namespace=system,owner=spine,schema=None 无 LLM 面;Consumer=任意 Provider/llm 客户端):
```python
DEFINITION = CapabilityDefinition(name="system.credentials", namespace="system",
    schema=None, danger="none", ctx_path=None, owner="spine",
    subscriptions=(("bus.*","redact_out"), ("system.error","redact_out"),
                   ("tool.result","redact_out")))       # 事件出口横切:写前脱敏
```
**③Provider 接口(签名;实现模板见 §2.2 与 PRD F016)**:
```python
class CredentialsProvider:
    async def enter(self, ctx) -> None              # 装载 credentials.yaml(权限 600,gitignore)+空缓存
    def get_secret(self, name: str) -> str          # 唯一读取口;缺失→CRED-701(拒绝而非空串)
    def redact(self, text: str) -> str              # 疑似 key 打码:sk-abc*** 
    async def redact_out(self, type_, payload)      # 出口订阅:payload 深脱敏后放行
    async def detach(self, ctx) -> None             # 清空缓存:进程退出/会话关即失
```
**④Consumer**:Provider 需密钥只调 `ctx.credentials.get_secret(name)`(web.fetch 鉴权头、llm api_key 等)——**禁止自己读环境变量/配置文件**(否则脱敏与审计失效);日志/事件/错误全出口经 redact_out 脱敏(INV-09 grep 无 32+ 位疑似密钥)。
**⑤生命周期**:enter=装载+清缓存;announce=注册 bus.*/system.error/tool.result 出口订阅+挂 ctx.credentials;detach=清缓存。运行中改环境变量需重启(不热重载,F021)。
**⑥注册/发现**:启动第④步硬接线;经 ctx.credentials 全局取。脱敏横切靠 owner=spine 订阅——spine 不可卸故脱敏不可关(只可加严,同 guard 单调精神)。
**⑦异常与错误码**(建议 CRED-7xx):CRED-701 缺失(拒绝而非空串);CRED-702 权限>600 启动拒载;CRED-703 脱敏自检发现疑似 key 外泄(写 system.error)。
**⑧关联测试(GWT≥3)**:
```text
G1 Given 环境与文件均无 DEEPSEEK_API_KEY → When get_secret(...) → Then CRED-701;无空串/None 续跑路径
G2 Given 事件含 "sk-abc1234567890..." 且 redact_out 生效 → When 事件落地 → Then 日志无 32+ 位疑似 key,只剩 sk-abc***;断言全绿
G3 Given credentials.yaml mode=0644 → When enter → Then CRED-702;拒绝装载
G4 Given 全 capabilities/ 树 → When grep os.environ|credentials → Then 仅 CredentialsProvider 命中
```
**⑨关联文档**:PRD F016、§6.5、R5、INV-09;ADD ADR-011;消费方=llm 客户端(F012)+web.fetch(F038)。

---

# 7 模式复用盘点与验证门禁

## 7.1 其余能力 → 同一模板(§3.2 服务清单即全量映射)

| 能力族 | Definition 形态 | Provider 类型 | Consumer 代表 |
|---|---|---|---|
| fs/web 工具(6) | expose_to_llm=True,danger 按写/删定级 | 无状态 IO,handle(args,ctx) | ToolExecutor(§2.3) |
| exec.subprocess/pty | critical/high + guard_hooks=("g-exec",) | 有状态会话(pty 单例) | ToolExecutor+g-exec |
| plan/goal/jobs/schedule | 编排类,expose_to_llm=False | 有状态,依赖 queue/segment | agent-loop F045-F051 |
| fork/compact/fts/repair | ctx.session 命名空间 | 读写日志投影 | 会话管理/斜杠命令 |
| kv | storage 命名空间 | SQLite WAL 串行写 | 跨轮状态读写 |
| sandbox/workspace | sys 命名空间,owner=spine | 边界对象(路径几何 F055) | fs.* Provider 内引用 |

三件套纪律对全部 24 项外围能力+7 项支撑服务一视同仁:每包必有 definition.py(grep 验收);消费协议四步不被 Provider 重复;Provider 禁反向 import Consumer(INV-04/06 守护)。

## 7.2 验证门禁(实现合入门)

1. `wc -c DIS-SEAM.md` ∈ [45000,55000];grep 无 TODO/待补/TBD/占位、无 TS 关键字(`interface`/`type X =`/`=>`)。
2. 每 `capabilities/*/` 有 definition.py 导出 DEFINITION;3. `python -m pyharness.demo_bus` 里程碑过;4. INV-04/05/06/09 全绿(§6.2 G4=INV-05 组件级展开);5. 关联闭环:§2/§4↔ADR-002/004,§5/§6↔F014/F015/F016/F039,冲突以 PRD-Core.md 为准。
