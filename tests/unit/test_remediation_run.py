"""tests/unit/test_remediation_run.py — Governed Auto-Fix Loop 验收测试。

覆盖 `scripts/remediation_run.py` 的四类判据:
  · Fix Unit 聚类(共同根因才合并)
  · Fail-closed 资格模型(每条例外/上限/禁止面)
  · 高风险 → HUMAN_REVIEW + Hx(尤其 P0/治理边界 → H4)
  · 循环防护(same-diff / oscillation / max-rounds)

全部用例把 `remediation_run._FIX_DIR` 指向 `tmp_path`,不触碰真实 `.audit/`。
"""
from __future__ import annotations

import pathlib
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))

import remediation_run as R  # noqa: E402


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "_FIX_DIR", tmp_path)
    return tmp_path


def _audit(findings, batches=None, scopes=None):
    return {"FINDINGS": findings, "BATCHES": batches or {}}


def _f(fid="F-1", sev="P2", batch="BX", detect="cmd", state="OPEN"):
    return {"id": fid, "sev": sev, "state": state, "class": "mandatory",
            "batch": batch, "detect": detect, "evidence": None}


def _enrich(root_cause, files, tests=("pytest x",), plan=None):
    return {"root_cause": root_cause, "files": list(files),
            "targeted_tests": list(tests),
            "change_plan": plan or [{"file": files[0], "find": "a", "replace": "b"}]}


# ------------------------------------------------------------- 聚类 (Phase 3)
def test_cluster_same_root_cause_merges():
    audit = _audit([_f("F-1"), _f("F-2"), _f("F-4")])
    doc = {"ENRICH": {i: _enrich("同一根因", ["scripts/x.py"]) for i in ("F-1", "F-2", "F-4")}}
    units = R.build_units(audit, doc)
    assert len(units) == 1
    assert units[0]["finding_ids"] == ["F-1", "F-2", "F-4"]


def test_cluster_different_root_cause_not_merged():
    audit = _audit([_f("F-1"), _f("F-2")])
    doc = {"ENRICH": {"F-1": _enrich("根因A", ["scripts/x.py"]),
                      "F-2": _enrich("根因B", ["scripts/x.py"])}}
    assert len(R.build_units(audit, doc)) == 2


def test_cluster_skips_closed_findings():
    audit = _audit([_f("F-1", state="CLOSED"), _f("F-2")])
    doc = {"ENRICH": {"F-2": _enrich("rc", ["scripts/x.py"])}}
    ids = [f for u in R.build_units(audit, doc) for f in u["finding_ids"]]
    assert ids == ["F-2"]


# ------------------------------------------------------ 资格模型 (Phase 4, fail-closed)
def _elig(unit_files, plan=None, sev="P2", rc="rc", tests=("pytest x",), scope=None):
    batch = {"BX": {"state": "AUTHORIZED", "scope": scope or list(unit_files),
                    "criterion": "c"}}
    audit = _audit([_f()], batch)
    doc = {"ENRICH": {"F-1": _enrich(rc, unit_files, tests, plan)}}
    unit = R.build_units(audit, doc)[0]
    unit["risk_level"] = sev
    return R.eligibility(unit, audit)


def test_eligibility_p0_is_h4():
    r = _elig(["scripts/x.py"], sev="P0")
    assert r["eligibility"] == "HUMAN_REVIEW" and r["escalation"] == "H4"
    assert any("D-1" in x for x in r["reason"])


def test_eligibility_governance_boundary_is_h4():
    r = _elig(["pyharness/governance/decision.py"])
    assert r["eligibility"] == "HUMAN_REVIEW" and r["escalation"] == "H4"
    assert any("D-4" in x for x in r["reason"])


def test_eligibility_architecture_boundary_is_h4():
    r = _elig(["pyharness/engine.py"])
    assert r["escalation"] == "H4" and any("D-3" in x for x in r["reason"])


def test_eligibility_protocol_is_h3():
    r = _elig([".ai-coding/PROTOCOL.md"])
    assert r["eligibility"] == "HUMAN_REVIEW" and r["escalation"] == "H3"


