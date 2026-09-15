"""tests/invariants/test_inv_governance.py — 治理不变量(INV-G1~G6 / INV-R1~R8)。

冻结依据:ADR-018 / `docs/EVENT-SCHEMA.md:492`(INV-G1~G6)· M4 裁定与 R-1/R-2/R-3。

分工说明:逐字段细节(R3 确定性 / R4 篡改判定)由
``tests/unit/test_governance_receipt.py`` 覆盖;本文件是**跨模块/结构性**层,
逐条钉死不变量的成立条件。
"""
from __future__ import annotations

import ast
import pathlib
import types

from pyharness.events import vocab as V
from pyharness.governance.decision import (Decision, Principal, PrincipalKind,
                                           Verdict)
from pyharness.governance.receipt import (EVENT_RECEIPT_EMITTED, ReceiptStore,
                                          rebuild_from_log, verify_chain,
                                          verify_receipt)

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_P = Principal(PrincipalKind.HUMAN, "cli", "cli")


class _Ev:
    def __init__(self, seq, type_, payload, trace=None, ts="T"):
        self.seq, self.type, self.payload = seq, type_, payload
        self.trace, self.ts = trace, ts


class _Log:
    """会话日志替身:``append``(写) + ``events_after``(只读),与 SessionLog 同型。"""

    def __init__(self):
        self.events: list[_Ev] = []

    async def append(self, type_, payload, *, actor, sync=False, trace=None):
        e = _Ev(len(self.events) + 1, type_, dict(payload), trace)
        self.events.append(e)
        return e

    def events_after(self, after: int = 0):
        return [e for e in self.events if e.seq > after]


def _dec(did, *, verdict=Verdict.ALLOW, approval_ref=None, supersedes=None):
    return Decision(decision_id=did, verdict=verdict, tool="fs.read_file",
                    policy_fingerprint="fp", inputs_digest="dg",
                    principal=_P, ts="T", approval_ref=approval_ref,
                    supersedes=supersedes)


def _issued(log, d, call_id):
    """把一次决策事实写入日志(与 ``decision.issued`` 载荷同形)。"""
    log.events.append(_Ev(len(log.events) + 1, "decision.issued", {
        "decision_id": d.decision_id, "verdict": str(d.verdict),
        "tool": d.tool, "guard_ids": ["g"], "policy_refs": ["P"],
        "policy_fingerprint": d.policy_fingerprint,
        "inputs_digest": d.inputs_digest, "principal_kind": "human",
        "principal_id": "cli", "principal_channel": "cli", "ts": d.ts,
        "approval_ref": d.approval_ref, "supersedes": d.supersedes},
        trace={"call_id": call_id}))


# ================================================================ INV-G4 / G5
def test_inv_g4_receipt_is_reference_and_hash_only():
    """INV-G4:receipt 持久化载荷**恰 5 字段**,不复制决策内容。"""
    m = V.payload_model_for(EVENT_RECEIPT_EMITTED)
    assert set(m.model_fields) == {"receipt_id", "decision_id", "kind",
                                   "digest", "prev_hash"}
    for copied in ("verdict", "tool", "inputs_digest", "policy_fingerprint",
                   "principal", "approval_ref", "supersedes", "supersedes"):
        assert copied not in m.model_fields


def test_inv_g5_governance_has_no_execution_api():
    """INV-G5:治理层无执行/放行 API;``DecisionEngine`` 纯(无 await/I/O)。"""
    banned = ("execute", "run", "bypass", "force_allow", "override", "allow")
    for f in _ROOT.joinpath("pyharness", "governance").glob("*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        names = {n.name for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        assert not (names & set(banned)), f"{f.name} 暴露执行/放行 API"
    tree = ast.parse((_ROOT / "pyharness/governance/decision.py")
                     .read_text(encoding="utf-8"))
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == "DecisionEngine")
    fn = next(n for n in cls.body
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "decide")
    assert sum(1 for n in ast.walk(fn) if isinstance(n, ast.Await)) == 0


# ================================================================ INV-G6
def test_inv_g6_receipt_event_inherits_sync_contract():
    """INV-G6:``receipt.emitted`` 强同步 ⇒ 继承 F-SYNC-1 修复后的
    "失败不静默成功 + 失败批不丢"(全量语义由
    ``tests/unit/test_persistence.py::TestSyncDurability`` 逐条覆盖)。"""
    assert EVENT_RECEIPT_EMITTED in V.SYNC_TYPES
    assert V.is_transient(EVENT_RECEIPT_EMITTED) is False
    assert V.is_registered(EVENT_RECEIPT_EMITTED) is True


