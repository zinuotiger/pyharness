"""tests/acceptance/test_governance_audit_acceptance.py — 治理审计验收（GAP-7）。

验收目标：**治理数据不仅"在盘上"，而且产品内可通过服务入口读到**。

修复前：``AuditSystem`` 已构造并注入 ``GovernanceContext``，但
``causal_chain`` / ``denied_report`` / ``reconcile`` 在 ``pyharness/`` 内零调用点，
桌面"审计"页只能用旧遥测（事件类型计数）顶上 ⇒ 治理可追溯性**不可达**。

本文件验收的真实调用路径：
    真实运行事件 → AuditSystem（重放） → ApplicationService.governance_audit
"""
from __future__ import annotations


from pyharness.application import ApplicationService


def _calls():
    """一轮里同时产生 allow 与 deny 两条治理事实。"""
    return [
        {"id": "ok1", "name": "fs.list_dir", "args": {"path": "."}},
        {"id": "no1", "name": "fs.write_file",
         "args": {"path": "../ESCAPED.txt", "content": "X"}},
    ]


async def test_acceptance_governance_audit_reads_real_runtime(e2e_factory):
    """服务入口读得到真实决策、被拒清单与一致性对账。"""
    s = await e2e_factory(_calls(), sid="s-acc-govaudit-01",
                          channel="desktop")
    await s.boot()
    assert (await s.run("do both")).ok

    svc = ApplicationService(s.ctx, channel="desktop")
    out = await svc.governance_audit(s.sid, reconcile=True)
    assert out["ok"] is True

    # 决策面：allow + reject 各一，且带完整依据与主体
    verdicts = {d["verdict"] for d in out["decisions"]}
    assert {"allow", "reject"}.issubset(verdicts)
    for d in out["decisions"]:
        assert d["decision_id"] and d["call_id"]
        assert d["principal_kind"] == "human"        # GAP-11 同源
        assert d["principal_channel"] == "desktop"

    # 被拒清单：可证"拦了且没执行"（executed=False 由 tool.result 配对推导）
    denied = out["denied"]
    assert denied, "应有一条被拒记录"
    d = next(r for r in denied if r["guard_id"] == "g-fs-path")
    assert d["policy_ref"] == "POL-FS-2"
    assert d["executed"] is False
    assert d["call_id"] == "no1"

    # 一致性对账：真实链路上无 SEQ-GAP / NO-GUARD-EVENT / NO-RECEIPT-FOR-GRANT
    assert out["findings"] == []


async def test_acceptance_receipt_verification_is_reachable(e2e_factory):
    """凭证核验面可达:README 曾自陈 ``verify_receipt`` 未接外壳,现已接上。

    验收点:经服务入口能拿到**逐条重算哈希**的核验结果与 ``prev_hash`` 链判定
    —— 而不是只有一个库内函数。
    """
    s = await e2e_factory(
        [{"id": "ok1", "name": "fs.list_dir", "args": {"path": "."}},
         {"id": "no1", "name": "fs.write_file",
          "args": {"path": "../X.txt", "content": "X"}}],
        sid="s-acc-receipt-0001", channel="desktop")
    await s.boot()
    assert (await s.run("go")).ok

    svc = ApplicationService(s.ctx, channel="desktop")
    out = await svc.governance_audit(s.sid)
    rec = out["receipts"]
    assert rec["count"] == 2, f"allow + reject 各一条凭证,实际 {rec['count']}"
    assert rec["verified"] == rec["count"], "全部凭证必须通过重算核验"
    assert rec["chain_valid"] is True, "prev_hash 链必须单调可验"
    # 创世凭证 prev_hash 为空,第二条指向前一条
    hashes = [r["content_hash"] for r in rec["items"]]
    assert rec["items"][0]["prev_hash"] is None
    assert rec["items"][1]["prev_hash"] == hashes[0]


async def test_acceptance_causal_chain_reconstructs_allow(e2e_factory):
    """由 decision_id 还原完整因果链（请求 → 决策 → 凭证 → 执行）。"""
    s = await e2e_factory([{"id": "ok1", "name": "fs.list_dir",
                            "args": {"path": "."}}],
                          sid="s-acc-causal-0001")
    await s.boot()
    assert (await s.run("list")).ok

    svc = ApplicationService(s.ctx, channel="desktop")
    audit = await svc.governance_audit(s.sid)
    did = audit["decisions"][0]["decision_id"]

    chain = (await svc.governance_audit(s.sid, decision_id=did))["chain"]
    assert chain["decision_id"] == did
    assert chain["call_id"] == "ok1"
    assert chain["denied"] is False
    assert chain["request"] is not None                    # tool.call 段
    assert chain["decision"]["verdict"] == "allow"
    assert chain["execution"] is not None                  # tool.result 段
    assert chain["receipt"]["kind"] == "decision"          # 凭证段
    assert chain["missing"] == [], f"链不应有缺环:{chain['missing']}"


