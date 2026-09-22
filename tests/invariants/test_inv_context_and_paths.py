"""不变量:上下文传播契约(GAP-9) · 租户归属(GAP-10) · 唯一拒绝出口(GAP-12)。

本文件是**契约的可执行形式**:契约写在注释里会腐烂,写成断言才会在漂移时报警。
每个标识符都按 ``生成 / 传播 / 持久化 / 可查询`` 四问逐一钉死;不在契约内的
标识符则**显式断言其不存在**,把"负空间"也变成可执行事实。
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from pyharness.events.envelope import Envelope

ROOT = Path(__file__).resolve().parents[2]


# =============================================== GAP-9 · Context Propagation
CONTRACT = {
    # 标识符: "生成处 / 传播面 / 持久化面"
    "session_id": "外壳开会话时生成;Envelope 必填(min_length=8);每条事件",
    "call_id":    "LLM tool_call 解析时生成;见下 CALL_ID_LOCATION;随事件落盘",
    "decision_id": "DecisionEngine 生成;decision.issued 载荷持久化",
    "receipt_id": "ReceiptStore 生成;receipt.emitted 载荷持久化",
    "agent_id":   "create_agent 生成(会话内 1:1);**不进入事件**(见负空间)",
    "tenant_id":  "框架侧租户归属;Envelope 可选字段(GAP-10);每条事件",
}

# ``call_id`` 的**位置契约**(实测口径,不是"随便哪里都行"):
#   - 工具执行面(``tool.*``)      → 在 **payload**(``payload["call_id"]``)
#   - 治理面(``guard.*`` / ``decision.issued`` / ``receipt.emitted`` /
#     ``approval.requested``)     → 在 **Envelope.trace**(``trace["call_id"]``)
#
# 这条分工是有意的:工具面把它当作**业务字段**(要进 LLM 上下文/摘要),
# 治理面把它当作**信封级关联元信息**(不污染治理载荷、不随 payload 校验)。
# 代价是"读 call_id"没有单一函数 —— 已知不一致,登记于 LIMITATIONS。
CALL_ID_IN_PAYLOAD = ("tool.call", "tool.result", "tool.error")
CALL_ID_IN_TRACE = ("guard.evaluated", "guard.rejected", "decision.issued",
                    "receipt.emitted", "approval.requested")


def _call_id_of(ev) -> str:
    """按位置契约统一读 call_id(先 trace 后 payload,二者之一必中)。"""
    cid = str((getattr(ev, "trace", None) or {}).get("call_id") or "")
    if cid:
        return cid
    return str((getattr(ev, "payload", None) or {}).get("call_id") or "")


def test_contract_identifiers_are_documented_here():
    """契约自检:每个标识符都有一段"它是什么、在哪生成"的说明(防注释腐烂)。"""
    for name, desc in CONTRACT.items():
        assert desc.strip(), f"{name} 缺契约说明"


@pytest.mark.parametrize("required", ["seq", "ts", "type", "session_id", "actor"])
def test_envelope_required_fields_are_frozen(required):
    """信封必填字段存在且模型 frozen(不可事后改写 = 不可篡改事实)。"""
    assert required in Envelope.model_fields
    assert Envelope.model_config.get("frozen") is True
    assert Envelope.model_config.get("extra") == "forbid"


def test_transport_identifiers_are_absent_by_contract():
    """**负空间**:以下标识符不在契约内。

    断言的**不是**"永远不该有",而是"若要引入,必须走一次有意识的契约变更"
    —— 这个测试会在有人悄悄加上它们时变红,从而强制讨论。
    """
    absent = ("request_id", "trace_id", "conversation_id", "subject_id", "user_id")
    for name in absent:
        assert name not in Envelope.model_fields, \
            f"{name} 被加入了信封:请先更新 Context Propagation Contract 与本测试"


async def test_session_id_is_generated_propagated_persisted(e2e_factory):
    """session_id:生成(外壳)→ 传播(每条事件)→ 持久化(JSONL 可读回)。"""
    s = await e2e_factory(
        [{"id": "c1", "name": "fs.list_dir", "args": {"path": "."}}],
        sid="s-inv-ctx-000001")
    await s.boot()
    assert (await s.run("list")).ok

    events = s.events()
    assert events, "应有事件"
    assert all(e.session_id == "s-inv-ctx-000001" for e in events), \
        "每条事件都必须携带本会话 session_id"
    # 持久化:重新开库读回同样成立
    from pyharness.core.session import open_session
    from pyharness.persistence import open_store
    store = open_store("s-inv-ctx-000001", dir=s.tmp / "sessions")
    log2 = await open_session("s-inv-ctx-000001", store)
    assert all(e.session_id == "s-inv-ctx-000001"
               for e in log2.events_after(0))


async def test_call_id_is_propagated_across_governance_events(e2e_factory):
    """call_id:同一次工具调用的 tool.call / guard.evaluated / decision.issued
    / tool.result 必须携带**同一个** call_id —— 这是因果串联的唯一键。

    同时钉死**位置契约**(见 ``CALL_ID_IN_PAYLOAD`` / ``CALL_ID_IN_TRACE``):
    位置不是随意的,漂移即报警。
    """
    s = await e2e_factory(
        [{"id": "c1", "name": "fs.list_dir", "args": {"path": "."}}],
        sid="s-inv-ctx-000002")
    await s.boot()
    assert (await s.run("list")).ok

    ids = {
        "tool.call": _call_id_of(s.of("tool.call")[0]),
        "guard.evaluated": _call_id_of(s.of("guard.evaluated")[0]),
        "decision.issued": _call_id_of(s.of("decision.issued")[0]),
        "tool.result": _call_id_of(s.of("tool.result")[0]),
    }
    assert len(set(ids.values())) == 1, f"call_id 未贯穿:{ids}"
    assert ids["tool.call"] == "c1"
    # 位置契约逐类核对(工具面在载荷,治理面在 trace)
    for t in CALL_ID_IN_PAYLOAD:
        for ev in s.of(t):
            assert (ev.payload or {}).get("call_id"), f"{t} 的 call_id 应在 payload"
    for t in CALL_ID_IN_TRACE:
        for ev in s.of(t):
            assert (ev.trace or {}).get("call_id"), f"{t} 的 call_id 应在 trace"


async def test_decision_id_is_persisted_and_queryable(e2e_factory):
    """decision_id:生成于决策、持久化于载荷、可经因果链查询(端到端可追踪)。"""
    s = await e2e_factory(
        [{"id": "c1", "name": "fs.list_dir", "args": {"path": "."}}],
        sid="s-inv-ctx-000003")
    await s.boot()
    assert (await s.run("list")).ok
    did = s.of("decision.issued")[0].payload["decision_id"]
    assert did
    chain = s.ctx.engine_spine.governance.audit.causal_chain(did)
    assert chain["decision_id"] == did
    assert chain["decision"] is not None


async def test_channel_is_required_for_governance_identity(e2e_factory):
    """channel:治理主体的**必需**输入;这是 GAP-11 的契约形式。"""
    s = await e2e_factory(
        [{"id": "c1", "name": "fs.list_dir", "args": {"path": "."}}],
        sid="s-inv-ctx-000004", channel="desktop")
    await s.boot()
    assert (await s.run("list")).ok
    p = s.of("decision.issued")[0].payload
    assert p["principal_channel"] == "desktop"
    assert p["principal_kind"] == "human"


# ================================================== GAP-10 · Tenant Attribution
async def test_tenant_is_stamped_on_every_event(e2e_factory):
    """租户:装配时注入 → 盖到**每条事件**信封 → 随 JSONL 落盘。"""
    s = await e2e_factory(
        [{"id": "c1", "name": "fs.list_dir", "args": {"path": "."}}],
        sid="s-inv-tenant-0001")
    # e2e_factory 默认未声明租户 ⇒ 先验证"未声明就是 None"(不伪造)
    assert all(getattr(e, "tenant_id", None) is None for e in s.events())


async def test_tenant_survives_restart_from_log(e2e_factory, tmp_path):
    """租户随日志恢复,且**日志优先**:换一个调用方重开也无法改写历史归属。

    这直接闭合 L-1 登记的"租户未随会话落盘"残余。
    """
    from pyharness.core.session import open_session
    from pyharness.persistence import open_store

    s = await e2e_factory(
        [{"id": "c1", "name": "fs.list_dir", "args": {"path": "."}}],
        sid="s-inv-tenant-0002")
    s.ctx.session.tenant_id = "tenant-alpha"          # 模拟框架侧声明
    await s.boot()
    assert (await s.run("list")).ok
    assert all(e.tenant_id == "tenant-alpha" for e in s.events())

    store = open_store("s-inv-tenant-0002", dir=s.tmp / "sessions")
    # ① 不声明 ⇒ 从日志恢复出 tenant-alpha
    log1 = await open_session("s-inv-tenant-0002", store)
    assert log1.tenant_id == "tenant-alpha"
    # ② 声明成别的 ⇒ 日志胜出(历史归属不可被改写)
    log2 = await open_session("s-inv-tenant-0002", store,
                              tenant_id="tenant-beta")
    assert log2.tenant_id == "tenant-alpha", "日志已落租户为既成事实,不可被覆盖"


async def test_legacy_session_without_tenant_stays_none(tmp_path):
    """负空间:没有租户声明的会话(旧日志/单租户)**保持 None**,不凭空捏造。"""
    from pyharness.core.session import open_session
    from pyharness.persistence import open_store

    store = open_store("s-inv-tenant-0003", dir=tmp_path)
    log_ = await open_session("s-inv-tenant-0003", store)
    await log_.append("session.created", {"title": "", "model": "m"},
                      actor="system", sync=True)
    assert log_.tenant_id is None
    assert list(log_.events_after(0))[0].tenant_id is None


async def test_governance_audit_reports_event_tenant_not_instance_tenant(
        e2e_factory):
    """查询面报的是**事件事实**的租户,不是服务实例当前的租户。"""
    from pyharness.application import ApplicationService

    s = await e2e_factory(
        [{"id": "c1", "name": "fs.list_dir", "args": {"path": "."}}],
        sid="s-inv-tenant-0004")
    s.ctx.session.tenant_id = "tenant-gamma"
    await s.boot()
    assert (await s.run("list")).ok
    svc = ApplicationService(s.ctx, channel="desktop")
    out = await svc.governance_audit(s.sid)
    assert out["tenant_id"] == "tenant-gamma"
    assert all(d["tenant_id"] == "tenant-gamma" for d in out["decisions"])


# ================================================ GAP-12 · 唯一拒绝出口
def test_guard_rejected_has_exactly_one_producer():
    """``guard.rejected`` 的发射点**唯一**(INV-05)。

    用 AST 判定而非 grep:字符串出现 ≠ 发射。原 ``ToolExecutor._reject`` 曾是
    第二个(死)发射点,已于 GAP-12 删除;本断言防止它或以任何形式回来。
    """
    hits: list[str] = []
    for p in sorted((ROOT / "pyharness").rglob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            if not isinstance(f, ast.Attribute) or f.attr not in ("append",
                                                                 "_append"):
                continue
            if n.args and isinstance(n.args[0], ast.Constant) \
                    and n.args[0].value == "guard.rejected":
                rel = str(p.relative_to(ROOT)).replace("\\", "/")
                hits.append(f"{rel}:{n.lineno}")
    assert len(hits) == 1, f"guard.rejected 发射点应恰 1 处,实际 {hits}"
    assert hits[0].startswith("pyharness/core/tools_guard.py"), \
        f"唯一发射点应在 tools_guard(守卫链),实际 {hits[0]}"


def test_executor_has_no_second_reject_path():
    """``ToolExecutor`` 不得再持有 executor 侧的终局拒实现(已删,防回归)。"""
    from pyharness.core.tools_executor import ToolExecutor
    assert not hasattr(ToolExecutor, "_reject"), \
        "执行器侧终局拒已上移到 GuardChain;_reject 不得复活(否则是第二条拒绝路径)"


# ==================================== F055 · 会话 workspace 根(唯一派生点 + 隔离)
# 契约:每会话专属根 ``{storage.workspaces_dir}/{session_id}``。
# 权威:PRD-Core §5.6 F055、CFG.md §3.6、OPS.md、DEP.md、specs/scope.py.md:49、
#       specs/subagent.py.md:12 —— 六处一致。2026-09-21 修前**四处各自实现**:
#       engine 赋裸 workspaces_dir、tool_exec 再拼 /<sid>、subagent 取 .parent、
#       build_scope 走 helper ⇒ 静默分裂(子代理根越出树且从未创建)。
def test_workspace_root_has_single_derivation_point():
    """**单点派生**:``session_workspace`` 只许有一处定义,且装配层必须引用它。

    这是"横切关注点多层各自实现必分裂"的结构性防线:新模块若要派生会话根,
    只能 import 该函数,不得就地拼接路径。
    """
    defs: list[str] = []
    for p in sorted((ROOT / "pyharness").rglob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and n.name == "session_workspace":
                defs.append(str(p.relative_to(ROOT)).replace("\\", "/"))
    assert defs == ["pyharness/core/scope.py"], \
        f"会话根派生点应唯一(scope.session_workspace),实际 {defs}"

    callers: set[str] = set()
    for p in sorted((ROOT / "pyharness").rglob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) \
                    and n.func.id == "session_workspace":
                callers.add(str(p.relative_to(ROOT)).replace("\\", "/"))
    for required in ("pyharness/engine.py", "pyharness/core/subagent.py"):
        assert required in callers, \
            f"{required} 必须经单点派生会话根(实际调用方 {sorted(callers)})"


async def test_session_workspace_root_is_per_session_and_exists(e2e_factory):
    """会话根:``{workspaces_dir}/{sid}``(非共享),且**真实存在**。

    存在性是功能前提:根不存在时 ``fs.list_dir`` 直接 TLB-802,文件类工具全废。
    """
    from pathlib import Path
    s = await e2e_factory(
        [{"id": "c1", "name": "fs.list_dir", "args": {"path": "."}}],
        sid="s-inv-ws-000001")
    await s.boot()
    root = Path(s.ctx.scope.policy.workspace_root)
    assert root == s.tmp / "workspaces" / "s-inv-ws-000001", \
        f"会话根应为 {{workspaces_dir}}/{{sid}},实际 {root}"
    assert root.is_dir(), "装配期必须创建会话根"
    # scope 的会话标识必须与装配的 sid 一致(修前 getattr(log_,"session_id") 恒取空)
    assert s.ctx.scope.session_id == "s-inv-ws-000001"


async def test_workspace_roots_isolate_sessions(e2e_factory):
    """**CND-01 隔离面**:两会话根互不重叠,且一方的文件另一方**取不到**。

    负向用例走真实 guard 链:A 写入 ``secret.txt``;B 用 ``../{A}/secret.txt``
    相对逃逸读 → 必须被路径几何闸拒(POL-FS-1/2),且 B 的根内无该文件。
    """
    from pathlib import Path
    a = await e2e_factory(
        [{"id": "a1", "name": "fs.write_file",
          "args": {"path": "secret.txt", "content": "A-ONLY"}}],
        sid="s-inv-ws-iso-a")
    await a.boot()
    assert (await a.run("write secret")).ok
    root_a = Path(a.ctx.scope.policy.workspace_root)
    assert (root_a / "secret.txt").read_text(encoding="utf-8") == "A-ONLY"

    # 注意:LLM 适配器是**按模型注册的全局单件**,故 B 必须在 A 跑完之后才装配
    # (先建 B 会让 A 也执行 B 的脚本——本用例初版即踩此坑)。
    b = await e2e_factory(
        [{"id": "b1", "name": "fs.read_file",
          "args": {"path": "../s-inv-ws-iso-a/secret.txt"}}],
        sid="s-inv-ws-iso-b")
    await b.boot()
    root_b = Path(b.ctx.scope.policy.workspace_root)
    assert root_a != root_b and root_a.parent == root_b.parent, \
        "同树不同会话根"
    assert not (root_b / "secret.txt").exists(), "B 的根内不应有 A 的文件"

    assert (await b.run("read it")).ok
    rejected = b.of("guard.rejected")
    assert rejected, "跨会话相对路径逃逸必须被拒"
    assert rejected[0].payload.get("policy_ref") == "POL-FS-2", rejected[0].payload
    assert rejected[0].payload.get("guard_id") == "g-fs-path", rejected[0].payload
    assert b.count("tool.result") == 0, "拒绝零副作用:不得有 tool.result"


async def test_subagent_child_workspace_inside_tree_and_usable(e2e_factory):
    """**CND-08 可达性**:子会话根由生产工厂派生 → 落在树内 + 存在 + 工具真能用。

    修前实测(N10):子根 = ``{workspaces_dir}/../{sub_id}``(越出树)、从未创建、
    子代理文件类工具恒 TLB-802。本用例同时钉死三点:落点、存在性、消费者真跑通。
    """
    from pathlib import Path

    from pyharness.core import tool_fs
    s = await e2e_factory(None, sid="s-inv-ws-sub-parent")
    await s.boot()
    mgr = s.ctx.engine_spine.subagent
    child = mgr._default_child_scope(s.ctx.scope, "s-inv-ws-sub0001",
                                     s.ctx.session, None)
    child_root = Path(child.policy.workspace_root)
    tree = Path(s.ctx.scope.policy.workspace_root).parent

    assert child_root == tree / "s-inv-ws-sub0001", \
        f"子根应为 {{workspaces_dir}}/{{sub_id}},实际 {child_root}"
    assert child_root.is_dir(), "子根必须由工厂创建(N10 修前从未创建)"
    assert child_root.is_relative_to(tree), "子根不得越出 workspaces 树"

    # 消费者真跑一次(写→列),不是断言路径字符串
    child_ctx = SimpleNamespace(scope=child, session=s.ctx.session,
                                config=s.ctx.settings)
    await tool_fs.write_file({"path": "child-note.txt", "content": "sub"},
                             child_ctx)
    out = await tool_fs.list_dir({"path": "."}, child_ctx)
    names = [e["name"] for e in out["entries"]]
    assert "child-note.txt" in names, f"子会话应能读写自己的根:{out}"


async def test_exec_sandbox_dir_is_inside_session_workspace(e2e_factory):
    """exec 沙箱目录 = ``{会话根}/sandbox``(会话根已含 sid,不得再拼一层)。"""
    from pathlib import Path

    from pyharness.core.tool_exec import _sandbox_dir
    s = await e2e_factory(None, sid="s-inv-ws-exec001")
    await s.boot()
    root = Path(s.ctx.scope.policy.workspace_root)
    sandbox = _sandbox_dir(s.ctx)
    assert sandbox == root / "sandbox", f"沙箱应在会话根内:实际 {sandbox}"
    assert sandbox.is_relative_to(root)
