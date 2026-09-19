"""engine 装配层测试 — 契约:engine.py 装配语义(desktop/CLI 共用脊柱)。

历史背景:engine.py 0% 覆盖——装配层没有直接测试,而"装配即真链"的接线点
全在这里(F010 sysprompt / F032 counters / F039 spill / F058 compactor /
F027 streaming / F013 fallback chain)。本文件锁定这些装配不变量,防回归。

不变量(装配即正确):
- spine 组件完备:counters/sysprompt/compactor 必在岗;streaming 读 cfg.loop
- 工具面:fs.* 4 + storage.spill 读工具注册,strict 下 schemas_for 可见
- spill 激活:首次 _agent_ctx_of 建 agent 时激活 provider(幂等),storage.spill
  挂载后 executor 关4 可用
- assemble_real_engine:ctx 形状含 counters,storage 与 spine.storage 同对象
"""
from __future__ import annotations

import asyncio
import pathlib
from types import SimpleNamespace

import pytest

from pyharness.config import Settings, load_settings
from pyharness.core.session import SessionLog
from pyharness.core.task_queue import Task
from pyharness.core.tools_guard import (FORCED_GUARDS, ToolCall, _BUILTIN_IDS,
                                        _RULE_POLICY_REFS)
from pyharness.engine import (_activate_storage_caps, _agent_ctx_of,
                              _owned_task_message, assemble_real_engine,
                              attach_engine_to_ctx, build_spine)


def _cfg(tmp_path) -> Settings:
    """临时存储根 Settings(绝不触碰真实 ~/.pyharness)。"""
    cfg = load_settings()
    cfg.storage.root = str(tmp_path)
    cfg.storage.sessions_dir = str(tmp_path / "sessions")
    cfg.storage.workspaces_dir = str(tmp_path / "workspaces")
    cfg.storage.spill_dir = str(tmp_path / "spill")
    cfg.storage.db_path = str(tmp_path / "pyharness.db")
    return cfg


def _sessions_dir(tmp_path):
    """open_store 要求 Path 对象(mkdir 面);与 cfg.storage.sessions_dir 同路径。"""
    import pathlib
    return pathlib.Path(tmp_path) / "sessions"


def _names_of(schemas) -> set:
    out = set()
    for s in schemas or []:
        fn = (s or {}).get("function") or {}
        if fn.get("name"):
            out.add(fn["name"])
    return out


@pytest.mark.asyncio
async def test_build_spine_components_present(tmp_path):
    """脊柱装配完备性:counters/sysprompt/compactor/streaming + spill 读工具。"""
    cfg = _cfg(tmp_path)
    spine = await build_spine(cfg, sid="s-eng-test-000001",
                              sessions_dir=_sessions_dir(tmp_path))
    try:
        # F032 计数与预算闸同源:scope._counters is spine.counters
        assert spine.counters is not None
        assert spine.scope._counters is spine.counters
        # F010 / F058 装配器在岗(agent-loop 消费面)
        assert spine.sysprompt is not None
        assert spine.compactor is not None
        # F027 默认关(非流式);cfg 开则 spine 透传
        assert spine.streaming is False
        # 工具面:fs.* 可见子集 + storage.spill 读工具(engine 注册);
        # fs.delete_file = critical 危险级 → strict 下不可见(guard 设计,非 bug)
        vis = _names_of(spine.tools.schemas_for(spine.scope))
        for need in ("fs.read_file", "fs.write_file", "fs.list_dir",
                     "storage.spill"):
            assert need in vis, f"strict 可见工具缺 {need};实际={sorted(vis)}"
        assert "fs.delete_file" not in vis, \
            "critical 级工具不应在 strict 下可见(can_use 单调拒绝)"
        # 初始 storage.spill 未激活(懒)
        assert spine.storage.spill is None
        # #39 预设默认 strict:goal/todo/storage.kv 等自管理/存储域可见
        for need in ("goal", "todo", "storage.kv", "schedule"):
            assert need in vis, f"工具 {need} 应可见;实际={sorted(vis)}"
        assert spine.goals is not None and spine.todos is not None
    finally:
        _close_spine(spine)


