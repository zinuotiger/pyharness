"""SubagentExecution operations, separated from facade assembly."""
from __future__ import annotations
from pyharness.core.lifecycle import admitted, stop_admissions
import asyncio
import inspect
import time
from typing import Any, Optional
from pyharness.errors import PyHError, raise_code, wrap_unexpected
from pyharness.core.subagent_models import log, SUBA_OWNER, _TERMINAL, SubagentSpec, ChildContext, ChildHandle, SubagentStatus, summarize, _scale_limits

class SubagentExecution:
    """SubagentExecution responsibility; state is owned by the composing facade."""

    @admitted
    async def spawn(self, spec: SubagentSpec, ctx: Any = None) -> str:
        """F049 核心入口:四道约束闸(深度/工具子集在册/并发/预算)全过 → 建独立
        子会话 → 父会话落 subagent.spawned → 登记 ChildHandle 并起 _run_child;
        返回 sub_id(join 的合法凭据,结果只可 join 一次)。

        异常:BUSY(深度 >3/层级不递增/并发满)、TLB-802(工具子集含未注册名)、
        EVT-100(spec 非法)、EVT-104(父会话已 finished——session.append 校验链拒)。
        任一步失败:并发闸已占则释放,零残留(无脏登记/无孤儿会话引导)。
        """
        self._ensure_session()
        self._validate_spec(spec)
        if spec.depth > self._max_depth:        # ① 递归闸上界 ≤3(PRD F049)
            raise PyHError("BUSY", ctx={
                "hint": f"子任务深度 {spec.depth} 超上限 {self._max_depth}",
                "advice": "子任务不得再嵌套超过 3 层;考虑平铺任务或合入父意图"})
        if spec.depth < self._depth + 1:        # 递归闸递增链(偏离 5)
            raise PyHError("BUSY", ctx={
                "hint": f"子任务深度 {spec.depth} 未达本层 {self._depth} + 1",
                "advice": "深度必须逐层 +1(平级/降级派发会绕过递归闸)"})
        # ② 工具子集必须已注册(幻觉名拦截;tools 未接线 → 日志降级跳过)
        tools = self._tools
        if ctx is not None:
            tools = tools or getattr(ctx, "tools", None)
        if tools is not None:
            has = getattr(tools, "has", None)
            for t in spec.tools_subset or []:
                if has is not None and not has(t):
                    raise_code("TLB-802", tool=t,
                               advice="tools_subset 含未注册工具;核对注册表,删幻觉名")
        # ④ 预算 = 父 × spec.budget_ratio(计入父任务:charge_hook 注入位,偏离 3)
        parent_scope = self._parent_scope
        if ctx is not None:
            parent_scope = getattr(ctx, "scope", None) or parent_scope
        limits = None
        if parent_scope is not None:
            limits = _scale_limits(getattr(parent_scope, "limits", None),
                                   float(spec.budget_ratio))
        # ③ 并发闸(满 → BUSY;随后任何失败路径都释放,零残留)
        await self._acquire_slot()
        sub_id = None
        try:
            sub_id = self._child_id_factory()
            child = self._session_factory(sub_id)
            child_scope = None
            factory = self._child_scope_factory
            if callable(factory):
                child_scope = factory(parent_scope, sub_id, child, limits,
                                      spec.deny_extra)
            if parent_scope is not None and child_scope is None:
                raise PyHError('GRD-403', ctx={'reason': 'child_scope_unavailable'})
            if child_scope is not None and spec.tools_subset is not None:
                permitted = set(spec.tools_subset)
                inherited = getattr(child_scope.policy, 'allowed_tools', None)
                child_scope.policy.allowed_tools = permitted if inherited is None else permitted & inherited
            anchor = self._parent_anchor(ctx)
            # 子会话引导(独立 seq=1;真实 JSONL 由装配注 persistence)
            await self._bootstrap_child(child, sub_id)
            # 父会话只记委派事实(普通落盘;actor=agent,EVENT-SCHEMA §3.5.3)
            env = await self._session.append(
                "subagent.spawned",
                {"sub_id": sub_id, "parent_seq": anchor, "task": spec.task},
                actor="agent", origin=SUBA_OWNER)
            h = ChildHandle(sub_id=sub_id, parent_sid=self._session.sid,
                            parent_seq=env.seq, spec=spec, session=child,
                            state="spawning",
                            result=asyncio.get_running_loop().create_future(),
                            started_ts=time.time(),
                            budget_limits=limits, scope=child_scope,
                            slot=True)
            h.env = ChildContext(sub_id=sub_id, session=child, spec=spec,
                                 scope=child_scope, budget_limits=limits,
                                 parent_sid=self._session.sid)
            self._children[sub_id] = h
            h.task = asyncio.create_task(self._run_child(h))
            h.state = "running"
            log.info("subagent spawned sub=%s parent=%s depth=%d task=%.60s",
                     sub_id, self._session.sid, spec.depth, spec.task)
            return sub_id
        except BaseException as primary:
            self._release_slot()
            if sub_id is not None:
                self._children.pop(sub_id, None)
                cleanup = getattr(self._runner, 'close_child', None)
                if callable(cleanup):
                    try:
                        result = cleanup(sub_id)
                        if inspect.isawaitable(result): await result
                    except BaseException as secondary:
                        primary.add_note(f'child rollback failed: {secondary!r}')
            raise

    async def _invoke_child(self, h: ChildHandle) -> Any:
        """子执行器调用 seam(偏离 7):runner.run_child(env) 异步/同步兼容。

        未装配 runner → CYC-999 快速失败(防 S-1"伪造已执行":宁失败不静默假完成,
        task_queue._run_runner 同款)。F007 唯一 LLM 入口纪律(INV-02)由 runner
        内部保证——子任务任何 LLM/工具动作都只能发生在 runner 提供的子循环内。
        """
        runner = self._runner
        if runner is None:
            raise_code("CYC-999", hint="SubagentManager 未装配子会话执行器",
                       advice="装配层注入 agent_loop 适配 runner(INV-02 纪律)")
        fn = getattr(runner, "run_child", runner)
        if not callable(fn):
            raise_code("CYC-999", hint=f"子执行器不可调用: {type(runner).__name__}")
        res = fn(h.env)
        if inspect.isawaitable(res):
            res = await res
        if self._charge_hook is not None:       # 子用量计入父任务(偏离 3)
            try:
                r = self._charge_hook(h.env)
                if inspect.isawaitable(r):
                    await r
            except Exception as exc:            # noqa: BLE001 记账失败不阻断回收
                log.warning("charge_hook 失败 sub=%s: %s", h.sub_id, exc)
        return res

    async def _run_child(self, h: ChildHandle) -> None:
        """子会话执行与回收(F049 核心):委派意图交子执行器(独立 session 上跑,
        不占用父会话 running)→ 终态摘要(≤2KB)→ subagent.joined;PyHError/取消 →
        subagent.failed / cancelled;finally 释放并发闸 + 摘登记(已回收即摘)。

        取消分支不吞:re-raise CancelledError 沿 await 链传播(F025);终态审计事件
        由 cancel()/_cancel_all_children() 统一落(见规格 _run_child 注释)。
        """
        try:
            out = await self._invoke_child(h)           # F007 子循环(seam)
            summary = summarize(out, self._summary_max_chars)   # ≤2KB(数据身份)
            if h.spec.notify:                           # 回收写父会话(普通落盘)
                await self._write_event(
                    "subagent.joined",
                    {"sub_id": h.sub_id, "summary": summary},
                    actor="agent", trace={"parent_seq": h.parent_seq})
            h.summary, h.state = summary, "done"
            if h.result is not None and not h.result.done():
                h.result.set_result(summary)
        except asyncio.CancelledError:                  # F025 取消传播:不吞
            h.state = "cancelled"
            if h.result is not None and not h.result.done():
                h.result.cancel()                       # 唤醒 join 等待者(取消态)
            raise
        except PyHError as e:                           # 结构化失败:原码透传
            h.error_code, h.state = e.code, "failed"
            if h.spec.notify:
                await self._write_event(
                    "subagent.failed",
                    {"sub_id": h.sub_id,
                     "summary": summarize(
                         f"[{e.code}] {e.spec.advice}", self._summary_max_chars)},
                    actor="agent", trace={"parent_seq": h.parent_seq})
            if h.result is not None and not h.result.done():
                h.result.set_exception(e)               # join 上抛原 PyHError
        except Exception as e:                          # 未预期兜底:CYC-999
            err = wrap_unexpected(e, "subagent")
            h.error_code, h.state = err.code, "failed"
            if h.spec.notify:
                await self._write_event(
                    "subagent.failed",
                    {"sub_id": h.sub_id,
                     "summary": summarize(
                         f"[{err.code}] {err.spec.advice}", self._summary_max_chars)},
                    actor="agent", trace={"parent_seq": h.parent_seq})
            if h.result is not None and not h.result.done():
                h.result.set_exception(err)
        finally:
            self._finalize(h)                           # 释放闸 + 摘登记(幂等)

    def _finalize(self, h: ChildHandle) -> None:
        """句柄收尾(幂等):并发闸归还(防双放:slot 标记) + 从在途表摘除。"""
        if h.slot:
            h.slot = False
            self._release_slot()
        if self._children.get(h.sub_id) is h:
            self._children.pop(h.sub_id, None)
            self._completed[h.sub_id] = (h.state, h.summary, h.error_code)
            while len(self._completed) > self._completed_limit:
                self._completed.popitem(last=False)
            if h.result is not None and h.result.done() and not h.result.cancelled():
                h.result.exception()  # Retrieve failures even without a waiting join.
            finalize=getattr(self._runner,'finalize_child',None)
            if callable(finalize):finalize(h.sub_id)

    def _historic_result(self, sub_id):
        """Only owned child IDs may be recovered from this parent's event log."""
        events = list(self._ensure_session().events_after(0))
        if not any(e.type == 'subagent.spawned' and e.payload.get('sub_id') == sub_id for e in events):
            return None
        for event in reversed(events):
            p = event.payload
            if p.get('sub_id') == sub_id:
                if event.type == 'subagent.joined':
                    return ('done', p['summary'], None)
                if event.type == 'subagent.failed':
                    import re
                    match = re.match(r'\[([A-Z]+-\d+)\]', p.get('summary', ''))
                    return ('failed', None, match[1] if match else 'CYC-999')
            if event.type == 'system.cancelled' and p.get('what') == 'subagent:'+sub_id:
                return ('cancelled', None, None)
        reader = getattr(self._runner, 'completed_result', None)
        return reader(sub_id) if callable(reader) else None

    async def join(self, sub_id: str, *, timeout: Optional[float] = None,
                   ctx: Any = None) -> Optional[str]:
        """主循环 await 子任务终态取回摘要:成功 → summary(≤2KB);失败 → raise 原
        PyHError(回喂可行动文本);timeout → None(调用方决定再等/取消/先干别的);
        Completed results support repeated join; evicted entries use owned history.
        """
        h = self._children.get(sub_id)
        if h is None:
            result = self._completed.get(sub_id) or self._historic_result(sub_id)
            if result is not None:
                state, summary, code = result
                if state == 'cancelled':
                    raise asyncio.CancelledError
                if state == 'failed':
                    raise PyHError(code or 'CYC-999')
                return summary
            raise_code("EVT-101", sub_id=sub_id,
                       hint="sub_id 不存在或没有可恢复的终态",
                       advice="使用本会话 spawn 返回的子任务 ID")
        if h.state in ("spawning", "running"):
            h.state = "joining"                 # 等待期状态(ChildState 词表)
        try:
            # shield:超时只取消本等待,不毒化 h.result 内部 Future——否则超时后
            # 再次 join 读到 cancelled future 直接抛 CancelledError、结果永久丢失
            # (task_queue.wait_for 同款处理,P2 修复)。
            return await asyncio.wait_for(asyncio.shield(h.result), timeout)
        except asyncio.TimeoutError:
            return None                         # 调用方:再等/取消/先干别的

    async def cancel(self, sub_id: str, *, by: str = "system",
                     reason: str = "user-cancel") -> bool:
        """取消在途子任务并归一化:system.cancelled 审计(父会话)→ 任务 cancel 传播
        → await 其清理(终态由子循环自记 state=cancelled)→ 取消注入早于任务启动
        (停 spawning)则补漏终态;已终态/不存在 → 幂等 False。
        """
        h = self._children.get(sub_id)
        if h is None or h.state in _TERMINAL:
            return False                        # 已终态/不存在 → 幂等 False
        if h.task is not None and not h.task.done():
            h.task.cancel()                     # 沿 await 链传播 CancelledError
        await self._write_event(                # 声明式取消审计(父会话)
            "system.cancelled",
            {"what": f"subagent:{sub_id}", "reason": reason},
            actor="system")
        if h.task is not None:
            await asyncio.gather(h.task, return_exceptions=True)  # 等清理完成
        if self._children.get(sub_id) is h and h.state not in _TERMINAL:
            h.state = "cancelled"               # 取消早于任务启动:补漏终态
            self._finalize(h)                   # 释放闸 + 摘登记(防泄漏)
        return True

    def status(self) -> SubagentStatus:
        """并发与在途查询(纯读,零副作用):running ≤limit,各在途 sub_id/state/age。"""
        now = time.time()
        return SubagentStatus(
            running=len(self._children),
            active_children=[{"sub_id": h.sub_id, "state": h.state,
                              "age_s": int(now - h.started_ts)}
                             for h in self._children.values()],
            limit=self._max_concurrent)

    async def _cancel_all_children(self, *, reason: str) -> None:
        """child-first 清理主体:先逐子在途任务声明式取消(审计 + cancel),await
        全部退出,再补漏(取消注入早于任务启动的 spawning 句柄落 subagent.failed
        终态事件 + 释放闸);摘净后才交还调用方(结构上杜绝"父已终态、子还在跑/
        回写 EVT-104")。
        """
        await stop_admissions(self)
        hs = list(self._children.values())
        if not hs:
            return
        errors = []
        for h in hs:
            if h.state not in _TERMINAL and h.task is not None and not h.task.done():
                h.task.cancel()
        for h in hs:
            if h.state in _TERMINAL:
                continue
            try:
                await self._write_event('system.cancelled',
                    {'what': f'subagent:{h.sub_id}', 'reason': reason}, actor='system')
            except BaseException as exc:
                errors.append(exc)
        await asyncio.gather(*(h.task for h in hs if h.task is not None), return_exceptions=True)
        for h in hs:
            try:
                if h.state not in _TERMINAL:
                    await self._write_event('subagent.failed',
                        {'sub_id':h.sub_id, 'summary':f'parent closed ({reason})'},
                        actor='agent', trace={'parent_seq':h.parent_seq})
                    h.state = 'cancelled'
            except BaseException as exc:
                errors.append(exc)
            finally:
                self._finalize(h)
        if errors:
            raise BaseExceptionGroup('child cancellation audit failed', errors)

    async def _on_session_closing(self, type_: str,
                                  payload: Any = None) -> None:
        """父会话关闭前 child-first 钩子(bus 订阅签名;announce 挂于 session.closing,
        规范预关钩子,偏离 11):先取消全部在途子任务并 await,落终态事件,随后才
        允许 finished 落盘——从结构上杜绝"父已终态、子还在跑/回写 EVT-104"。
        """
        await self._cancel_all_children(reason="parent-closed")

    async def close_children(self, *, reason: str = "parent-closed") -> None:
        """child-first 清理公开入口(detach/_on_session_closing 共用;装配层可在
        agent.close 落 finished 前显式触发)。"""
        await self._cancel_all_children(reason=reason)

