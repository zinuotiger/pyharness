"""SubagentLifecycle operations, separated from facade assembly."""
from __future__ import annotations
from pyharness.core.lifecycle import stop_admissions
from typing import Any
from pyharness.errors import PyHError, raise_code
from pyharness.core.subagent_models import log, TOOL_NAME, EVENT_TYPES, SubagentSpec
from pyharness.core.subagent_provider import SubagentSpawnProvider

class SubagentLifecycle:
    """SubagentLifecycle responsibility; state is owned by the composing facade."""

    async def enter(self, ctx: Any = None) -> None:
        """能力装载(DIS-SEAM §2.4 第①动作):登记 subagent.* 事件类型 schema。

        幂等:已 enter 直接返回(重复 enter 静默通过)。事件类型按 EventBus 实例
        弱引用去重——同 bus 二次 enter(含 detach 后重入)不撞 register_type 的
        EVT-102(偏离 9);登记失败(真冲突/总线异常)→ 回 detached 不 announce。
        """
        if self._entered:
            return
        bus = self._bus if ctx is None else (getattr(ctx, "bus", None)
                                             or self._bus)
        self._closing = False
        if bus is not None:
            self._register_bus_types(bus)
        self._entered = True
        log.info("subagent capability entered session=%s",
                 getattr(self._session, "sid", None))

    def _register_bus_types(self, bus: Any) -> None:
        """EventBus 事件类型登记(弱引用去重;失败上抛 → enter 失败回 detached)。"""
        try:
            if bus in self._typed_buses:
                return                          # 该总线已登记(重入/多 manager 共享)
        except TypeError:                       # 不可弱引用对象:退化为逐次尝试
            pass
        for t in EVENT_TYPES:
            bus.register_type(t, None)          # 二次注册 EVT-102 → 上抛
        try:
            self._typed_buses[bus] = None
        except TypeError:                       # 同上:仅本次登记失败,不阻断
            pass

    def definition(self) -> Any:
        """subagent.spawn 工具 Definition(announce 第③步注册给 LLM)。

        danger=none:派发本身不越权,子任务内动作仍过各自 guard(F014/F015 同权,
        无父级担保);approval=never 与 none 级一致(ToolDefinition 契约)。schema 为
        JSON-Schema dict(注册时编译 F026);depth 不进 LLM 参数(递归层级由装配层
        依 ctx 推 depth=self.depth+1,防 LLM 谎报层级绕过递归闸)。
        """
        from pyharness.core.tools_registry import ToolDefinition
        return ToolDefinition(
            name=TOOL_NAME,
            description="派生子 agent 并行处理可隔离子任务(独立 session,预算=父×1/4,"
                        "深度≤3),完成返回 ≤2KB 摘要",
            schema={"type": "object",
                    "properties": {
                        "task": {"type": "string",
                                 "description": "委派意图(可隔离子任务描述)"},
                        "tools_subset": {"type": "array", "items": {"type": "string"},
                                         "description": "只收窄子任务可用工具(可选)"},
                        "deny_extra": {"type": "array", "items": {"type": "string"},
                                       "description": "追加收紧工具名(可选)"},
                        "creds_allowed": {"type": "array", "items": {"type": "string"},
                                          "description": "子任务所需凭据名子集(可选)"},
                        "notify": {"type": "boolean",
                                   "description": "完成是否回写 joined 事件(默认 true)"}},
                    "required": ["task"]},
            danger="none", approval="never", owner=self._owner,
            ctx_path="ctx.agent.subagent", version="1.0.0")

    async def announce(self, ctx: Any = None) -> None:
        """对外宣布(DIS-SEAM §2.4 第②动作,五步):注册工具 → 挂订阅 → mount →
        registry.updated 留痕;任一步失败回滚已做步骤并回 detached(半装必回滚)。

        已 announce 幂等返回。mount 目标 = ctx.agent 命名空间(装配层未来 locator
        宿主;未挂 agent 命名空间时日志降级不阻断——工具已注册,主循环仍可经
        tools.execute 触达)。
        """
        if self._announced:
            return
        done: list[str] = []
        try:
            ctx = ctx or self._announce_ctx
            bus = self._bus if ctx is None else (getattr(ctx, "bus", None)
                                                 or self._bus)
            tools = self._tools if ctx is None else (getattr(ctx, "tools", None)
                                                     or self._tools)
            if tools is not None:
                # ① ③ LLM 可见:register_tool 绑 Provider(spawn+join 适配器,偏离 8)
                register = getattr(tools, "register_tool", None) or \
                    getattr(tools, "register_definition", None)
                if callable(register):
                    try:
                        register(self.definition(), provider=SubagentSpawnProvider(self))
                    except TypeError:
                        register(self.definition())     # 无 provider 面的注册表别名
                    done.append("tool")
            if bus is not None:
                # ② 挂订阅:session.closing 预关钩子(规范预留,偏离 11)
                bus.subscribe("session.closing", self._on_session_closing,
                              owner=self._owner)
                done.append("sub")
            # ④ mount ctx.agent.subagent(定位器;可缺省降级)
            mounted = self._mount(ctx, "subagent")
            if mounted:
                done.append("mount")
            if bus is not None:
                # ⑤ registry.updated 留痕(瞬时事件,仅总线)
                await bus.emit("registry.updated",
                               {"op": "attach", "kind": "capability",
                                "key": "ctx.agent.subagent"})
            self._announce_ctx = ctx
            self._announced = True
            log.info("subagent capability announced session=%s",
                     getattr(self._session, "sid", None))
        except BaseException:
            for step in reversed(done):         # 半装必回滚(逆序)
                try:
                    self._rollback(step, ctx)
                except Exception:               # noqa: BLE001 回滚自身失败不吞原错
                    log.exception("announce 回滚失败 step=%s", step)
            self._entered = False               # 回 detached(可重入重试)
            raise

    def _rollback(self, step: str, ctx: Any) -> None:
        """announce 单步回滚(逆序摘除已做步骤;TLB-802 = 已摘,幂等静默)。"""
        if step == "tool":
            tools = self._tools if ctx is None else (getattr(ctx, "tools", None)
                                                     or self._tools)
            if tools is not None:
                unreg = getattr(tools, "unregister_definition", None) or \
                    getattr(tools, "unregister", None)
                if callable(unreg):
                    try:
                        unreg(TOOL_NAME)
                    except PyHError as e:
                        if e.code != "TLB-802":
                            log.warning("announce rollback 注销失败 code=%s", e.code)
        elif step == "sub":
            bus = self._bus if ctx is None else (getattr(ctx, "bus", None)
                                                 or self._bus)
            if bus is not None:
                bus.unsubscribe_all(owner=self._owner)
        elif step == "mount":
            self._unmount(ctx)

    async def detach(self, ctx: Any = None) -> None:
        """能力摘除,child-first(DIS-SEAM §2.4 第③动作):先杀光在途子任务并落
        终态事件,再摘订阅/注销工具/unmount;单步失败记 broken 告警仍继续摘除
        (半卸 > 僵尸);幂等(重复调用安全)。
        """
        if not self._entered and not self._announced and not self._children:
            return                             # 幂等:无装载无在途 → 无事可做
        await stop_admissions(self)
        cleanup_errors = []
        # ① child-first:先杀孩子(取消传播 F025 + 终态事件 + 等全部真正退出)
        try:
            await self._cancel_all_children(reason="parent-detach")
        except BaseException as exc:
            cleanup_errors.append(exc)
        ctx = ctx or self._announce_ctx
        bus = self._bus if ctx is None else (getattr(ctx, "bus", None)
                                             or self._bus)
        tools = self._tools if ctx is None else (getattr(ctx, "tools", None)
                                                 or self._tools)
        broken: list[str] = []
        # ② 摘订阅(owner 精确摘除;幂等)
        if bus is not None:
            try:
                bus.unsubscribe_all(owner=self._owner)
            except Exception as exc:           # noqa: BLE001 半卸 > 僵尸
                log.warning("detach 摘订阅失败: %s", exc)
                broken.append("sub")
        # ③ 注销工具(TLB-802 = 已摘,幂等静默)
        if tools is not None:
            unreg = getattr(tools, "unregister_definition", None) or \
                getattr(tools, "unregister", None)
            if callable(unreg):
                try:
                    unreg(TOOL_NAME)
                except PyHError as e:
                    if e.code != "TLB-802":
                        log.warning("detach 注销工具失败 code=%s", e.code)
                        broken.append("tool")
                except Exception as exc:       # noqa: BLE001
                    log.warning("detach 注销工具异常: %s", exc)
                    broken.append("tool")
        # ④ unmount(ctx.agent.subagent)
        try:
            self._unmount(ctx)
        except Exception as exc:               # noqa: BLE001
            log.warning("detach unmount 失败: %s", exc)
            broken.append("mount")
        # ⑤ registry.updated op=del 留痕(尽力而为)
        if bus is not None and not broken:
            try:
                await bus.emit("registry.updated",
                               {"op": "del", "kind": "capability",
                                "key": "ctx.agent.subagent"})
            except Exception:                  # noqa: BLE001
                log.warning("detach registry.updated 留痕失败")
        self._announced = False
        self._entered = False
        self._children.clear()
        if broken:
            cleanup_errors.append(RuntimeError("subagent detach incomplete: " + ",".join(broken)))
        if cleanup_errors:
            raise BaseExceptionGroup("subagent detach failed", cleanup_errors)

    def _mount(self, ctx: Any, key: str) -> bool:
        """locator.mount:把本 manager 挂到 ctx.agent 命名空间的 subagent 键。

        挂载前记录旧值(detach 恢复);ctx.agent 命名空间未建(装配层 locator 未
        接线)→ 日志降级返回 False(工具面仍可用,不阻断 announce)。
        """
        if ctx is None:
            return False
        ns = getattr(ctx, "agent", None)
        if ns is None:
            log.warning("ctx.agent 命名空间未挂载,ctx.agent.subagent 未 mount"
                        " (tools.execute 面仍可用)")
            return False
        self._mount_ns = ns
        self._mount_prev = getattr(ns, key, None)
        try:
            setattr(ns, key, self)
            return True
        except Exception as exc:               # noqa: BLE001 只读/受限目标
            log.warning("mount ctx.agent.%s 失败: %s", key, exc)
            self._mount_ns = None
            self._mount_prev = None
            return False

    def _unmount(self, ctx: Any = None) -> None:
        """locator.unmount 逆操作:仅当现值仍为本 manager 时摘除并恢复旧值。"""
        ns = self._mount_ns
        if ns is None and ctx is not None:
            ns = getattr(ctx, "agent", None)
        if ns is None:
            return
        key = "subagent"
        if getattr(ns, key, None) is self:
            if self._mount_prev is None:
                try:
                    delattr(ns, key)
                except Exception:              # noqa: BLE001
                    log.debug("subagent namespace unmount failed", exc_info=True)
            else:
                try:
                    setattr(ns, key, self._mount_prev)
                except Exception:              # noqa: BLE001
                    log.debug("subagent namespace restore failed", exc_info=True)
        self._mount_ns = None
        self._mount_prev = None

    def _parent_anchor(self, ctx: Any) -> int:
        """发起轮锚(spawned payload.parent_seq):ctx.round_seq(spec 命名)→ 主循环
        run.input_seq → 父会话当前 seq;均无 → EVT-100(父会话未引导,EVT-106 语义)。
        """
        if ctx is not None:
            rs = getattr(ctx, "round_seq", None)
            if isinstance(rs, int) and rs >= 1:
                return rs
            loop = getattr(ctx, "loop", None)
            cur = getattr(loop, "current", None) if loop is not None else None
            iq = getattr(cur, "input_seq", None) if cur is not None else None
            if isinstance(iq, int) and iq >= 1:
                return iq
        sess = self._session
        if sess is not None and hasattr(sess, "stats"):
            last = sess.stats().get("seq", 0)
            if last >= 1:
                return last
        raise_code("EVT-100", hint="父会话未引导或无发起轮锚(parent_seq 需 ≥1)",
                   advice="子派发须在父会话 created 后、运行轮内发起")

    def _validate_spec(self, spec: SubagentSpec) -> None:
        """派发前 spec 校验(异常表:非法 → EVT-100):空 task/非正值或超上限
        budget_ratio/tools_subset 与 deny_extra、creds_allowed 类型/层级非 int。"""
        if not isinstance(spec, SubagentSpec):
            raise_code("EVT-100", hint="spec 须为 SubagentSpec 实例")
        if not spec.task or not str(spec.task).strip():
            raise_code("EVT-100", field="task", hint="委派任务为空,spawn 拒绝")
        if not isinstance(spec.budget_ratio, (int, float)) \
                or not (0 < float(spec.budget_ratio) <= self._budget_ratio):
            raise_code("EVT-100", field="budget_ratio",
                       ratio=spec.budget_ratio, cap=self._budget_ratio,
                       hint=f"预算比例须 ∈ (0, {self._budget_ratio}](≤config "
                            f"budget.subagent_ratio;默认父×1/4)")
        if spec.tools_subset is not None and not isinstance(spec.tools_subset, list):
            raise_code("EVT-100", field="tools_subset",
                       hint="tools_subset 须为 list[str] 或 None")
        for t in spec.tools_subset or []:
            if not isinstance(t, str):
                raise_code("EVT-100", field="tools_subset", tool=t,
                           hint="tools_subset 元素须为工具名字符串")
        if not isinstance(spec.deny_extra, list) \
                or not isinstance(spec.creds_allowed, list):
            raise_code("EVT-100", hint="deny_extra/creds_allowed 须为 list")
        if not isinstance(spec.depth, int):
            raise_code("EVT-100", field="depth", depth=spec.depth,
                       hint="depth 须为 int(递归层级)")

    async def _acquire_slot(self) -> None:
        """并发闸(BUSY 拒新,不排队;偏离 4):满 → BUSY,语义同 JOB-001 族。"""
        if self._active >= self._max_concurrent:
            raise PyHError("BUSY", ctx={
                "hint": f"子 agent 并发已达上限 {self._max_concurrent}",
                "advice": "先 join/取消若干在途子任务再派发(F049 并发边界 ≤8)"})
        self._active += 1                       # 计数增减间无 await → 原子

    def _release_slot(self) -> None:
        """并发闸释放(子任务终态调用;防双放由调用方 slot 标记兜底)。"""
        if self._active > 0:
            self._active -= 1

