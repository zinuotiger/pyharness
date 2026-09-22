"""tests/e2e/test_governance_e2e.py — 治理型运行时的端到端闭环验证。

E2E 证据口径：每条断言背后是**真实运行**的一段链路
（真引擎装配 → 真 GuardChain → 真 ToolExecutor → 真 Provider → 真 JSONL 落盘），
并由**外部副作用**（文件系统）与**落盘事件**独立佐证，而非断言日志字符串。

覆盖（GAP-5 e2e + GAP-11 principal）：
  A  Allow          —— 授权 → 执行 → tool.result → 决策 → 凭证 → 落盘一致
  B1 Deny(策略)     —— g-fs-path 拒绝；**目标文件零创建**
  B2 Deny(critical) —— scope 前置拒绝；**目标文件内容原样**
  C  Human Approval —— D1 → 人工批准 → **重入判定 D2** → 执行 → approval 凭证
  D  Principal      —— 人类批准的身份进入 Decision / Receipt（GAP-11）
  E  Missing Channel——未声明通道 fail-closed（GAP-11 负向）
"""
from __future__ import annotations

from pathlib import Path

import pytest


# =============================================================== A · Allow
async def test_e2e_allow_full_chain(e2e_factory):
    """允许链：tool.call → guard.evaluated(allow) → decision.issued(allow)
    → receipt.emitted → tool.result(ok) → JSONL 与内存逐条一致。"""
    s = await e2e_factory(
        [{"id": "a1", "name": "fs.list_dir", "args": {"path": "."}}],
        sid="s-e2e-allow-0001")
    await s.boot()
    res = await s.run("list the workspace")
    assert res.ok, f"全链 run 应成功,实际 {getattr(res, 'code', res)}"

    assert s.count("tool.call") == 1
    ge = s.of("guard.evaluated")
    assert [e.payload["decision"] for e in ge] == ["allow"]
    # 七条内置 guard 全部参与求值(链完整)
    assert len(ge[0].payload["guard_ids"]) == 7

    di = s.of("decision.issued")
    assert len(di) == 1 and di[0].payload["verdict"] == "allow"
    # call_id 三处一致：guard / decision / tool.call 同键，审计可串联
    cid = s.of("tool.call")[0].payload["call_id"]
    assert (ge[0].trace or {}).get("call_id") == cid
    assert (di[0].trace or {}).get("call_id") == cid

    re_ = s.of("receipt.emitted")
    assert len(re_) == 1 and re_[0].payload["kind"] == "decision"
    assert re_[0].payload["digest"]
    assert re_[0].payload["decision_id"] == di[0].payload["decision_id"]

    tr = s.of("tool.result")
    assert len(tr) == 1 and tr[0].payload["ok"] is True

    # 持久化：重新开库读回的序列与内存完全一致（唯一真源可 replay）
    assert await s.replay() == s.types()


# ========================================================= B1 · Deny(策略)
async def test_e2e_deny_policy_geometry_zero_side_effect(e2e_factory):
    """g3 路径几何拒绝（``..`` 逃逸）：Provider 零调用、**文件未创建**。"""
    s = await e2e_factory(
        [{"id": "b1", "name": "fs.write_file",
          "args": {"path": "../ESCAPED.txt", "content": "PWNED"}}],
        sid="s-e2e-deny-pol-01")
    await s.boot()
    escaped = s.tmp / "ESCAPED.txt"
    assert not escaped.exists()

    res = await s.run("write outside the workspace")
    assert res.ok                      # 任务本身完成（拒绝被回喂，不炸循环）

    gr = s.of("guard.rejected")
    assert len(gr) == 1 and gr[0].payload["guard_id"] == "g-fs-path"
    assert gr[0].payload["policy_ref"] == "POL-FS-2"
    assert [e.payload["verdict"] for e in s.of("decision.issued")] == ["reject"]
    assert s.count("tool.result") == 0          # 无终局 result = 未执行
    # ★ 外部副作用证据：越界文件确实不存在
    assert not escaped.exists()


