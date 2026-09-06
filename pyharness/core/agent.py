"""pyharness/core/agent.py — 会话实体 + ctx 门面(specs/agent.py.md 契约;阶段 1 核心)

Agent 是会话的宿主(F007/F064 联动):持有 agent_id ↔ session_id 1:1 句柄,管理
生命周期 init/ready/busy/stopping/closed,是能力 seam 的消费端总闸。ctx 门面
构造期按拓扑序注入脊柱 8 模块(persistence→session→{scope,llm,tools,
system_prompt}→loop),能力层经 ctx.* 命名空间挂载调用(attach_capability =
DIS-SEAM announce 语义的消费侧入口)。

核心语义:
- 一会话一 agent:spine.active_agents 占位登记 + _session_lock 双保险(防双
  loop 竞争 seq);create_agent 查重 → BUSY;close 释放。
- submit = 外壳唯一写口:状态校验 → user.message 强同步落盘(先落盘后执行,
  崩溃一致性锚点)→ loop.wake;headless 模式 run 结束自动 close(reason)。
- session.finished 全生命周期至多一条(EVT-104),唯一合法写入路径 = close;
  close 幂等(stopping/closed 直接返回)。
- 错误全走 raise_code(EVT-1xx/PERS-2xx/BUSY/TLB-8xx),禁裸 raise str。

偏离说明(契约以 specs/agent.py.md 为准,以下为与既有实现冲突处的取舍):
1. created payload 的 model 字段:spec 伪码用 config.llm.primary,但 config.py
   已落地键面为 llm.model(CFG.md §3 权威,DEFAULTS/ENV_WHITELIST 同键)→ 取
   cfg.llm.model;llm 缺省时回落 "deepseek-chat"。
2. headless 判定:config.Settings 无 headless 键(CFG.md R8:headless 由外壳按
   stdin 是否 tty 判定,无配置开关;load_settings 未知键被 extra=ignore 丢弃)→
   解析顺序 cfg.headless(如存在)→ spine.headless → False,见 _resolve_headless。
3. registry.updated 留痕:spec 伪码 append 入会话日志,但 events.vocab 把
   registry.updated 列为瞬时事件,已实现的 session.append 对瞬时事件拒写
   (EVT-100,见 session.py 校验链)→ attach/announce/detach 的留痕改经
   bus.emit 广播(与 "registry.updated 仅经总线分发" 的 events 层语义一致);
   相应地 announce 无落盘信封可返,返回 None(签名保持 Optional[Envelope])。
4. 命名空间遮蔽护栏:ctx 六命名空间 {agent,session,storage,sys,ui,tools} 中与
   脊柱注入成员重名者(session/tools/storage/sys)若直接 setattr 会遮蔽 agent
   自身依赖的 ctx.session 等 → 按 INV-08 "cap 不可覆盖脊柱对象" 拒绝(仍抛
   TLB-802);白名单外(ns=llm/scope/loop…)同样 TLB-802(GWT-A2-04 断言)。
5. submit 对 state=init 显式拒 BUSY(spec 状态机要求 enter 激活后方可 submit,
   伪码未列此态,补防御)。
6. close 落盘 finished 失败时回滚 state 至原态再上抛——spec 异常表要求 close
   "幂等可重入(PERS-202 repair 后重试)",若停在 stopping 则重试会被幂等短路,
   永远写不上 finished;finished 落盘后的终局清理(摘订阅/注销/逆序 detach)
   尽力而为,单步失败记日志不阻断终局达成。
7. _on_bus_event 的订阅模式 session:{sid}:* 按阶段 0 总线语义(按事件 type 前缀
   匹配)实际不命中任何会话事件——事件由 session 分发/落盘,此处订阅仅为
   规范占位 + 未来恢复信号(approval.granted → loop.resume)预留;resume 语义
   在单测中直接驱动 handler 验证。
"""
from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any, Optional

from pyharness.errors import PyHError, raise_code

if TYPE_CHECKING:  # 仅类型标注;装配对象一律鸭子注入,不硬 import 阶段后模块
    from pyharness.bus import EventBus, Registry
    from pyharness.config import Settings
    from pyharness.core.session import SessionLog
    from pyharness.events import Envelope

log = logging.getLogger("pyharness.agent")