@pytest.mark.asyncio
async def test_streaming_flag_pass_through(tmp_path):
    """cfg.loop.streaming=True → spine.streaming=True(桌面 SSE 装配路径)。"""
    cfg = _cfg(tmp_path)
    cfg.loop.streaming = True
    spine = await build_spine(cfg, sid="s-eng-stream-000001",
                              sessions_dir=_sessions_dir(tmp_path))
    try:
        assert spine.streaming is True
    finally:
        _close_spine(spine)


@pytest.mark.asyncio
async def test_agent_ctx_of_activates_spill_once(tmp_path):
    """spill 激活:首建 agent → provider 挂载(storage.spill 立即可见);幂等。"""
    cfg = _cfg(tmp_path)
    spine = await build_spine(cfg, sid="s-eng-spill-000001",
                              sessions_dir=_sessions_dir(tmp_path))
    try:
        env = _env_of(spine)
        ctx1 = await _agent_ctx_of(spine, env)          # 首次:建 agent + 激活
        prov = spine.storage.spill
        assert prov is not None, "spill provider 应在首建 agent 时激活"
        # 激活一次(幂等):第二次不再新建
        ctx2 = await _agent_ctx_of(spine, env)
        assert ctx2 is ctx1
        assert spine.storage.spill is prov
        # 会话级 agent 1:1 占位
        assert spine.active_agents.get(spine.session.sid) is not None
        # executor 消费面可用:put 落盘 + ref(需要 ctx 已具 config/bus/session)
        meta = await prov.put("x" * 3000, kind="output")
        assert isinstance(meta, dict)
        assert meta.get("ref"), f"put 应返回含 ref 的元数据:{meta}"
    finally:
        _close_spine(spine)


@pytest.mark.asyncio
async def test_assemble_real_engine_ctx_shape(tmp_path):
    """一键装配 ctx 形状:counters 在岗;storage 与 spine 同对象;脊柱齐备。"""
    cfg = _cfg(tmp_path)
    ctx = await assemble_real_engine(
        cfg, sid="s-eng-asmb-000001",
        sessions_dir=_sessions_dir(tmp_path))
    try:
        spine = ctx.engine_spine
        assert ctx.counters is spine.counters
        assert ctx.storage is spine.storage               # spill 激活后同对象可见
        for attr in ("session", "bus", "llm", "scope", "loop", "tools",
                     "registry", "guard", "approval", "task_runner"):
            assert getattr(ctx, attr, None) is not None, f"ctx.{attr} 缺失"
        assert ctx.agent is None                            # 懒建(首次 submit)
        # 降级链装配:fallback_models 非空默认 → llm._chain 在岗
        assert getattr(ctx.llm, "_chain", None) is not None
    finally:
        _close_spine(spine)


def _env_of(spine) -> object:
    """runner 同形 env:携带 session_id 的鸭子信封。"""
    class _Env:
        session_id = spine.session.sid
        seq = 1
        type = "user.message"
        payload = {"content": "probe"}
    return _Env()


def _close_spine(spine) -> None:
    """尽力收尾:释放 scope 占位(不等待任务排水)。"""
    try:
        if getattr(spine, "scope", None) is not None:
            spine.scope.release()
    except Exception:                                    # noqa: BLE001 收尾尽力
        pass


