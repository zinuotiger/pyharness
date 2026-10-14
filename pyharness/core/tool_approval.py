"""ToolApproval operations, separated from facade assembly."""
from __future__ import annotations
from typing import Any
from pyharness.core.tools_guard import ToolCall
from pyharness.errors import PyHError, raise_code
from pyharness.core.execution_result import ApprovalRoundResult, summarize

class ToolApproval:
    """ToolApproval responsibility; state is owned by the composing facade."""

    async def _approval_round(self, ctx: Any, call: ToolCall,
                              args: dict, *, prior: Any,
                              binding: str) -> ApprovalRoundResult:
        """关2.5 审批编排(request → granted 重入治理授权 → 放行/拒绝裁决)。

        返回 ``ApprovalRoundResult``(S3-2-2:取代混合字符串契约)。语义:

        - ``denied`` / ``timeout``:不产生 D2、不再次 authorize、不执行 provider
          ——返回 ``decision=prior(D1)``, ``approved=False``, ``executed=False``;
        - ``APR-501``(headless 无通道):同上,并记 ``_rejected``;
        - ``granted``:重入治理授权 ``authorize(prior=D1)`` 产生 **D2**;
          重入 reject → GRD-403(批准作废,单调性高于人类即时意志);
          重入仍 approval → 须过绑定校验(本调用参数指纹 == 审批提供器为该
          call_id 记录的绑定)才放行,不一致 = 批准针对异参数 → 拒(偏离 3 收紧);
          allow/校验通过 → 放行至关3。

        **R1**:``denied``/``timeout`` 的语义归 approval transport + executor,
        不向 ``DecisionEngine`` 传 ``approval_verdict``。
        """
        ap = getattr(ctx, "approval", None)
        if ap is None:
            raise_code("CYC-999", module="tools_executor",
                       hint="ctx.approval 未接线:guard 判 need_approval 但审批"
                            "服务缺失;拒绝执行(fail-closed)")
        # binding:由 execute() 传入的授权↔执行绑定指纹(与 Decision.inputs_digest
        # 同源,全流程只算一次;不再在本函数重复计算)。
        try:
            verdict = await ap.request(call, summarize(args), ctx, binding=binding)
        except PyHError as e:
            if e.code == "APR-501":                     # headless 无通道:直接拒
                self._rejected.add(call.call_id)
                # ADR-022 D-7(a):本路径**不发** approval.requested(零事件零等待),
                # 故派生层**没有任何事件可供反查 call_id** ⇒ 必须经**既有**
                # tool.error 通道补配对,否则 assistant.tool_calls 悬空 → 端点 400。
                # 仅更换**事件出口**(副作用即本调用目的),判定语义与返回类型不变;
                # 未新增事件类型、未触碰 guard.rejected/approval.denied 的产生逻辑。
                await self._on_error(ctx, call, "APR-501",
                                     "审批不可用(APR-501),未执行")
                return ApprovalRoundResult(decision=prior, approved=False,
                                           executed=False,
                                           message="审批不可用(APR-501),未执行")
            raise                                       # 其余错误:装配/落盘故障上抛
        if verdict != "granted":                        # denied/timeout = 不执行
            return ApprovalRoundResult(decision=prior, approved=False,
                                       executed=False,
                                       message=f"审批{verdict},未执行")
        # granted ≠ 放行:重入治理授权(第二次求值 → D2;supersedes=D1)
        # inputs_digest 与首次一致(同 call/同 args → 同绑定指纹)。approval_ref =
        # 本次 approval lifecycle 的**真实 identity**(= approval.requested 的 seq),
        # 由 approval 侧只读访问器提供;D1 无此值(它在请求产生**之前**已发射),
        # 属正确语义——见 INV-APPROVAL-REF(S4-P1-2 冻结口径)。
        get_ref = getattr(ap, "approval_ref_of", None)
        approval_ref = get_ref(call.call_id) if get_ref is not None else None
        d2 = await self._authorize_governance(ctx, call, prior=prior,
                                            inputs_digest=binding,
                                            approval_ref=approval_ref)
        if d2 == "reject":                              # 批准时策略收紧 → 作废
            self._rejected.add(call.call_id)
            return ApprovalRoundResult(decision=d2, approved=True, executed=False,
                                       message="审批后 guard 重入拒绝(GRD-403 语义)")
        if d2 == "approval":                            # 重入仍要求审批(同参已批)
            check = getattr(ap, "grant_binding", None)
            if check is not None and check(call.call_id) != binding:
                # 绑定不一致 = 该 grant 不是针对本次参数的授权(篡改/复用)
                self._rejected.add(call.call_id)
                return ApprovalRoundResult(
                    decision=d2, approved=True, executed=False,
                    message="审批绑定校验失败(GRD-403):批准与本次调用参数不一致,"
                            "拒绝执行")
        return ApprovalRoundResult(decision=d2, approved=True, executed=True)