def test_eligibility_too_many_files_is_h4():
    r = _elig(["scripts/a.py", "scripts/b.py", "scripts/c.py", "scripts/d.py"])
    assert r["eligibility"] == "HUMAN_REVIEW"
    assert any("D-13" in x for x in r["reason"])       # 文件数超限被记录


def test_eligibility_too_many_lines_is_h4():
    plan = [{"file": "scripts/x.py",
             "find": "\n".join(f"l{i}" for i in range(60)), "replace": "z"}]
    r = _elig(["scripts/x.py"], plan=plan)
    assert r["eligibility"] == "HUMAN_REVIEW"
    assert any("D-14" in x for x in r["reason"])       # 行数超限被记录


def test_eligibility_scope_expansion_is_h4():
    r = _elig(["scripts/x.py"], scope=["scripts/other.py"])
    assert r["escalation"] == "H4" and any("D-5" in x for x in r["reason"])


def test_eligibility_missing_targeted_test_requires_review():
    r = _elig(["scripts/x.py"], tests=())
    assert r["eligibility"] == "HUMAN_REVIEW"
    assert any("D-9" in x for x in r["reason"])


def test_eligibility_forbidden_faces_fail_closed():
    for path in ("tests/unit/test_x.py", "scripts/audit_run.py", "uv.lock"):
        r = _elig([path])
        assert r["eligibility"] == "HUMAN_REVIEW", path


def test_eligibility_clean_low_risk_is_auto_fix():
    """低风险 + 单文件 + 可回滚 + 有定向测试 ⇒ AUTO_FIX。

    使用**真实存在的未提交文件**(本模块自身,untracked 且属 allowed root `scripts/`),
    以便 E-6/D-10 的回滚判据能真实成立。
    """
    r = _elig(["scripts/remediation_run.py"])
    assert r["eligibility"] == "AUTO_FIX", r["reason"]
    assert r["escalation"] is None


# ------------------------------------------- AFL-INT-04: D-10 回滚语义
def test_aflint04_tracked_clean_file_is_rollbackable(monkeypatch):
    """AFL-INT-04:已提交且**干净**的文件必须可回滚(git checkout 即还原)。

    修复前 D-10 误落 git 派生回落档 ⇒ 这类文件恒被拒 ⇒ 真实自动修复不可达。
    """
    monkeypatch.setattr(R, "_uncommitted", lambda: set())      # 工作区干净
    monkeypatch.setattr(R, "_is_tracked", lambda p: True)      # 已追踪
    r = _elig(["scripts/refresh_metrics.py"])
    assert r["eligibility"] == "AUTO_FIX", r["reason"]


def test_aflint04_declared_files_are_the_run_files(monkeypatch):
    """声明的文件集即 RUN_FILES(显式白名单档),而非 git 派生回落档。"""
    seen = {}
    monkeypatch.setattr(R, "_uncommitted", lambda: seen.setdefault("called", set()))
    monkeypatch.setattr(R, "_is_tracked", lambda p: True)
    r = _elig(["scripts/refresh_metrics.py"])
    assert r["eligibility"] == "AUTO_FIX"
    assert seen.get("called") == set()                        # 走了白名单档,未被回落档拒


def test_d10_still_denies_preexisting_dirty_file(monkeypatch):
    """安全语义保留:文件**已有在途未提交改动** ⇒ 回滚会波及他人工作 ⇒ 拒(H1)。"""
    monkeypatch.setattr(R, "_uncommitted", lambda: {"scripts/refresh_metrics.py"})
    monkeypatch.setattr(R, "_is_tracked", lambda p: True)
    r = _elig(["scripts/refresh_metrics.py"])
    assert r["eligibility"] == "HUMAN_REVIEW" and r["escalation"] == "H1"
    assert any("D-10" in x for x in r["reason"])


def test_d10_still_denies_committed_baseline(monkeypatch):
    """安全语义保留:命中本 Run 的 COMMITTED_FILES(已提交基线) ⇒ 拒(H1)。"""
    monkeypatch.setattr(R, "_uncommitted", lambda: set())
    monkeypatch.setattr(R, "_is_tracked", lambda p: True)
    batch = {"BX": {"state": "AUTHORIZED", "scope": ["scripts/refresh_metrics.py"],
                    "criterion": "c"}}
    audit = _audit([_f()], batch)
    audit["COMMITTED_FILES"] = ["scripts/refresh_metrics.py"]
    doc = {"ENRICH": {"F-1": _enrich("rc", ["scripts/refresh_metrics.py"])}}
    unit = R.build_units(audit, doc)[0]
    r = R.eligibility(unit, audit)
    assert r["escalation"] == "H1" and any("D-10" in x for x in r["reason"])


