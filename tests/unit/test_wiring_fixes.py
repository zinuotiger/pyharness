"""2026-09-12 接线修复回归测试:本轮"审计发现 → 修复"的每条改动都钉一个断言。

覆盖(每条对应一处审计缺口):
1. core/budget.py        —— ctx.budget 门面(BudgetGate)读数与 Scope 同判据
2. core/scope.py         —— note_tighten 落 scope.updated 审计(preset 切换留痕)
3. core/session_query.py —— max_seq 对账读数(F060 索引落后检测前置)
4. core/tool_exec.py     —— exec.pty 注册(danger=high,真 PTY 后端)
5. cli.py                —— workflow / skill 两个新子命令(14→16)
6. engine.py             —— ctx.budget / ctx.session_query 装配面存在
"""
from __future__ import annotations

import inspect

import pytest

from pyharness import cli
from pyharness.core.budget import BudgetGate, BudgetSnapshot
from pyharness.core.llm import UsageCounters
from pyharness.core.scope import BudgetLimits, Scope, ScopePolicy
from pyharness.core.session_query import SessionQueryIndex
from pyharness.core.tools_registry import ToolRegistry
from pyharness.engine import EngineContext, EngineSpine


class SyncFakeSession:
    """同步会话替身:scope._record 的契约是"append 同步返回即直接记录";
    写成 async def 会走 create_task 路径,无运行循环时事件被丢弃(实测踩到)。
    """

    def __init__(self) -> None:
        self.events: list[tuple] = []

    def append(self, type_, payload, *, actor):
        self.events.append((type_, payload, actor))


class FakeSession:
    """最小异步会话替身:只收集 append 调用(actor 必填契约照真 API)。"""

    def __init__(self) -> None:
        self.events: list[tuple] = []

    async def append(self, type_, payload, *, actor):
        self.events.append((type_, payload, actor))

    def events_after(self, seq=0):
        return []

    def stats(self):
        return {"seq": len(self.events)}


# ---------------------------------------------------------------- 1. 预算门面
class TestBudgetGate:
    def _gate(self, counters=None, session=None):
        limits = BudgetLimits(max_in_tokens=1000, max_out_tokens=100,
                              max_cost_yuan=1.0, warn_ratio=0.8)
        scope = Scope(ScopePolicy(), limits, session_id="s-test",
                      counters=counters, session=session)
        return BudgetGate(limits=limits, counters=counters, scope=scope,
                          session_id="s-test")

    def test_empty_counts_are_ok(self):
        gate = self._gate(UsageCounters())
        snap = gate.snapshot("s-test")
        assert isinstance(snap, BudgetSnapshot)
        assert snap.limit_cny == 1.0 and snap.used_cny == 0.0
        assert snap.ratio == 0.0 and snap.warned is False and snap.state == "ok"

    def test_counts_flow_into_snapshot(self):
        counters = UsageCounters()
        counters.task_add({"model": "m", "in_tokens": 10, "out_tokens": 90,
                           "cost_est": 0.9})
        snap = self._gate(counters).snapshot("s-test")
        assert snap.used_out_tokens == 90 and snap.used_cny == pytest.approx(0.9)
        assert snap.ratio == pytest.approx(0.9) and snap.warned is True
        assert snap.state == "warn"                # 90/100 ≥ 80% → warn,未 exhausted

    def test_exhausted_when_over_hard_limit(self):
        counters = UsageCounters()
        counters.task_add({"model": "m", "in_tokens": 1, "out_tokens": 101,
                           "cost_est": 0.1})
        assert self._gate(counters).snapshot().state == "exhausted"

    def test_snapshot_is_readonly_and_stateless(self):
        counters = UsageCounters()
        counters.task_add({"model": "m", "out_tokens": 5, "cost_est": 0.01})
        gate = self._gate(counters)
        before = counters.snapshot()
        gate.snapshot(); gate.snapshot()
        assert counters.snapshot() == before        # 纯只读:多次调用零副作用


# ---------------------------------------------------------------- 2. 预设留痕
class TestPresetAudit:
    def test_note_tighten_appends_scope_updated(self):
        sess = SyncFakeSession()
        scope = Scope(ScopePolicy(), BudgetLimits(), session_id="s-test",
                      session=sess)
        scope.note_tighten({"exec.shell_run", "fs.delete_file"},
                           reason="preset:locked")
        kinds = [e[0] for e in sess.events]
        assert "scope.updated" in kinds
        _, payload, actor = sess.events[0]
        assert payload["op"] == "tighten" and payload["reason"] == "preset:locked"
        assert payload["added"] == ["exec.shell_run", "fs.delete_file"]
        assert actor == "system"

    def test_note_tighten_empty_is_noop(self):
        sess = SyncFakeSession()
        scope = Scope(ScopePolicy(), BudgetLimits(), session_id="s-test",
                      session=sess)
        scope.note_tighten(set(), reason="preset:strict")
        assert sess.events == []


