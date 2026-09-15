"""tests/unit/test_governance_audit.py — 审计视图单测(S5-3a + S5-3b)。

- **S5-3a**:因果链完整性(ALLOW / REJECT / D1 / D2)· 审批与凭证关联 ·
  ``denied_report`` 两类来源 · replay 一致性 · **零副作用** · 缺失语义 ·
  边界与依赖边界。
- **S5-3b**:``engine`` 装配(只构造 + 只注入)· 单实例 · **不新增事件订阅**。

铁律:全部经**真实事件日志**(替身只提供 ``events_after``/``replay``,与
``SessionLog`` 同型);**不 mock 派生逻辑**;断言 ``audit.py`` 内**零 append**。
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from pyharness.governance.audit import CHAIN_SEGMENTS, AuditSystem

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SID = "s-abc12345"


class _Ev:
    def __init__(self, seq, type_, payload, trace=None, ts="T", session_id=_SID):
        self.seq, self.type, self.payload = seq, type_, payload
        self.trace, self.ts, self.session_id = trace, ts, session_id


class _Log:
    """会话日志替身(``events_after`` + ``append``,与 SessionLog 同型)。"""

    def __init__(self) -> None:
        self.events: list[_Ev] = []

    async def append(self, type_, payload, *, actor, sync=False, trace=None):
        e = _Ev(len(self.events) + 1, type_, dict(payload), trace)
        self.events.append(e)
        return e

    def events_after(self, after: int = 0):
        return [e for e in self.events if e.seq > after]


class _Ctx:
    def __init__(self, log): self.session = log


def _dec_payload(did, *, verdict, tool="fs.write_file", refs=("POL-FS-1",),
                 guards=("g-schema",), approval_ref=None, supersedes=None):
    return {"decision_id": did, "verdict": verdict, "tool": tool,
            "guard_ids": list(guards), "policy_refs": list(refs),
            "policy_fingerprint": "fp", "inputs_digest": "dg",
            "principal_kind": "human", "principal_id": "cli",
            "principal_channel": "cli", "ts": "T",
            "approval_ref": approval_ref, "supersedes": supersedes}


def _log() -> _Log:
    """四场景日志(seq → 事件)。

    1  tool.call(cA) · 2  decision.issued(D-A,allow) · 3  receipt.emitted(R-A)
    4  tool.result(cA, ok) · 5  tool.call(cB) · 6  guard.rejected(g-danger)
    7  decision.issued(D-B,reject) · 8  receipt.emitted(R-B)
    9  tool.call(cC) · 10 decision.issued(D-C1,approval)
    11 approval.requested(cC)   ← approval_id = 11
    12 approval.granted(approval_id=11)
    13 decision.issued(D-C2,approval,approval_ref=11,supersedes=D-C1)
    14 receipt.emitted(R-C2,approval) · 15 tool.result(cC, ok)
    """
    log = _Log()
    log.events += [
        _Ev(1, "tool.call", {"name": "fs.write_file", "args": {},
                             "raw_args": {}, "call_id": "cA"}),
        _Ev(2, "decision.issued", _dec_payload("D-A", verdict="allow"),
            trace={"call_id": "cA"}),
        _Ev(3, "receipt.emitted", {"receipt_id": "R-A", "decision_id": "D-A",
                                   "kind": "decision", "digest": "hA",
                                   "prev_hash": None}, trace={"call_id": "cA"}),
        _Ev(4, "tool.result", {"name": "fs.write_file", "call_id": "cA",
                               "ok": True, "summary": "ok", "truncated": False}),
        _Ev(5, "tool.call", {"name": "fs.delete", "args": {}, "raw_args": {},
                             "call_id": "cB"}),
        _Ev(6, "guard.rejected", {"tool": "fs.delete", "guard_id": "g-danger",
                                  "reason": "POL-DGR-1",
                                  "policy_ref": "POL-DGR-1"},
            trace={"call_id": "cB"}),
        _Ev(7, "decision.issued",
            _dec_payload("D-B", verdict="reject", tool="fs.delete",
                         refs=("POL-DGR-1",), guards=("g-danger",)),
            trace={"call_id": "cB"}),
        _Ev(8, "receipt.emitted", {"receipt_id": "R-B", "decision_id": "D-B",
                                   "kind": "decision", "digest": "hB",
                                   "prev_hash": "hA"}, trace={"call_id": "cB"}),
        _Ev(9, "tool.call", {"name": "fs.write_file", "args": {},
                             "raw_args": {}, "call_id": "cC"}),
        _Ev(10, "decision.issued", _dec_payload("D-C1", verdict="approval"),
            trace={"call_id": "cC"}),
        _Ev(11, "approval.requested", {"tool": "fs.write_file",
                                       "args_summary": "a", "ttl_ms": 1,
                                       "risk": "high"},
            trace={"call_id": "cC"}),
        _Ev(12, "approval.granted", {"approval_id": 11, "by": "cli:alice",
                                     "ttl_ms": 1}),
        _Ev(13, "decision.issued",
            _dec_payload("D-C2", verdict="approval", approval_ref=11,
                         supersedes="D-C1"), trace={"call_id": "cC"}),
        _Ev(14, "receipt.emitted", {"receipt_id": "R-C2", "decision_id": "D-C2",
                                    "kind": "approval", "digest": "hC2",
                                    "prev_hash": "hB"}, trace={"call_id": "cC"}),
        _Ev(15, "tool.result", {"name": "fs.write_file", "call_id": "cC",
                                "ok": True, "summary": "ok", "truncated": False}),
    ]
    return log


# ================================================================ T-1 完整性
def test_t1_allow_chain_complete():
    a = AuditSystem(session=_log())
    c = a.causal_chain("D-A")
    assert set(c) == {"decision_id", "call_id", "request", "policy", "decision",
                      "approval", "supersedes", "receipt", "execution",
                      "denied", "missing"}
    assert c["call_id"] == "cA"
    assert c["request"] == {"tool": "fs.write_file", "seq": 1}
    assert c["policy"]["fingerprint"] == "fp"
    assert c["decision"]["verdict"] == "allow" and c["decision"]["seq"] == 2
    assert c["decision"]["principal"] == {"kind": "human", "id": "cli",
                                          "channel": "cli"}
    assert c["approval"] is None and c["supersedes"] is None
    assert c["receipt"]["receipt_id"] == "R-A"
    assert c["execution"] == {"ok": True, "seq": 4}
    assert c["denied"] is False and c["missing"] == []


def test_t1_reject_chain_complete():
    c = AuditSystem(session=_log()).causal_chain("D-B")
    assert c["denied"] is True
    assert c["request"]["tool"] == "fs.delete"
    assert c["decision"]["verdict"] == "reject"
    assert c["receipt"]["receipt_id"] == "R-B"
    assert c["execution"] is None                    # 被拒 ⇒ 无执行(合法)
    assert c["missing"] == []                        # 且**不算缺失**


def test_t1_d1_chain_no_receipt_no_approval_outcome():
    """D1(审批请求决策):无凭证(INV-R7),审批结果尚未产生。"""
    c = AuditSystem(session=_log()).causal_chain("D-C1")
    assert c["decision"]["verdict"] == "approval"
    assert c["receipt"] is None
    assert c["approval"] is None                     # approval_ref 为空 ⇒ 不查
    assert c["missing"] == []                        # D1 本不应有凭证与审批结果


# ================================================================ T-2 / T-3
def test_t2_t3_d2_links_approval_and_receipt():
    """D2:approval_ref == requested.seq == granted.approval_id;凭证按 decision_id 关联。"""
    c = AuditSystem(session=_log()).causal_chain("D-C2")
    assert c["approval"] == {"approval_id": 11, "outcome": "granted",
                             "by": "cli:alice", "seq": 12}
    assert c["supersedes"] == "D-C1"
    assert c["receipt"]["kind"] == "approval" and c["receipt"]["seq"] == 14
    assert c["execution"]["ok"] is True


# ================================================================ T-4 拒绝报告
def test_t4_denied_report_two_sources_and_not_executed():
    rows = AuditSystem(session=_log()).denied_report()
    assert [r["seq"] for r in rows] == [6, 7]         # 升序
    assert set(rows[0]) == {"source", "seq", "tool", "call_id", "guard_id",
                            "policy_ref", "decision_id", "reason", "executed"}
    by_source = {r["source"]: r for r in rows}
    # ① 规则级:guard.rejected
    assert by_source["guard.rejected"]["guard_id"] == "g-danger"
    assert by_source["guard.rejected"]["policy_ref"] == "POL-DGR-1"
    assert by_source["guard.rejected"]["call_id"] == "cB"
    # ② 治理级:decision.issued(verdict=reject)
    assert by_source["decision.issued"]["decision_id"] == "D-B"
    assert by_source["decision.issued"]["reason"] == "POL-DGR-1"
    # 两类均可证"拦了且没执行"(INV-A3)
    assert all(r["executed"] is False for r in rows)


# ================================================================ T-5 replay
def test_t5_replay_consistency():
    """同一日志 ⇒ 结果相等;全新实例(无订阅历史)⇒ 结果相同。"""
    log = _log()
    a = AuditSystem(session=log)
    b = AuditSystem(session=log)
    assert a.causal_chain("D-C2") == b.causal_chain("D-C2")
    assert a.denied_report() == b.denied_report()


# ================================================================ T-6 零副作用
def test_t6_no_side_effects():
    log = _log()
    before = len(log.events)
    a = AuditSystem(session=log)
    a.causal_chain("D-A"); a.causal_chain("D-B"); a.denied_report()
    assert len(log.events) == before                 # 行为证据:事件数不变
    tree = ast.parse((_ROOT / "pyharness/governance/audit.py")
                     .read_text(encoding="utf-8"))

    def dotted(node):
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return f"{dotted(node.value)}.{node.attr}"
        return ""

    # 结构证据:不得对**会话类对象**写入(列表的 ``rows.append`` 不算)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "append"):
            continue
        recv = dotted(node.func.value)
        assert "sess" not in recv.lower(), f"audit.py 疑似写事件:{recv}.append"


# ================================================================ T-7 缺失语义
def test_t7_unknown_decision_reports_missing_not_fabricated():
    c = AuditSystem(session=_log()).causal_chain("NOPE")
    assert c["missing"] == ["decision.issued"]
    for seg in ("request", "policy", "decision", "approval", "receipt",
                "execution"):
        assert c[seg] is None                        # 未臆造
    assert c["denied"] is False


def test_t7_missing_receipt_is_declared():
    """应有凭证而无 → 显式登记 ``receipt.emitted``(不静默)。"""
    log = _log()
    log.events = [e for e in log.events if e.seq != 3]   # 去掉 D-A 的凭证
    c = AuditSystem(session=log).causal_chain("D-A")
    assert "receipt.emitted" in c["missing"]


# ================================================================ T-8 边界
def test_t8_degrades_without_session():
    a = AuditSystem()
    assert a.causal_chain("D-A")["missing"] == ["decision.issued"]
    assert a.denied_report() == []


def test_t8_since_seq_and_session_filter():
    a = AuditSystem(session=_log())
    assert [r["seq"] for r in a.denied_report(since_seq=6)] == [7]
    assert a.denied_report(session_id=_SID) != []
    assert a.denied_report(session_id="s-other999") == []


# ================================================================ T-9 依赖边界
def test_t9_dependency_boundary():
    tree = ast.parse((_ROOT / "pyharness/governance/audit.py")
                     .read_text(encoding="utf-8"))
    mods = {n.module or "" for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom)}
    mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
             for a in n.names}
    for m in mods:
        assert not m.startswith(("pyharness.core", "pyharness.persistence",
                                 "pyharness.bus", "pyharness.engine")), m
    # 不订阅:模块内不得出现 subscribe
    assert not any(isinstance(x, ast.Attribute) and x.attr == "subscribe"
                   for x in ast.walk(tree))


# ================================================================ 规则一致性
@pytest.mark.parametrize("verdict,ref,expected", [
    ("allow", None, True), ("reject", None, True),
    ("approval", None, False), ("approval", 11, True),
])
def test_receipt_expectation_matches_receipt_kind_rule(verdict, ref, expected):
    """``_receipt_expected`` 必须与 ``receipt.receipt_kind_of`` **同一规则**。"""
    from pyharness.governance.audit import _receipt_expected
    from pyharness.governance.decision import (Decision, Principal,
                                               PrincipalKind, Verdict)
    from pyharness.governance.receipt import receipt_kind_of
    assert _receipt_expected(verdict, ref) is expected
    d = Decision(decision_id="d", verdict=Verdict(verdict), tool="t",
                 principal=Principal(PrincipalKind.HUMAN, "cli", "cli"),
                 approval_ref=ref)
    assert (receipt_kind_of(d) is not None) is expected


def test_chain_segments_constant_covers_output():
    """``CHAIN_SEGMENTS`` 是输出段名的权威清单(防字段漂移)。"""
    c = AuditSystem(session=_log()).causal_chain("D-A")
    for seg in CHAIN_SEGMENTS:
        assert seg in c


# ============================== S5-3b:engine 装配(只构造 + 只注入,不订阅)
class TestAuditWiring:
    """S5-3b:``AuditSystem`` 由 engine 构造并注入;**不新增任何事件订阅**。

    自带 cfg 辅助(不跨测试文件引用),保持 S5-3b 白名单不变。
    """

    @staticmethod
    def _cfg(tmp_path):
        from pyharness.config import load_settings
        cfg = load_settings()
        cfg.storage.root = str(tmp_path)
        cfg.storage.sessions_dir = str(tmp_path / "sessions")
        cfg.storage.workspaces_dir = str(tmp_path / "workspaces")
        cfg.storage.spill_dir = str(tmp_path / "spill")
        cfg.storage.db_path = str(tmp_path / "pyharness.db")
        return cfg

    async def _spine(self, tmp_path, sid):
        from pyharness.engine import assemble_real_engine
        return await assemble_real_engine(
            self._cfg(tmp_path), sid=sid, sessions_dir=tmp_path / "sessions")

    async def test_single_instance_and_no_audit_subscription(self, tmp_path):
        """① ``ctx.governance.audit`` 与 ``spine.governance.audit`` 同一实例;
        ② **不新增订阅**(订阅 owner 中无 audit 相关)。"""
        ctx = await self._spine(tmp_path, "s-eng-aud-000001")
        gov = ctx.engine_spine.governance
        assert isinstance(gov.audit, AuditSystem)
        assert ctx.governance is gov                       # 单实例
        assert gov.audit is ctx.engine_spine.governance.audit
        owners = {s.owner for subs in ctx.engine_spine.bus._by_type.values()
                  for s in subs}
        assert not any("audit" in str(o).lower() for o in owners), owners

    async def test_replay_only_reads_real_log(self, tmp_path):
        """replay-only:直接读**真实日志**(无需任何订阅/缓存)。"""
        ctx = await self._spine(tmp_path, "s-eng-aud-000002")
        gov = ctx.engine_spine.governance
        await ctx.session.append("session.created",
                                 {"title": "", "model": "m"}, actor="system")
        before = len(list(ctx.session.events_after(0)))
        assert gov.audit.causal_chain("NOPE")["missing"] == ["decision.issued"]
        assert gov.audit.denied_report() == []
        assert len(list(ctx.session.events_after(0))) == before   # 零副作用