# 生命周期合法取值(§状态机;类型标注 + 文档锚,不引入运行时枚举开销)
AGENT_STATES = ("init", "ready", "busy", "stopping", "closed")


def _raise_busy(**ctx: Any) -> None:
    """字面量 BUSY 上抛(BUSY = 无域前缀字面量码,ERR.md §2.11)。

    errors.raise_code 对未登记码兜底改判 CYC-999,故 BUSY 按 spec 伪码
    (raise PyHError("BUSY", ctx=…))直接构造——code 字段保持字面量 BUSY,
    供调用方/测试按 .code 断言;结构化错误(EVT/PERS/TLB)仍走 raise_code。
    """
    raise PyHError("BUSY", ctx=dict(ctx))


# ------------------------------------------------------------------ ctx 门面
class Ctx:
    """Agent.ctx 门面:内聚 session/scope/tools/llm/loop + 能力挂载面。

    构造期按拓扑序注入脊柱模块(依赖环不可能,INV-08);bus/registry/config/
    host 由 create_agent 装配期补线。能力经 attach_capability 以命名空间挂载
    (setattr),能力层只认 ctx 契约不认实现(原则 2)。
    """

    # 六命名空间白名单:能力挂载点(PRD §2.3);脊柱对象(scope/llm/loop/bus/
    # registry/config/host 等)不在白名单 → 挂载越界即 TLB-802(GWT-A2-04)。
    NAMESPACES: frozenset = frozenset(
        {"agent", "session", "storage", "sys", "ui", "tools"})

    def __init__(self, *, session: Any = None, scope: Any = None,
                 tools: Any = None, llm: Any = None, loop: Any = None,
                 sys: Any = None, storage: Any = None,
                 persistence: Any = None) -> None:
        # 脊柱 8 模块(按注入拓扑序;scope/llm/tools 可能为 None——阶段 1 装配期
        # 逐模块落地,agent 只依赖 session/loop/tools/bus/registry)
        self.session = session          # SessionLog 事件日志门面
        self.scope = scope              # 作用域:预算/窗口(F032 闸源)
        self.tools = tools              # 工具注册表(register_definition…)
        self.llm = llm                  # LLM 客户端(唯一 chat 出口归 agent_loop)
        self.loop = loop                # 三态机循环驱动器(wake/cancel/resume)
        self.sys = sys                  # 系统边界能力命名空间占位
        self.storage = storage          # 域存储(spill/kv)占位
        self.persistence = persistence  # JSONL 真源存储(SessionStore)
        # 装配期后补(create_agent 接线;None 哨兵 = 尚未注入)
        self.bus: Any = None            # EventBus:注册表/能力留痕出口
        self.registry: Any = None       # Registry:plugin/tool/capability 索引
        self.config: Any = None         # Settings 启动只读快照
        self.host: Any = None           # 能力宿主(close = 逆序 detach 全部 caps)

    def __repr__(self) -> str:  # 调试可读
        return (f"<Ctx session={getattr(self.session, 'sid', None)!r} "
                f"loop={getattr(self.loop, 'state', None)!r}>")


# ------------------------------------------------------------------ 能力宿主
class _CapabilityHost:
    """能力宿主适配器(DIS-SEAM §2.4):ctx.host.close = 逆序 detach 已装载能力。

    真正的 CapabilityHost(注册表驱动,阶段 3+)落地前,由本适配器把"会话关闭
    → 逆序 detach 全部 caps"的职责绑到 Agent 自身挂载面;半卸 > 僵尸——单个
    detach 内部消化失败,不阻断其余摘除(见 detach_capability)。
    """

    def __init__(self, agent: "Agent") -> None:
        self._agent = agent

    async def close(self) -> None:
        """逆序摘除全部已挂载能力(后挂先摘,与 attach 序相反)。"""
        for ns in reversed(list(self._agent.caps)):
            await self._agent.detach_capability(ns)