# ---------------------------------------------------------------- 3. 索引读数
class TestSessionQueryMaxSeq:
    def test_zero_when_not_loaded(self):
        assert SessionQueryIndex(":memory:").max_seq("s-x") == 0

    @pytest.mark.asyncio
    async def test_reads_indexed_max_seq(self):
        ix = SessionQueryIndex(":memory:")
        await ix.enter(None)                        # 建表(无总线:ctx=None 分支)
        ix._db.execute(
            "INSERT INTO fts_rows(session_id, seq) VALUES ('s-a', 3), ('s-a', 9)")
        ix._db.commit()
        assert ix.max_seq("s-a") == 9
        assert ix.max_seq("s-other") == 0           # 未索引会话 = 0(不算落后)


# ---------------------------------------------------------------- 4. exec.pty
class TestExecPtyTool:
    def test_pty_registered_with_high_danger(self):
        from pyharness.core import tool_exec
        reg = ToolRegistry()
        names = tool_exec.register(reg)
        assert "exec.pty" in names
        d = reg.lookup("exec.pty")
        assert d.danger == "high"                   # 远端/本机执行面 → 一律审批

    def test_pty_schema_shape(self):
        from pyharness.core import tool_exec
        reg = ToolRegistry()
        tool_exec.register(reg)
        schema = reg.lookup("exec.pty").schema
        assert schema["required"] == ["command"]
        assert schema["additionalProperties"] is False
        assert set(schema["properties"]) == {"command", "input", "timeout_s"}

    def test_pty_backend_available_or_clear_error(self):
        from pyharness.core import pty as pty_mod
        if pty_mod.pty_supported():
            assert pty_mod.MAX_OUTPUT_CHARS > 0
        else:                                       # 缺后端必须给 TLB-807 而非静默
            with pytest.raises(Exception) as ei:
                pty_mod.open_pty("x", cwd=".")
            assert "TLB-807" in str(ei.value)


# ---------------------------------------------------------------- 5. 新子命令
class TestNewSubcommands:
    def test_cmd_table_has_16_with_new_two(self):
        assert len(cli.CMD_TABLE) == 16
        assert cli.CMD_TABLE["workflow"].kind == "once"
        assert cli.CMD_TABLE["skill"].offline is True

    @pytest.mark.parametrize("cmd", ["workflow", "skill"])
    def test_parse_new_subcommands(self, cmd):
        r = cli.parse_args([cmd, "步骤一", "步骤二"])
        assert r.cmd == cmd and r.positional == ["步骤一", "步骤二"]

    def test_workflow_is_a_coroutine_handler(self):
        assert inspect.iscoroutinefunction(cli._cmd_workflow)

    def test_skill_cmd_lists_bundled_skills(self, capsys):
        rc = cli._skill_cmd([], {"config": None})
        out = capsys.readouterr().out
        assert rc == 0
        assert "技能库" in out and "interview-pitch" in out

    def test_skill_cmd_unknown_name_raises_skl901(self):
        from pyharness.errors import PyHError
        with pytest.raises(PyHError) as ei:
            cli._skill_cmd(["不存在的技能名"], {"config": None})
        assert ei.value.code == "SKL-901"


# ---------------------------------------------------------------- 6. 装配面
class TestEngineWiring:
    def test_spine_dataclass_has_new_fields(self):
        fields = set(EngineSpine.__dataclass_fields__)
        assert {"budget", "fts", "_fts_entered"} <= fields

    def test_engine_context_has_budget(self):
        assert "budget" in EngineContext.__dataclass_fields__

    @pytest.mark.asyncio
    async def test_session_caps_wire_fts_tool_and_budget(self, tmp_path):
        """端到端:建 spine → 建 agent → 激活会话能力 → FTS 工具真进注册表。

        这是本轮修复的核心断言(此前 SessionQueryIndex 有实现有单测、装配层零
        调用点,LLM 一调 session.fts_query 即抛"索引未挂载")。
        """
        from pyharness import engine as eng
        from pyharness.bus import EventBus
        from pyharness.config import load_settings
        from pyharness.core.session import open_session
        from pyharness.persistence import open_store

        cfg = load_settings()
        cfg.storage.db_path = str(tmp_path / "ix.db")     # 隔离:不碰用户索引库
        sid = "s-wiringtest"
        store = open_store(sid, dir=tmp_path)
        log_ = await open_session(sid, store)
        bus = EventBus()
        spine = eng.build_runner_components(
            cfg, log_=log_, bus=bus, sessions_dir=tmp_path, store=store,
            attach_persistence=False)
        names_before = [d.name for d in spine.tool_registry.iter_definitions()]
        assert "session.fts_query" not in names_before     # 懒挂:装配期不注册

        ag = eng.create_agent(sid, spine, cfg)
        await eng._activate_session_caps(ag, spine)

        names = [d.name for d in spine.tool_registry.iter_definitions()]
        assert "session.fts_query" in names                # 激活后真进注册表
        assert ag.ctx.session_query is spine.fts            # 挂载面(repair 读数用)
        assert getattr(log_, "fts", None) is spine.fts      # ctx.session.fts 定位器
        assert ag.ctx.budget is spine.budget               # 预算门面同对象
        assert spine.budget.snapshot(sid).state == "ok"
        await spine.close()