# =====================================================================
# _owned_task_message:严格归属窗口(P1-1 修复)
# =====================================================================
async def test_owned_task_message_assigns_each_tasks_own_message():
    """每个任务只拿到自己 (上一个 enqueued, 本 enqueued] 窗口内的 user.message;
    纯意图任务(无配套消息)返回 None 供 intent 补写。"""
    log_ = SessionLog("s-owntest01")
    await log_.append("session.created", {"title": "", "model": "m"},
                      actor="system")          # seq 1
    await log_.append("user.message", {"content": "前台问题"},
                      actor="user")            # seq 2
    await _enq(log_, "t-1")                    # seq 3
    await log_.append("user.message", {"content": "调度的意图B"},
                      actor="user", origin="schedule:jb")  # seq 4
    await _enq(log_, "t-2")                    # seq 5
    await _enq(log_, "t-3")                    # seq 6 (纯意图:无配套消息)
    await log_.append("user.message", {"content": "调度的意图C"},
                      actor="user", origin="schedule:jc")  # seq 7
    await _enq(log_, "t-4")                    # seq 8

    # t-1 窗口 (0, 3] → 前台问题
    assert _owned_task_message(log_, Task("t-1", "x", enqueued_seq=3)).payload["content"] \
        == "前台问题"
    # t-2 窗口 (3, 5] → 意图B(修复前会拿 seq2 的"前台问题")
    assert _owned_task_message(log_, Task("t-2", "x", enqueued_seq=5)).payload["content"] \
        == "调度的意图B"
    # t-3 窗口 (5, 6] 无消息 → None(plan 步等纯意图)
    assert _owned_task_message(log_, Task("t-3", "plan一下", enqueued_seq=6)) is None
    # t-4 窗口 (5, 8] → 意图C
    assert _owned_task_message(log_, Task("t-4", "x", enqueued_seq=8)).payload["content"] \
        == "调度的意图C"


async def _enq(log_, task_id: str) -> None:
    await log_.append("task.enqueued", {"task_id": task_id, "pos": 1},
                      actor="system")


# ============================================== S1-01 GuardChain 经工厂接线
async def test_guard_from_config_injects_schema_validator(tmp_path):
    """S1-01:engine 经 GuardChain.from_config 装配 → g1 g-schema 的 validator
    真实注入。

    修复前 engine 直构 GuardChain(session,bus) → validator 恒 None →
    g_schema_check 直接 return allow → "内层复查防旁路"(INV-04)在生产链路上
    永不生效。证据:畸形参数被 g1 拒、合规参数放行。
    """
    from pyharness.core.tools_guard import ToolCall

    cfg = _cfg(tmp_path)
    spine = await build_spine(cfg, sid="s-eng-gv-000001",
                              sessions_dir=_sessions_dir(tmp_path))
    try:
        # build_spine 不写 session.created(首事件由调用方负责)→ 引导后才能审计
        await spine.session.append("session.created",
                                   {"title": "", "model": "deepseek-chat"},
                                   actor="system")
        # 畸形:fs.read_file 缺必填 path → g1 复查失败(TLB-803) → 终局拒
        bad = ToolCall(name="fs.read_file", raw_args={}, call_id="c-bad")
        assert str(await spine.guard.evaluate(bad, spine.scope)) == "reject", \
            "g1 未生效:validator 缺失时 g-schema 恒 allow(INV-04 失效)"

        # 合规:path 在 workspace 内 → 全链 allow(证明拒来自 schema 而非误拦)
        good = ToolCall(name="fs.read_file", raw_args={"path": "a.txt"},
                        call_id="c-good")
        assert str(await spine.guard.evaluate(good, spine.scope)) == "allow"
    finally:
        _close_spine(spine)


