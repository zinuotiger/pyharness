# specs/bus.py.md — 编码规格

> 目标代码:pyharness/bus/(event_bus.py、registry.py、plugin.py),即 PRD 阶段 0 F001-F006 的实现载体。AI 编码 Agent 只读本文件即可写出总线全量代码,无需翻阅其他文档。权威源:PRD-Core.md §3.7、§5.1 F001-F006、§8.2 勾选清单;DIS-SEAM.md §4(伪代码权威展开);ERR.md §2.1/§2.2(错误码权威);CFG.md §3.7(plugins.* 键)。全中文,仅 Python,禁 TS。

## 模块职责
一句话:单进程内唯一通信通道——注册表(Registry 索引插件/工具/能力)+ 事件总线(EventBus 三模式分发 sequential/waterfall/parallel)+ 插件宿主(PluginManager 五态生命周期与热插拔),总线只中转不落盘,落盘由日志订阅者完成。

## 依赖
| import | 用途 |
|---|---|
| asyncio | emit 协程分发、gather 并发、to_thread 同步 handler |
| collections.deque | F005 per-sender FIFO 队列 |
| dataclasses | Subscription 数据结构 |
| typing(Callable、Optional、Literal) | 订阅回调与状态类型标注 |
| pydantic(BaseModel) | 事件类型 schema 注册容器(_types,EVT-102 判定) |
| logging | EVT-103/BUS-xxx 单行结构化日志(struct_error) |
| pyharness.errors(PyHError、raise_code、struct_error) | 全错误经 F019 唯一入口,禁裸 raise str |
| pyharness.events | Envelope 模型供日志订阅者强同步落盘(总线不直接依赖落盘实现) |

禁止:pluggy/Cordis/任何现成插件框架;跨进程/多进程实现(原则 5,INV-07)。

## 类与函数清单

### Subscription 数据类
**功能**:一条订阅记录,精确/通配统一入列,owner 供卸载按属主精确摘除。
**伪代码**:
```python
@dataclass
class Subscription:
    pattern: str            # "user.message" 或 "tool.*"
    handler: Callable       # async/同步均可,签名 (type_, payload) 或 (payload)
    owner: str              # 插件/能力 id;uninstall 按此摘除(F004)
    when: Optional[Callable] = None   # payload 谓词,只读(F002)
    wildcard: bool = False
    seq: int = 0            # 注册序,同命中按序执行
```

### EventBus.__init__(backpressure_limit=1000)
**功能**:构建总线,背压阈值默认 1000 在途(F005 可配上限来自 `plugins.backpressure_limit`)。
**参数表**:
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| backpressure_limit | int | 否 | per-sender 在途上限,满=拒新不丢旧,默认 1000 |
**伪代码**:
```python
def __init__(self, backpressure_limit: int = 1000):
    self._by_type: dict[str, list[Subscription]] = {}   # 精确索引
    self._wild: list[Subscription] = []                 # 通配索引(tool.*)
    self._types: dict[str, type[BaseModel]] = {}        # 事件 schema 注册表(EVT-102)
    self._queues: dict[str, deque] = {}                 # per-sender FIFO
    self.dropped: dict[str, int] = {}                   # 背压丢弃计数(可观测)
    self._inflight: set[str] = set()                    # 投递中 owner(F004 busy 判定)
    self.backpressure_limit = backpressure_limit
```
**异常表**:无(构造不失败)。
**关联测试**:TC-F001 → tests/acceptance/test_f001_bus.py(§8.2 阶段 0)。

### register_type(type_: str, model: type[BaseModel]) -> None
**功能**:预注册事件类型 schema;未注册类型 emit → EVT-102(先注册才能发,F001 边界)。
**参数表**:
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| type_ | str | 是 | 事件名,词汇表或插件命名空间 `plugin.<id>.<name>` |
| model | type[BaseModel] | 是 | 该事件 payload 的 pydantic 校验模型 |
**伪代码**:
```python
def register_type(self, type_: str, model: type[BaseModel]) -> None:
    if type_ in self._types:                    # 事件类型只增不改(§3.8)
        raise_code("EVT-102", type_=type_, detail="类型重复注册")
    self._types[type_] = model
```
**异常表**:
| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| PyHError(EVT-102) | 同类型二次注册(破坏 §3.8 只增不改) | EVT-102 | 类型定义错误,换新类型名;词表冻结语义不可改 |
**关联测试**:TC-F001 → test_f001_bus.py、TC-GWT-ERR-07 → tests/acceptance/test_f019_error_codes.py(§8)。

