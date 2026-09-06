# specs/agent.py.md — 编码规格

> **模块文件**:`pyharness/core/agent.py` | **功能编号**:F007(联动)· F064(装配)· PRD §2.2(模块2)/§2.3/§2.5 | **权威口径**:PRD-Core §2.3/§2.5、DIS-CORE §2(伪代码级)、DIS-SEAM §2.4(能力生命周期 enter→announce→detach 映射)
> **一句话**:会话实体 + ctx 门面——聚合 session/scope/tools/llm/loop,是能力 seam 的消费端总闸;管理生命周期与 `session.finished` 唯一归属;强制"一会话一 agent、同会话单 running"互斥。

## 模块职责

1. **会话实体(句柄)**:`agent_id ↔ session_id` 1:1 绑定(不可换);对外壳/注册表暴露可寻址句柄(registry 键 `plugin:{agent_id}`);持有生命周期状态 `init/ready/busy/stopping/closed`。
2. **ctx 门面**:构造期按拓扑序注入脊柱 8 模块(persistence→session→{scope,llm,tools,system_prompt}→loop),内聚为 `Agent.ctx`;能力层经 `ctx.*` 命名空间挂载调用,只认契约不认实现(原则 2)。脊柱命名空间(agent/session/storage/sys/ui/tools)写死保留,cap 不可覆盖(INV-08)。
3. **submit = 外壳唯一写口**:三壳(CLI/桌面/ACP)平权、同代码路径、无特权路径;写操作与用户输入同级过 session.append 校验链;外壳零业务逻辑。
4. **生命周期与 finished 唯一归属**:`session.finished` 全生命周期至多一条(EVT-104),唯一合法写入路径 = `Agent.close`;close 幂等;任何模块自行写 finished 即内部 bug(INV-01 追查)。
5. **互斥纪律**:① 同会话已存在 active agent 时重复 create/enter → BUSY(防双 loop 竞争 seq);② loop 层 running 互斥透传为 agent.state=busy。
6. **能力挂载面**:`attach_capability(ns, iface)`(= DIS-SEAM announce 语义的消费侧)/ `detach_capability(ns)`(幂等摘除,半卸 > 僵尸);会话关闭时由注入的 host 逆序 detach(DIS-SEAM §2.4)。

## 依赖

- **依赖方向**(构造期注入,依赖环不可能,INV-08):`agent → {scope, session, tools, llm}` + loop + persistence(经 ctx 引用,不 import 外围能力)。
- 注入源:`spine` = 8 模块束(bus/persistence/session/scope/llm/tools/system_prompt/agent_loop),启动第 4-6 步硬接线(PRD §2.5)。
- 外部依赖:`bus.subscribe/unsubscribe_all`(阶段0)、`registry.register`(F003)、`errors.raise_code`(F020)。
- 只读白名单:`Ctx.NAMESPACES = {"agent","session","storage","sys","ui","tools"}`(脊柱保留,挂载越界 → TLB-802)。

## 类与函数清单

### 关键数据结构(字段级)

| 字段 | 类型 | 规则 |
|---|---|---|
| `agent_id` / `session_id` | str | 句柄标识;uuid4().hex / 会话 UUID;1:1 不可换 |
| `state` | `Literal["init","ready","busy","stopping","closed"]` | 生命周期(§状态机) |
| `ctx` | `Ctx` | 门面:内聚 session/scope/tools/llm/loop/persistence/sys/storage |
| `caps` | `dict[str, CapNamespace]` | 已挂载能力命名空间 → iface 束 |
| `config` | `Settings` | CFG 启动只读;`headless` 决定 run 结束是否自动 close |
| `_session_lock` | 布尔互斥 | 一会话一 agent:enter 抢占,close 释放 |

### 状态机(ASCII + 转移表)

```text
 create_agent → enter()
 INIT ────────► READY ──submit──► BUSY(loop running 透传)
                 │  ▲               │
                 │  │run 自然结束    │ 取消/超时
                 │  └───────────────┤
                 ▼                  ▼
              STOPPING ◄── close/在途取消清理 ─┘
                 │
                 ▼
              CLOSED(终局;submit→BUSY 显式拒)
```

| 当前→目标 | 触发 | 动作与事件 |
|---|---|---|
| init→ready | `enter()` 接线完成 | 抢占会话互斥、registry.register(plugin, agent_id)、bus.subscribe 会话事件流 |
| ready→busy | `submit()` 且 loop idle | user.message 已强同步落盘;loop.run() |
| busy→ready | run 自然结束且会话保持(交互) | 交互模式回 READY,不写 finished |
| busy/ready→stopping | `close()` / 在途取消 | loop.cancel;追加 session.finished(强同步) |
| stopping→closed | 清理完成 | 摘订阅、释放会话互斥、log;终局 |
| closed→— | `submit()` | BUSY 显式拒(不排队不静默,建议新开会话) |