async def test_guard_from_config_applies_cfg_disabled(tmp_path):
    """S1-01:cfg.security.guards.disabled 在**生产装配路径**生效。

    修复前 from_config 零生产调用点 → 禁用声明永不生效(运维以为关了,实际仍
    在链上)。证据:同一越界调用,默认链被 g-fs-path 拒;禁用后放行。
    """
    from pyharness.core.tools_guard import ToolCall

    def _escape() -> object:
        # danger=none 且不被 g4(读凭据)/g6(exec)/g7(覆写)/g5(外发)管辖
        return ToolCall(name="fs.list_dir", raw_args={"path": "../../outside"},
                        call_id="c-esc")

    cfg_on = _cfg(tmp_path)
    spine_on = await build_spine(cfg_on, sid="s-eng-gd-on-000001",
                                 sessions_dir=_sessions_dir(tmp_path))
    try:
        await spine_on.session.append("session.created",
                                      {"title": "", "model": "deepseek-chat"},
                                      actor="system")
        assert "g-fs-path" in spine_on.guard.enabled_guard_ids()
        assert str(await spine_on.guard.evaluate(_escape(), spine_on.scope)) \
            == "reject", "默认链应由 g-fs-path 拒越界路径"
    finally:
        _close_spine(spine_on)

    cfg_off = _cfg(tmp_path)
    cfg_off.security.guards.disabled = ["g-fs-path"]
    spine_off = await build_spine(cfg_off, sid="s-eng-gd-off-000001",
                                  sessions_dir=_sessions_dir(tmp_path))
    try:
        await spine_off.session.append("session.created",
                                       {"title": "", "model": "deepseek-chat"},
                                       actor="system")
        assert "g-fs-path" not in spine_off.guard.enabled_guard_ids(), \
            "cfg 声明的禁用项未生效(修复前 from_config 未被调用)"
        assert str(await spine_off.guard.evaluate(_escape(), spine_off.scope)) \
            == "allow", "禁用 g-fs-path 后该越界调用不应再被 g3 拒"
    finally:
        _close_spine(spine_off)


# =====================================================================
# S2-3:治理层装配(只进装配链,不进运行链)
# =====================================================================
async def _gov_spine(tmp_path, sid, *, preset=None, disabled=None):
    """建带治理装配的 spine;preset="standard" → basic 沙箱(放开域约束)。"""
    cfg = _cfg(tmp_path)
    if preset is not None:
        cfg.security.policy.preset = preset
    if disabled is not None:
        cfg.security.guards.disabled = list(disabled)
    spine = await build_spine(cfg, sid=sid, sessions_dir=_sessions_dir(tmp_path))
    await spine.session.append("session.created",
                               {"title": "", "model": "deepseek-chat"},
                               actor="system")
    return spine


def _tc(spine, name, **raw):
    """生产同形 ToolCall:附 defn(关1a 会挂契约;g2 danger 面读它)。"""
    return ToolCall(name=name, raw_args=dict(raw), call_id="c1",
                    defn=spine.tool_registry.lookup(name))


# ---------------------------------------------------------------- T-B
async def test_s23_tb_single_chain_and_single_governance(tmp_path):
    """T-B:**只一条 GuardChain** + 治理层单实例 + create_agent 路径挂载同一对象。

    ``spine.guard is spine.governance.policy.chain`` 封死"两条链"(S2-3.1 的 R-A):
    guard 必须取自 policy.chain(chain_factory 产出),不得另行直调 from_config。
    """
    spine = await _gov_spine(tmp_path, "s-s23-tb-000001")
    try:
        assert spine.governance is not None
        assert spine.guard is spine.governance.policy.chain       # 只一条链
        ctx1 = await _agent_ctx_of(spine, _env_of(spine))
        assert ctx1.guard is spine.guard
        assert ctx1.governance is spine.governance                # 同一实例
        ctx2 = await _agent_ctx_of(spine, _env_of(spine))         # 幂等
        assert ctx2 is ctx1 and ctx2.governance is ctx1.governance
    finally:
        _close_spine(spine)


# ---------------------------------------------------------------- T-C
async def test_s23_tc_attach_path_mounts_same_governance(tmp_path):
    """T-C:attach_engine_to_ctx 路径(CLI/ACP 门面)挂载**同一**治理实例。"""
    cfg = _cfg(tmp_path)
    cfg.llm.api_key = "env:PH_S23_TEST_KEY"      # 过 CRED-701 引用检查(不解析值)
    ctx = SimpleNamespace()
    log_ = SessionLog("s-s23-tc-000001")
    spine = await attach_engine_to_ctx(
        ctx, cfg, log_=log_, sessions_dir=_sessions_dir(tmp_path), preload=False)
    try:
        assert ctx.guard is spine.guard
        assert ctx.governance is spine.governance                 # 同一对象
        assert spine.guard is spine.governance.policy.chain
    finally:
        _close_spine(spine)