# ------------------------------------------------------------------ 装配辅助
def _resolve_headless(config: Any, spine: Any) -> bool:
    """headless 解析(config.Settings 无此键,CFG.md R8:外壳按 stdin tty 判定)。

    顺序:cfg.headless(显式注入/非 Settings 配置对象)→ spine.headless(装配层
    挂载的运行上下文)→ False(交互默认)。见模块 docstring 偏离 2。
    """
    for holder in (config, spine):
        if holder is None:
            continue
        try:
            v = getattr(holder, "headless", None)
        except Exception:  # noqa: BLE001 防御:第三方对象 getattr 钩子异常
            v = None
        if v is not None:
            return bool(v)
    return False


def _created_model(config: Any) -> str:
    """session.created payload 的 model 字段取值。

    spec 伪码用 config.llm.primary;config.py 已实现键面为 llm.model
    (CFG.md §3 权威)→ 取 llm.model,缺失回落默认模型名(见偏离 1)。
    """
    llm = getattr(config, "llm", None)
    if llm is not None:
        model = getattr(llm, "model", None)
        if model:
            return model
    return "deepseek-chat"


# ------------------------------------------------------------------ Agent
class Agent:
    """会话实体(F007/F064):一会话一 agent 句柄 + ctx 门面 + 生命周期管理。

    状态机(§specs/agent.py.md):
        INIT ─enter()→ READY ─submit()→ BUSY ─run 结束(loop idle)→ READY
        READY/BUSY ─close()→ STOPPING →(清理完成)→ CLOSED(终局)
        CLOSED 后 submit/enter → BUSY 显式拒(不排队不静默)

    字段:agent_id/session_id(1:1 不可换)、state、ctx(Ctx 门面)、caps(已挂载
    命名空间 → iface 束)、config(Settings 只读)、headless(自动关会话判定)、
    _session_lock(一会话一 agent 互斥:enter 抢占,close 释放)、_created_event
    (created 首事件守卫,EVT-106)。
    """

    def __init__(self, agent_id: str, session_id: str,
                 state: str = "init", config: Any = None,
                 caps: Optional[dict[str, Any]] = None, *,
                 headless: Optional[bool] = None) -> None:
        if state not in AGENT_STATES:  # 构造期即锁死合法态(防非法字面量漂移)
            _raise_busy(state=state, detail="非法生命周期初值")
        self.agent_id: str = agent_id
        self.session_id: str = session_id
        self.state: str = state
        self.config: Any = config              # CFG 启动只读快照(Settings)
        self.caps: dict[str, Any] = dict(caps or {})   # 已挂载能力命名空间
        # headless 决定 run 结束是否自动 close;显式注入优先,否则回落配置
        self.headless: bool = (bool(headless) if headless is not None
                               else _resolve_headless(config, None))
        self.ctx: Ctx = None                   # 门面:create_agent 硬接线注入
        self._spine: Any = None                # 装配束引用(active_agents 释放)
        self._session_lock: bool = False       # 一会话一 agent 互斥(enter 抢占)
        self._created_event: bool = False      # created 已落守卫(EVT-106)
        self._registered: bool = False         # registry 插件注册记账(幂等注销)

    # ========================================================== 会话占位激活
    async def enter(self) -> None:
        """会话占位激活:引导 created(新会话 seq=1)→ 注册 → 订阅 → ready。

        幂等:已 ready/busy 直接返回;stopping/closed 拒(BUSY,建议新建 agent)。
        失败回滚:释放 active_agents 占位与注册表项,保持可重试(enter 失败则
        回滚释放,spec 工厂注释语义)。
        """
        if self.state in ("ready", "busy"):
            return                                # 幂等:已激活直接返回
        if self.state in ("stopping", "closed"):
            _raise_busy(advice="句柄已关闭,请 create 新 agent")
        try:
            # 新会话引导:首事件 seq=1(EVT-106 守卫由 session.append 把关);
            # 会话已有事件(恢复回放)则 created 已在日志,跳过追加
            if (not self._created_event
                    and self.ctx.session.stats()["seq"] == 0):
                await self.ctx.session.append(
                    "session.created",
                    {"title": "", "model": _created_model(self.config)},
                    actor="system", sync=True)
            self._created_event = True
            # F003:注册自身,可被外壳/宿主寻址(plugin:{agent_id})
            self.ctx.registry.register("plugin", self.agent_id, self)
            self._registered = True
            # 订阅本会话事件流(owner=agent_id,close 时按属主精确摘除)
            self.ctx.bus.subscribe(f"session:{self.session_id}:*",
                                   self._on_bus_event, owner=self.agent_id)
            self.state = "ready"
            self._session_lock = True             # 抢占会话互斥(占用占位)
            log.info("agent ready", agent_id=self.agent_id,
                     session=self.session_id)
        except BaseException:
            self._rollback_enter()
            raise

    def _rollback_enter(self) -> None:
        """enter 失败回滚:注销已注册插件项 + 释放会话占位(半激活 > 僵尸)。"""
        if self._registered:
            try:
                self.ctx.registry.unregister("plugin", self.agent_id)
            except PyHError as e:
                if e.code != "TLB-801":
                    log.error("enter rollback unregister failed",
                              code=e.code)
            self._registered = False
        self._release_occupancy()

    def _release_occupancy(self) -> None:
        """释放会话占位:active_agents 摘除 + 互斥锁复位(close/回滚共用)。"""
        self._session_lock = False
        aa = getattr(self._spine, "active_agents", None)
        if aa is not None and aa.get(self.session_id) is self:
            aa.pop(self.session_id, None)

    # ========================================================== 外壳唯一入口
    async def submit(self, text: str, *,
                     meta: Optional[dict] = None) -> Any:
        """外壳唯一写口:user.message 强同步落盘 → loop.wake(idle 拉起/入队)。

        三壳(CLI/桌面/ACP)平权同路径,无特权通道;写操作与用户输入同级过
        session.append 校验链。headless=True 且 run 同步结束时自动 close。
        返回 loop.wake 的结果(RunResult | None;RunResult 属 agent_loop 模块,
        此处鸭子返回)。meta 为外壳透传参数(当前规范未消费,保留签名)。

        异常:BUSY(未 enter/已关闭)、EVT-100(空消息)、PERS-202(强同步落盘
        失败,repair 后重试——输入未生效不丢话)。
        """
        if self.state == "init":                  # 状态机:enter 后方可 submit
            _raise_busy(advice="agent 未激活:先 await enter() 再 submit")
        if self.state in ("stopping", "closed"):
            _raise_busy(advice="会话已关闭,请新开会话")
        if not text or not text.strip():          # 空消息:信封层前置拒绝
            raise_code("EVT-100", field="content", advice="消息不能为空")
        self.state = "busy"                       # busy = loop running 透传
        try:
            # 强同步①:user.message 落盘成功才继续(崩溃一致性锚点,§3.6)
            env = await self.ctx.session.append(
                "user.message", {"content": text}, actor="user", sync=True)
            result = await self.ctx.loop.wake(env)   # idle→run;running→入队
            if self.headless and result is not None: # headless:run 结束即关会话
                await self.close(reason=getattr(result, "reason", "complete"))
            return result
        finally:
            # 交互模式:run 自然结束(loop 回 idle)且会话保持 → 回 READY
            if (self.state == "busy"
                    and getattr(self.ctx.loop, "state", None) == "idle"):
                self.state = "ready"

    # ========================================================== 会话关闭
    async def close(self, reason: str = "idle") -> None:
        """会话关闭(session.finished 唯一归属;幂等)。

        流程:stopping 置位 → 在途 run 声明式取消(cancel reason=close)→
        session.finished 强同步落盘(EVT-104 单次;失败回滚 state 供重试)→
        终局清理(close_marker/摘订阅/注销插件句柄/逆序 detach 全部 caps,
        尽力而为)→ 释放会话互斥 → closed。

        异常:EVT-104(日志已有 finished = 绕过本路径的写口,INV-01 追查)、
        PERS-202(finished 落盘失败,state 回滚,repair 后重试可重入)。
        """
        if self.state in ("stopping", "closed"):
            return                                  # 幂等:双触发(外壳/repair)安全
        prev, self.state = self.state, "stopping"
        # 1) 有在途 run(running/paused)→ 先声明式取消(协作式,不杀进程)
        if getattr(self.ctx.loop, "state", None) in ("running", "paused"):
            await self.ctx.loop.cancel(reason="close")
        # 2) finished 唯一写路径:失败回滚 state → close 可重入(见偏离 6)
        try:
            await self.ctx.session.append("session.finished",
                                          {"reason": reason},
                                          actor="system", sync=True)
        except BaseException:
            self.state = prev
            raise
        # 3) 终局清理:单步失败记日志不阻断终局(半卸 > 僵尸)
        try:
            self.ctx.session.close_marker()          # SessionLog._closed=True
            self.ctx.bus.unsubscribe_all(owner=self.agent_id)  # 摘订阅
            if self._registered:                     # 释放句柄寻址
                try:
                    self.ctx.registry.unregister("plugin", self.agent_id)
                except PyHError as e:                # TLB-801:已摘(双触发/repair)
                    if e.code != "TLB-801":
                        raise
                finally:
                    self._registered = False
            await self.ctx.host.close()              # 逆序 detach 全部 caps
        except PyHError as e:
            log.error("agent close cleanup failed", agent_id=self.agent_id,
                      code=e.code, exc=str(e))
        finally:
            self._release_occupancy()                # 释放会话互斥(可新开)
            self.state = "closed"                    # 终局
        log.info("agent closed", agent_id=self.agent_id,
                 session=self.session_id, reason=reason)

    # ========================================================== 能力挂载面
    async def attach_capability(self, ns: str, iface: Any) -> None:
        """能力挂载(announce 语义消费侧入口):白名单校验 → 挂载 → announce。

        ns 越出六命名空间白名单 → TLB-802;ns 与脊柱注入成员重名(遮蔽 agent
        自身依赖)→ TLB-802(INV-08 cap 不可覆盖);重复挂载 → TLB-801。
        挂载成功后 ctx.{ns} 立即可消费,并 announce 留痕广播(DIS-SEAM §2.4)。
        """
        if ns not in Ctx.NAMESPACES:                 # 白名单越界(TLB-802)
            raise_code("TLB-802", what=f"ctx.{ns}",
                       advice="脊柱命名空间不接受插件挂载(INV-08);"
                              "脊柱子系统注册走 BUS-002")
        if ns in self.caps:
            raise_code("TLB-801", what=f"ctx.{ns}",
                       detail=f"命名空间 {ns} 已挂载:先 detach_capability 再挂")
        if hasattr(self.ctx, ns):                    # 遮蔽脊柱成员 = 破坏依赖
            raise_code("TLB-802", what=f"ctx.{ns}",
                       advice="命名空间与脊柱注入成员重名,cap 不可覆盖(INV-08)")
        self.caps[ns] = iface                        # iface = Definition + Provider 束
        setattr(self.ctx, ns, iface)                 # 挂载后 ctx.{ns} 立即可消费
        await self.announce(ns)                      # registry.updated 留痕广播

    async def announce(self, ns: str) -> Optional["Envelope"]:
        """能力就绪宣布:registry.updated 广播 + expose_to_llm 进 tools 注册表。

        无此能力(attach 前调用)→ 静默返回 None。留痕为瞬时总线广播(session
        append 拒瞬时事件,见偏离 3),无落盘信封可返 → 恒返回 None。
        """
        iface = self.caps.get(ns)
        if iface is None:
            return None                              # 无此能力:静默
        if getattr(iface, "expose_to_llm", False):   # 声明对 LLM 可见
            self.ctx.tools.register_definition(iface.definition)
        await self.ctx.bus.emit(                     # F003 语义留痕(瞬时)
            "registry.updated",
            {"op": "attach", "kind": "capability", "key": ns})
        return None

    async def detach_capability(self, ns: str) -> None:
        """能力摘除(幂等):注销工具定义 → ctx 摘除 → registry.updated(op=del)。

        未挂载 → 直接返回;单步失败内部消化并告警留痕,继续摘除剩余步骤
        (半卸 > 僵尸,spec:detach 抛错仍继续,失败标记 broken)。
        """
        if ns not in self.caps:
            return                                   # 幂等:已 detached
        iface = self.caps.pop(ns)
        try:
            if getattr(iface, "expose_to_llm", False):
                self.ctx.tools.unregister_definition(iface.definition.name)
            if hasattr(self.ctx, ns):
                delattr(self.ctx, ns)                # ctx 门面摘除
            await self.ctx.bus.emit(                 # op=del 留痕(瞬时)
                "registry.updated",
                {"op": "del", "kind": "capability", "key": ns})
        except Exception as e:                       # 半卸 > 僵尸:告警不中断
            log.error("cap detach failed", ns=ns, exc=e)
            try:
                await self.ctx.bus.emit("bus.backpressure",
                                        {"ns": ns, "broken": True})
            except Exception:                        # noqa: BLE001 告警自身失败
                log.error("cap detach alert failed", ns=ns)

    # ========================================================== 事件订阅回调
    async def _on_bus_event(self, type_: str, env: Any) -> None:
        """会话事件订阅入口(owner=agent_id;按 session:{sid}:* 前缀投递)。

        前缀订阅兜底过滤(env.session_id 必须为本会话);普通事件仅可见性钩子,
        事实已在日志,禁止改动状态;loop paused + approval.granted → resume
        (审批通过 → 重入 guard 链起点)。订阅者异常由总线 EVT-103 封装隔离。
        """
        if getattr(env, "session_id", None) != self.session_id:
            return                                   # 前缀订阅兜底过滤
        if (type_ == "approval.granted"
                and getattr(self.ctx.loop, "state", None) == "paused"):
            await self.ctx.loop.resume()             # 审批通过 → 重入 guard 链
        # 其余事件:事实已在日志,此处只做可见性钩子,禁止改动状态

    # ========================================================== 只读查询
    def snapshot(self) -> dict:
        """只读句柄查询(外壳/审计):纯只读,禁止由此路径修改状态。"""
        loop_state = getattr(self.ctx.loop, "state", None) if self.ctx else None
        seq = 0
        if self.ctx is not None and self.ctx.session is not None:
            seq = self.ctx.session.stats()["seq"]
        pending = getattr(self.ctx.loop, "pending", None) if self.ctx else None
        return {
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "state": self.state,
            "loop_state": loop_state,
            "seq": seq,
            "queue_len": len(pending) if pending is not None else 0,
            "caps": sorted(self.caps),
        }