### `def create_agent(session_id: str, spine: Spine, cfg: Settings) -> Agent` — 会话实体工厂(创建)

**功能**:启动第 4-6 步脊柱硬接线;构造 `Agent(state="init")`、组装 Ctx(8 模块按拓扑序注入)、绑定 config;随后必须经 `await agent.enter()` 激活方可 submit。

**参数表**:`session_id` 目标会话;`spine` 8 模块束;`cfg` 分层配置(Settings)。

```python
def create_agent(session_id, spine, cfg):            # 工厂:纯构造,不占用会话
    if session_id in spine.active_agents:            # 一会话一 agent(DIS §2.6-1)
        raise PyHError("BUSY", ctx={"session_id": session_id,
            "advice": "该会话已有 active agent,请复用句柄或新开会话"})
    ag = Agent(agent_id=uuid4().hex, session_id=session_id,
               state="init", config=cfg, caps={})
    ag.ctx = Ctx(session=spine.session, scope=spine.scope, tools=spine.tools,
                 llm=spine.llm, loop=spine.loop, sys=spine.sys,
                 storage=spine.storage, persistence=spine.persistence)
    spine.active_agents[session_id] = ag             # 占位登记(enter 失败则回滚释放)
    return ag                                        # 新会话的 session.created 由 enter 落
```

**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError("BUSY")` | 同会话已有 active agent | BUSY | 复用旧句柄或新开会话(防双 loop 竞争 seq) |
| `PyHError("TLB-801")` | registry 重名注册 | TLB-801 | 注销旧句柄重注册 |

### `async def Agent.enter(self) -> None` — 会话占位激活

**功能**:激活句柄进入会话:确认会话文件可打开/创建(`open_session`,新会话则先落 `session.created` seq=1)、registry.register、bus.subscribe(`session:{sid}:*` → `_on_bus_event`)、state init→ready。**幂等**:已 ready/busy 直接返回;stopping/closed 拒。

```python
async def enter(self):
    if self.state in ("ready", "busy"): return      # 幂等:已激活直接返回
    if self.state in ("stopping", "closed"):
        raise PyHError("BUSY", ctx={"advice": "句柄已关闭,请 create 新 agent"})
    if not self._created_event:                      # 新会话引导:首事件 seq=1(EVT-106 守卫)
        await self.ctx.session.append("session.created",
            {"title": "", "model": self.config.llm.primary}, actor="system", sync=True)
    self._created_event = True
    registry.register("plugin", self.agent_id, self) # F003:可被外壳/宿主寻址
    bus.subscribe(f"session:{self.session_id}:*", self._on_bus_event)
    self.state = "ready"
    log.info("agent ready", agent_id=self.agent_id, session=self.session_id)
```

**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError("BUSY")` | closed 后 enter | BUSY | 新开会话 |
| `PyHError("TLB-801")` | registry 重名 | TLB-801 | 先注销 |
| `EVT-106` | append 先于 created 校验失败 | EVT-106 | 查 bootstrap 顺序(正常不可达) |

### `async def Agent.submit(self, text: str, *, meta: dict | None = None) -> RunResult` — 外壳唯一入口

**功能**:三壳平权提交;状态校验(stopping/closed → BUSY;空消息 → EVT-100)→ `user.message` 强同步落盘(先落盘后执行,崩溃一致性锚点)→ `loop.wake`(idle 拉起 run / running 入队)。headless=True 时 run 结束自动 `close(reason)` 写 finished。

```python
async def submit(self, text, *, meta=None):
    if self.state in ("stopping", "closed"):         # 关闭后 submit:显式拒,不排队
        raise PyHError("BUSY", ctx={"advice": "会话已关闭,请新开会话"})
    if not text.strip():                             # 空消息:信封层前置拒绝
        raise PyHError("EVT-100", ctx={"field": "content", "advice": "消息不能为空"})
    self.state = "busy"                              # busy=loop running 透传(乐观置位)
    try:
        env = await self.ctx.session.append("user.message",
            {"content": text}, actor="user", sync=True)      # 强同步①:落盘才继续(§3.6)
        result = await self.ctx.loop.wake(env)               # idle→run;running→入队
        if self.config.headless and result is not None:      # headless:run 结束即关会话
            await self.close(reason=result.reason)
        return result
    finally:
        if self.state == "busy" and self.ctx.loop.state == "idle":
            self.state = "ready"                             # 交互模式回 READY
```

