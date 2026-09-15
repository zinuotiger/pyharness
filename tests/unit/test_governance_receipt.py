"""tests/unit/test_governance_receipt.py — M4 决策凭证单测。

冻结口径:R-1(``digest`` == ``content_hash``)· R-2(approval 凭证在 D2 处)·
R-3(``Receipt.ts`` = ``receipt.emitted`` 的 ``Envelope.ts``)。

铁律:全部走真实 ``ReceiptStore`` / ``verify_receipt``;日志替身只提供
``events_after``(与 ``SessionLog`` 同型),**不 mock 哈希与链逻辑**。
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass

import pytest

from pyharness.governance.decision import Decision, Principal, PrincipalKind, Verdict
from pyharness.governance.receipt import (KIND_APPROVAL, KIND_DECISION,
                                          EVENT_RECEIPT_EMITTED,
                                          DecisionReceipt, ReceiptStore,
                                          content_hash_for, rebuild_from_log,
                                          receipt_kind_of, verify_chain,
                                          verify_receipt)

P = Principal(PrincipalKind.HUMAN, "cli", "cli")


def _dec(*, did="d1", verdict=Verdict.ALLOW, approval_ref=None, supersedes=None,
         tool="fs.read_file", digest="deadbeef", fingerprint="fp1",
         principal=P) -> Decision:
    return Decision(decision_id=did, verdict=verdict, tool=tool,
                    policy_refs=("POL-FS-1",), guard_ids=("g-fs-path",),
                    policy_fingerprint=fingerprint, inputs_digest=digest,
                    principal=principal, ts="2026-09-15T00:00:00Z",
                    approval_ref=approval_ref, supersedes=supersedes)


@dataclass
class _Ev:
    seq: int
    type: str
    payload: dict
    trace: dict | None = None
    ts: str = "2026-09-15T00:00:01Z"


class _Log:
    """会话日志替身:仅 ``events_after``(与 SessionLog 同型)。"""

    def __init__(self) -> None:
        self.events: list[_Ev] = []
        self._seq = 0

    async def append(self, type_, payload, *, actor, sync=False, trace=None):
        self._seq += 1
        e = _Ev(seq=self._seq, type=type_, payload=dict(payload), trace=trace)
        self.events.append(e)
        return e

    def events_after(self, after_seq: int = 0):
        return [e for e in self.events if e.seq > after_seq]


class _Ctx:
    def __init__(self, log): self.session = log


# ---------------------------------------------------------------- 产生规则
def test_r7_d1_produces_no_receipt():
    """INV-R7:D1(verdict=APPROVAL 且 approval_ref is None)不产生凭证。"""
    assert receipt_kind_of(_dec(verdict=Verdict.APPROVAL)) is None


@pytest.mark.parametrize("verdict", [Verdict.ALLOW, Verdict.REJECT])
def test_decision_kind_for_allow_reject(verdict):
    assert receipt_kind_of(_dec(verdict=verdict)) == KIND_DECISION


def test_d2_kind_is_approval():
    """D2:approval_ref 非 None ⇒ approval 凭证(真实 verdict 仍为 APPROVAL)。"""
    d2 = _dec(verdict=Verdict.APPROVAL, approval_ref=7, supersedes="d1")
    assert receipt_kind_of(d2) == KIND_APPROVAL


# ---------------------------------------------------------------- 确定性哈希
def test_r3_same_facts_same_hash():
    """INV-R3:同受保护事实 ⇒ 同 content_hash(与 receipt_id 无关)。"""
    d = _dec()
    h1 = content_hash_for(decision=d, kind=KIND_DECISION, prev_hash=None)
    h2 = content_hash_for(decision=d, kind=KIND_DECISION, prev_hash=None)
    assert h1 == h2 and len(h1) == 64


@pytest.mark.parametrize("field,value", [
    ("did", "d2"), ("tool", "fs.write_file"), ("digest", "other"),
    ("fingerprint", "fp2"), ("approval_ref", 9), ("supersedes", "d0"),
])
def test_r3_hash_changes_with_protected_facts(field, value):
    d = _dec()
    h1 = content_hash_for(decision=d, kind=KIND_DECISION, prev_hash=None)
    h2 = content_hash_for(decision=_dec(**{field: value}),
                          kind=KIND_DECISION, prev_hash=None)
    assert h1 != h2


def test_r3_prev_hash_binds_chain():
    d = _dec()
    a = content_hash_for(decision=d, kind=KIND_DECISION, prev_hash=None)
    b = content_hash_for(decision=d, kind=KIND_DECISION, prev_hash="x" * 64)
    assert a != b


# ---------------------------------------------------------------- emit / 链
async def test_emit_genesis_then_chain():
    """创世 ``prev_hash=None``;第二条指向前一条 ``content_hash``。"""
    log = _Log()
    store = ReceiptStore(id_factory=lambda: "r1", clock=lambda: "T1")
    r1 = await store.emit(_Ctx(log), _dec(did="d1"), call_id="c1")
    assert r1.prev_hash is None and r1.event_seq == 1 and r1.ts == "T1"
    assert verify_receipt(r1) is True
    store2 = ReceiptStore(id_factory=lambda: "r2")
    r2 = await store2.emit(_Ctx(log), _dec(did="d2"), call_id="c2")
    assert r2.prev_hash == r1.content_hash
    assert verify_receipt(r2) is True


async def test_emit_payload_is_five_fields():
    """持久化 payload 冻结为 5 字段;``digest`` == content_hash(R-1)。"""
    log = _Log()
    store = ReceiptStore(id_factory=lambda: "rX")
    r = await store.emit(_Ctx(log), _dec(), call_id="c9")
    p = log.events[0].payload
    assert set(p) == {"receipt_id", "decision_id", "kind", "digest", "prev_hash"}
    assert p["digest"] == r.content_hash
    assert log.events[0].type == EVENT_RECEIPT_EMITTED
    assert log.events[0].trace == {"call_id": "c9"}


async def test_emit_skips_d1_and_reports_none():
    log = _Log()
    store = ReceiptStore()
    assert await store.emit(_Ctx(log), _dec(verdict=Verdict.APPROVAL)) is None
    assert log.events == []


async def test_of_decision_is_tuple_semantics():
    """``of_decision`` 返回**复数**(保留 mixed-model 语义)。"""
    log = _Log()
    store = ReceiptStore()
    await store.emit(_Ctx(log), _dec(did="dA"))
    assert len(store.of_decision("dA")) == 1
    assert store.of_decision("nope") == ()
    assert store.get(store.all()[0].receipt_id) is not None


# ---------------------------------------------------------------- verify
async def test_r4_tamper_any_protected_field_fails():
    """INV-R4:修改任一受保护字段 ⇒ verify() == False(fail-closed)。"""
    log = _Log()
    store = ReceiptStore()
    r = await store.emit(_Ctx(log), _dec())
    assert verify_receipt(r) is True
    for bad in (dataclasses.replace(r, tool="tampered"),
                dataclasses.replace(r, verdict="reject"),
                dataclasses.replace(r, inputs_digest="zzz"),
                dataclasses.replace(r, policy_fingerprint="zzz"),
                dataclasses.replace(r, approval_ref=99),
                dataclasses.replace(r, prev_hash="zzz"),
                dataclasses.replace(r, principal=Principal(
                    PrincipalKind.SYSTEM, "system", None))):
        assert verify_receipt(bad) is False


def test_r4_kind_semantics_enforced():
    """approval 必须带 approval_ref;decision 必须不带。"""
    base = dict(receipt_id="r", decision_id="d", verdict="allow", tool="t",
                inputs_digest="dg", policy_fingerprint="fp", principal=P,
                supersedes=None, event_seq=1, call_id="c",
                ts="T", content_hash="h")
    assert verify_receipt(DecisionReceipt(kind=KIND_APPROVAL,
                                          approval_ref=None, **base)) is False
    assert verify_receipt(DecisionReceipt(kind=KIND_DECISION,
                                          approval_ref=5, **base)) is False
    assert verify_receipt(DecisionReceipt(kind="bogus", approval_ref=None,
                                          **base)) is False
    assert verify_receipt("not-a-receipt") is False


# ---------------------------------------------------------------- rebuild
async def test_r2_rebuild_from_log_and_chain():
    """INV-R2/R8:由事件真源重建;链可验证;篡改任一环 ⇒ 断链。"""
    log = _Log()
    store = ReceiptStore()
    for i, v in enumerate((Verdict.ALLOW, Verdict.REJECT)):
        d = _dec(did=f"d{i}", verdict=v)
        await log.append("decision.issued", {
            "decision_id": d.decision_id, "verdict": str(d.verdict),
            "tool": d.tool, "guard_ids": ["g"], "policy_refs": ["P"],
            "policy_fingerprint": d.policy_fingerprint,
            "inputs_digest": d.inputs_digest,
            "principal_kind": "human", "principal_id": "cli",
            "principal_channel": "cli", "ts": d.ts,
            "approval_ref": None, "supersedes": None},
            actor="system", trace={"call_id": f"c{i}"})
        await store.emit(_Ctx(log), d, call_id=f"c{i}")
    reb = rebuild_from_log(log)
    assert len(reb) == 2
    assert all(verify_receipt(x) for x in reb)
    assert verify_chain(reb) is True
    # 断链检测
    broken = (reb[0], dataclasses.replace(reb[1], prev_hash="0" * 64))
    assert verify_chain(broken) is False


async def test_chain_survives_store_recreation(monkeypatch):
    """链头**由日志恢复**(不依赖内存状态)⇒ 新 store 续链正确(INV-R5)。"""
    log = _Log()
    s1 = ReceiptStore()
    r1 = await s1.emit(_Ctx(log), _dec(did="d1"))
    s2 = ReceiptStore()                       # 模拟重启:全新 store
    r2 = await s2.emit(_Ctx(log), _dec(did="d2"))
    assert r2.prev_hash == r1.content_hash
