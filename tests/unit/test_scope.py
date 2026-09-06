"""tests/unit/test_scope.py — scope 模块单测(F014/F021/F032/F054/F055/F059)。

覆盖:会话注册隔离(每会话恰一 scope/重复 build 拒)、最小权限编译与越权拒绝
(CFG-601:空 deny 无防线/域名通配过宽)、can_use 前置查权(deny/strict 域/
critical,单调)、tighten 只紧不松(无 relax/un-tighten API)、预算状态机
(ok→warn→exhausted,budget.paused 首达落事件)、check_budget 超限抛
BudgetExhausted、快照不可变与 fork 遮蔽(F059 COW)、release 解绑后查询拒、
与 llm_fallback.BudgetGuard 同数据源协作。

GWT 对应:specs/scope.py.md 关联测试 GWT-S6-01/02/03/04 + DIS §6.4 状态机。
"""
import asyncio
from types import SimpleNamespace

import pytest

from pyharness.config import Settings
from pyharness.core import scope as scope_mod
from pyharness.core.llm_fallback import BudgetGuard, TaskUsage
from pyharness.core.scope import (
    ALLOWLIST_DOMAINS,
    BudgetExhausted,
    BudgetLimits,
    Scope,
    ScopePolicy,
    ScopeSnapshot,
    WORKSPACE_DOMAINS,
    active_scope,
    build_scope,
    from_snapshot,
)
from pyharness.errors import ConfigError, PyHError

SID = "test-sess-001"


# ===================================================================== 替身
class FakeSession:
    """同步事件落点替身(SessionLog.append 异步真实面由 test_async_* 覆盖)。"""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def append(self, type_, payload, *, actor, **kw):  # 同步:立即记录
        self.events.append({"type": type_, "payload": dict(payload),
                            "actor": actor})

    def of(self, type_) -> list[dict]:
        return [e for e in self.events if e["type"] == type_]


class AsyncSession:
    """真实 SessionLog 同型异步落点(验证 fire-and-forget 投递)。"""

    def __init__(self) -> None:
        self.events: list[dict] = []

    async def append(self, type_, payload, *, actor, **kw):
        self.events.append({"type": type_, "payload": dict(payload),
                            "actor": actor})
        return SimpleNamespace(seq=len(self.events))

    def of(self, type_) -> list[dict]:
        return [e for e in self.events if e["type"] == type_]