### subscribe(pattern, handler, *, owner="anonymous", when=None) -> Subscription
**功能**:注册订阅,支持精确名与 `*.` 通配(F002),谓词 when 只读过滤;谓词抛异常 = 该订阅跳过记 EVT-103。
**参数表**:
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| pattern | str | 是 | 事件名或以 `.*` 结尾的通配 |
| handler | Callable | 是 | async 优先;同步 handler 由总线 to_thread 执行 |
| owner | str | 否 | 插件/能力 id,默认 anonymous |
| when | Callable | 否 | payload 谓词,返回 truthy 才投递,禁止改写 payload |
**伪代码**:
```python
def subscribe(self, pattern, handler, *, owner="anonymous", when=None) -> Subscription:
    if pattern not in self._types and not pattern.endswith(".*"):
        pass                                    # 精确类型未注册:允许先订阅,emit 侧仍拒(EVT-102)
    sub = Subscription(pattern, handler, owner, when,
                       wildcard=pattern.endswith(".*"), seq=len(self._subs))
    if sub.wildcard:
        self._wild.append(sub)
    else:
        self._by_type.setdefault(pattern, []).append(sub)
    return sub
```
**异常表**:
| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| 无(语法校验失败在调用侧) | handler 非 Callable | — | 调用方自查;谓词异常在分发期被 EVT-103 隔离 |
**关联测试**:TC-F002 → test_f002_dispatch.py(谓词/通配/注册序,§8)。

### unsubscribe_all(owner: str) -> int
**功能**:按属主精确摘除全部订阅(F004 卸载唯一摘除口);返回摘除条数。
**参数表**:
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| owner | str | 是 | 插件/能力 id,与 subscribe 时一致 |
**伪代码**:
```python
def unsubscribe_all(self, owner: str) -> int:
    before = len(self._subs)
    self._subs = [s for s in self._subs if s.owner != owner]
    self._by_type = {t: [s for s in ss if s.owner != owner]
                     for t, ss in self._by_type.items()}
    self._wild = [s for s in self._wild if s.owner != owner]
    return before - len(self._subs)             # 返回实际摘除数,0 也合法
```
**异常表**:无(幂等,重复卸载返回 0)。
**关联测试**:TC-F004 → test_f004_hotplug.py(卸载摘净,§8)。

### emit(type_: str, payload: dict, mode: str = "sequential") -> dict
**功能**:总线入口:类型校验(EVT-102)→ 匹配(精确先、通配后,各按注册序)→ 按模式分发 → 返回投递统计。总线不落盘。
**参数表**:
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| type_ | str | 是 | 事件名,必须已 register_type |
| payload | dict | 是 | 事件负载 |
| mode | str | 否 | sequential(默认)/waterfall/parallel,强同步三类恒 sequential |
**伪代码**:
```python
async def emit(self, type_: str, payload: dict, mode: str = "sequential") -> dict:
    if type_ not in self._types:
        raise_code("EVT-102", type_=type_)               # 拒投,提示先 register_type
    subs = self._match(type_)
    stats = {"delivered": 0, "errored": 0}
    if mode == "sequential":                             # 注册序逐条 await,异常隔离后继续
        for s_ in subs:
            if s_.when and not safe_call(s_.when, payload):
                continue
            ok = await self._run(s_, type_, payload)
            stats["delivered" if ok else "errored"] += 1
    elif mode == "waterfall":                            # 前返回值 = 后入参;STOP 短路
        acc = payload
        for s_ in subs:
            if s_.when and not safe_call(s_.when, acc):
                continue
            r = await self._run(s_, type_, acc)
            if r is STOP:
                break
            if r is not None:
                acc = r; stats["delivered"] += 1
        return {"result": acc, **stats}
    else:                                                # parallel:只读投影 gather
        todo = [s_ for s_ in subs if not (s_.when and not safe_call(s_.when, payload))]
        res = await asyncio.gather(*(self._run(s_, type_, payload) for s_ in todo),
                                   return_exceptions=True)
        stats["delivered"] = sum(1 for r in res if not isinstance(r, Exception))
        stats["errored"] = sum(1 for r in res if isinstance(r, Exception))
    return stats
```
**异常表**:
| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| UnknownEventType | type_ 未注册 | EVT-102 | 拒投,先 register_type;查拼写/插件是否已装 |
| handler 内异常(不外抛) | 订阅者/谓词崩溃 | EVT-103 | _run 捕获封装,delivered/errored 统计可见,不中断他人 |
**关联测试**:TC-F001 → test_f001_bus.py(异常隔离)、TC-G2(DIS-SEAM §4.7)→ test_f002_dispatch.py(§8)。

