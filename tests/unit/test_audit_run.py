"""tests/unit/test_audit_run.py — M1-M5 Orchestrator 验收测试。

覆盖 Acceptance Review 记录的三条 Finding 的修复:

  · **F-A3**(阻断)  `advance` 失败时必须**持久化递增** RETRIES ⇒ 达到 retry limit
                    后稳定 ESCALATE,不存在无限循环。
  · **F-A1**        `RECOVER` 必须**真实可达**(非仅 docstring 声称):证据缺失型
                    Gate 失败可由已声明检测器自动重采后重验。
  · **F-A2**        `optional` / `deferred` / `observation` 类 finding **不阻塞**
                    M5 收敛(不得误报 H4)。
  · **VAL-B2**      三处证据判据不得"非空即通过":`detect` 必须是白名单内可执行
                    命令,`finding.evidence` 与 `C_VOTES[cid]` 必须是能解析到真实
                    EVIDENCE 记录的指针。legacy 豁免须带理由且必须可见。
  · **VAL-B3**      `detect` 的白名单判定必须是 **argv 级**而非前缀 —— 前缀会放过
                    `'grep X -> 0'` 这类"以白名单词开头、末尾注解了结果"的散文。
                    另加语义兜底:CLOSED finding 的 `detect` 本身必须已真实执行。

隔离:全部用例把 `audit_run._RUNS_DIR` 指向 `tmp_path`,不触碰真实 `.audit/runs/`。
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))

import audit_run as A  # noqa: E402


@pytest.fixture()
def runs(tmp_path, monkeypatch):
    """把 manifest 目录隔离到 tmp_path。"""
    monkeypatch.setattr(A, "_RUNS_DIR", tmp_path)
    return tmp_path


def _ns(run_id: str) -> SimpleNamespace:
    return SimpleNamespace(run_id=run_id)


# ---- VAL-B2 夹具 ----------------------------------------------------------
# CORE-03 判定记录:`_converged_manifest` 此前用 `"detect": "d"` / `"evidence": "e"`
# 这类单字母占位。判定为**缺陷固化**而非契约 —— 那些用例断言的是"状态机能从 M1
# 收敛到 STOP",占位值是夹具便利,不是被锁定的行为。占位值恰好编码了 VAL-B2 要
# 修的空转语义('d' 不是可执行命令、'e' 不是证据指针),故随判据一并更正。
_DETECT = _EV_CMD = "{PY} -m pytest"        # 白名单内、且与 EVIDENCE 记录逐字对应
# 注:detect 必须与 EVIDENCE 记录的 cmd 一致 —— VAL-B3 的 `_detect_ran` 要求
# 「detect 本身真的跑过」,而不只是「有一条不相干的证据」。


def _ev_record(exit_code: int = 0, out: str = "1763 passed, 2 skipped") -> dict:
    return {"kind": "regression", "cmd": A._resolve_cmd(_EV_CMD),
            "exit": exit_code, "out": out}


def _ptr(cmd: str = _EV_CMD) -> dict:
    return {"kind": "regression", "cmd": cmd}


def _c_votes_mechanical(ptr: dict | None = None) -> dict:
    """C1-C8 中机械项的**证据指针**(非机械项 C5 走 attest)。

    VAL-B2:不再接受裸 'PASS' 字符串 —— 机械项必须指向真实证据记录。
    """
    p = ptr or _ptr()
    return {c: [dict(p)] for c, (_n, mech, _d) in A.C_CHECKS.items() if mech}


# ----------------------------------------------------------------- F-A3
def test_fa3_retries_persisted_and_bounded(runs):
    """advance 连续失败 retry_limit+2 次 ⇒ RETRIES 恰好到上限且稳定 ESCALATE。"""
    rid = "TEST-FA3"
    m = A.new_manifest(rid, "fa3")
    m["CURRENT_STAGE"] = "M4"
    m["RETRY_LIMIT"] = 2
    # 真实失败(exit=1):regression_ran 通过但 regression_green 失败
    # ⇒ 不可 RECOVER,必须走 RETRY → ESCALATE
    m["EVIDENCE"] = [{"kind": "regression", "cmd": "x", "exit": 1, "out": "2 failed"}]
    A.save(rid, m)

    observed = []
    for _ in range(m["RETRY_LIMIT"] + 2):
        A._cmd_advance(_ns(rid))
        _, cur = A.load(rid)
        observed.append((cur["RETRIES"], A.decide(cur)["verdict"]))

    retries = [r for r, _ in observed]
    verdicts = [v for _, v in observed]

    # ① 计数确实递增(修复前恒为 0)
    assert retries[0] == 1, f"RETRY 未持久化递增: {observed}"
    # ② 恰好停在上限,不超过
    assert max(retries) == m["RETRY_LIMIT"] == 2
    # ③ 达到上限后稳定 ESCALATE,不再循环
    assert verdicts[-1] == "ESCALATE"
    assert verdicts[-2:] == ["ESCALATE", "ESCALATE"]
    # ④ 上限后计数不再增长(无无限循环)
    assert retries[-1] == retries[-2] == m["RETRY_LIMIT"]


def test_fa3_retry_count_survives_reload(runs):
    """计数写入 manifest(重新 load 不丢) —— 中断后可恢复。"""
    rid = "TEST-FA3B"
    m = A.new_manifest(rid, "fa3b")
    m["CURRENT_STAGE"] = "M4"
    m["EVIDENCE"] = [{"kind": "regression", "exit": 1, "out": "2 failed"}]
    A.save(rid, m)
    A._cmd_advance(_ns(rid))
    _, again = A.load(rid)          # 完全重新读取
    assert again["RETRIES"] == 1


# ----------------------------------------------------------------- F-A1
def test_fa1_recover_is_reachable(runs, monkeypatch):
    """证据缺失型 M4 失败 ⇒ decide 返回 RECOVER;recover 后可解除。"""
    rid = "TEST-FA1"
    m = A.new_manifest(rid, "fa1")
    m["CURRENT_STAGE"] = "M4"
    m["GATES"] = {"RELEASE": "PASS"}    # Release Gate 已评过 ⇒ 不构成"未做的工作"
    m["EVIDENCE"] = []                  # 仅回归证据缺失 ⇒ regression_ran(+其派生)失败
    A.save(rid, m)

    d = A.decide(m)
    assert d["verdict"] == "RECOVER", f"RECOVER 不可达: {d}"
    assert d["escalate_h"] is None

    # 桩掉真实检测器(避免 50s 全量回归),只验证恢复编排路径
    def _fake(rid_, mm, kind, cmd):
        mm.setdefault("EVIDENCE", []).append(
            {"kind": kind, "cmd": cmd, "exit": 0, "out": "1749 passed"})
        return 0

    monkeypatch.setattr(A, "_run_and_record", _fake)
    assert A._cmd_recover(_ns(rid)) == 0

    _, cur = A.load(rid)
    assert A.decide(cur)["verdict"] != "RECOVER", "recover 后仍停在 RECOVER"


def test_fa1_real_failure_is_not_recoverable(runs):
    """真实失败(回归 exit=1)不得走 RECOVER —— 防止把失败伪装成"可自动恢复"。"""
    rid = "TEST-FA1B"
    m = A.new_manifest(rid, "fa1b")
    m["CURRENT_STAGE"] = "M4"
    m["GATES"] = {"RELEASE": "PASS"}
    m["EVIDENCE"] = [{"kind": "regression", "exit": 1, "out": "2 failed"}]
    m["RETRIES"] = 0
    A.save(rid, m)
    assert A.decide(m)["verdict"] == "RETRY"


def test_fa1_undone_work_is_not_recoverable(runs):
    """未做的工作(Release Gate 未评)不算"证据缺失" ⇒ 不得 RECOVER。"""
    rid = "TEST-FA1D"
    m = A.new_manifest(rid, "fa1d")
    m["CURRENT_STAGE"] = "M4"
    m["EVIDENCE"] = []                  # 回归证据缺失
    # GATES 为空 ⇒ release_gate_pass 亦失败,且它不是任何可重采谓词的派生
    A.save(rid, m)
    d = A.decide(m)
    assert d["verdict"] == "RETRY", f"不得把未做的工作当作可自动恢复: {d}"
    assert A._recoverable_failures(["regression_ran", "release_gate_pass"]) is None


def test_fa1_recover_cli_refuses_when_not_recoverable(runs):
    """非 RECOVER 态调用 recover ⇒ 拒绝(不误触发重采)。"""
    rid = "TEST-FA1C"
    m = A.new_manifest(rid, "fa1c")
    m["CURRENT_STAGE"] = "M4"
    m["EVIDENCE"] = [{"kind": "regression", "exit": 0, "out": "1763 passed"}]
    A.save(rid, m)
    assert A._cmd_recover(_ns(rid)) == 1


# ----------------------------------------------------------------- F-A2
def _converged_manifest(optional_open: bool) -> dict:
    m = A.new_manifest("TEST-FA2", "fa2")
    m["FINDINGS"] = [
        {"id": "M1", "sev": "P0", "state": "CLOSED", "class": "mandatory",
         "batch": "B1", "detect": _DETECT, "evidence": [_ptr()]},
    ]
    if optional_open:
        m["FINDINGS"].append(
            {"id": "O1", "sev": "P2", "state": "OPEN", "class": "optional",
             "batch": "B2", "detect": _DETECT})
    m["BATCHES"] = {
        "B1": {"state": "DONE", "scope": ["x"], "criterion": "c", "changed_files": ["x"]},
        "B2": {"state": "NOT_STARTED", "scope": ["y"], "criterion": "c"},
    }
    m["GATES"] = {"RELEASE": "PASS"}
    m["EVIDENCE"] = [_ev_record()]
    m["CURRENT_STAGE"] = "M5"
    m["C_VOTES"] = _c_votes_mechanical()
    m["C_ATTEST"] = {"C5": "残余已声明"}
    return m


def test_fa2_optional_does_not_block_convergence(runs):
    """F-03/F-19 型 mandatory 全关闭 + F-09 型 optional 仍开 ⇒ S1 PASS 且可 STOP。"""
    m = _converged_manifest(optional_open=True)
    sp = A.stop_predicates(m)
    assert sp["S1"] is True, f"optional 阻塞了 S1: {sp}"
    assert sp["S9"] is True
    assert all(sp.values()), f"未收敛: {[k for k, v in sp.items() if not v]}"
    # 关键回归:不得再误报 H4
    d = A.decide(m)
    assert d["verdict"] == "STOP", f"应 STOP,实为 {d}"
    assert d["escalate_h"] is None


def test_fa2_mandatory_still_blocks(runs):
    """mandatory 未关闭仍然阻塞 —— 修复不得放宽 mandatory 约束。"""
    m = _converged_manifest(optional_open=True)
    m["FINDINGS"][0]["state"] = "OPEN"          # 把 mandatory 打开
    sp = A.stop_predicates(m)
    assert sp["S1"] is False
    assert A.decide(m)["escalate_h"] == "H4"


def test_fa2_class_defaults_to_mandatory(runs):
    """未分类 finding 默认 mandatory(fail-closed:未分类不得静默放行)。"""
    assert A._class_of({}) == "mandatory"
    assert A._class_of({"class": "weird"}) == "mandatory"
    assert A._class_of({"class": "optional"}) == "optional"


# ----------------------------------------------------------------- 状态机整体
def test_stage_machine_advances_m1_to_m5(runs):
    """M1→M5 全程由机械条件自动推进,零人工输入。"""
    rid = "TEST-SM"
    m = A.new_manifest(rid, "sm")
    m["FINDINGS"] = [{"id": "F1", "sev": "P1", "state": "CLOSED", "class": "mandatory",
                      "route": "A", "batch": "B1", "detect": _DETECT,
                      "evidence": [_ptr()]}]
    m["BATCHES"] = {"B1": {"state": "DONE", "scope": ["x"], "criterion": "c",
                           "changed_files": ["x"]}}
    m["GATES"] = {"RELEASE": "PASS"}
    m["EVIDENCE"] = [_ev_record(out="1763 passed")]
    m["C_VOTES"] = _c_votes_mechanical()
    m["C_ATTEST"] = {"C5": "ok"}
    A.save(rid, m)

    for _ in range(4):                       # M1→M2→M3→M4→M5
        assert A._cmd_advance(_ns(rid)) == 0
    _, cur = A.load(rid)
    assert cur["CURRENT_STAGE"] == "M5"
    assert A.decide(cur)["verdict"] == "STOP"
    assert A._cmd_close(_ns(rid)) == 0
    _, done = A.load(rid)
    assert done["STOP_DECISION"] == "AUDIT COMPLETE"


def test_denied_commands_fail_closed(runs):
    """H1 护栏:破坏性命令一律拒绝(默认拒绝)。"""
    for cmd in ("git push", "git commit -m x", "git reset --hard HEAD~1",
                "git rebase -i HEAD~2", "rm -rf /", "git clean -fd"):
        assert A.is_denied_command(cmd) is not None, cmd
    assert A.is_denied_command("git status --porcelain") is None


def test_denied_commands_run_via_cli_are_refused(runs):
    """经 collect 入口的破坏性命令必须被拒(不执行)。"""
    rid = "TEST-GUARD"
    A.save(rid, A.new_manifest(rid, "guard"))
    rc = A._cmd_collect(SimpleNamespace(run_id=rid, kind="t", cmd="git push origin main"))
    assert rc == 2
    _, m = A.load(rid)
    assert not m.get("EVIDENCE")             # 未产生任何证据 ⇒ 确实没执行


# ----------------------------------------------------------------- CLOSE-02-F1
def _m5_unconverged_manifest() -> dict:
    """M5 且 Stop 未全真、但**无未闭合 mandatory target** ⇒ decide() 返回 CONTINUE。

    这是 CLOSE-02-F1 的触发态:M5 是终态,`_next_stage("M5") is None`。
    """
    m = _converged_manifest(optional_open=False)
    m["UNQUALIFIED_OVERCLAIMS"] = 1          # S5 = False ⇒ Stop 谓词不全真
    return m


def test_close02_f1_terminal_stage_rejects_advance(runs, capsys):
    """M5 + CONTINUE ⇒ 非法推进被拒绝(不写 CURRENT_STAGE、不标 DONE、rc != 0)。"""
    rid = "TEST-C02F1"
    m = _m5_unconverged_manifest()
    A.save(rid, m)
    assert A.decide(m)["verdict"] == "CONTINUE"       # 前置:确为 CONTINUE 态

    rc = A._cmd_advance(_ns(rid))
    out = capsys.readouterr().out
    _, cur = A.load(rid)

    assert rc != 0                                    # ① 返回非 0
    assert cur["CURRENT_STAGE"] == "M5"               # ② stage 未被修改
    assert cur["STAGE_STATUS"]["M5"] != "DONE"        # ③ 未被误标 DONE
    assert cur["CURRENT_STAGE"] is not None           # ④ 未产生 None(可恢复契约)
    assert "close" in out                             # ⑤ 明确提示改用 close


def test_close02_f1_normal_m4_to_m5_still_advances(runs):
    """反向:M4 → M5 正常推进**不受**终态守卫影响。"""
    rid = "TEST-C02F1R"
    m = _converged_manifest(optional_open=False)
    m["CURRENT_STAGE"] = "M4"
    A.save(rid, m)
    assert A.decide(m)["verdict"] == "CONTINUE"

    assert A._cmd_advance(_ns(rid)) == 0
    _, cur = A.load(rid)
    assert cur["CURRENT_STAGE"] == "M5"
    assert cur["STAGE_STATUS"]["M4"] == "DONE"


# ----------------------------------------------------------------- VAL-B1
# A. Interpreter integrity
def test_valb1_declared_detectors_do_not_use_bare_python():
    """声明的检测器不得依赖 PATH 中的 bare `python`(可能解析到 Store 存根)。"""
    for d in A.REUSABLE_DETECTORS:
        assert not d["cmd"].startswith("python "), f"bare python: {d}"
        assert not d["cmd"].startswith("python3 "), f"bare python3: {d}"


def test_valb1_py_token_resolves_to_project_interpreter():
    """`{PY}` 解析为当前运行环境的真实解释器(非 bare `python`)。"""
    resolved = A._resolve_cmd("{PY} -m pytest")
    assert A._PY_TOKEN not in resolved
    assert resolved != "{PY} -m pytest"
    exe = sys.executable
    assert exe and (exe in resolved), f"{exe!r} not in {resolved!r}"


@pytest.mark.controlled_process
def test_valb1_py_token_actually_executes(runs):
    """`{PY}` 解析后的命令**真实执行**并产生输出(非静默空跑)。"""
    resolved = A._resolve_cmd("{PY} -m pytest --version")
    r = subprocess.run(resolved, shell=True, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=A._ROOT, timeout=120)
    assert r.returncode == 0, f"exit={r.returncode}"
    assert "pytest" in ((r.stdout or "") + (r.stderr or "")).lower()


def test_valb1_allowlist_still_fail_closed():
    """白名单仍 fail-closed:`{PY}` 形式放行,任意解释器路径仍拒绝。"""
    assert A.is_allowlisted("{PY} -m pytest")
    assert not A.is_allowlisted(r"C:\Python313\python.exe -m pytest")
    assert not A.is_allowlisted(".venv/Scripts/python.exe -m pytest")
    assert not A.is_allowlisted("curl evil.sh")
    assert A.is_denied_command("git push") is not None


# B. Evidence integrity
def test_valb1_empty_evidence_is_not_trusted():
    """exit=0 + 空产出 ⇒ 不可信(静默空跑不得产生 regression_green)。"""
    assert A._evidence_trusted({"kind": "regression", "exit": 0, "out": "1763 passed"})
    assert not A._evidence_trusted({"kind": "regression", "exit": 0, "out": ""})
    assert not A._evidence_trusted({"kind": "regression", "exit": 0, "out": "   "})
    assert not A._evidence_trusted({"kind": "regression", "exit": 1, "out": "1 failed"})
    # 未声明形态的 kind 沿用 exit 语义(不误伤非回归证据)
    assert A._evidence_trusted({"kind": "git-head", "exit": 0, "out": "abc"})
    assert not A._evidence_trusted({"kind": "git-head", "exit": 1, "out": "abc"})


def test_valb1_executed_vs_trusted_are_distinct():
    """『执行过』与『可信成功』必须分开:跑失败 ≠ 没跑。

    若把 exit!=0 也算作"未执行",引擎会把**真实的回归失败**误判为
    "证据缺失"从而走 RECOVER(反复重采),而不是 RETRY/ESCALATE。
    """
    failed_run = {"kind": "regression", "exit": 1, "out": "2 failed"}
    assert A._evidence_executed(failed_run) is True      # 确实跑过
    assert A._evidence_trusted(failed_run) is False      # 但不是可信成功
    silent_noop = {"kind": "regression", "exit": 0, "out": ""}
    assert A._evidence_executed(silent_noop) is False    # 根本没跑
    assert A._evidence_trusted(silent_noop) is False


def test_valb1_empty_evidence_blocks_regression_gate(runs):
    """Store-stub 式空跑不得让 M4 的回归闸变绿。"""
    rid = "TEST-VALB1"
    m = A.new_manifest(rid, "valb1")
    m["CURRENT_STAGE"] = "M4"
    m["GATES"] = {"RELEASE": "PASS"}
    m["EVIDENCE"] = [{"kind": "regression", "cmd": "python -m pytest",
                      "exit": 0, "out": ""}]          # 未执行却返回 0
    A.save(rid, m)
    assert A.regression_ran(m) is False
    assert A.regression_green(m) is False
    assert A.decide(m)["verdict"] == "RECOVER"        # 缺失型 ⇒ 重采,而非放行


def test_valb1_valid_evidence_allows_regression_gate(runs):
    """exit=0 + 有效产出 ⇒ 可信,闸正常放行。"""
    rid = "TEST-VALB1OK"
    m = A.new_manifest(rid, "valb1ok")
    m["CURRENT_STAGE"] = "M4"
    m["GATES"] = {"RELEASE": "PASS"}
    m["EVIDENCE"] = [{"kind": "regression", "cmd": "{PY} -m pytest",
                      "exit": 0, "out": "1763 passed, 2 skipped in 42s"}]
    A.save(rid, m)
    assert A.regression_ran(m) is True
    assert A.regression_green(m) is True
    assert A.decide(m)["verdict"] == "CONTINUE"


def test_valb1_recover_no_effect_does_not_loop(runs, capsys):
    """RECOVER 重采后仍不可信 ⇒ 返回非 0 并登记 blocker(不无限重试)。"""
    rid = "TEST-VALB1RC"
    m = A.new_manifest(rid, "valb1rc")
    m["CURRENT_STAGE"] = "M4"
    m["GATES"] = {"RELEASE": "PASS"}
    m["EVIDENCE"] = []
    A.save(rid, m)
    assert A.decide(m)["verdict"] == "RECOVER"

    def _noop(rid_, mm, kind, cmd):                       # 空跑:不产生可信证据
        mm.setdefault("EVIDENCE", []).append(
            {"kind": kind, "cmd": cmd, "exit": 0, "out": ""})
        return 0

    monkeypatch_target = A._run_and_record
    A._run_and_record = _noop
    try:
        rc = A._cmd_recover(_ns(rid))
    finally:
        A._run_and_record = monkeypatch_target
    assert rc != 0
    _, cur = A.load(rid)
    assert any(b["id"] == "RECOVER-NO-EFFECT" for b in cur["BLOCKERS"])


def test_valb1_state_machine_unchanged(runs):
    """VAL-B1 修复不得改变 M1→M5 状态机与 Decision 语义。"""
    rid = "TEST-VALB1SM"
    m = A.new_manifest(rid, "valb1sm")
    m["FINDINGS"] = [{"id": "F1", "sev": "P1", "state": "CLOSED", "class": "mandatory",
                      "route": "A", "batch": "B1", "detect": _DETECT,
                      "evidence": [_ptr()]}]
    m["BATCHES"] = {"B1": {"state": "DONE", "scope": ["x"], "criterion": "c",
                           "changed_files": ["x"]}}
    m["GATES"] = {"RELEASE": "PASS"}
    m["EVIDENCE"] = [_ev_record(out="1763 passed")]
    m["C_VOTES"] = _c_votes_mechanical()
    m["C_ATTEST"] = {"C5": "ok"}
    A.save(rid, m)

    seen = []
    for _ in range(4):                                    # M1→M2→M3→M4→M5
        rc = A._cmd_advance(_ns(rid))
        seen.append((rc, A.load(rid)[1]["CURRENT_STAGE"]))
    assert seen == [(0, "M2"), (0, "M3"), (0, "M4"), (0, "M5")]
    assert A.decide(A.load(rid)[1])["verdict"] == "STOP"  # STOP 语义未变


# ----------------------------------------------------------------- VAL-B2
# 证据判据不得"非空即通过"。每条拒绝路径与接受路径各一例。
def test_valb2_placeholder_detect_rejected(runs):
    """占位句子不算 detect —— 真实 manifest 里的 'frozen-audit detect for F-01'
    非空,执行时却 exit!=0。"""
    m = _converged_manifest(optional_open=False)
    m["FINDINGS"][0]["detect"] = "frozen-audit detect for M1"
    assert A.all_findings_detected(m) is False
    m["FINDINGS"][0]["detect"] = _DETECT
    assert A.all_findings_detected(m) is True


def test_valb2_detect_must_be_allowlisted_and_not_denied(runs):
    """detect 必须在白名单内且不命中禁止片段(静态策略判定,不执行)。"""
    m = _converged_manifest(optional_open=False)
    f = m["FINDINGS"][0]
    f["detect"] = r"C:\Python313\python.exe -m pytest"       # 非白名单
    assert A.all_findings_detected(m) is False
    f["detect"] = "git push origin main"                     # 命中禁止片段
    assert A.all_findings_detected(m) is False
    f["detect"] = "{PY} -m pytest -q"                        # 白名单
    assert A.all_findings_detected(m) is True


def test_valb2_prose_evidence_rejected(runs):
    """散文型 evidence 不得满足 S4 —— 'closed via B1 (ba0aab8)' 指不到任何记录。"""
    m = _converged_manifest(optional_open=False)
    m["FINDINGS"][0]["evidence"] = "closed via B1 (ba0aab8)"
    assert A.evidence_complete(m) is False
    assert A.stop_predicates(m)["S4"] is False


def test_valb2_evidence_pointer_must_resolve_to_trusted_record(runs):
    """指针必须解析到**可信**记录:exit!=0 或空产出都不算。"""
    m = _converged_manifest(optional_open=False)
    m["EVIDENCE"] = [_ev_record(exit_code=1, out="2 failed")]
    assert A.evidence_complete(m) is False            # 跑失败
    m["EVIDENCE"] = [_ev_record(exit_code=0, out="")]
    assert A.evidence_complete(m) is False            # 静默空跑
    m["EVIDENCE"] = [_ev_record()]
    assert A.evidence_complete(m) is True


def test_valb2_evidence_pointer_must_match_kind_and_cmd(runs):
    """指针必须与实际执行的命令对应 —— 不得张冠李戴。"""
    m = _converged_manifest(optional_open=False)
    f = m["FINDINGS"][0]
    f["evidence"] = [_ptr(cmd="{PY} -m pytest tests/other.py")]
    assert A.evidence_complete(m) is False            # 命令不符
    f["evidence"] = [{"kind": "git-head", "cmd": _EV_CMD}]
    assert A.evidence_complete(m) is False            # kind 不符
    f["evidence"] = [{"cmd": _EV_CMD}]
    assert A.evidence_complete(m) is False            # 缺 kind
    f["evidence"] = ["not-a-dict"]
    assert A.evidence_complete(m) is False            # 非指针
    f["evidence"] = []
    assert A.evidence_complete(m) is False            # 空列表
    f["evidence"] = [_ptr()]
    assert A.evidence_complete(m) is True


def test_valb2_c_votes_bare_pass_rejected(runs):
    """C 门机械项不接受裸 'PASS' —— 手填字符串不得冒充实测依据。"""
    m = _converged_manifest(optional_open=False)
    m["C_VOTES"] = {c: "PASS" for c, (_n, mech, _d) in A.C_CHECKS.items() if mech}
    assert A.c_gate_meets_bar(m) is False
    assert A.stop_predicates(m)["S8"] is False        # M5 阶段门含 c_gate


def test_valb2_c_votes_missing_one_mechanical_fails(runs):
    """任一机械 C 缺证据即整门失败。"""
    m = _converged_manifest(optional_open=False)
    m["C_VOTES"].pop("C2")
    assert A.c_gate_meets_bar(m) is False


def test_valb2_c5_attestation_must_be_nonempty_string(runs):
    """C5 是人工判断项,但同样不接受真值型占位。"""
    m = _converged_manifest(optional_open=False)
    m["C_ATTEST"] = {"C5": ""}
    assert A.c_gate_meets_bar(m) is False
    m["C_ATTEST"] = {"C5": "残余已声明:L-6 无法自动化验证"}
    assert A.c_gate_meets_bar(m) is True


def test_valb2_legacy_without_reason_is_not_an_exemption(runs):
    """无名豁免不认:legacy 必须带 legacy_reason。"""
    m = _converged_manifest(optional_open=False)
    f = m["FINDINGS"][0]
    f["detect"] = "占位句子"
    f["evidence"] = "散文"
    f["legacy"] = True
    assert A.all_findings_detected(m) is False
    assert A.evidence_complete(m) is False


def test_valb2_legacy_with_reason_exempts_and_is_reported(runs, capsys):
    """带理由的 legacy 生效,**且必须出现在输出里**(不静默)。"""
    rid = "TEST-VALB2LG"
    m = _converged_manifest(optional_open=False)
    f = m["FINDINGS"][0]
    f["detect"] = "占位句子"
    f["evidence"] = "散文"
    f["legacy"] = True
    f["legacy_reason"] = "v0.3 期闭合,当时无 detect 可执行要求"
    assert A.all_findings_detected(m) is True
    assert A.evidence_complete(m) is True
    assert [x["id"] for x in A.legacy_exemptions(m)] == ["M1"]

    A.save(rid, m)
    assert A._cmd_status(_ns(rid)) == 0
    out = capsys.readouterr().out
    assert "legacy" in out and "M1" in out, out


def test_valb2_converged_manifest_still_converges(runs):
    """反向:合规形态(白名单 detect + 证据指针)仍能走完全程 STOP —— 不得误伤。"""
    rid = "TEST-VALB2OK"
    A.save(rid, _converged_manifest(optional_open=False))
    assert A.decide(A.load(rid)[1])["verdict"] == "STOP"
    assert A._cmd_close(_ns(rid)) == 0


# ----------------------------------------------------------------- VAL-B3
# detect 白名单必须是 argv 级,不能被"以白名单词开头的散文"绕过。
def test_valb3_result_annotation_prose_rejected():
    """实测里的 9 条假阳性形态:'grep X -> 0' 把观测结果写进了命令字段。"""
    m = _converged_manifest(optional_open=False)
    for prose in ("grep 75 payload -> 0",
                  "grep origin/main=d3597a6 -> 0",
                  "grep 当前RC -> 0",
                  "grep 不在运行路径上 -> 1",
                  "grep M2 Root Cause Correction => 1"):
        assert A.is_allowlisted(prose) is False, prose
        m["FINDINGS"][0]["detect"] = prose
        assert A.all_findings_detected(m) is False, prose


def test_valb3_argv0_is_exact_not_prefix():
    """argv[0] 精确匹配 —— 同前缀的假命令不得放行。"""
    assert A.is_allowlisted("pytest -q")
    assert not A.is_allowlisted("pytest_evil -q")
    assert not A.is_allowlisted("pythonista x.py")
    assert not A.is_allowlisted("gitx status")


def test_valb3_git_subcommand_is_exact():
    """git 子命令精确匹配 —— 前缀写法会放过 `git statuses`。"""
    assert A.is_allowlisted("git status --porcelain")
    assert A.is_allowlisted("git diff --name-only")
    assert not A.is_allowlisted("git statuses")
    assert not A.is_allowlisted("git diffs")
    assert not A.is_allowlisted("git")


def test_valb3_single_command_only():
    """单命令约束:串联 / 管道 / 替换一律拒绝。"""
    for multi in ("git status; rm -rf x", "git status && git log", "grep a | head",
                  "git log `whoami`", "grep $(cat x) y"):
        assert not A.is_allowlisted(multi), multi


def test_valb3_unparseable_quotes_rejected():
    """引号不配对的命令不得放行(不可解析 ⇒ 不可复现)。"""
    assert not A.is_allowlisted('grep "unclosed x')


def test_valb3_detect_must_have_actually_run():
    """语义兜底:CLOSED finding 的 `detect` **本身**必须已执行并留证。

    防的是"结构规则被新散文形态绕过" —— 即使某条内容通过了 argv 判定,只要它
    没有对应的执行记录,闭合仍然失败。
    """
    m = _converged_manifest(optional_open=False)
    m["FINDINGS"][0]["detect"] = "grep -r never-ran pyharness"
    assert A.all_findings_detected(m) is True        # 结构判定放行
    assert A.evidence_complete(m) is False           # 语义兜底拦下
    assert A.stop_predicates(m)["S4"] is False


def test_valb3_detect_ran_requires_trusted_record():
    """与 detect 逐字对应的记录必须**可信**(exit=0 且有预期产出)。"""
    m = _converged_manifest(optional_open=False)
    f = m["FINDINGS"][0]
    assert A._detect_ran(m, f) is True
    m["EVIDENCE"] = [_ev_record(exit_code=1, out="2 failed")]
    assert A._detect_ran(m, f) is False              # 跑失败
    m["EVIDENCE"] = [_ev_record(exit_code=0, out="")]
    assert A._detect_ran(m, f) is False              # 静默空跑


def test_valb3_valid_commands_still_pass():
    """反向:真命令不受影响 —— 收紧不得误伤合规 detect。"""
    for ok in ("{PY} -m pytest", "python scripts/demo_phase0.py",
               "git status --porcelain", "grep -rn foo pyharness/",
               "sha256sum README.md"):
        assert A.is_allowlisted(ok), ok