class FakeBus:
    """budget.warn 尽力出口替身(同步 emit)。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit(self, type_, payload):
        self.events.append((type_, dict(payload)))

    def of(self, type_) -> list[tuple[str, dict]]:
        return [e for e in self.events if e[0] == type_]


class FakeCounters:
    """任务级用量聚合替身(llm_fallback.UsageCounters 协议:task_total)。"""

    def __init__(self, usage: TaskUsage = None) -> None:
        self.usage = usage or TaskUsage()

    def task_total(self) -> TaskUsage:
        return self.usage


def make_cfg(**overrides) -> Settings:
    """最小 Settings(缺省回落 L1 默认;pydantic 只校验传入键)。"""
    return Settings.model_validate(overrides)


def bind_scope(cfg, sid: str = SID, *, usage: TaskUsage = None,
               session=None, bus=None) -> Scope:
    """build_scope + 注入替身(session/counters/bus 一次到位)。"""
    sess = session if session is not None else FakeSession()
    cnt = FakeCounters(usage) if usage is not None else FakeCounters()
    return build_scope(cfg, sid, session=sess, counters=cnt, bus=bus)


@pytest.fixture(autouse=True)
def _clean_registry():
    """每测后清空会话作用域表(注册隔离;防跨测串扰)。"""
    yield
    scope_mod._scopes.clear()


# ===================================================================== 创建与注册隔离
def test_build_scope_default_minimal_permission():
    """GWT-S6-01 前置:默认配置编译最小权限策略并注册绑定。"""
    cfg = make_cfg()
    s = bind_scope(cfg)
    assert isinstance(s, Scope)
    # 策略编译:默认 strict / role 兜底 / 无 extra deny / workspace 根按会话派生
    assert s.policy.sandbox_level == "strict"
    assert s.policy.role == "user"
    assert s.policy.deny_tools == set()
    assert s.policy.workspace_root.endswith(SID)
    # 内置防线:fs.delete_file=critical(与 tool_fs"注册即拒"同源)
    assert s.can_use("fs.delete_file") is False
    # 窗口配置化:loop.max_context_tokens / compact.trigger_ratio(F058 同源)
    assert s.window_tokens == cfg.loop.max_context_tokens
    assert s.window_ratio == cfg.loop.compact.trigger_ratio
    # 注册表一对一 + 只读查询可见
    assert active_scope(SID) is s
    assert scope_mod._scopes[SID] is s


def test_build_scope_duplicate_same_session_rejected():
    """注册隔离:同会话二次 build → BUSY 拒,首 scope 不受影响。"""
    s = bind_scope(make_cfg())
    with pytest.raises(PyHError) as ei:
        build_scope(make_cfg(), SID)
    assert ei.value.code == "BUSY"
    assert active_scope(SID) is s                  # 原绑定未被动


def test_from_snapshot_duplicate_child_rejected():
    """子会话已存在 scope → BUSY(与 build 同注册隔离)。"""
    s = bind_scope(make_cfg())
    from_snapshot(s.snapshot(), "child-1")
    with pytest.raises(PyHError) as ei:
        from_snapshot(s.snapshot(), "child-1")
    assert ei.value.code == "BUSY"


def test_build_scope_cfg601_wildcard_domain():
    """越权:域名 allowlist 通配过宽 → CFG-601 列字段,拒绝启动(注册为空)。"""
    cfg = make_cfg(security={"network": {"allowed_domains": ["*.com"]}})
    with pytest.raises(ConfigError) as ei:
        build_scope(cfg, "cfg601-1")
    assert ei.value.code == "CFG-601"
    assert "allowed_domains" in " ".join(map(str, ei.value.ctx.get("fields", [])))
    assert "cfg601-1" not in scope_mod._scopes     # 拒启动:零注册


def test_validate_minimal_empty_no_defense_cfg601():
    """越权:空 deny 且无 critical 禁项 = scope 无防线 → CFG-601(偏离 2 口径)。"""
    bare = ScopePolicy(deny_tools=set(), danger_marks=[])
    with pytest.raises(ConfigError) as ei:
        scope_mod._validate_minimal(bare)
    assert ei.value.code == "CFG-601"
    # 有任一防线(deny 或 critical 禁项)即通过
    scope_mod._validate_minimal(
        ScopePolicy(deny_tools={"exec.run"}, danger_marks=[]))
    scope_mod._validate_minimal(
        ScopePolicy(deny_tools=set(),
                    danger_marks=[{"pattern": "fs.delete_file",
                                   "level": "critical"}]))


def test_build_scope_wiring_injected():
    """注入式装配(偏离 3):counters/session/bus 透传;未接线时预算按零读数。"""
    cfg = make_cfg(budget={"task": {"max_out_tokens": 100}})
    s = bind_scope(cfg)
    assert s.budget_state() == "ok"                # 计数器 None → 零读数(降级)
    cnt = FakeCounters(TaskUsage(out_tokens=90))
    s2 = build_scope(cfg, "wired-1", session=FakeSession(),
                     counters=cnt, bus=FakeBus())
    assert s2.budget_state() == "warn"             # 注入后读到真实用量

# ===================================================================== can_use 前置查权
def test_can_use_default_strict_domains():
    """strict 默认域(偏离 7):fs./workspace. 放行;web./net. 需 allowlist;
    exec. 等其它域不可见(DIS §6.6.2 仅 workspace 文件 + allowlist 网络)。"""
    s = bind_scope(make_cfg())
    assert s.can_use("fs.read_file") is True       # workspace 域(路径由 g3 约束)
    assert s.can_use("workspace.list_dir") is True
    assert s.can_use("web.fetch") is False         # allowlist 空 → 不可见
    assert s.can_use("net.curl") is False
    assert s.can_use("exec.run") is False          # strict 下默认不可见


def test_can_use_strict_allowlist_network():
    """strict + allowed_domains 非空:web./net. 可见(URL 级仍由 g5 复核)。"""
    cfg = make_cfg(security={"network": {"allowed_domains":
                                         ["api.example.com"]}})
    s = bind_scope(cfg)
    assert s.policy.allowed_domains == {"api.example.com"}
    assert s.can_use("web.fetch") is True
    assert s.can_use("net.curl") is True
    assert s.can_use("exec.run") is False          # 非 allowlist 域仍不可见


def test_can_use_basic_sandbox_relaxes_strict_domain_only():
    """basic 沙箱:strict 域闸解除;deny 与 critical 仍是单调硬拦。"""
    cfg = make_cfg(security={"sandbox": {"level": "basic"},
                             "policy": {"deny_tools_extra": ["exec.run"]}})
    s = bind_scope(cfg)
    assert s.policy.sandbox_level == "basic"
    assert s.can_use("exec.other") is True         # 域闸解除(exec.* 仅 high)
    assert s.can_use("exec.run") is False          # 显式 deny 仍拦
    assert s.can_use("fs.delete_file") is False    # critical 仍拦(不可审批)


def test_can_use_critical_extra_not_approvable():
    """danger extra 上调 critical → 全沙箱级别终局不可用(不可审批,§6.3.1)。"""
    p = ScopePolicy(
        sandbox_level="off",
        allowed_domains={"example.com"},
        danger_marks=[{"pattern": "web.fetch", "level": "critical"},
                      {"pattern": "exec.*", "level": "high"}])
    s = Scope(p, BudgetLimits(), "crit-1", counters=FakeCounters(),
              session=FakeSession())
    assert s._danger_mark("web.fetch") == "critical"
    assert s.can_use("web.fetch") is False         # critical 精确规则前置命中
    assert s.can_use("exec.run") is True           # high 放行(转审批,不在此拦)


def test_can_use_deny_and_critical_overlap():
    """deny 表命中优先于域/分级判定(can_use 第一个检查点)。"""
    cfg = make_cfg(security={"policy": {"deny_tools_extra": ["fs.read_file"]}})
    s = bind_scope(cfg)
    assert "fs.read_file" in s.policy.deny_tools
    assert s.can_use("fs.read_file") is False      # deny 直接终局
    assert s.can_use("fs.list_dir") is True        # 同域其它工具不受牵连


# ===================================================================== 单调收紧
def test_tighten_monotonic_no_relax_api():
    """GWT-S6-01:tighten 只追加;无 relax/un_tighten API(结构上不可能放宽)。"""
    s = bind_scope(make_cfg())
    s.tighten({"b"}, reason="sandbox strict")
    assert "b" in s.policy.deny_tools
    # 尝试"去掉 a":没有对应方法 → AttributeError(spec 断言)
    assert not hasattr(s, "relax")
    assert not hasattr(s, "un_tighten")
    with pytest.raises(AttributeError):
        s.relax({"b"})                              # noqa: B018 期望 AttributeError
    # 只增不减:已加的不消失
    s.tighten({"a"}, reason="guard rejected")
    assert s.policy.deny_tools == {"a", "b"}


def test_tighten_idempotent_no_event():
    """无新增 deny → 幂等返回,不产生 scope.updated 事件(防刷日志)。"""
    sess = FakeSession()
    s = bind_scope(make_cfg(), session=sess)
    s.tighten({"a"}, reason="r1")
    assert len(sess.of("scope.updated")) == 1
    s.tighten({"a"}, reason="r2")                  # 已含,幂等
    assert len(sess.of("scope.updated")) == 1
    s.tighten(set(), reason="empty")               # 空集同样幂等
    assert len(sess.of("scope.updated")) == 1


def test_tighten_event_payload():
    """收紧留痕:scope.updated{op/added(reason 排序)/reason},actor=system。"""
    sess = FakeSession()
    s = bind_scope(make_cfg(), session=sess)
    s.tighten({"fs.write_file", "exec.run"}, reason="guard GRD-401")
    ev = sess.of("scope.updated")[0]
    assert ev["payload"]["op"] == "tighten"
    assert ev["payload"]["added"] == ["exec.run", "fs.write_file"]
    assert ev["payload"]["reason"] == "guard GRD-401"
    assert ev["actor"] == "system"

# ===================================================================== 预算状态机
def test_budget_state_ok_warn_exhausted_transitions():
    """GWT-S6-02:90/100 → warn;+20 → exhausted 且 budget.paused 事件落盘;
    重复查询 exhausted 不再刷事件(首达才落)。"""
    sess = FakeSession()
    bus = FakeBus()
    usage = TaskUsage(out_tokens=0)
    cfg = make_cfg(budget={"task": {"max_out_tokens": 100, "max_in_tokens": 500,
                                    "max_cost_yuan": 1.0},
                           "warn_ratio": 0.8})
    s = bind_scope(cfg, usage=usage, session=sess, bus=bus)
    assert s.limits.max_out_tokens == 100
    usage.out_tokens = 90
    assert s.budget_state() == "warn"                # ≥80% 提醒
    assert bus.of("budget.warn") == [("budget.warn", {"used_out": 90})]
    usage.out_tokens = 110
    assert s.budget_state() == "exhausted"           # 硬闸超限
    paused = sess.of("budget.paused")
    assert len(paused) == 1 and paused[0]["payload"]["state"] == "exhausted"
    assert paused[0]["payload"]["used"]["out_tokens"] == 110
    # 重复查询:状态保持 exhausted,不再重复落事件(防刷)
    assert s.budget_state() == "exhausted"
    assert len(sess.of("budget.paused")) == 1


def test_budget_state_warn_emitted_once():
    """warn 只提醒一次(防刷);回到 ok 后再超仍可再提醒(去重水位非真源)。"""
    bus = FakeBus()
    usage = TaskUsage(out_tokens=0)
    cfg = make_cfg(budget={"task": {"max_out_tokens": 100}})
    s = bind_scope(cfg, usage=usage, session=FakeSession(), bus=bus)
    usage.out_tokens = 85
    assert s.budget_state() == "warn"
    assert s.budget_state() == "warn"
    assert len(bus.of("budget.warn")) == 1
    # out_tokens 单调不减 → 不会回落 ok(预算不存第二份状态,INV-01)


def test_budget_state_in_and_cost_hard_gates():
    """硬闸三闸:in/cost 任一 ≥ 上限同样 exhausted(budget_state 全闸判定)。"""
    usage = TaskUsage()
    cfg = make_cfg(budget={"task": {"max_out_tokens": 100,
                                    "max_in_tokens": 2000,
                                    "max_cost_yuan": 1.0}})
    # cost 闸:估算成本 = 上限 → exhausted
    s1 = bind_scope(cfg, "cost-gate", usage=TaskUsage(cost_est=1.0))
    assert s1.budget_state() == "exhausted"
    # in 闸:输入 token 超限 → exhausted(out 未超也终态)
    s2 = bind_scope(cfg, "in-gate", usage=TaskUsage(in_tokens=2000))
    assert s2.budget_state() == "exhausted"
    # 边界:恰达上限即 exhausted(>= 判据)
    s3 = bind_scope(cfg, "edge", usage=TaskUsage(out_tokens=100))
    assert s3.budget_state() == "exhausted"
    # 90% 未达硬闸但已过 80% 线 → warn(只提醒不拦)
    s4 = bind_scope(cfg, "warn-line", usage=TaskUsage(out_tokens=90))
    assert s4.budget_state() == "warn"
    # 50% → ok
    s5 = bind_scope(cfg, "half", usage=TaskUsage(out_tokens=50))
    assert s5.budget_state() == "ok"
    assert usage  # 哨兵防误用


def test_check_budget_ok_warn_return_no_raise():
    """check_budget:ok/warn 原样返回不抛(agent-loop 每轮前置)。"""
    usage = TaskUsage(out_tokens=0)
    cfg = make_cfg(budget={"task": {"max_out_tokens": 100}})
    s = bind_scope(cfg, usage=usage)
    assert s.check_budget() == "ok"
    usage.out_tokens = 90
    assert s.check_budget() == "warn"


def test_check_budget_exhausted_raises_signal():
    """超限抛 BudgetExhausted 循环信号(非 PyHError——终态 reason=budget)。"""
    usage = TaskUsage(out_tokens=101)
    cfg = make_cfg(budget={"task": {"max_out_tokens": 100}})
    s = bind_scope(cfg, usage=usage)
    with pytest.raises(BudgetExhausted):
        s.check_budget()
    # 不继承 PyHError:不得被 agent_loop PyHError 域捕获吞掉(偏离 8)
    assert not issubclass(BudgetExhausted, PyHError)


def test_budget_state_no_counters_zero_ok():
    """未注入计数器(偏离 3)→ 按零读数返回 ok;check_budget 不误抛。"""
    s = build_scope(make_cfg(), "no-cnt")
    assert s.budget_state() == "ok"
    assert s.check_budget() == "ok"


async def test_budget_guard_coop_same_data_source():
    """与 llm_fallback.BudgetGuard 协作:同计数器/同预算键 → 同判据同结论。
    BudgetGuard.check 超限抛 PyHError(BUDGET-EXHAUSTED,async 前置闸);
    scope.budget_state 同步读数 → exhausted。两闸同源(report_usage 唯一写入)。"""
    sess = AsyncSession()                          # BudgetGuard 内部 await append
    usage = TaskUsage(out_tokens=0)
    cfg = make_cfg(budget={"task": {"max_out_tokens": 100,
                                    "max_cost_yuan": 1.0}})
    s = bind_scope(cfg, usage=usage, session=sess)
    ctx = SimpleNamespace(config=cfg, counters=FakeCounters(usage),
                          session=sess)
    # 90/100:两闸同判 warn
    usage.out_tokens = 90
    assert await BudgetGuard.check(ctx) == "warn"
    assert s.budget_state() == "warn"
    # 110/100:scope 判 exhausted;BudgetGuard 前置闸抛 BUDGET-EXHAUSTED 信号
    usage.out_tokens = 110
    assert s.budget_state() == "exhausted"
    await asyncio.sleep(0)                         # 排空 scope fire-and-forget 投递
    with pytest.raises(PyHError) as ei:
        await BudgetGuard.check(ctx)
    assert ei.value.code == "BUDGET-EXHAUSTED"
    # 预算事件同一落点:scope 一次(budget.paused 留痕)+ BudgetGuard 一次
    assert len(sess.of("budget.paused")) == 2

# ===================================================================== 快照与 fork 遮蔽
def test_snapshot_frozen_and_cow():
    """快照不可变(frozen:字段赋值 → FrozenInstanceError)+ 值拷贝(COW):
    原 scope 后续 tighten 不影响旧快照。"""
    s = bind_scope(make_cfg())
    snap = s.snapshot()
    assert isinstance(snap, ScopeSnapshot)
    assert snap.taken_at                            # 审计时间戳非空
    old_deny = set(snap.policy.deny_tools)
    s.tighten({"a"}, reason="r")
    assert "a" in s.policy.deny_tools
    assert snap.policy.deny_tools == old_deny       # 旧快照不随 tighten 变化
    with pytest.raises(Exception) as ei:             # frozen:结构不可变
        snap.taken_at = "2099-01-01T00:00:00+00:00"  # noqa: SLF001 期望 FrozenInstanceError
    assert type(ei.value).__name__ == "FrozenInstanceError"
    # 新快照反映收紧后策略
    assert "a" in s.snapshot().policy.deny_tools


def test_fork_snapshot_isolation():
    """GWT-S6-04/F059 遮蔽:子会话=父快照;父后续 tighten 不影响子;子收紧
    不透写父层(独立演进,只紧不松)。"""
    parent = bind_scope(make_cfg(), "parent-1")
    parent.tighten({"fs.delete_file"}, reason="pre-fork")
    snap = parent.snapshot()
    child = from_snapshot(snap, "child-1", session=FakeSession(),
                          counters=FakeCounters())
    # 继承:策略/预算/窗口与快照一致(值拷贝起点)
    assert child.policy.deny_tools == {"fs.delete_file"}
    assert child.limits == snap.limits
    assert child.window_tokens == snap.window_tokens
    assert child.parent_snap is snap
    # 父会话后续收紧 → 子会话不受影响(F059)
    parent.tighten({"b"}, reason="parent later")
    assert "b" not in child.policy.deny_tools
    # 子层收紧只追加本层 → 不透写父层
    child.tighten({"c"}, reason="child only")
    assert "c" in child.policy.deny_tools
    assert "c" not in parent.policy.deny_tools
    # 各自独立注册(隔离于父表)
    assert active_scope("parent-1") is parent
    assert active_scope("child-1") is child


def test_within_window_boundary():
    """窗口余量判定:hist < ratio×window 为 True(75% 阈值,F058 同源)。"""
    s = bind_scope(make_cfg())
    s.window_tokens = 1000
    s.window_ratio = 0.75
    assert s.within_window(0) is True
    assert s.within_window(749) is True
    assert s.within_window(750) is False            # ≥75%:触发压缩
    assert s.within_window(1000) is False


# ===================================================================== 释放解绑
def test_release_idempotent_unbind_and_query_rejected():
    """release:解绑注册表 + 清策略引用;重复 release 幂等;释放后查询拒
    (CYC-999;test_f032_scope_budget"释放后查询拒"语义)。"""
    s = bind_scope(make_cfg())
    assert active_scope(SID) is s
    s.release()
    assert active_scope(SID) is None                # 已解绑
    assert s.policy is None and s.limits is None    # 引用清空(GC 可回收)
    s.release()                                     # 幂等:重复释放无害
    with pytest.raises(PyHError) as ei:
        s.budget_state()                            # 释放后查询拒
    assert ei.value.code == "CYC-999"
    with pytest.raises(PyHError):
        s.can_use("fs.read_file")
    with pytest.raises(PyHError):
        s.snapshot()


def test_release_only_unbinds_own_scope():
    """release 只摘除自身(session_id 归属校验,防误释放他 scope)。"""
    a = bind_scope(make_cfg(), "rel-a")
    b = build_scope(make_cfg(), "rel-b")
    a.release()
    assert active_scope("rel-a") is None
    assert active_scope("rel-b") is b               # 他 scope 不受影响
    # 哨兵:同 sid 重建可复用(注册隔离解除)
    c = build_scope(make_cfg(), "rel-a")
    assert active_scope("rel-a") is c


# ===================================================================== 异步事件出口
async def test_async_session_event_fire_and_forget():
    """真实 SessionLog 同型(async append)下 scope 事件 fire-and-forget 投递
    (偏离 4:有运行中事件循环 → create_task;失败只记日志)。"""
    sess = AsyncSession()
    usage = TaskUsage(out_tokens=101)
    cfg = make_cfg(budget={"task": {"max_out_tokens": 100}})
    s = build_scope(cfg, "async-1", session=sess, counters=FakeCounters(usage))
    assert s.budget_state() == "exhausted"
    assert len(sess.events) == 0                    # 尚未投递(任务未运行)
    await asyncio.sleep(0)                          # 让出事件循环 → 投递完成
    assert len(sess.of("budget.paused")) == 1
    assert sess.of("budget.paused")[0]["actor"] == "system"