### emit_ordered(sender: str, type_: str, payload: dict) -> dict
**功能**:同 sender 事件严格 FIFO 串行(F005);在途超阈值 → 拒新不丢旧,记 dropped 并发 bus.backpressure 瞬时事件。
**参数表**:
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| sender | str | 是 | 来源标识(如 plugin:feeder / session:xxx) |
| type_ / payload | str/dict | 是 | 事件名与负载 |
**伪代码**:
```python
async def emit_ordered(self, sender: str, type_: str, payload: dict) -> dict:
    q = self._queues.setdefault(sender, deque())
    if len(q) >= self.backpressure_limit:                # 满 = 拒新
        self.dropped[sender] = self.dropped.get(sender, 0) + 1
        await self.emit("bus.backpressure",
                        {"sender": sender, "dropped": self.dropped[sender]})
        return {"delivered": 0, "errored": 0, "dropped": True}
    q.append((type_, payload))                           # 入队 = 已承诺,绝不丢
    total = {"delivered": 0, "errored": 0}
    while q:                                             # 未完成不取下一件(FIFO)
        t, p = q.popleft()
        st = await self.emit(t, p, mode="sequential")
        total["delivered"] += st["delivered"]; total["errored"] += st["errored"]
    return total
```
**异常表**:
| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| 背压丢弃(非异常) | 在途 ≥ backpressure_limit | bus.backpressure 事件 | 可观测,禁静默;队列内旧件按序投递完 |
| EVT-102 | 队内类型被注销 | EVT-102 | 拒投该件,计数留痕 |
**关联测试**:TC-F005 → test_f005_backpressure.py(FIFO/拒新不丢旧/指标,§8)。

### _run(sub: Subscription, type_: str, payload: dict) -> Optional[bool]
**功能**:单订阅执行器:async 直接 await、同步 to_thread;异常 → EVT-103 单行日志后返回 None,不扩散。
**参数表**:
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| sub | Subscription | 是 | 目标订阅 |
| type_ / payload | str/dict | 是 | 事件上下文 |
**伪代码**:
```python
async def _run(self, sub: Subscription, type_: str, payload: dict):
    self._inflight.add(sub.owner)                        # F004:投递中不可 deactivate
    try:
        fn = sub.handler(type_, payload) if _needs_two_args(sub.handler) else sub.handler(payload)
        return await fn if asyncio.iscoroutine(fn) else await asyncio.to_thread(fn)
    except asyncio.CancelledError:                       # 取消不吞,继续传播(F025)
        raise
    except Exception as e:                               # 吞错不扩散,EVT-103
        log.error(struct_error("EVT-103", type_=type_, owner=sub.owner, exc=e))
        return None
    finally:
        self._inflight.discard(sub.owner)
```
**异常表**:
| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| 订阅者任何异常 | handler 内部错误 | EVT-103 | 封装隔离;errored 计数可见,修复后重载插件 |
| CancelledError | 会话取消传播 | (无码,re-raise) | 交 F025 取消协议归一 |
**关联测试**:TC-F001(异常隔离)、TC-G2 → test_f001_bus.py(§8)。