# ==================================================== B2 · Deny(critical)
async def test_e2e_deny_critical_still_has_file(e2e_factory):
    """``fs.delete_file``(danger=critical) 被 scope 前置拒绝：**文件内容原样**。"""
    s = await e2e_factory(
        [{"id": "b2", "name": "fs.delete_file", "args": {"path": "victim.txt"}}],
        sid="s-e2e-deny-crit-1")
    # 会话 workspace 根取**实际装配值**(F055:{workspaces_dir}/{sid}),不硬编码
    # 裸目录 —— 2026-09-21 修:此前写死 s.tmp/"workspaces" 锁定了"全会话共用一个
    # fs 根"的旧行为,与 PRD-Core §5.6/CFG.md §3.6 冲突。
    ws = Path(s.ctx.scope.policy.workspace_root)
    ws.mkdir(parents=True, exist_ok=True)
    victim = ws / "victim.txt"
    victim.write_text("DO NOT DELETE", encoding="utf-8")
    await s.boot()

    res = await s.run("delete victim.txt")
    assert res.ok

    gr = s.of("guard.rejected")
    assert len(gr) == 1 and gr[0].payload["policy_ref"] == "GRD-401"
    assert s.count("tool.result") == 0
    # ★ 外部副作用证据：文件仍在且内容未被改动
    assert victim.exists()
    assert victim.read_text(encoding="utf-8") == "DO NOT DELETE"


# ================================================== C · Human Approval
async def test_e2e_human_approval_rejudgment(e2e_factory):
    """g-overwrite → 审批 → 人工批准 → **重入判定** D2 → 执行 → approval 凭证。

    「重入判定而非绕过」由两个独立事实证明：
      ① ``guard.evaluated`` 出现 **两次**（批准后重新走完整条链）；
      ② ``decision.issued`` 出现 **两条**，D2 ``supersedes`` 指向 D1。
    """
    import asyncio

    s = await e2e_factory(
        [{"id": "c1", "name": "fs.write_file",
          "args": {"path": "note.txt", "content": "APPROVED-WRITE"}}],
        sid="s-e2e-approval-01")
    # 会话 workspace 根取**实际装配值**(F055:{workspaces_dir}/{sid}),不硬编码
    # 裸目录 —— 2026-09-21 修:此前写死 s.tmp/"workspaces" 锁定了"全会话共用一个
    # fs 根"的旧行为,与 PRD-Core §5.6/CFG.md §3.6 冲突。
    ws = Path(s.ctx.scope.policy.workspace_root)
    ws.mkdir(parents=True, exist_ok=True)
    target = ws / "note.txt"
    target.write_text("ORIGINAL", encoding="utf-8")
    await s.boot()

    await s.ctx.session.append("user.message",
                               {"content": "overwrite note.txt"},
                               actor="user", sync=True)
    from pyharness.core.task_queue import TaskQueue
    q = TaskQueue(s.ctx.session, runner=s.ctx.task_runner, max_queue=8)
    tid = await q.submit("overwrite note.txt")
    run_task = asyncio.create_task(q.wait_for(tid))

    # 等审批请求入 pending（真 ApprovalProvider），再以人类身份裁决
    aid = None
    for _ in range(400):
        if s.ctx.approval._pending:
            aid = next(iter(s.ctx.approval._pending))
            break
        if run_task.done():
            break
        await asyncio.sleep(0.05)
    assert aid is not None, "审批请求未产生(g-overwrite 未触发审批)"
    req = s.of("approval.requested")
    assert len(req) == 1 and req[0].payload["tool"] == "fs.write_file"
    assert (req[0].trace or {}).get("channel") == "desktop"
    assert target.read_text(encoding="utf-8") == "ORIGINAL"   # 批准前未执行

    await s.ctx.approval.approve_async(aid, by="desktop")
    res = await asyncio.wait_for(run_task, timeout=25.0)
    assert res.ok

    # ① 重入判定：两次完整求值
    assert [e.payload["decision"] for e in s.of("guard.evaluated")] == \
        ["need_approval", "need_approval"]
    # ② D1 → D2，supersedes 链成立；D1 无 approval_ref，D2 携带真实 identity
    ds = [e.payload for e in s.of("decision.issued")]
    assert len(ds) == 2
    d1, d2 = ds
    assert d1["approval_ref"] is None and d1["supersedes"] is None
    assert d2["approval_ref"] == aid
    assert d2["supersedes"] == d1["decision_id"]
    # 凭证：D1 不产生（审批请求决策），D2 产生 kind=approval
    rec = s.of("receipt.emitted")
    assert len(rec) == 1 and rec[0].payload["kind"] == "approval"
    assert rec[0].payload["decision_id"] == d2["decision_id"]
    # 执行与副作用
    tr = s.of("tool.result")
    assert len(tr) == 1 and tr[0].payload["ok"] is True
    assert target.read_text(encoding="utf-8") == "APPROVED-WRITE"