# ================================================================ INV-R1
async def test_inv_r1_receipt_binds_exactly_one_decision():
    """INV-R1:每条 receipt 唯一绑定一个 decision_id;``of_decision`` 保复数语义。"""
    log = _Log()
    store = ReceiptStore()
    ctx = types.SimpleNamespace(session=log)
    await store.emit(ctx, _dec("dX"))
    got = store.of_decision("dX")
    assert len(got) == 1 and got[0].decision_id == "dX"
    assert store.of_decision("other") == ()
    assert store.get(got[0].receipt_id) is got[0]


# ================================================================ INV-R5
def test_inv_r5_no_second_persistence_source():
    """INV-R5:凭证模块不持持久化(无文件/DB 写);不 import core/persistence/bus。"""
    src = (_ROOT / "pyharness/governance/receipt.py").read_text(encoding="utf-8")
    for forbidden in ("open(", "Path(", "sqlite", "json.dump(", "os.replace"):
        assert forbidden not in src, f"receipt.py 出现独立持久化:{forbidden}"
    tree = ast.parse(src)
    mods = {n.module or "" for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom)}
    mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
             for a in n.names}
    for m in mods:
        assert not m.startswith(("pyharness.core", "pyharness.persistence",
                                 "pyharness.bus", "pyharness.engine")), m


# ================================================================ INV-R3 / R4 / R8
async def test_inv_r3_r4_r8_hash_tamper_chain():
    """INV-R3(确定性)/R4(篡改必败)/R8(链可验且不可断链)。"""
    import dataclasses
    log = _Log()
    store = ReceiptStore()
    ctx = types.SimpleNamespace(session=log)
    r1 = await store.emit(ctx, _dec("d1"))
    r2 = await store.emit(ctx, _dec("d2"))
    assert verify_receipt(r1) and verify_receipt(r2)
    assert r2.prev_hash == r1.content_hash              # R8:链
    assert verify_chain((r1, r2)) is True
    # R3:同事实同哈希(重算一致)
    assert dataclasses.replace(r1).content_hash == r1.content_hash
    # R4:篡改任一受保护字段 → False(fail-closed)
    assert verify_receipt(dataclasses.replace(r1, tool="TAMPERED")) is False
    assert verify_receipt(dataclasses.replace(r1, inputs_digest="x")) is False
    # R8:断链/重排 → False
    assert verify_chain((r1, dataclasses.replace(r2, prev_hash=None))) is False
    assert verify_chain((r2, r1)) is False


# ================================================================ INV-G1/G2/R2/R6/R7
async def test_inv_g1_g2_r2_r6_r7_full_approval_lifecycle():
    """审批生命周期全程可 replay、可唯一追踪;D1 不产生凭证。"""
    log = _Log()
    store = ReceiptStore()
    ctx = types.SimpleNamespace(session=log)

    # 1) D1:有 decision.issued(INV-G1 的持久事实面),**无** receipt(INV-R7)
    d1 = _dec("D1", verdict=Verdict.APPROVAL)
    _issued(log, d1, "cid")
    assert await store.emit(ctx, d1, call_id="cid") is None
    assert not [e for e in log.events if e.type == EVENT_RECEIPT_EMITTED]

    # 2) approval.granted(approval_id = 请求事件 seq)
    log.events.append(_Ev(len(log.events) + 1, "approval.granted",
                          {"approval_id": 9, "by": "cli:alice"},
                          trace={"call_id": "cid"}))

    # 3) D2:approval_ref 指向该 approval_id → approval 凭证
    d2 = _dec("D2", verdict=Verdict.APPROVAL, approval_ref=9, supersedes="D1")
    _issued(log, d2, "cid")
    rec = await store.emit(ctx, d2, call_id="cid")
    assert rec is not None and rec.kind == "approval" and rec.approval_ref == 9

    # INV-G2 / R6:granted → D2.approval_ref → D2.decision_id → receipt(唯一)
    granted = [e for e in log.events if e.type == "approval.granted"][0]
    receipts = [e for e in log.events if e.type == EVENT_RECEIPT_EMITTED]
    assert len(receipts) == len([e for e in log.events
                                 if e.type == "approval.granted"])
    issued = {e.payload["decision_id"]: e for e in log.events
              if e.type == "decision.issued"}
    target = [r for r in receipts
              if issued[r.payload["decision_id"]].payload["approval_ref"]
              == granted.payload["approval_id"]]
    assert len(target) == 1

    # INV-R2:由事件真源重建 → 全部可验 + 链完整
    reb = rebuild_from_log(log)
    assert len(reb) == 1 and verify_chain(reb) is True
    assert reb[0].approval_ref == granted.payload["approval_id"]