### Registry.register(kind: str, key: str, obj) -> None
**功能**:三类索引写入(plugin/tool/capability);脊柱 8 名保留(BUS-002)、重名拒绝(TLB-801);写 registry.updated 瞬时事件留痕(F003)。
**参数表**:
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| kind | str | 是 | plugin / tool / capability |
| key | str | 是 | plugin=插件 id;tool=Definition.name;capability=ctx 路径 |
| obj | Any | 是 | 注册对象 |
**伪代码**:
```python
class Registry:
    RESERVED = {"agent-loop", "tools", "session", "llm", "system-prompt",
                "scope", "agent", "persistence"}                 # 脊柱 8 名(BUS-002)
    def __init__(self, bus: EventBus):
        self._index = {"plugin": {}, "tool": {}, "capability": {}}
        self._bus = bus
    def register(self, kind: str, key: str, obj) -> None:
        if kind == "plugin" and key in self.RESERVED:
            raise_code("BUS-002", key=key)                        # 脊柱不可换(原则 5)
        if key in self._index[kind]:
            raise_code("TLB-801", kind=kind, key=key)             # 重名拒绝,无脏数据
        self._index[kind][key] = obj
        self._bus.emit("registry.updated", {"op": "add", "kind": kind, "key": key})
```
**异常表**:
| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| BusReserved | kind=plugin 且 key ∈ RESERVED(含 guard/approval/credentials 子系统名) | BUS-002 | 换名;确认不在保留八名 |
| DuplicateKey | 同 kind 同 key 二次注册 | TLB-801 | 注销旧定义后重注册(改 = 注销重注册留痕) |
**关联测试**:TC-F003 → test_f003_registry.py(索引/重名/保留名,§8)。

### Registry.lookup(kind: str, key: str)
**功能**:O(1) 查询;key 不存在 → KeyNotFound 结构化错误,不静默返回 None(F003 边界)。
**参数表**:
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| kind / key | str | 是 | 索引类与键 |
**伪代码**:
```python
def lookup(self, kind: str, key: str):
    v = self._index[kind].get(key)
    if v is None:
        raise_code("TLB-802" if kind == "tool" else "BUS-000",
                   kind=kind, key=key, hint="查注册表与拼写")
    return v
```
**异常表**:
| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| KeyNotFound | key 未注册/已卸载 | TLB-802(tool)/KeyNotFound(其余) | 工具名查 tools.schemas();插件名查 plugin.installed 时间线 |
**关联测试**:TC-F003 → test_f003_registry.py、TC-ERR-04(§8)。

### Registry.unregister(kind: str, key: str) -> None
**功能**:摘除索引并广播 registry.updated(op=del);卸载插件时由宿主按逆序调用(F004)。
**参数表**:
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| kind / key | str | 是 | 索引类与键 |
**伪代码**:
```python
def unregister(self, kind: str, key: str) -> None:
    if key not in self._index[kind]:                # 注销不存在 key 不静默
        raise_code("BUS-000", kind=kind, key=key, detail="unregister-unknown")
    del self._index[kind][key]
    self._bus.emit("registry.updated", {"op": "del", "kind": kind, "key": key})
```
**异常表**:
| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| KeyNotFound | 注销不存在键 | KeyNotFound | 幂等场景调用方先 lookup;防重复卸载逻辑 |
**关联测试**:TC-F003 → test_f003_registry.py(§8)。