async def test_acceptance_causal_chain_reconstructs_deny(e2e_factory):
    """被拒决策的因果链：denied=True，且**无执行段**（拒绝零副作用可证）。"""
    s = await e2e_factory([{"id": "no1", "name": "fs.write_file",
                            "args": {"path": "../ESCAPED.txt", "content": "X"}}],
                          sid="s-acc-causal-deny1")
    await s.boot()
    assert (await s.run("escape")).ok

    svc = ApplicationService(s.ctx, channel="desktop")
    audit = await svc.governance_audit(s.sid)
    did = audit["decisions"][0]["decision_id"]
    chain = (await svc.governance_audit(s.sid, decision_id=did))["chain"]

    assert chain["decision"]["verdict"] == "reject"
    assert chain["denied"] is True
    assert chain["execution"] is None, "被拒调用不得有执行段(INV-05)"
    assert chain["request"] is not None


async def test_acceptance_reconcile_detects_missing_fact(e2e_factory):
    """负向：破坏一条事实后，对账**必须**报出来（读侧不能自我粉饰）。

    实现说明：``Envelope`` 是 pydantic **frozen** 模型（INV-01 append-only 在
    模型层强制），因此无法就地篡改事件。这里用**过滤视图**（真实日志去掉
    ``guard.evaluated`` 后的只读投影）模拟"执行了但没有求值留痕"，并把它交给
    ``AuditSystem``——顺带证明该视图**只依赖日志重放**，不依赖任何订阅态索引。
    """
    from pyharness.governance import AuditSystem

    s = await e2e_factory([{"id": "ok1", "name": "fs.list_dir",
                            "args": {"path": "."}}],
                          sid="s-acc-reconcile-01")
    await s.boot()
    assert (await s.run("list")).ok

    spine = s.ctx.engine_spine
    assert await spine.governance.audit.reconcile() == []   # 完好时无发现

    class _DropGuardEvaluated:
        """只读投影：真实日志去掉指定类型的事件。"""

        def __init__(self, log, drop: str) -> None:
            self._log, self._drop = log, drop

        def events_after(self, seq):
            return [e for e in self._log.events_after(seq)
                    if e.type != self._drop]

    view = _DropGuardEvaluated(s.ctx.session, "guard.evaluated")
    findings = await AuditSystem(session=view).reconcile()
    assert any(f.startswith("NO-GUARD-EVENT") for f in findings), \
        f"抹掉求值留痕后对账应报 NO-GUARD-EVENT,实际 {findings}"
    # 未被破坏的会话本身仍是一致的（对账不污染真源）
    assert await spine.governance.audit.reconcile() == []


async def test_acceptance_unknown_decision_id_is_not_fabricated(e2e_factory):
    """读侧纪律：未知 decision_id 只报"缺哪一段"，不臆造因果链。"""
    s = await e2e_factory(None, sid="s-acc-unknown-0001")
    await s.boot()
    svc = ApplicationService(s.ctx, channel="desktop")
    chain = (await svc.governance_audit(s.sid,
                                        decision_id="does-not-exist"))["chain"]
    assert chain["decision"] is None
    assert chain["missing"] == ["decision.issued"]


async def test_acceptance_evidence_query_surface(e2e_factory):
    """GAP-8 验收:证据归档面可由服务入口读到,且**索引与日志重建一致**(INV-E3)。"""
    s = await e2e_factory(
        [{"id": "e1", "name": "fs.list_dir", "args": {"path": "."}}],
        sid="s-acc-evidence-0001", channel="desktop")
    await s.boot()
    assert (await s.run("list")).ok

    svc = ApplicationService(s.ctx, channel="desktop")
    out = await svc.governance_evidence(s.sid, task_id=s.last_task_id)
    assert out["ok"] is True
    assert out["artifacts"], "应有归档工件"
    # **回答取自日志重建**(INV-E3),不取订阅态内存索引:本服务实例是事后构造的,
    # 其 EvidenceCollector 在归档事件之后才订阅 ⇒ indexed 合法为 0,而 rebuilt 为 1。
    # 这正是读侧必须 replay 而非读缓存的原因(二者不符不是 bug,是订阅起点的差异)。
    assert out["rebuilt"] == len(out["artifacts"]) == 1
    assert out["indexed"] <= out["rebuilt"]
    assert len(out["for_task"]) == 1
    art = out["for_task"][0]
    assert art["refs"], "证据必须携带可解析引用"
    kinds = {r["kind"] for r in art["refs"]}
    assert "segment" in kinds and "decision_id" in kinds


async def test_acceptance_evidence_live_index_matches_rebuild_on_owning_spine(
        e2e_factory):
    """同一 spine 上:活订阅者索引 == 日志重建索引(GAP-8 闭合性)。"""
    from pyharness.governance import EvidenceCollector

    s = await e2e_factory(
        [{"id": "e2", "name": "fs.list_dir", "args": {"path": "."}}],
        sid="s-acc-evidence-0002", channel="desktop")
    await s.boot()
    assert (await s.run("list")).ok
    live = s.ctx.engine_spine.governance.evidence
    rebuilt = await EvidenceCollector.from_log(s.ctx.session)
    assert live.evidence_count() == rebuilt.evidence_count() == 1


async def test_acceptance_evidence_empty_for_governance_free_session(e2e_factory):
    """负向:无治理决策的会话不产出证据(不为空段造工件)。"""
    s = await e2e_factory(None, sid="s-acc-evidence-empty")
    await s.boot()
    assert (await s.run("hi")).ok
    svc = ApplicationService(s.ctx, channel="desktop")
    out = await svc.governance_evidence(s.sid, task_id=s.last_task_id)
    assert out["artifacts"] == []
    assert out["for_task"] == []