**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError("BUSY")` | closed/stopping 后 submit;队列满 | BUSY | 建议新开会话/稍后重试 |
| `PyHError("EVT-100")` | 空消息/信封非法 | EVT-100 | 修正后重发 |
| `PyHError("PERS-202")` | user.message 强同步落盘失败 | PERS-202 | append 抛错;repair 后重试(输入未生效,不丢话) |

**关联测试**:GWT-A2-01(submit 后 user.message 在 llm.chat 前已落盘)、GWT-A2-02(headless:恰一条 finished(complete)、state=closed、再 submit → BUSY)、GWT-A2-03(交互:两次 submit finished 计数 == 0 直到显式 close)。

### `async def Agent.close(self, reason: str = "idle") -> None` — 会话关闭(销毁;finished 唯一归属)

**功能**:会话终态唯一写 `session.finished` 的路径(EVT-104 单次);幂等(stopping/closed 直接返回);有在途 run(paused/running)先 `loop.cancel(reason="close")`;再强同步 append finished;随后摘订阅、释放会话互斥、state=closed;最后驱动注入的 host 逆序 detach 已装载能力(DIS-SEAM)。

```python
async def close(self, reason="idle"):
    if self.state in ("stopping", "closed"): return   # 幂等:双触发(外壳/repair)安全
    self.state = "stopping"
    if self.ctx.loop.state in ("running", "paused"):  # 有在途 run → 先声明式取消
        await self.ctx.loop.cancel(reason="close")
    await self.ctx.session.append("session.finished",  # 唯一归属:finished 仅此一处
        {"reason": reason}, actor="system", sync=True)
    bus.unsubscribe_all(owner=self.agent_id)          # 摘订阅
    registry.unregister("plugin", self.agent_id)      # 释放句柄寻址
    self.ctx.session.close_marker()                   # SessionLog._closed=True(拒后续 append)
    await self.ctx.host.close()                       # 逆序 detach 全部已装载能力(DIS-SEAM §2.4)
    self._session_lock.release(); self.state = "closed"
```

**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `EVT-104` | 日志已有 finished(正常不可达) | EVT-104 | 暴露内部 bug;查绕过本路径的写口(INV-01) |
| `PyHError("PERS-202")` | finished 强同步落盘失败 | PERS-202 | append 抛错;repair 后重试(close 幂等可重入) |

**参数表**:`reason ∈ {complete, idle, timeout, budget, max_turns, cancelled, error, stall, close}`(与 RunResult.reason 对齐)。

### `async def Agent.attach_capability(self, ns: str, iface: CapNamespace) -> None` — 能力挂载(announce 语义)

**功能**:能力 seam 装载点(= DIS-SEAM 生命周期 announce 在消费侧的入口):ns 白名单校验(越界 → TLB-802)、查重(重复 → TLB-801)、写 caps + `setattr(ctx, ns, iface)` 挂载、`announce(ns)` 留痕广播。消费协议自动附带校验/guard/审批/计量——Provider 不自实现(原则 2)。

```python
async def attach_capability(self, ns, iface):
    if ns not in Ctx.NAMESPACES:                     # 脊柱命名空间保留:插件不可覆盖
        raise PyHError("TLB-802", ctx={"what": f"ctx.{ns}",
            "advice": "脊柱命名空间不接受插件挂载(INV-08)"})
    if ns in self.caps: raise PyHError("TLB-801", ctx={"what": f"ctx.{ns}"})
    self.caps[ns] = iface                            # iface = Definition + Provider 束
    setattr(self.ctx, ns, iface)                     # 挂载后 ctx.{ns} 立即可消费
    await self.announce(ns)                          # registry.updated 留痕 + 可选 tools 注册
```

**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError("TLB-802")` | ns 越出 {agent,session,storage,sys,ui,tools} 白名单 | TLB-802 | 换命名空间;脊柱子系统注册走 BUS-002 |
| `PyHError("TLB-801")` | ns 重复挂载 | TLB-801 | 先 detach_capability 再挂 |
| `EVT-102` | announce 广播未注册事件类型 | EVT-102 | 先 register_type |

### `async def Agent.announce(self, ns: str) -> Envelope` — 能力就绪宣布

**功能**:attach_capability 的对外宣布段:对总线广播 `registry.updated(op="attach", kind="capability", key=ns)` 持久留痕;若 iface 声明 expose_to_llm,则把 Definition 编译注册进 tools 注册表(LLM 立即可见)。幂等:已 announce 且未 detach → 重复调用 TLB-801。