# ================================================================ INV-E3
class _EvE:
    def __init__(self, seq, type_, payload):
        self.seq, self.type, self.payload = seq, type_, payload


class _LogE:
    """会话日志替身(``events_after`` + ``append``,与 SessionLog 同型)。"""

    def __init__(self):
        self.events: list[_EvE] = []

    async def append(self, type_, payload, *, actor, sync=False, trace=None):
        e = _EvE(len(self.events) + 1, type_, dict(payload))
        self.events.append(e)
        return e

    def events_after(self, after: int = 0):
        return [e for e in self.events if e.seq > after]


async def test_inv_e3_evidence_index_is_rederivable_not_truth_source():
    """INV-E3:``evidence.archived`` **非强同步** ⇒ 证据索引**可由事件日志完全再派生**
    (索引是缓存,不是事实源);且 ``on_event`` 只读(事件数不变)。"""
    from pyharness.governance.evidence import EvidenceCollector

    log = _LogE()
    log.events.append(_EvE(1, "segment.start", {"task_id": "t-1"}))
    log.events.append(_EvE(2, "evidence.archived",
                           {"evidence_id": "E1", "claim": "c",
                            "refs": [{"kind": "segment", "locator": "t-1:seg"}],
                            "artifact_path": None}))

    # 订阅态
    col = EvidenceCollector()
    for e in log.events:
        await col.on_event(e)
    before = len(log.events)
    # 再派生:全新 collector 仅靠 replay
    rebuilt = await EvidenceCollector.from_log(log)
    assert ([x.evidence_id for x in rebuilt.collect_for_task("t-1")]
            == [x.evidence_id for x in col.collect_for_task("t-1")] == ["E1"])
    assert len(log.events) == before               # 只读:零写入


# ================================================================ INV-A1~A3
async def test_inv_a1_a2_a3_audit_is_replay_only_fail_closed_report():
    """INV-A1(缺失显式,不臆造)/ A2(纯只读,无写点)/ A3(拒绝可追溯,可证未执行)。"""
    from pyharness.governance.audit import AuditSystem

    log = _Log()
    log.events += [
        _Ev(1, "tool.call", {"name": "fs.delete", "call_id": "cB"}),
        _Ev(2, "guard.rejected", {"tool": "fs.delete", "guard_id": "g-danger",
                                  "reason": "POL-DGR-1",
                                  "policy_ref": "POL-DGR-1"},
            trace={"call_id": "cB"}),
        _Ev(3, "decision.issued", {"decision_id": "D-B", "verdict": "reject",
                                   "tool": "fs.delete",
                                   "policy_refs": ["POL-DGR-1"],
                                   "guard_ids": ["g-danger"],
                                   "approval_ref": None},
            trace={"call_id": "cB"}),
    ]
    audit = AuditSystem(session=log)

    # INV-A1:不存在的 decision_id ⇒ 显式缺失,不臆造、不抛
    chain = audit.causal_chain("NOPE")
    assert chain["missing"] == ["decision.issued"]
    for seg in ("request", "policy", "decision", "approval", "receipt",
                "execution"):
        assert chain[seg] is None

    # INV-A2:纯只读 —— 事件数不变
    before = len(log.events)
    audit.causal_chain("D-B")
    audit.denied_report()
    assert len(log.events) == before

    # INV-A3:拒绝可追溯 → 两类来源可区分,且可证"拦了且没执行"
    rows = audit.denied_report()
    assert {r["source"] for r in rows} == {"guard.rejected", "decision.issued"}
    assert all(r["executed"] is False for r in rows)
    assert {r["call_id"] for r in rows} == {"cB"}