def test_d10_does_not_deny_the_runs_own_prior_changes(monkeypatch):
    """本 Run 前几轮已改的文件,不得被当作"在途他人改动"而阻断重试。"""
    monkeypatch.setattr(R, "_uncommitted", lambda: {"scripts/refresh_metrics.py"})
    monkeypatch.setattr(R, "_is_tracked", lambda p: True)
    batch = {"BX": {"state": "AUTHORIZED", "scope": ["scripts/refresh_metrics.py"],
                    "criterion": "c"}}
    audit = _audit([_f()], batch)
    doc = {"ENRICH": {"F-1": _enrich("rc", ["scripts/refresh_metrics.py"])}}
    unit = R.build_units(audit, doc)[0]
    unit["changed_files"] = ["scripts/refresh_metrics.py"]     # 本 Run 自己改过
    assert R.eligibility(unit, audit)["eligibility"] == "AUTO_FIX"


def test_eligibility_defaults_to_human_review_when_unverifiable(monkeypatch):
    """fail-closed:已有在途未提交改动 ⇒ D-10 ⇒ 拒绝。"""
    monkeypatch.setattr(R, "_uncommitted", lambda: {"scripts/a.py"})
    monkeypatch.setattr(R, "_is_tracked", lambda p: True)
    r = _elig(["scripts/a.py"])
    assert r["eligibility"] == "HUMAN_REVIEW"


# ------------------------------------------------------ 循环防护 (Phase 8)
def test_loop_protection_same_diff():
    assert R.no_progress({"ROUNDS": 2, "HISTORY": [{"diff_hash": "a"}, {"diff_hash": "a"}]})["verdict"] == "NO_PROGRESS"


def test_loop_protection_oscillation():
    d = {"ROUNDS": 3, "HISTORY": [{"diff_hash": "a"}, {"diff_hash": "b"}, {"diff_hash": "a"}]}
    assert R.no_progress(d)["verdict"] == "OSCILLATION"


def test_loop_protection_max_rounds():
    d = {"ROUNDS": R.MAX_FIX_ROUNDS, "HISTORY": [{"diff_hash": "a"}, {"diff_hash": "b"}]}
    assert R.no_progress(d)["verdict"] == "MAX_ROUNDS"


def test_loop_protection_ok_on_progress():
    assert R.no_progress({"ROUNDS": 1, "HISTORY": [{"diff_hash": "a"}]})["verdict"] == "OK"


# ------------------------------------------------------ Human Review / Resume (Phase 9)
def test_authorize_resumes_paused_unit(isolated):
    R.save_fix("T", {"RUN_ID": "T", "FIX_UNITS": [
        {"id": "FIX-001", "status": "HUMAN_REVIEW", "requires_human": True,
         "finding_ids": ["F-1"], "files": [], "attempts": 0, "evidence": {}}],
        "HISTORY": [], "ROUNDS": 0})
    rc = R._cmd_authorize(SimpleNamespace(run_id="T", unit="FIX-001", by="human"))
    assert rc == 0
    u = R.load_fix("T")["FIX_UNITS"][0]
    assert u["status"] == "AUTHORIZED"
    assert u["requires_human"] is False
    assert u["evidence"]["authorization"]["by"] == "human"


# ------------------------------------------------------ 硬性上限常量
def test_hard_limits_are_declared():
    assert R.MAX_FIX_ROUNDS == 3
    assert R.MAX_ATTEMPTS == 2
    assert R.MAX_FILES == 3
    assert R.MAX_LINES == 50


def test_forbidden_faces_include_audit_gate_and_protocol():
    """防"改审计门让自己消失"与"改 Protocol"(要求 3 / 11)。"""
    assert "scripts/audit_run.py" in R.FORBIDDEN_EXACT
    assert ".ai-coding/" in R.FORBIDDEN_PREFIXES
    assert "tests/" in R.FORBIDDEN_PREFIXES