### PluginManager.install(manifest: dict) -> None
**功能**:声明式装载:校验 api_version → 依赖拓扑排序(循环依赖检出失败,F006)→ 依序激活依赖 → 状态 installed,写 plugin.installed 事件。
**参数表**:
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| manifest | dict | 是 | 含 id/version/requires[]/api_version;requires 为插件 id 列表 |
**伪代码**:
```python
async def install(self, manifest: dict, ctx) -> None:
    if manifest["id"] in Registry.RESERVED:
        raise_code("BUS-002", key=manifest["id"])            # 脊柱不可装为插件
    if manifest.get("api_version") != self.API_VERSION:
        raise_code("BUS-003", detail=f"api_version={manifest.get('api_version')} 不匹配")
    order = toposort(manifest.get("requires", []), self._deps)  # 循环依赖 → InstallFailed
    for dep in order:                                        # 先激活依赖
        if self.state(dep) != "active":
            raise_code("BUS-003", detail=f"依赖 {dep} 未激活")
    self._set_state(manifest["id"], "installed")
    await ctx.session.append("plugin.installed", plugin_id=manifest["id"],
                             version=manifest["version"], api_version=manifest["api_version"])
```
**异常表**:
| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| BusReserved | 插件 id = 脊柱名 | BUS-002 | 换 id |
| InstallFailed | 依赖缺失/循环依赖拓扑检出失败 | BUS-003(状态机层) | 按返回依赖清单补齐;循环 = 改 manifest |
| api_version 不符 | manifest.api_version ≠ 框架 API_VERSION | BUS-003 | 升级插件或拒绝装载 |
**关联测试**:TC-F006 → test_f006_lifecycle.py(拓扑排序/非法迁移拒,§8)。

### PluginManager._set_state(pid: str, target: str) -> None
**功能**:五态状态机唯一迁移闸:installed→activating→active→deactivating→inactive(→uninstalled/activating);非法迁移 BUS-003 拒绝(F006)。
**参数表**:
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| pid | str | 是 | 插件 id |
| target | str | 是 | 目标态 |
**伪代码**:
```python
def _set_state(self, pid: str, target: str) -> None:
    legal = {"installed": ["activating"],
             "activating": ["active"],
             "active": ["deactivating"],
             "deactivating": ["inactive"],
             "inactive": ["uninstalled", "activating"]}       # F006 迁移表
    cur = self._state.get(pid, "absent")
    if target not in legal.get(cur, []):
        raise_code("BUS-003", pid=pid, detail=f"{cur}→{target} 非法")
    self._state[pid] = target
```
**异常表**:
| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| IllegalTransition | 迁移不在 legal 表(如 active→installed) | BUS-003 | 对照 F006 状态机;查调用顺序 |
**关联测试**:TC-F006 → test_f006_lifecycle.py、TC-G4(DIS-SEAM §4.7)(§8)。

### PluginManager.activate(pid) / deactivate(pid)
**功能**:activate 导入模块、逐能力 enter+announce(Definition 注册与订阅装载);deactivate 逆操作摘订阅但不注销注册表(F004 四操作之二)。
**参数表**:
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| pid | str | 是 | 目标插件 |
**伪代码**:
```python
async def activate(self, pid: str, ctx) -> None:
    self._set_state(pid, "activating")
    try:
        mod = import_module(self._manifest[pid]["entry"])      # 导入
        for cap in mod.capabilities:                           # Definition 注册 + 订阅
            await self.host.enter(cap, ctx)                    # announce:注册 tool/capability 双键
        self._set_state(pid, "active")
    except Exception:
        self._state[pid] = "installed"                         # 失败回滚,不留半激活态
        raise
async def deactivate(self, pid: str, ctx) -> None:
    p = self.registry.lookup("plugin", pid)
    if pid in self._bus._inflight: raise BusyUninstall(pid)    # 投递中不可卸(BUSY 语义)
    self._set_state(pid, "deactivating")
    for cap in reversed(p.capabilities):                       # 逆序 detach(后装先卸)
        await self.host.detach(cap, ctx)
    self._set_state(pid, "inactive")
```
**异常表**:
| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| BusyUninstall | 插件 handler 正在投递 | BUSY(字面量,非 NNN) | 等当前投递完成再 deactivate |
| IllegalTransition | 状态不满足前驱 | BUS-003 | 查 manifest 依赖顺序 |
**关联测试**:TC-F004 → test_f004_hotplug.py、TC-F006 → test_f006_lifecycle.py(§8)。

