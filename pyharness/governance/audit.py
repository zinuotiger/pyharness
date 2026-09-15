"""pyharness/governance/audit.py — 治理审计视图(S5-3a)。

一句话职责:回答**"某一条决策的完整因果链是什么"**与**"哪些调用被拒、依据什么"**。
``AuditSystem`` 是**纯派生视图(derived view)**——不是新真源,不是缓存,不订阅、
不写事件、不跨调用保存状态。

依赖方向(ADR-018:308,强制):只 import 标准库、``pyharness.errors`` 与本包;
**禁止** ``pyharness.core.*`` / ``engine`` / ``bus`` / ``persistence``。会话经
``ctx.session``/构造注入**鸭子类型**只读使用。

**replay-only(仅重放,本模块的第一纪律)**:

- **不订阅**实时事件 —— 订阅态索引在重启后为空,审计必须**仅凭日志**可用;
- **不复用** ``EvidenceCollector`` 的索引 —— 其订阅面仅 5 类,缺
  ``tool.result`` / ``guard.*`` / ``approval.*``;且它按**段**组织,审计按**因果**串联;
- 每次调用**即时全量重放** ``_read_events(session)``(O(n);v1.0 接受,见 Change Report);
- 因此:同一日志 ⇒ 同一审计结果(可复现,不依时序、不依订阅先后)。

**与 EvidenceCollector 的边界**:Evidence 回答"**这个任务**有哪些证据"(段轴,
只覆盖**被归档**的证据);Audit 回答"**这条决策**为什么发生、经过了什么"
(因果轴,覆盖**全部**决策,含未归档者)。

**本步(S5-3a)边界**:只有 ``causal_chain`` 与 ``denied_report``,**无写点**。
``reconcile()`` / ``legacy_session_audit()`` 属 **S5-4**;``TraceabilityMatrix`` 属 **S7**。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from pyharness.governance.receipt import _read_events

# 因果链各段名(输出字典的键;``missing`` 里出现即表示"应有而未找到")
CHAIN_SEGMENTS: tuple[str, ...] = (
    "request", "policy", "decision", "approval", "receipt", "execution",
)


def _receipt_expected(verdict: str, approval_ref: Any) -> bool:
    """该决策**是否应有凭证**。

    与 ``receipt.receipt_kind_of`` **同一规则**(approval_ref 为空且 verdict=approval
    ⇒ 审批请求决策 D1,不产生凭证);此处按**事件载荷**判定,故不复用对象级函数。
    一致性由 ``tests/unit/test_governance_audit.py`` 守卫。
    """
    return not (str(verdict) == "approval" and approval_ref is None)


@dataclass(frozen=True)
class AuditSystem:
    """治理审计视图(**replay-only**;无订阅、无写点、无跨调用状态)。"""

    session: Optional[Any] = None

    # ------------------------------------------------------------ 只读重放
    def _events(self, ctx: Any = None) -> list:
        """即时全量重放(唯一数据来源)。无会话 ⇒ 空列表(降级,不抛)。"""
        sess = getattr(ctx, "session", None) if ctx is not None else self.session
        if sess is None:
            return []
        return _read_events(sess)

    @staticmethod
    def _trace_call_id(ev: Any) -> str:
        return str((getattr(ev, "trace", None) or {}).get("call_id") or "")

    # ------------------------------------------------------------ 因果链
    def causal_chain(self, decision_id: str, *, ctx: Any = None) -> dict:
        """由 ``decision_id`` 还原完整因果链(**replay-only**,无副作用)。

        输出键(冻结):``decision_id`` / ``call_id`` / ``request`` / ``policy`` /
        ``decision`` / ``approval`` / ``supersedes`` / ``receipt`` / ``execution`` /
        ``denied`` / ``missing``。

        **不臆造**:未命中的环节一律 ``None``,并按其"是否应有"记入 ``missing``;
        ``decision.issued`` 本身缺失 ⇒ 直接返回并只记该段(**不抛**)。
        """
        out: dict = {
            "decision_id": str(decision_id), "call_id": "", "request": None,
            "policy": None, "decision": None, "approval": None,
            "supersedes": None, "receipt": None, "execution": None,
            "denied": False, "missing": [],
        }
        events = self._events(ctx)
        # ① 定位本决策事实
        dec_ev = next((e for e in events
                       if getattr(e, "type", None) == "decision.issued"
                       and str((getattr(e, "payload", None) or {})
                               .get("decision_id") or "") == str(decision_id)), None)
        if dec_ev is None:
            out["missing"] = ["decision.issued"]
            return out
        p = getattr(dec_ev, "payload", None) or {}
        verdict = str(p.get("verdict") or "")
        approval_ref = p.get("approval_ref")
        call_id = self._trace_call_id(dec_ev)
        out["call_id"] = call_id
        out["denied"] = verdict == "reject"
        out["supersedes"] = p.get("supersedes")
        out["decision"] = {"verdict": verdict, "ts": p.get("ts"),
                           "seq": getattr(dec_ev, "seq", None),
                           "principal": {"kind": p.get("principal_kind"),
                                         "id": p.get("principal_id"),
                                         "channel": p.get("principal_channel")}}
        out["policy"] = {"fingerprint": p.get("policy_fingerprint"),
                         "refs": list(p.get("policy_refs") or []),
                         "guards": list(p.get("guard_ids") or [])}
        # ② 按 call_id 关联请求 / 执行(同一 call_id 可能有多次求值 → 取首个请求)
        if call_id:
            tc = next((e for e in events
                       if getattr(e, "type", None) == "tool.call"
                       and str((getattr(e, "payload", None) or {})
                               .get("call_id") or "") == call_id), None)
            if tc is None:
                out["missing"].append("tool.call")
            else:
                tp = getattr(tc, "payload", None) or {}
                out["request"] = {"tool": tp.get("name", ""),
                                  "seq": getattr(tc, "seq", None)}
            tr = next((e for e in events
                       if getattr(e, "type", None) == "tool.result"
                       and str((getattr(e, "payload", None) or {})
                               .get("call_id") or "") == call_id), None)
            if tr is not None:
                rp = getattr(tr, "payload", None) or {}
                out["execution"] = {"ok": bool(rp.get("ok")),
                                    "seq": getattr(tr, "seq", None)}
        # ③ 审批段:approval_ref(= approval.requested 的 seq) → 结果事件
        if approval_ref is not None:
            ref = int(approval_ref)
            outcome = next((e for e in events
                            if getattr(e, "type", None) in (
                                "approval.granted", "approval.denied",
                                "approval.timeout")
                            and int((getattr(e, "payload", None) or {})
                                    .get("approval_id") or -1) == ref), None)
            if outcome is None:
                out["missing"].append("approval")
            else:
                op = getattr(outcome, "payload", None) or {}
                out["approval"] = {
                    "approval_id": ref,
                    "outcome": str(getattr(outcome, "type", "")).rsplit(".", 1)[-1],
                    "by": op.get("by", ""),
                    "seq": getattr(outcome, "seq", None)}
        # ④ 凭证段:按 decision_id 关联
        rc = next((e for e in events
                   if getattr(e, "type", None) == "receipt.emitted"
                   and str((getattr(e, "payload", None) or {})
                           .get("decision_id") or "") == str(decision_id)), None)
        if rc is not None:
            rp = getattr(rc, "payload", None) or {}
            out["receipt"] = {"receipt_id": rp.get("receipt_id"),
                              "kind": rp.get("kind"), "digest": rp.get("digest"),
                              "prev_hash": rp.get("prev_hash"),
                              "seq": getattr(rc, "seq", None)}
        elif _receipt_expected(verdict, approval_ref):
            out["missing"].append("receipt.emitted")
        return out

    # ------------------------------------------------------------ 拒绝报告
    def denied_report(self, *, session_id: str = "", since_seq: int = 0,
                      ctx: Any = None) -> list[dict]:
        """被拒调用清单(**replay-only**;按事件 seq 升序)。

        **两类来源必须可区分**(``source`` 字段):

        - ``"guard.rejected"`` —— 规则级拒绝(``INV-05`` 的强同步留痕);
        - ``"decision.issued"`` —— 治理级拒绝(``verdict=reject``)。

        每条记录键(冻结):``source`` / ``seq`` / ``tool`` / ``call_id`` /
        ``guard_id`` / ``policy_ref`` / ``decision_id`` / ``reason`` / ``executed``。
        ``executed`` = 该 ``call_id`` 是否存在配对 ``tool.result``(审计可证
        "拦了且没执行";``INV-A3``)。
        """
        events = self._events(ctx)
        if session_id:
            events = [e for e in events
                      if str(getattr(e, "session_id", "") or "") == session_id]
        executed = {str((getattr(e, "payload", None) or {}).get("call_id") or "")
                    for e in events
                    if getattr(e, "type", None) == "tool.result"}
        rows: list[dict] = []
        for e in events:
            seq = int(getattr(e, "seq", 0) or 0)
            if seq <= int(since_seq or 0):
                continue
            t = getattr(e, "type", None)
            p = getattr(e, "payload", None) or {}
            if t == "guard.rejected":
                cid = self._trace_call_id(e)
                rows.append({
                    "source": "guard.rejected", "seq": seq,
                    "tool": str(p.get("tool") or ""), "call_id": cid,
                    "guard_id": str(p.get("guard_id") or ""),
                    "policy_ref": str(p.get("policy_ref") or ""),
                    "decision_id": "", "reason": str(p.get("reason") or ""),
                    "executed": bool(cid) and cid in executed})
            elif t == "decision.issued" and str(p.get("verdict") or "") == "reject":
                cid = self._trace_call_id(e)
                rows.append({
                    "source": "decision.issued", "seq": seq,
                    "tool": str(p.get("tool") or ""), "call_id": cid,
                    "guard_id": "", "policy_ref": "",
                    "decision_id": str(p.get("decision_id") or ""),
                    "reason": ",".join(str(r) for r in (p.get("policy_refs") or [])),
                    "executed": bool(cid) and cid in executed})
        rows.sort(key=lambda r: r["seq"])
        return rows


__all__ = ["CHAIN_SEGMENTS", "AuditSystem"]