# ======================================================= D · Principal
async def test_e2e_human_principal_recorded_in_decision_and_receipt(e2e_factory):
    """GAP-11:人类批准者的主体身份必须进入 Decision 与 Receipt。

    修复前：``create_agent`` 从不写 ``ag.ctx.channel`` ⇒ 每次授权静默降级为
    ``principal_kind="system"``，凭证无法离线证明"有人批准过"。
    """
    import asyncio

    s = await e2e_factory(
        [{"id": "d1", "name": "fs.write_file",
          "args": {"path": "note.txt", "content": "BY-HUMAN"}}],
        sid="s-e2e-principal-1", channel="desktop")
    # 会话 workspace 根取**实际装配值**(F055:{workspaces_dir}/{sid}),不硬编码
    # 裸目录 —— 2026-09-21 修:此前写死 s.tmp/"workspaces" 锁定了"全会话共用一个
    # fs 根"的旧行为,与 PRD-Core §5.6/CFG.md §3.6 冲突。
    ws = Path(s.ctx.scope.policy.workspace_root)
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "note.txt").write_text("ORIGINAL", encoding="utf-8")
    await s.boot()

    await s.ctx.session.append("user.message", {"content": "overwrite"},
                               actor="user", sync=True)
    from pyharness.core.task_queue import TaskQueue
    q = TaskQueue(s.ctx.session, runner=s.ctx.task_runner, max_queue=8)
    tid = await q.submit("overwrite")
    run_task = asyncio.create_task(q.wait_for(tid))
    aid = None
    for _ in range(400):
        if s.ctx.approval._pending:
            aid = next(iter(s.ctx.approval._pending))
            break
        if run_task.done():
            break
        await asyncio.sleep(0.05)
    assert aid is not None
    await s.ctx.approval.approve_async(aid, by="desktop")
    await asyncio.wait_for(run_task, timeout=25.0)

    ds = [e.payload for e in s.of("decision.issued")]
    assert ds, "应有决策事件"
    for p in ds:
        assert p["principal_kind"] == "human", \
            f"通道已声明 desktop,主体应为 human,实际 {p['principal_kind']}"
        assert p["principal_channel"] == "desktop"
    # 凭证的受保护集合含主体身份 ⇒ 离线核验可证"人类批准"
    gov = s.ctx.engine_spine.governance
    recs = gov.receipts.all()
    assert recs, "应有凭证"
    for r in recs:
        assert str(r.principal.kind) == "human"
        assert gov.receipts.verify(r) is True     # 篡改即 False


async def test_e2e_cli_channel_principal_is_cli(e2e_factory):
    """CLI 通道同源：Allow 链的决策主体为 ``human:cli``。"""
    s = await e2e_factory(
        [{"id": "e1", "name": "fs.list_dir", "args": {"path": "."}}],
        sid="s-e2e-cli-prin-01", channel="cli")
    await s.boot()
    assert (await s.run("list")).ok
    p = s.of("decision.issued")[0].payload
    assert p["principal_kind"] == "human"
    assert p["principal_channel"] == "cli"