# ---------------------------------------------------------------- T-A
async def test_s23_ta_rules_match_chain(tmp_path):
    """T-A:治理层持有的规则与链实际执行的规则**逐条同构**(序/开关/forced/refs)。

    注:不可断言 check/match 对象同一 —— describe_rules 自建 guard 实例
    (tools_guard 不改,X-5(a) 已否决),故只做结构同一性。
    """
    spine = await _gov_spine(tmp_path, "s-s23-ta-000001")
    try:
        pol = spine.governance.policy.policy                        # Policy 对象
        assert [r.rule_id for r in pol.rules] == [g.id for g in spine.guard.chain]
        assert [r.rule_id for r in pol.enabled_rules()] \
            == spine.guard.enabled_guard_ids()
        for r in pol.rules:
            assert r.forced == (r.rule_id in FORCED_GUARDS)
            assert r.policy_refs == _RULE_POLICY_REFS[r.rule_id]
        assert pol.disabled == frozenset()                          # 无禁用声明
        assert pol.fingerprint == spine.governance.policy.fingerprint() != ""
    finally:
        _close_spine(spine)


async def test_s23_ta_cfg_disabled_reaches_policy(tmp_path):
    """T-A(续):cfg 禁用面同时进入**策略 disabled 面**与**链**(两面一致)。"""
    spine = await _gov_spine(tmp_path, "s-s23-ta2-000001",
                             disabled=["g-fs-path"])
    try:
        assert "g-fs-path" in spine.governance.policy.policy.disabled
        assert "g-fs-path" not in spine.guard.enabled_guard_ids()
    finally:
        _close_spine(spine)


# ---------------------------------------------------------------- T-C
async def test_s23_tc_policy_rules_carry_injected_params(tmp_path):
    """T-C:治理层**持有的规则**确实带着注入参数(与链同源),封 S2-3.1 的 R-B。

    对象同一不可断言(``describe_rules`` 自建 guard 实例);故用**行为**验证:
    直接调用**策略规则自己的** ``check``——若注入参数没到达描述面,
    ``validator=None`` 会让 g-schema 恒 allow(INV-04 空转),本例必红。
    """
    spine = await _gov_spine(tmp_path, "s-s23-tc2-000001")
    try:
        rules = {r.rule_id: r for r in spine.governance.policy.policy.rules}
        g_schema = rules["g-schema"]
        # 畸形参数(缺必填 path)→ 策略规则应拒(TLB-803),证明 validator 已注入
        d, ref = await g_schema.check(_tc(spine, "fs.read_file"), spine.scope)
        assert (d, ref) == ("reject", "TLB-803")
        # 合规参数 → 该规则不反对
        assert await g_schema.check(
            _tc(spine, "fs.read_file", path="a.txt"), spine.scope) == ("allow", None)
        # 与链上同名规则的行为一致(同一判定语义)
        assert await spine.guard.evaluate(
            _tc(spine, "fs.read_file"), spine.scope) == "reject"
    finally:
        _close_spine(spine)