```python
async def announce(self, ns):
    if ns not in self.caps: return None              # 无此能力:静默(attach 前调用无效果)
    env = await self.ctx.session.append("registry.updated",
        {"op": "attach", "kind": "capability", "key": ns}, actor="system")
    if self.caps[ns].expose_to_llm:                  # 声明对 LLM 可见 → 进 tools 注册表
        self.ctx.tools.register_definition(self.caps[ns].definition)
    return env
```

**关联测试**:GWT-A2-04(attach("agent",…) 成功而 attach("llm",…) 抛 TLB-802;cap 经 ctx 调工具仍需过 guard——INV-04)。

### `async def Agent.detach_capability(self, ns: str) -> None` — 能力摘除

**功能**:逆操作:幂等(未挂载 → 直接返回);从 caps 移除、`delattr(ctx, ns)`、注销 expose_to_llm 工具、`registry.updated(op="del")` 留痕;detach 抛错仍继续摘除(半卸 > 僵尸),失败标记 broken 告警。会话关闭时由 host 逆序调用全部。

```python
async def detach_capability(self, ns):
    if ns not in self.caps: return                   # 幂等:已 detached
    try:
        iface = self.caps.pop(ns)
        if iface.expose_to_llm:                      # 同步注销 tools 注册表项
            self.ctx.tools.unregister_definition(iface.definition.name)
        delattr(self.ctx, ns)                        # ctx 门面摘除
        await self.ctx.session.append("registry.updated",
            {"op": "del", "kind": "capability", "key": ns}, actor="system")
    except Exception as e:                           # 半卸>僵尸:失败继续摘+告警
        log.error("cap detach failed", ns=ns, exc=e)
        bus.emit("bus.backpressure", {"ns": ns, "broken": True})
```

**异常表**:无上抛(内部消化 + 告警留痕);append 失败由 session 校验层抛(PERS/EVT 系列)时记日志继续。

### `async def Agent._on_bus_event(self, type_: str, env: Envelope) -> None` — 会话事件订阅入口

**功能**:句柄对本会话事件流的订阅回调(bus 按 `session:{sid}:*` 前缀匹配投递):普通事件仅计数(事件已由 session 分发/落盘,不重复处理);当 loop 处于 paused 且收到恢复信号(approval.granted / budget 恢复类)时驱动 `loop.resume()` 重入 guard 链起点。异常 → EVT-103 由总线封装隔离。

```python
async def _on_bus_event(self, type_, env):
    if env.session_id != self.session_id: return     # 前缀订阅兜底过滤
    if type_ == "approval.granted" and self.ctx.loop.state == "paused":
        await self.ctx.loop.resume()                 # 审批通过 → 重入 guard 链起点(原则 3)
    # 其余事件:事实已在日志,此处只做可见性钩子,禁止改动状态
```

**异常表**:订阅者异常 → EVT-103(总线捕获封装,不中断其他订阅者)。

### `def Agent.snapshot(self) -> dict` — 只读句柄查询

**功能**:外壳/审计查询句柄状态:`{agent_id, session_id, state, loop_state, seq, queue_len, caps: [ns…]}`;纯只读,禁止由此路径修改。

## 边界与限制

1. **一会话一 agent**(重复 create/enter → BUSY);防双 loop 竞争 seq 单调性。
2. **外壳无特权**:submit 是唯一写口,三壳同代码路径;写操作全过 session.append 校验(§3 校验链)。
3. **只读门面**:cap 无法反向覆盖脊柱对象(INV-08);ctx.* 不提供 unregister(摘除走 detach_capability)。
4. **close 幂等**:CLOSED 后 submit 显式报错;finished 唯一归属 close(EVT-104 由日志侧再次钉死)。
5. **enter 幂等 / attach 幂等可逆**:生命周期动作均可重复安全调用或干净回滚,不留半装载。

## 关联测试(汇总)

GWT-A2-01 submit 强同步序 · GWT-A2-02 headless 自动关 · GWT-A2-03 交互不写 finished · GWT-A2-04 cap 隔离与 guard 覆盖(`tests/acceptance/test_f007_agent_ctx.py`);另:close 幂等双触发、closed 后 submit → BUSY、enter 幂等回归。

## 关联文档

- PRD-Core.md §2.2(模块2)/§2.3(ctx.* 挂载表)/§2.4(数据流步 1-4)/§2.5(启动六步/强同步三类)
- DIS-CORE.md §2(本模块伪代码级唯一权威)、§0.2(会话/run 口径、finished 归属)
- DIS-SEAM.md §2.4(能力生命周期 enter→announce→detach 状态机)、§2.5(locator 挂摘)
- EVENT-SCHEMA.md §3.1(session.created/finished 字段与校验)、§3.3(registry.updated)
- ERR.md §2.2/§2.10(EVT-104/EVT-106)、§2.11(BUSY 字面量);ADD.md ADR-002/ADR-010