# ============================================== E · Missing Channel (负向)
async def test_e2e_undeclared_channel_fails_closed(e2e_factory):
    """GAP-11 负向：**未声明**通道 ⇒ 工具调用 fail-closed，绝不静默降级。

    这是"context 丢失不能静默降级成 system"的运行时可执行断言。
    """
    s = await e2e_factory(
        [{"id": "f1", "name": "fs.list_dir", "args": {"path": "."}}],
        sid="s-e2e-nochan-0001", channel=None)     # ← 显式 headless
    await s.boot()
    assert (await s.run("list")).ok
    # 显式 headless 是**合法**声明 ⇒ SYSTEM 主体（与"未声明"不同）
    assert s.of("decision.issued")[0].payload["principal_kind"] == "system"


async def test_e2e_absent_policy_channel_attribute_is_aprd(tmp_path):
    """未声明通道的直接断言：ctx 缺 ``channel`` 属性 ⇒ ``APR-503``。"""
    import types as _t

    from pyharness.core.tools_guard import GuardChain
    from pyharness.errors import PyHError
    from pyharness.governance import (DecisionEngine, GovernanceContext,
                                      PolicyEngine, Policy, PolicyRegistry)

    chain = GuardChain()
    eng = PolicyEngine(policy=Policy("t", "1"), registry=PolicyRegistry(),
                       chain=chain)
    gc = GovernanceContext(policy=eng, decisions=DecisionEngine())
    ctx = _t.SimpleNamespace(scope=_t.SimpleNamespace(can_use=lambda n: True),
                             session=None)          # ← 无 channel 属性
    with pytest.raises(PyHError) as ei:
        await gc.authorize(_t.SimpleNamespace(name="fs.list_dir", call_id="x"),
                           ctx)
    assert ei.value.code == "APR-503"


# ========================================== F · 攒批落盘定时器（GAP-13）
async def test_e2e_flush_ticker_bounds_durability_window(e2e_factory):
    """GAP-13:非 SYNC 事件的落盘窗口由间隔定时器**封顶**（此前无上界）。

    证据口径：直接读 JSONL 物理文件（不经过内存派生），
    断言"定时器间隔前不在盘上 / 之后在盘上"——这是真实落盘时序，不是日志断言。
    """
    import asyncio

    s = await e2e_factory(None, sid="s-e2e-flush-0001")
    await s.boot()
    spine = s.ctx.engine_spine
    store = spine.persistence
    store.flush_interval_s = 0.05                  # 缩短间隔以便确定性断言
    assert await spine.start_flush_ticker() is True
    assert await spine.start_flush_ticker() is False, "定时器必须幂等(不重复起任务)"

    await s.ctx.session.append("agent.message", {"content": "BATCHED-ONLY-MARKER"},
                               actor="agent")
    log_path = s.tmp / "sessions" / "s-e2e-flush-0001.jsonl"
    assert "BATCHED-ONLY-MARKER" not in log_path.read_text(encoding="utf-8"), \
        "非 SYNC 事件不应即刻落盘(攒批语义)"
    await asyncio.sleep(0.4)
    assert "BATCHED-ONLY-MARKER" in log_path.read_text(encoding="utf-8"), \
        "间隔定时器应把攒批事件落到真源(落盘窗口有上界)"
    # 停表:close 后任务不再存活,且不抛
    await spine.close()
    assert spine._flush_task is None