# ------------------------------------------------------------------ 工厂
def create_agent(session_id: str, spine: Any, cfg: Any) -> Agent:
    """会话实体工厂:脊柱硬接线 + ctx 装配 + 会话占位登记(纯构造,不 activate)。

    一会话一 agent(DIS §2.6-1):active_agents 已有同会话 agent → BUSY(防双
    loop 竞争 seq)。构造 state=init,必须经 await agent.enter() 激活方可
    submit;enter 失败则回滚释放占位。

    异常:BUSY(同会话已有 active agent / state 非法)、TLB-801(enter 阶段
    registry 重名注册,由 enter 上抛)。

    参数:spine = 8 模块束(session/bus/registry/scope/tools/llm/loop/… +
    active_agents 占位表);cfg = 分层配置 Settings。
    """
    aa = getattr(spine, "active_agents", None)
    if aa is None:                     # 防御:装配束未建占位表 → 就地补建
        aa = {}
        spine.active_agents = aa
    if session_id in aa:
        _raise_busy(session_id=session_id,
                   advice="该会话已有 active agent,请复用句柄或新开会话")
    ag = Agent(agent_id=uuid.uuid4().hex, session_id=session_id,
               state="init", config=cfg, caps={},
               headless=_resolve_headless(cfg, spine))
    # ctx 装配:脊柱 8 模块按拓扑序注入(依赖环不可能,INV-08);sys/storage/
    # persistence 允许缺席(阶段装配期逐模块落地,getattr 兜底 None)
    ag.ctx = Ctx(session=spine.session, scope=getattr(spine, "scope", None),
                 tools=getattr(spine, "tools", None),
                 llm=getattr(spine, "llm", None),
                 loop=getattr(spine, "loop", None),
                 sys=getattr(spine, "sys", None),
                 storage=getattr(spine, "storage", None),
                 persistence=getattr(spine, "persistence", None))
    ag.ctx.bus = spine.bus                       # 事件总线(留痕/订阅出口)
    ag.ctx.registry = spine.registry             # 注册表(F003 寻址)
    ag.ctx.config = cfg
    ag.ctx.host = _CapabilityHost(ag)            # 关闭时逆序 detach 全部 caps
    ag._spine = spine                            # 释放占位需回写装配束
    aa[session_id] = ag                          # 占位登记(enter 失败回滚释放)
    return ag


__all__ = ["Agent", "Ctx", "create_agent"]