# ---------------------------------------------------------------- T-D
async def test_s23_td_guard_behavior_smoke(tmp_path):
    """T-D:guard 行为 smoke —— S2-3 前后判定一致(断言**冻结期望**)。

    standard 预设 → basic 沙箱,使 fs/exec 域可见,从而覆盖 g3/g4/g5/g7 与
    g2 的 approval 路径;strict 档另测 scope 前置终局拒。
    """
    spine = await _gov_spine(tmp_path, "s-s23-td-000001", preset="standard")
    try:
        ws = pathlib.Path(spine.scope.policy.workspace_root)
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "exists.txt").write_text("x", encoding="utf-8")

        async def verdict(name, **raw):
            d = await spine.guard.evaluate(_tc(spine, name, **raw), spine.scope)
            return str(d)

        # g3 路径几何:.. 逃逸越界 → reject
        assert await verdict("fs.list_dir", path="../../outside") == "reject"
        # g4 凭据读:workspace 内凭据形态名 → reject
        assert await verdict("fs.read_file", path="credentials.yaml") == "reject"
        # g7 覆写:目标已存在 → approval(新建则不触发)
        assert await verdict("fs.write_file", path="exists.txt",
                             content="y") == "approval"
        assert await verdict("fs.write_file", path="new.txt",
                             content="z") == "allow"
        # 合规读 → allow
        assert await verdict("fs.read_file", path="exists.txt") == "allow"
        # g6 在 basic 放行,但 g2 见 danger=high → approval(不可审批例外见 strict)
        assert await verdict("exec.shell_run", command="echo hi") == "approval"
        # g5 外发:allowlist 空 → reject
        assert await verdict("web.fetch", url="http://x/") == "reject"
    finally:
        _close_spine(spine)


async def test_s23_td_strict_scope_precheck(tmp_path):
    """T-D(续):strict 默认档下 critical 与域外工具在 scope 前置即终局拒。"""
    spine = await _gov_spine(tmp_path, "s-s23-td2-000001")
    try:
        for name, raw in (("fs.delete_file", {"path": "a"}),
                          ("exec.shell_run", {"command": "echo hi"}),
                          ("web.fetch", {"url": "http://x/"})):
            d = await spine.guard.evaluate(_tc(spine, name, **raw), spine.scope)
            assert str(d) == "reject", f"{name} 在 strict 下应终局拒"
    finally:
        _close_spine(spine)


# ---------------------------------------------------------------- F-1 守卫
def test_s23_f1_rule_policy_refs_covers_builtin():
    """F-1(S2-1 Exit Review):声明表必须覆盖**全部**内置 guard。

    describe_rules 用 ``_RULE_POLICY_REFS.get(g.id, ())`` —— 新增内置 guard 漏登记
    会**静默给出空 refs**;本断言把该缺口封死。
    """
    assert set(_BUILTIN_IDS) <= set(_RULE_POLICY_REFS)


# =====================================================================
# S2-4:强同步真源收敛(关闭 ADR-019 P-3)
# =====================================================================
async def test_s24_engine_adapter_derives_sync_from_vocab():
    """S2-4:engine 落盘适配器对**每个词表类型**的 ``sync`` 取值 == `type in SYNC_TYPES`。

    封 ADR-019 P-3:此前 ``_record_to`` 内有一份 11 名硬编码副本,与词表真源
    各成第二真源(新增强同步事件漏改即**静默漂移**——``policy.updated`` 已发生过)。
    """
    from pyharness.engine import _record_to
    from pyharness.events.vocab import EVENT_TYPES, SYNC_TYPES

    class _Rec:
        def __init__(self) -> None:
            self.calls: list = []

        async def append(self, env: Any, sync: bool = False) -> None:
            self.calls.append((env.type, sync))

    rec = _Rec()
    record = _record_to(rec)
    payload = SimpleNamespace(model_dump_json=lambda: "{}")   # 非 dict → 不跳过
    for t in sorted(EVENT_TYPES):
        payload.type = t
        await record(t, payload)
    got = dict(rec.calls)
    assert got == {t: (t in SYNC_TYPES) for t in EVENT_TYPES}, \
        "engine 适配器的 sync 取值必须逐类型等于 SYNC_TYPES 成员资格"
    assert got["policy.updated"] is True        # 治理层策略事件:强同步(Q3)
    assert got["scope.updated"] is False        # 运行时 scope 收紧:非强同步(Q1)