### PluginManager.uninstall(plugin_id: str, ctx) -> None
**功能**:热卸载:查插件 → running 拒绝 → 逆序 detach 能力(摘订阅+注销工具+unmount)→ registry 摘 plugin → 写 plugin.uninstalled 事件;不回溯已发生事件(F004)。
**参数表**:
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| plugin_id | str | 是 | 插件 id |
| ctx | AgentCtx | 是 | 会话上下文(经 ctx.session.append 留痕) |
**伪代码**:
```python
async def uninstall(self, plugin_id: str, ctx) -> None:
    p = self.registry.lookup("plugin", plugin_id)          # 不存在 → KeyNotFound
    if p.state == "running":
        raise BusyUninstall(plugin_id)                     # 投递中不可卸
    for key in p.tool_keys:                                # 摘工具注册
        self.registry.unregister("tool", key)
    for cap in reversed(p.capabilities):                   # 逆序 detach 能力
        await self.host.detach(cap, ctx)                   # detach 内含 unsubscribe_all(owner=plugin_id)
    self.registry.unregister("plugin", plugin_id)
    await ctx.session.append("plugin.uninstalled", plugin_id=plugin_id)   # 留痕(F004)
```
**异常表**:
| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| KeyNotFound | 插件不存在 | KeyNotFound | 查 plugin.installed 时间线;无需重复卸载 |
| BusyUninstall | running/投递中 | BUSY | 等当前事件分发完成 |
| BusReserved | 尝试卸载脊柱模块 | BUS-002 | 脊柱不可热插拔,拒绝并留事件 |
**关联测试**:TC-F004 → test_f004_hotplug.py(热卸载后事件停、卸载摘净、不回溯,§8)。

### emit_as(plugin_id: str, type_: str, payload: dict) -> dict
**功能**:插件以自身身份发事件:自动补 origin=plugin:<id>,并保证类型已注册(EVT-102 前置);会话事件须经 session.append 打 seq,插件不可自报 seq。
**参数表**:
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| plugin_id | str | 是 | 声明 origin |
| type_ / payload | str/dict | 是 | 事件名与负载 |
**伪代码**:
```python
async def emit_as(self, plugin_id: str, type_: str, payload: dict) -> dict:
    if type_ not in self._bus._types:                    # 插件先注册类型 schema 才能 emit
        raise_code("EVT-102", type_=type_,
                   hint="插件事件命名空间 plugin.<id>.<name>,先 register_type")
    return await self._bus.emit(type_, {**payload, "origin": f"plugin:{plugin_id}"})
```
**异常表**:
| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| UnknownEventType | 类型未注册即发 | EVT-102 | 插件 install 阶段先 register_type |
| SeqOutOfOrder | 插件自报 seq/绕 session.append | EVT-101 | 会话事件一律走 session.append(INV-01) |
**关联测试**:TC-F001/TC-G1(DIS-SEAM §4.7 双插件互发)→ test_f001_bus.py、test_f004_hotplug.py(§8)。

### _wild_match(pattern: str, type_: str) -> bool
**功能**:通配匹配辅助:段级 `前缀.*` 精确前缀命中,禁正则(保持确定性)。
**伪代码**:
```python
def _wild_match(pattern: str, type_: str) -> bool:
    prefix = pattern[:-2]                      # 去掉尾部 ".*"
    return type_.startswith(prefix + ".") and pattern.endswith(".*")
```
**关联测试**:TC-F002 → test_f002_dispatch.py(§8)。

## 关联文档
- PRD-Core.md(§2.1 分层总图、§3.7 EVT 处置表、§5.1 F001-F006、§8.2 阶段 0 勾选清单)
- DIS-SEAM.md(§4 自研插件总线伪代码权威:订阅/匹配/三模式分发/注册表/装载卸载顺序、§4.7 G1-G4)
- ERR.md(§2.1 BUS-0xx、§2.2 EVT-1xx、§1.3 三轴分类)
- EVENT-SCHEMA.md(§3.5.6 plugin.installed/uninstalled/bus.backpressure、§8.1 落盘矩阵——registry.updated/llm.chunk 仅内存)
- CFG.md(§3.7 插件域 plugins.enabled/backpressure_limit/deadletter_samples)
