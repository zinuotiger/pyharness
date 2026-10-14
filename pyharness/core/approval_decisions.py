"""ApprovalDecisions operations, separated from facade assembly."""
from __future__ import annotations
import asyncio
import json
import sys
import time
from typing import TYPE_CHECKING, Any, Optional
from pyharness.errors import raise_code
from pyharness.core.approval_models import logger, CHANNELS, VERDICTS, _OUTCOME_EVENTS, ApprovalRequest
if TYPE_CHECKING:  # 仅类型标注:executor 传入的 ToolCall 鸭子契约,运行期不依赖
    pass

class ApprovalDecisions:
    """ApprovalDecisions responsibility; state is owned by the composing facade."""

    def approve(self, approval_id: int, *, by: str) -> None:
        """人类批准入口(CLI/Web/ACP;外壳层唯一裁决通道,LLM 无权调用)。

        by 由框架按通道上下文打(cli:<user>/web:<会话>/acp:<client_id>);身份
        非法(llm:/tool:/plugin: 前缀)或 id 未知/已消费 → APR-503(防重放)。
        结果事件强同步落盘后经总线分发 → on_verdict 唤醒等待者。
        """
        self._spawn_outcome("granted", approval_id, by)

    async def approve_async(self, approval_id: int, *, by: str) -> None:
        """等待强同步裁决、必要队列恢复及等待者可继续后才返回。"""
        await self._decide_async("granted", approval_id, by)

    def deny(self, approval_id: int, *, by: str) -> None:
        """人类拒绝入口(同 approve:落 approval.denied)。"""
        self._spawn_outcome("denied", approval_id, by)

    async def deny_async(self, approval_id: int, *, by: str) -> None:
        """等待拒绝生效及本审批持有的队列暂停解除后才返回。"""
        await self._decide_async("denied", approval_id, by)

    async def user_choice(self, options: list[str],
                          *, prompt: str = "选择(输入序号/文字): ") -> str:
        """plan 单步失败三选一裁决通道(F046):仅交互 cli 通道可用。

        headless/desktop 无键盘通道 → APR-501(安全默认由 plan 层中止);
        输入接受序号或命中选项文字,词表外一律按“中止”安全默认。
        """
        if self._headless or self._channel != "cli":
            raise_code("APR-501", reason="no-user-choice",
                       hint="无用户裁决通道(ctx.approval.user_choice 未接线)",
                       advice="headless/桌面无键盘三选一;单步失败直接中止")
        for i, opt in enumerate(options, 1):
            print(f"  {i}. {opt}", file=sys.stderr)
        try:
            ans = (await asyncio.to_thread(input, prompt)).strip()
        except (EOFError, KeyboardInterrupt):
            return "中止"
        if ans.isdigit() and 1 <= int(ans) <= len(options):
            return options[int(ans) - 1]
        hit = next((o for o in options if o in ans), None)
        return hit or "中止"

    def _spawn_outcome(self, verdict: str, approval_id: int, by: str) -> None:
        self._require_human(by)
        if approval_id not in self._pending and self.owns_initializing(approval_id):
            # CLI is a synchronous command sender. Its valid early command must
            # join registration too; unknown/replayed IDs still fail below.
            self._own(self._decide_async(verdict, approval_id, by))
            return
        req, sess, type_ = self._prepare_outcome(verdict, approval_id, by)
        req.completion = self._own(self._emit_outcome(req, sess, type_, by, actor="user"))

    async def _decide_async(self, verdict: str, approval_id: int, by: str) -> None:
        # A request event may already be visible while registration is awaiting
        # persistence. Join registration before looking up its ID.
        async with self._transition_lock:
            req, sess, type_ = self._prepare_outcome(verdict, approval_id, by)
            req.completion = self._own(self._emit_outcome(req, sess, type_, by, actor="user"))
        await asyncio.shield(req.completion)

    def _prepare_outcome(self, verdict: str, approval_id: int, by: str, *, reviewed=False) -> tuple:
        """同步完成身份与 pending 校验,保证无效裁决立即抛错而不是后台吞掉。"""
        if not reviewed:
            self._require_human(by)
        elif self._reviewer is None or by != self._reviewer.identity:
            raise_code('APR-503', why='无独立审核器授权')
        if self._detached:
            raise_code("APR-503", why="审批服务已关闭")
        req = self._pending.get(int(approval_id))
        if req is None or req.state != "pending":
            raise_code("APR-503", approval_id=approval_id,
                       why="未知或已裁决的 approval_id;同一审批至多一个结果(防重放)")
        if req.batch_mono and (time.monotonic()-req.batch_mono)*1000 >= req.ttl_ms:
            raise_code('APR-503', approval_id=approval_id, why='审批请求已过期')
        if req.decision_inflight is not None:
            raise_code("APR-503", approval_id=approval_id,
                       why=f"审批正在处理({req.decision_inflight}),拒绝重复裁决")
        sess = req.log or self._session
        if sess is None:
            raise_code("CYC-999", module="approval",
                       hint="请求无会话日志落点,裁决无法强同步落盘")
        type_ = _OUTCOME_EVENTS[verdict]
        req.decision_inflight = verdict
        return req, sess, type_

    async def _review_request(self, req, invocation):
        """Same completion owner as human decisions; guards remain unchanged."""
        import hashlib
        try:
            result = await asyncio.wait_for(self._reviewer.evaluate(invocation), self._reviewer.timeout)
            if not isinstance(result,dict) or result.get('decision') not in ('approve','deny','manual') or not isinstance(result.get('reason'),str):
                raise ValueError('invalid review result')
        except Exception:
            result = {'decision':'manual','reason':'自动审核失败或超时，转为人工审批'}
        async with self._transition_lock:
            if req.state != 'pending' or req.decision_inflight: return
            await req.log.append('approval.reviewed', {'approval_id':req.approval_id,
                'call_id':req.call_id,'reviewer':self._reviewer.identity, **result,
                'invocation_sha256':hashlib.sha256(json.dumps(invocation['args'],sort_keys=True).encode()).hexdigest(),
                'context_sha256':hashlib.sha256(json.dumps(invocation['context']).encode()).hexdigest(),
                'scope':invocation['scope']}, actor='system',sync=True)
            if result['decision'] == 'manual': return
            lead, sess, type_ = self._prepare_outcome('granted' if result['decision']=='approve' else 'denied',
                req.approval_id,self._reviewer.identity,reviewed=True)
            lead.completion = self._own(self._emit_outcome(lead,sess,type_,self._reviewer.identity,actor='system'))
        await asyncio.shield(lead.completion)

    async def _emit_outcome(self, req: ApprovalRequest, sess: Any, type_: str,
                            by: str, *, actor: str, emit: bool = True) -> None:
        """Only completion owner: durable verdict, queue acknowledgement, waiter."""
        async with self._transition_lock:
            verdict = req.decision_inflight
            if req.is_terminal:
                return
            resume_attempted = False
            try:
                if emit:
                    await self._emit_verdict(sess, type_,
                        {"approval_id": req.approval_id, "by": by, "ttl_ms": req.ttl_ms},
                        actor=actor)
                resume_attempted = True
                await self._release_if_last(req)
            except BaseException:
                # No granted waiter/slot on any incomplete commit. Retain the
                # pending record if its queue transition still needs recovery.
                released = False
                if not resume_attempted:
                    try:
                        await self._release_if_last(req)
                        released = True
                    except Exception:
                        logger.warning("approval recovery failed; preserving commit error",
                                       exc_info=True)
                self._settle(req, "denied", by="system", remove=released)
                raise
            if (verdict == "granted" and not self._detached
                    and self._enabled and req.channel in CHANNELS):
                req.by = by
                self._remember(req)
            self._settle(req, verdict, by)

    async def _release_if_last(self, req: ApprovalRequest) -> None:
        if self._suspended and not any(w is not req for w in self._pending.values()):
            await self._resume_queue_or_record_async(req.log)
            self._suspended = False

    async def _emit_verdict(self, sess: Any, type_: str, payload: dict, *,
                            actor: str) -> Any:
        return await sess.append(type_, payload, actor=actor, sync=True,
                                 trace={"kind": "approval.verdict"})

    async def on_verdict(self, type_: str, payload: Any) -> None:
        """Legacy external verdict delivery; locally owned events are observations.

        Never await our own completion from a SessionLog subscriber: append must
        finish dispatch and strong flush before completion can proceed.
        """
        p = payload.payload if hasattr(payload, "payload") else (payload or {})
        aid = p.get("approval_id") if isinstance(p, dict) else None
        req = self._pending.get(aid) if isinstance(aid, int) else None
        if req is None or req.state != "pending" or self._cross_session(payload, req):
            await self._note_replay(aid)
            return
        if req.decision_inflight is not None:
            return
        verdict = type_.rsplit(".", 1)[-1] if type_ else ""
        if verdict not in VERDICTS or self._detached:
            return
        by = p.get("by") or ("system" if verdict == "timeout" else "")
        req.decision_inflight = verdict
        req.completion = self._own(self._emit_outcome(
            req, req.log, type_, by, actor="system", emit=False))
        await asyncio.shield(req.completion)

    def _cross_session(self, payload: Any, req: ApprovalRequest) -> bool:
        """信封会话与本请求会话不符 → 异会话串扰过滤(偏离 6;无信封视为同会话)。"""
        if not hasattr(payload, "session_id"):
            return False
        return bool(req.session_id) and payload.session_id != req.session_id

    async def _note_replay(self, aid: Optional[int]) -> None:
        """重放/未知裁决留痕:system.error(APR-503),不重复执行。"""
        logger.warning("APR-503 未知或已处理的裁决 id=%s,已忽略(防重放)", aid)
        sess = self._session
        if sess is None:
            return
        try:
            await sess.append("system.error",
                              {"code": "APR-503",
                               "hint": f"未知或已处理的裁决 id={aid},已忽略(防重放)"},
                              actor="system")
        except Exception as exc:                 # noqa: BLE001 尽力而为
            logger.warning("approval system.error 落盘失败: %s", exc)

    def _settle(self, req: ApprovalRequest, verdict: str, by: str, *,
                remove: bool = True) -> None:
        """终态迁移单点:批内全部 pending 成员迁移 + 取消 TTL + 解决各自 waiter。

        幂等:成员已终态跳过(防重复解决);lead 从 _pending 摘除后,同 id 再收
        裁决即 APR-503(重放闸)。合并等待者各自 waiter 在此级联解决(每个等待者
        各自配对裁决结果,granted 后各自独立重入 guard 链)。
        """
        lead_ref = req.approval_id                   # approval identity(仅 lead 有)
        for w in list(req.batch):
            if w.state != "pending":
                continue
            w.state = verdict                    # 一次性迁移,终态不再接受裁决
            w.by = by
            w.decision_inflight = None
            self._cancel_timer(w)
            if verdict == "granted" and w.call_id:
                # 授权↔执行绑定:executor 重入 approval 决策时按 call_id 取此校验
                self._grant_slots[w.call_id] = w.binding
            if w.call_id and lead_ref is not None:
                # S4-P1-2:绑定 call_id → approval identity(= approval.requested 的
                # seq)。merged waiter 自身无 approval_id,按批共享 lead 的 identity;
                # 不新造 uuid,identity 恒来自真实请求事件。
                self._ref_slots[w.call_id] = lead_ref
            if not w.waiter.done():
                w.waiter.set_result(verdict)     # 唤醒 request() 等待者
        if remove and req.approval_id is not None and req.approval_id in self._pending:
            self._pending.pop(req.approval_id, None)

    def _start_ttl(self, req: ApprovalRequest) -> None:
        """起 TTL 定时器(仅 lead);到点无人裁决 → _on_timeout(与外部迟到裁决竞争)。"""
        if req.ttl_ms <= 0 or req.is_terminal:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("approval 无运行中事件循环,TTL 定时器未启动 id=%s",
                           req.approval_id)
            return
        req.timer = loop.call_later(req.ttl_ms / 1000.0, self._ttl_tick, req)

    def _ttl_tick(self, req: ApprovalRequest) -> None:
        """定时器到点回调:转异步 _on_timeout(仍 pending 才落 timeout 事件)。"""
        req.timer = None
        if req.state != "pending":
            return
        coro = self._on_timeout(req)
        try:
            self._spawn(coro)
        except Exception as exc:                 # noqa: BLE001 定时器路径不扩散
            logger.warning("approval TTL 触发失败 id=%s: %s", req.approval_id, exc)

    async def _on_timeout(self, req: ApprovalRequest) -> None:
        if req.state != "pending" or req.decision_inflight is not None:
            return
        req.decision_inflight = "timeout"
        req.completion = self._own(self._emit_outcome(
            req, req.log, "approval.timeout", "system", actor="system"))
        await asyncio.shield(req.completion)

    async def _wait_any(self, req: ApprovalRequest) -> str:
        try:
            return await asyncio.shield(req.waiter)
        except asyncio.CancelledError:
            try:
                await self._cancel_request(req)
            except Exception:
                logger.warning("approval cancel cleanup failed", exc_info=True)
            raise

    def _cancel_request_start(self, req):
        lead = req.batch[0]
        if lead.state == "pending" and lead.decision_inflight is None:
            lead.decision_inflight = "denied"
            lead.completion = self._own(self._emit_outcome(
                lead, lead.log, "approval.denied", "system", actor="system", emit=False))
        return lead.completion

    async def _cancel_request(self, req):
        completion = self._cancel_request_start(req)
        if completion is not None:
            await asyncio.shield(completion)

    def cancel_all(self, reason: str = "detach") -> None:
        """Reserve denial synchronously; request/aclose join its full completion."""
        for req in list(self._pending.values()):
            self._cancel_request_start(req)