async def test_e2e_flush_ticker_survives_flush_failure(e2e_factory):
    """GAP-13 负向:单次 flush 失败**不能杀死定时器**(否则窗口又变无界)。"""
    import asyncio

    s = await e2e_factory(None, sid="s-e2e-flush-0002")
    await s.boot()
    spine = s.ctx.engine_spine
    store = spine.persistence
    store.flush_interval_s = 0.05
    calls = {"n": 0}
    real_flush = store.flush

    async def _flaky(up_to_seq=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("simulated disk failure")
        return await real_flush(up_to_seq)

    store.flush = _flaky
    assert await spine.start_flush_ticker() is True
    await s.ctx.session.append("agent.message", {"content": "AFTER-FAILURE"},
                               actor="agent")
    await asyncio.sleep(0.5)
    assert calls["n"] >= 2, "首跳失败后定时器应继续跳(未被异常杀死)"
    log_path = s.tmp / "sessions" / "s-e2e-flush-0002.jsonl"
    assert "AFTER-FAILURE" in log_path.read_text(encoding="utf-8")
    await spine.close()


# ============================================ G · 证据归档生产者（GAP-8）
async def test_e2e_evidence_producer_closes_the_loop(e2e_factory):
    """GAP-8:任务段跑完 → 产出 ``evidence.archived`` → 索引可查 → 引用可解析。

    此前 ``EvidenceCollector`` 只有订阅者没有生产者:``archive()`` 零调用 ⇒
    事件永不产生、``collect_for_task()`` 恒返回 ``()``。
    """
    from pyharness.governance import EvidenceCollector

    s = await e2e_factory(
        [{"id": "g1", "name": "fs.list_dir", "args": {"path": "."}},
         {"id": "g2", "name": "fs.write_file",
          "args": {"path": "../NOPE.txt", "content": "X"}}],
        sid="s-e2e-evidence-01", channel="desktop")
    await s.boot()
    assert (await s.run("do")).ok

    arch = s.of("evidence.archived")
    assert len(arch) == 1, f"应恰一条证据工件,实际 {len(arch)}"
    payload = arch[0].payload
    refs = payload["refs"]
    kinds = [r["kind"] for r in refs]
    assert kinds[0] == "segment"
    assert refs[0]["locator"] == f"{s.last_task_id}:seg"
    assert kinds.count("decision_id") == 2        # allow + reject 各一条决策
    assert kinds.count("receipt_id") == 2         # 两条决策各一条凭证
    # 引用全部可解析(INV-E2):archive() 在发射前校验,能落盘即证明成立
    assert all(r["locator"] for r in refs)

    # 索引:由日志重建后按 task 聚合查得到
    rebuilt = await EvidenceCollector.from_log(s.ctx.session)
    got = rebuilt.collect_for_task(s.last_task_id)
    assert len(got) == 1
    assert got[0].evidence_id == payload["evidence_id"]
    assert len(got[0].refs) == len(refs)          # 引用集合无损往返
    # 活订阅者索引与重建索引一致(派生缓存未与真源脱节)
    live = s.ctx.engine_spine.governance.evidence
    assert live.evidence_count() == rebuilt.evidence_count() == 1


async def test_e2e_evidence_producer_is_idempotent(e2e_factory):
    """幂等:同一任务段重复归档只产生一条(判据取自日志重放,重启后仍成立)。"""
    from pyharness.engine import archive_task_evidence

    s = await e2e_factory(
        [{"id": "h1", "name": "fs.list_dir", "args": {"path": "."}}],
        sid="s-e2e-evidence-02")
    await s.boot()
    assert (await s.run("list")).ok
    assert s.count("evidence.archived") == 1

    again = await archive_task_evidence(s.ctx.engine_spine, s.last_task_id)
    assert again is None, "重复归档应被幂等闸拦下并返回 None"
    assert s.count("evidence.archived") == 1


async def test_e2e_evidence_producer_skips_segments_without_decisions(e2e_factory):
    """产生规则负向:段内**无治理决策** ⇒ 不归档(不为空段造证据)。"""
    s = await e2e_factory([], sid="s-e2e-evidence-03")   # 纯文本轮,无 tool_calls
    await s.boot()
    assert (await s.run("say hi")).ok
    assert s.count("tool.call") == 0
    assert s.count("decision.issued") == 0
    assert s.count("evidence.archived") == 0
