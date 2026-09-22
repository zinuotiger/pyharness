"""pyharness/governance/receipt.py — 决策凭证(S4/M4)。

一句话职责:把一次**已经发生并持久化**的治理决策,升格为**可离线核验的凭证**
——`DecisionReceipt` 是**证明对象**,不是新业务状态;持久化形态只有
``receipt.emitted`` 事件(薄引用 + 完整性链),**事件日志仍是唯一真源**。

依赖方向(ADR-018:308,强制):本模块只 import 标准库、``pyharness.errors`` 与本包;
**禁止** ``pyharness.core.*`` / ``engine`` / ``bus`` / ``persistence``。需要会话时
经 ``ctx`` 鸭子类型只读使用(与 ``context.authorize`` 同型)。

关键裁定(2026-09-15 M4 Entry Analysis,R-1/R-2/R-3):

1. **R-1**:``receipt.emitted.payload.digest`` == ``Receipt.content_hash``(与相邻
   ``prev_hash`` 同属完整性链)。``inputs_digest`` 语义**不变**(表达"本次授权针对
   什么 inputs"),**不得**混用。不新增 payload 字段。
2. **R-2**:approval 凭证在 **D2 处**发射(``approval.granted`` 早于 D2;D1 无
   ``approval_ref``)。链路:``approval.granted.approval_id → D2.approval_ref →
   D2.decision_id → approval Receipt``。
3. **R-3**:``Receipt.ts`` = ``receipt.emitted`` 的 ``Envelope.ts``(凭证**生成**时刻);
   与 ``Decision.ts``(决策**形成**时刻)语义不同,两者并存不混。

**content_hash 确定性**(M4 硬约束):受保护集合**排除**随机 ``receipt_id``
(否则同一事实每次 hash 不同)与 append 时才产生的 ``ts``/``event_seq``。
规范化复用 ``policy.compute_fingerprint`` 同款 canonical JSON 规则
(``sort_keys=True`` / ``ensure_ascii=False`` / ``separators=(",",":")`` /
``default=str``)+ sha256——**不新建第三套 canonicalization/hash**。

**prev_hash 链**:链头由 ``ReceiptStore`` 首次 emit 时**从会话日志 replay 推出**
(只读使用唯一真源);内存 ``_last_hash`` 仅为缓存,重启可由日志重建 ⇒ 不构成第二
persistence source of truth(INV-R5)。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from uuid import uuid4

from pyharness.governance.decision import Decision, Principal, Verdict

# 凭证类别:decision = ALLOW/REJECT 结论;approval = 审批后重新授权(D2)
KIND_DECISION = "decision"
KIND_APPROVAL = "approval"
RECEIPT_KINDS: tuple[str, ...] = (KIND_DECISION, KIND_APPROVAL)

EVENT_RECEIPT_EMITTED = "receipt.emitted"


def _canonical(payload: dict) -> str:
    """规范化 JSON(与 ``policy.compute_fingerprint`` 同款规则;不新建第二套)。"""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), default=str)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def receipt_kind_of(decision: Decision) -> Optional[str]:
    """决策 → 凭证类别(M4 产生规则)。``None`` = **不产生凭证**。

    - ``approval_ref is not None`` ⇒ ``approval``(D2:审批后重新授权,携带真实
      approval identity ⇒ 闭合 INV-G2 链);
    - ``verdict=APPROVAL`` 且 ``approval_ref is None`` ⇒ **D1(审批请求决策)**:
      不产生凭证——它是"需要进一步审批"的治理中间态,不授权任何执行;
    - 其余(ALLOW / REJECT)⇒ ``decision``。
    """
    if decision.approval_ref is not None:
        return KIND_APPROVAL
    if decision.verdict is Verdict.APPROVAL:
        return None
    return KIND_DECISION


def _protected_content(*, decision: Decision, kind: str,
                       prev_hash: Optional[str]) -> dict:
    """``content_hash`` 的受保护集合(**确定性**;发射前全部已知)。

    刻意排除:``receipt_id``(随机)、``ts`` / ``event_seq``(append 时才产生)。
    """
    p = decision.principal
    return {
        "decision_id": str(decision.decision_id),
        "kind": str(kind),
        "verdict": str(decision.verdict),
        "tool": str(decision.tool),
        "inputs_digest": str(decision.inputs_digest),
        "policy_fingerprint": str(decision.policy_fingerprint),
        "principal_kind": str(p.kind) if p is not None else "",
        "principal_id": str(p.id) if p is not None else "",
        "principal_channel": (p.channel if p is not None else None),
        "approval_ref": decision.approval_ref,
        "supersedes": decision.supersedes,
        "prev_hash": prev_hash,
    }


def content_hash_for(*, decision: Decision, kind: str,
                     prev_hash: Optional[str]) -> str:
    """凭证内容哈希 = sha256(canonical(受保护集合))。同事实 ⇒ 同哈希(INV-R3)。"""
    return _sha256(_canonical(_protected_content(
        decision=decision, kind=kind, prev_hash=prev_hash)))


@dataclass(frozen=True)
class DecisionReceipt:
    """★ 可离线核验的裁决凭证(设计 §3.4)。

    运行时对象**字段较丰富**;持久化的 ``receipt.emitted`` payload **只 5 字段**
    (``receipt_id``/``decision_id``/``kind``/``digest``/``prev_hash``)——其余由
    ``decision_id`` 指向的 ``decision.issued`` **派生**(INV-G4:只含引用与哈希)。
    """

    receipt_id: str
    decision_id: str
    kind: str
    verdict: str
    tool: str
    inputs_digest: str
    policy_fingerprint: str
    principal: Optional[Principal]
    approval_ref: Optional[int]
    supersedes: Optional[str]
    event_seq: Optional[int]
    call_id: str
    ts: str
    content_hash: str
    prev_hash: Optional[str] = None
    signature: Optional[str] = None      # v1.0 留空(设计 §3.4)

    @property
    def is_approval(self) -> bool:
        return self.kind == KIND_APPROVAL

    def protected(self) -> dict:
        """本凭证的受保护集合(供 verify 重算哈希)。"""
        p = self.principal
        return {
            "decision_id": str(self.decision_id),
            "kind": str(self.kind),
            "verdict": str(self.verdict),
            "tool": str(self.tool),
            "inputs_digest": str(self.inputs_digest),
            "policy_fingerprint": str(self.policy_fingerprint),
            "principal_kind": str(p.kind) if p is not None else "",
            "principal_id": str(p.id) if p is not None else "",
            "principal_channel": (p.channel if p is not None else None),
            "approval_ref": self.approval_ref,
            "supersedes": self.supersedes,
            "prev_hash": self.prev_hash,
        }

    def to_payload(self) -> dict:
        """持久化形态(冻结 5 字段;``digest`` == content_hash)。"""
        return {"receipt_id": self.receipt_id, "decision_id": self.decision_id,
                "kind": self.kind, "digest": self.content_hash,
                "prev_hash": self.prev_hash}


def _read_events(sess: Any) -> list:
    """只读取出会话事件(鸭子类型)。

    优先 ``events_after(0)``(``SessionLog``);回落 ``replay()``(``SessionStore``
    等持久化面)。二者均为**唯一真源**的只读视图——本模块不 import 任何 core 模块
    (ADR-018:308),也不缓存为第二真源。
    """
    for name in ("events_after", "replay"):
        reader = getattr(sess, name, None)
        if reader is None:
            continue
        try:
            return list(reader(0) if name == "events_after" else reader())
        except Exception:                            # noqa: BLE001 只读探测:尽力
            return []
    return []


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def verify_receipt(receipt: Any) -> bool:
    """离线核验(**fail-closed**,ADR-018:318;INV-R4)。

    返回 ``False`` ⇒ 调用方**必须**拒绝执行(沿用 INV-05 纪律)。检查:

    1. 类型与 ``kind`` 合法;
    2. ``content_hash`` 与受保护集合**重算值**一致(任一受保护字段被改 → False);
    3. ``kind`` 与 ``approval_ref`` 语义自洽(approval↔非 None / decision↔None)。
    """
    if not isinstance(receipt, DecisionReceipt):
        return False
    if receipt.kind not in RECEIPT_KINDS:
        return False
    if not receipt.content_hash or not receipt.decision_id:
        return False
    expected = _sha256(_canonical(receipt.protected()))
    if expected != receipt.content_hash:
        return False
    if receipt.kind == KIND_APPROVAL and receipt.approval_ref is None:
        return False
    if receipt.kind == KIND_DECISION and receipt.approval_ref is not None:
        return False
    return True


class ReceiptStore:
    """凭证的生成、持久化、查询、核验(M4)。

    **不持有第二份持久化**:唯一写动作 = ``session.append("receipt.emitted", …)``;
    索引与哈希链头均为**可由日志重建的缓存**(INV-R5)。
    """

    def __init__(self, *, id_factory: Optional[Callable[[], str]] = None,
                 clock: Optional[Callable[[], str]] = None) -> None:
        self._id_factory = id_factory or (lambda: uuid4().hex)
        self._clock = clock or _utc_now
        self._index: dict[str, DecisionReceipt] = {}
        self._last_hash: Optional[str] = None
        self._chain_loaded = False

    # ------------------------------------------------------------ 链头
    def _load_chain(self, sess: Any) -> None:
        """从会话日志 replay 出链头(只读;唯一真源=JSONL)。幂等,仅首次生效。"""
        if self._chain_loaded:
            return
        self._chain_loaded = True
        for e in _read_events(sess):
            if getattr(e, "type", None) != EVENT_RECEIPT_EMITTED:
                continue
            d = (getattr(e, "payload", None) or {}).get("digest")
            if d:
                self._last_hash = str(d)

    # ------------------------------------------------------------ 发射
    async def emit(self, ctx: Any, decision: Decision, *,
                   call_id: str = "") -> Optional[DecisionReceipt]:
        """按产生规则生成凭证并落盘(强同步)。``None`` = 该决策不产生凭证(D1)。

        异常:落盘失败按 PERS-202 语义上抛(F-SYNC-1 修复后**不再 silent success**),
        调用方 fail-closed。
        """
        kind = receipt_kind_of(decision)
        if kind is None:
            return None                              # D1:审批请求决策,无凭证
        sess = getattr(ctx, "session", None)
        if sess is None:
            return None                              # 未接线(纯内存/单测):降级
        self._load_chain(sess)
        prev = self._last_hash
        chash = content_hash_for(decision=decision, kind=kind, prev_hash=prev)
        receipt_id = self._id_factory()
        payload = {"receipt_id": receipt_id, "decision_id": decision.decision_id,
                   "kind": kind, "digest": chash, "prev_hash": prev}
        trace = {"call_id": call_id} if call_id else None
        r = sess.append(EVENT_RECEIPT_EMITTED, payload, actor="system",
                        sync=True, trace=trace)
        if hasattr(r, "__await__"):
            r = await r
        receipt = DecisionReceipt(
            receipt_id=receipt_id, decision_id=decision.decision_id, kind=kind,
            verdict=str(decision.verdict), tool=decision.tool,
            inputs_digest=decision.inputs_digest,
            policy_fingerprint=decision.policy_fingerprint,
            principal=decision.principal, approval_ref=decision.approval_ref,
            supersedes=decision.supersedes,
            event_seq=getattr(r, "seq", None), call_id=call_id,
            ts=self._clock(), content_hash=chash, prev_hash=prev)
        self._index[receipt_id] = receipt
        self._last_hash = chash
        return receipt

    # ------------------------------------------------------------ 查询
    def get(self, receipt_id: str) -> Optional[DecisionReceipt]:
        return self._index.get(receipt_id)

    def of_decision(self, decision_id: str) -> tuple[DecisionReceipt, ...]:
        """该决策的全部凭证(**复数**——decision/approval 混合模型)。"""
        return tuple(r for r in self._index.values()
                     if r.decision_id == decision_id)

    def all(self) -> tuple[DecisionReceipt, ...]:
        return tuple(self._index.values())

    def verify(self, receipt: Any) -> bool:
        """见模块级 ``verify_receipt``(fail-closed)。"""
        return verify_receipt(receipt)


def rebuild_from_log(sess: Any) -> tuple[DecisionReceipt, ...]:
    """从**事件真源**重建全部凭证(INV-G2/R2:可 replay、可核验)。

    仅用 ``receipt.emitted``(定位 + 哈希链)与 ``decision.issued``(决策事实),
    **不依赖任何内存缓存**;返回的凭证必须全部 ``verify_receipt`` 为真。
    """
    decisions: dict[str, Any] = {}
    emitted: list[Any] = []
    for e in _read_events(sess):
        t = getattr(e, "type", None)
        if t == "decision.issued":
            d = getattr(e, "payload", None) or {}
            did = d.get("decision_id")
            if did:
                decisions[str(did)] = (d, getattr(e, "trace", None) or {})
        elif t == EVENT_RECEIPT_EMITTED:
            emitted.append(e)
    out: list[DecisionReceipt] = []
    for e in emitted:
        p = getattr(e, "payload", None) or {}
        did = str(p.get("decision_id") or "")
        d, trace = decisions.get(did, ({}, {}))
        if not d:
            continue                                 # 无对应决策:跳过(不臆造)
        kind = str(p.get("kind") or "")
        pid = str(d.get("principal_id") or "")
        pch = d.get("principal_channel")
        principal = Principal.from_legacy_by(
            f"{pch}:{pid}" if (pch and pch != pid) else (pid or "system"))
        out.append(DecisionReceipt(
            receipt_id=str(p.get("receipt_id") or ""), decision_id=did, kind=kind,
            verdict=str(d.get("verdict") or ""), tool=str(d.get("tool") or ""),
            inputs_digest=str(d.get("inputs_digest") or ""),
            policy_fingerprint=str(d.get("policy_fingerprint") or ""),
            principal=principal, approval_ref=d.get("approval_ref"),
            supersedes=d.get("supersedes"),
            event_seq=getattr(e, "seq", None),
            call_id=str(trace.get("call_id") or ""),
            ts=str(getattr(e, "ts", "") or ""),
            content_hash=str(p.get("digest") or ""),
            prev_hash=p.get("prev_hash")))
    return tuple(out)


def verify_chain(receipts: tuple[DecisionReceipt, ...]) -> bool:
    """校验 ``prev_hash`` 链单调可验(INV-R8):首条创世为 ``None``,其余指向前一条。"""
    prev: Optional[str] = None
    for r in receipts:
        if r.prev_hash != prev:
            return False
        if not verify_receipt(r):
            return False
        prev = r.content_hash
    return True


__all__ = [
    "KIND_DECISION", "KIND_APPROVAL", "RECEIPT_KINDS", "EVENT_RECEIPT_EMITTED",
    "DecisionReceipt", "ReceiptStore", "content_hash_for", "receipt_kind_of",
    "verify_receipt", "verify_chain", "rebuild_from_log",
]
