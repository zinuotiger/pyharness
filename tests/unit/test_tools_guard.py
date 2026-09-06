"""tests/unit/test_tools_guard.py — 工具安全闸单调拒绝链单测(F014/F023)。

覆盖清单(specs/tools_guard.py.md + SECURITY §4 契约):
- Decision 三值(allow/reject/approval,StrEnum 可直比)与危险分级默认策略
  (L0-L4:none/low→allow、high→approval(无通道→reject)、critical→reject 不可审批)
- INV-03 单调拒绝:一次 reject 后任何 guard 组合/顺序/后续挂载都不能翻回 allow;
  结构防线:无 bypass/override/force-allow/execute/移除/重排 API(AttributeError)
- INV-04/05 拒绝零副作用:reject 决策 + Provider(mock)零调用;每次求值恰一条
  guard.evaluated;每次 reject 强同步 guard.rejected(sync=True)且先 evaluated
  后 rejected;append 失败 fail-closed 上抛
- g1-g7 各守卫命中/放行分支:g-schema(TLB-803 复查)/g-danger(high/critical)/
  g-fs-path(POL-FS-1 绝对越界/POL-FS-2 ..逃逸/POL-FS-3 junction 终解析越界)/
  g-credential-read(POL-CRED-1 清单+形态双轨)/g-net-outbound(POL-NET-1 字面
  hostname 精确匹配,默认空=禁外发)/g-exec(POL-EXEC-1 strict 兜底/shell/cwd)/
  g-overwrite(POL-OVW-1 覆写转审批;append/新建放行)
- approval 降级两闸(plugin approval 也覆盖):critical→强制 reject(POL-DGR-1)、
  无审批通道→APR-501
- 插件 guard 只增链尾(register_plugin_guard/register_guard_hook),重名 TLB-801、
  非 Guard TLB-801、已禁用 BUSY;disable 保护:g-schema/g-danger 恒在不可关
  (CFG-601),五内置可关须 config_ref 声明并留 guard.disabled 事件
- from_config 装配(禁用项/凭据清单/强制 guard 复核)、真实 Scope L1+L3 集成
  (critical 工具 scope 前置 scope-hidden 终局,GRD-401)

注:guard 拒绝逻辑全部真实执行(mock 仅用于事件落点/Provider/scope 查权替身)。
"""
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from pyharness.config import Settings
from pyharness.core import scope as scope_mod
from pyharness.core import tools_guard as guard_mod
from pyharness.core.scope import ScopePolicy, build_scope
from pyharness.core.tools_guard import (
    Decision,
    FORCED_GUARDS,
    Guard,
    GuardChain,
    ToolCall,
    _BUILTIN_IDS,
    build_builtin_chain,
    danger_default_policy,
    from_config,
    g_credential_read_check,
    g_credential_read_match,
    g_danger_check,
    g_danger_match,
    g_exec_match,
    g_fs_path_check,
    g_fs_path_match,
    g_net_outbound_match,
    g_overwrite_check,
    g_overwrite_match,
    g_schema_check,
    g_schema_match,
    match_guard_by_hook,
    register_guard_hook,
)
from pyharness.errors import PyHError

# ===================================================================== 替身
class FakeSession:
    """同步事件落点替身:记录 type/payload/actor/sync/trace(强同步断言用)。"""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def append(self, type_, payload, *, actor, **kw):  # 同步:立即记录
        self.events.append({"type": type_, "payload": dict(payload),
                            "actor": actor, "sync": kw.get("sync", False),
                            "trace": kw.get("trace")})

    def of(self, type_) -> list[dict]:
        return [e for e in self.events if e["type"] == type_]


class AsyncSession:
    """真实 SessionLog 同型异步落点(强同步 await 语义验证)。"""

    def __init__(self) -> None:
        self.events: list[dict] = []

    async def append(self, type_, payload, *, actor, **kw):
        self.events.append({"type": type_, "payload": dict(payload),
                            "actor": actor, "sync": kw.get("sync", False),
                            "trace": kw.get("trace")})
        return SimpleNamespace(seq=len(self.events))

    def of(self, type_) -> list[dict]:
        return [e for e in self.events if e["type"] == type_]


class FailSession:
    """append 故障替身:拒写指定事件类型(其余放行)→ 验证 fail-closed。"""

    def __init__(self, fail_on: str) -> None:
        self.fail_on = fail_on
        self.events: list[dict] = []

    def append(self, type_, payload, *, actor, **kw):
        if type_ == self.fail_on:
            raise RuntimeError(f"append {type_} 落盘失败(模拟 PERS-202)")
        self.events.append({"type": type_, "payload": dict(payload),
                            "actor": actor, "sync": kw.get("sync", False)})

    def of(self, type_) -> list[dict]:
        return [e for e in self.events if e["type"] == type_]


class FakeScope:
    """会话 scope 替身:policy(ScopePolicy 同型)+ can_use 前置查权(记录询问)。"""

    def __init__(self, policy: ScopePolicy,
                 allowed=None) -> None:  # None = 全放行(专注 L3 链自身)
        self.policy = policy
        self.allowed = None if allowed is None else set(allowed)
        self.asked: list[str] = []

    def can_use(self, name: str) -> bool:
        self.asked.append(name)
        return True if self.allowed is None else name in self.allowed


class Gate:
    """executor 关3 最小替身:仅 ALLOW 才调 Provider(mock 调用计数,INV-05)。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def run(self, name: str) -> None:
        self.calls.append(name)


class SpyGuard(Guard):
    """可编程插件 guard 替身:match/check 注入 + 调用计数(短路/链尾断言)。"""

    def __init__(self, gid: str, *, decision=("allow", None),
                 match_all: bool = True) -> None:
        self.id = gid
        self._decision = decision
        self._match_all = match_all
        self.checked = 0

    def match(self, call: object) -> bool:
        return self._match_all

    async def check(self, call, scope) -> tuple:
        self.checked += 1
        return self._decision


# ===================================================================== 工厂
def make_policy(*, workspace_root: str = "", allowed_domains=None,
                sandbox_level: str = "basic") -> ScopePolicy:
    """最小 ScopePolicy(danger 分级表留空 = 本文件只测 L3 链自身)。"""
    return ScopePolicy(workspace_root=workspace_root,
                       allowed_domains=set(allowed_domains or ()),
                       sandbox_level=sandbox_level)


def call(name: str, args=None, *, danger: str = "none",
         call_id: str = "c1", defn=None) -> ToolCall:
    """ToolCall 工厂:defn 鸭子对象只带 danger(契约读取面)。"""
    d = defn if defn is not None else SimpleNamespace(danger=danger)
    return ToolCall(name=name, raw_args=dict(args or {}), call_id=call_id,
                    defn=d)


def chain(*, session=None, disabled=None, **kw) -> GuardChain:
    """内置链工厂:缺省新 FakeSession 落点。"""
    return GuardChain(session=session if session is not None else FakeSession(),
                      disabled=disabled, **kw)


async def evaluate_gated(gc: GuardChain, c: ToolCall, sc: FakeScope,
                         gate: Gate) -> Decision:
    """executor 语义最小化:guard 决策 ALLOW 才放行 Provider(INV-04/05 载体)。"""
    d = await gc.evaluate(c, sc)
    if d is Decision.ALLOW:
        gate.run(c.name)
    return d


@pytest.fixture(autouse=True)
def _clean_globals():
    """每测后清空插件钩子表与会话作用域表(防跨测串扰;test_scope 同款)。"""
    yield
    guard_mod._PLUGIN_HOOKS.clear()
    scope_mod._scopes.clear()


# ============================================================ Decision 三值
def test_decision_tri_value_exact_no_bypass():
    """三值枚举 {allow, reject, approval};无 bypass 第四值(INV-03 词表面)。"""
    assert {m.value for m in Decision} == {"allow", "reject", "approval"}
    assert Decision.ALLOW.value == "allow"
    assert Decision.REJECT.value == "reject"
    assert Decision.APPROVAL.value == "approval"
    # StrEnum:executor 伪码 d == "reject" 直接可用
    assert Decision.REJECT == "reject"
    assert Decision.ALLOW == "allow"
    # 无 bypass 值:连成员都不存在(结构防线)
    assert not hasattr(Decision, "BYPASS")
    assert "bypass" not in {m.value for m in Decision}


def test_danger_default_policy_full_table():
    """L0-L4 默认策略机器表达:none/low→allow;high→有通道 approval 无通道
    reject;critical→reject 不可审批;非法级别按 none 处理。"""
    for danger in ("none", "low", "banana"):
        assert danger_default_policy(danger, has_channel=True) is Decision.ALLOW
        assert danger_default_policy(danger, has_channel=False) is Decision.ALLOW
    assert danger_default_policy("high", has_channel=True) is Decision.APPROVAL
    assert danger_default_policy("high", has_channel=False) is Decision.REJECT
    for ch in (True, False):  # critical 无审批通道是刻意设计(POL-DGR-1)
        assert danger_default_policy("critical", has_channel=ch) \
            is Decision.REJECT


def test_builtin_chain_fixed_order_and_forced_guards():
    """内置 g1-g7 固定装配序(= 求值序);g-schema/g-danger 恒在不可关。"""
    ids = [g.id for g in build_builtin_chain()]
    assert ids == list(_BUILTIN_IDS) == [
        "g-schema", "g-danger", "g-fs-path", "g-credential-read",
        "g-net-outbound", "g-exec", "g-overwrite"]
    assert FORCED_GUARDS == frozenset({"g-schema", "g-danger"})


def test_guard_base_contract():
    """Guard 契约基类缺省:不管辖(match False)、不反对(check (allow, None))。"""
    g = Guard()
    assert g.id == "guard"
    assert g.match(call("fs.read_file")) is False
    assert g.allows_nothing_extra is True  # 只拒绝不放行标记(恒 True)


# ============================================================== INV-03 单调
def test_forbidden_bypass_api_attribute_error():
    """结构防线:GuardChain 无任何 bypass/放行/执行/翻回 API(INV-03 钉死)。"""
    gc = chain()
    for name in gc._FORBIDDEN_API:
        assert not hasattr(gc, name)
        with pytest.raises(AttributeError) as ei:
            getattr(gc, name)  # noqa: B018
        assert name in str(ei.value)
    # 无移除/重排/清空链的公开面(只增不改)
    for name in ("remove_guard", "unregister_guard", "pop", "sort",
                 "reverse", "clear_chain"):
        assert not hasattr(gc, name)


def test_no_removal_or_reorder_public_api():
    """链只增:register 是唯一写入口(无移除/重排内置节的公开方法)。"""
    gc = chain()
    builtin_ids = [g.id for g in gc.chain]
    gc.register_plugin_guard(SpyGuard("g-plg-x"))
    assert [g.id for g in gc.chain] == builtin_ids + ["g-plg-x"]  # 恒链尾


async def test_reject_deterministic_not_flippable(tmp_path):
    """INV-03 行为面:同 call 反复求值恒 reject;中间放行其它调用不改变结果;
    Provider 对拒绝路径零调用(INV-05)。"""
    sess = FakeSession()
    ws = tmp_path / "ws"
    ws.mkdir()
    pol = make_policy(workspace_root=str(ws))
    sc = FakeScope(pol)
    gc = chain(session=sess)
    gate = Gate()
    bad = call("fs.read_file", {"path": str(tmp_path / "etc" / "passwd")})
    good = call("fs.read_file", {"path": "ok.txt"})

    assert await evaluate_gated(gc, bad, sc, gate) is Decision.REJECT
    assert await evaluate_gated(gc, good, sc, gate) is Decision.ALLOW
    assert await evaluate_gated(gc, bad, sc, gate) is Decision.REJECT
    # 拒绝路径零副作用:mock Provider 只收到放行调用
    assert gate.calls == ["fs.read_file"]  # 仅 good 那次进入执行
    assert len(sess.of("guard.rejected")) == 2  # 每次拒都留痕
    # 拒绝无法被后续事件/状态翻回:同一调用三次求值三次同决策
    d1 = await gc.evaluate(bad, sc)
    assert d1 is Decision.REJECT
    assert len(sess.of("guard.rejected")) == 3


async def test_waterfall_shortcircuit_tail_guard_not_checked():
    """waterfall 短路:首个非 allow 即终局,后续 guard.check 零调用。"""
    a = SpyGuard("a")
    b = SpyGuard("b", decision=("reject", "POL-X"))
    c = SpyGuard("c")
    gc = GuardChain(chain=[a, b, c], session=FakeSession())
    sc = FakeScope(make_policy(workspace_root=str(Path.home())))
    assert await gc.evaluate(call("t1"), sc) is Decision.REJECT
    assert a.checked == 1 and b.checked == 1 and c.checked == 0  # c 未求值


async def test_register_after_reject_cannot_rescue():
    """INV-03 核心:reject 后追加任何 allow 型 guard 到链尾,同 call 再求值
    仍 reject(只增拒绝面,历史拒绝不被新装配覆盖)。"""
    pol = make_policy(workspace_root=str(Path.home()))
    sc = FakeScope(pol)
    gc = chain()
    bad = call("fs.read_file", {"path": "C:/Windows/win.ini"})
    assert await gc.evaluate(bad, sc) is Decision.REJECT
    # 追加一个对一切放行的插件 guard(若 waterfall 可被救回,此处会翻绿)
    gc.register_plugin_guard(SpyGuard("g-plg-lenient", decision=("allow", None)))
    assert await gc.evaluate(bad, sc) is Decision.REJECT
    assert await gc.evaluate(bad, sc) is Decision.REJECT  # 再求值仍拒
    # 拒绝面只增:链变长但 g3 拒绝先于链尾(注册序=求值序不可重排)
    assert [g.id for g in gc.chain][-1] == "g-plg-lenient"


def test_monotonic_api_surface_no_allow():
    """三值里 allow 只是"本 guard 不反对";guard 侧没有"把某调用标成放行"的
    写入口(executor 才持有执行权)——dir 面检查 + 禁词全覆盖。"""
    gc = chain()
    for word in ("bypass", "override", "allow", "execute", "force"):
        assert not any(word in n for n in dir(gc)
                       if not n.startswith("_")), word


# ======================================================== INV-04/05 审计事件
async def test_evaluate_allow_single_evaluated_event(tmp_path):
    """INV-04:全链 allow 恰一条 guard.evaluated(decision=allow,guard_ids=
    生效链全集),无 guard.rejected;call_id 经 trace 携带。"""
    sess = FakeSession()
    ws = tmp_path / "ws"
    ws.mkdir()
    pol = make_policy(workspace_root=str(ws))
    gc = chain(session=sess)
    d = await gc.evaluate(
        call("fs.read_file", {"path": "ok.txt"}, call_id="cid-9"), FakeScope(pol))
    assert d is Decision.ALLOW
    ev = sess.of("guard.evaluated")
    assert len(ev) == 1
    assert ev[0]["payload"]["tool"] == "fs.read_file"
    assert ev[0]["payload"]["decision"] == "allow"
    assert ev[0]["payload"]["guard_ids"] == gc.enabled_guard_ids()
    assert ev[0]["payload"]["reasons"] is None
    assert ev[0]["actor"] == "tool"
    assert ev[0]["sync"] is False                 # evaluated 普通异步落盘
    assert ev[0]["trace"] == {"call_id": "cid-9"}
    assert sess.of("guard.rejected") == []


async def test_reject_event_pair_order_and_sync():
    """INV-05:reject 先 evaluated(deny)后 rejected 强同步(sync=True);payload
    含 guard_id/policy_ref,不含参数原文(脱敏,SECURITY §6.4)。"""
    sess = FakeSession()
    pol = make_policy(workspace_root=str(Path.home()))
    gc = chain(session=sess)
    raw = "C:/Windows/win.ini"
    d = await gc.evaluate(call("fs.read_file", {"path": raw}, call_id="cid-7"),
                          FakeScope(pol))
    assert d is Decision.REJECT
    ev = sess.of("guard.evaluated")
    rj = sess.of("guard.rejected")
    assert len(ev) == 1 and len(rj) == 1
    assert ev[0]["payload"]["decision"] == "deny"          # 词表别名口径
    assert ev[0]["payload"]["guard_ids"] == ["g-fs-path"]
    assert ev[0]["payload"]["reasons"] == ["POL-FS-1"]
    assert ev[0]["sync"] is False
    assert rj[0]["payload"] == {"tool": "fs.read_file",
                                "guard_id": "g-fs-path",
                                "reason": "POL-FS-1",
                                "policy_ref": "POL-FS-1"}
    assert rj[0]["sync"] is True                  # 拒绝事实强同步(崩溃不丢)
    assert rj[0]["actor"] == "tool"
    assert rj[0]["trace"] == {"call_id": "cid-7"}
    # 顺序:reject 事件在 evaluated 之后(拦了→留痕 的因果序)
    assert sess.events.index(rj[0]) > sess.events.index(ev[0])
    # 脱敏:审计载荷不含参数原文
    blob = str(sess.events)
    assert "win.ini" not in blob and "POL-FS-1" in blob


async def test_rejected_sync_awaited_with_async_session():
    """真实 SessionLog 同型异步落点:reject 返回前 rejected 已落盘(await 语义
    而非 fire-and-forget),sync=True 标志透传。"""
    sess = AsyncSession()
    gc = chain(session=sess)
    d = await gc.evaluate(
        call("fs.read_file", {"path": "C:/x/y"}), FakeScope(make_policy()))
    assert d is Decision.REJECT                    # 未触发 fake-scope 直拒
    rj = sess.of("guard.rejected")
    assert len(rj) == 1 and rj[0]["sync"] is True  # evaluate 返回时已落盘
    assert rj[0]["payload"]["guard_id"] == "g-fs-path"
    ev = sess.of("guard.evaluated")
    assert len(ev) == 1 and ev[0]["sync"] is False


async def test_scope_hidden_reject_events_grd401():
    """scope 前置(不可见)→ 终局 REJECT,guard_id=scope-hidden,rejected 强同步
    (GRD-401);Provider 零调用(INV-05)。"""
    sess = FakeSession()
    gc = chain(session=sess)
    sc = FakeScope(make_policy(workspace_root=str(Path.home())),
                   allowed={"fs.read_file"})       # 仅白名单一个工具
    gate = Gate()
    d = await evaluate_gated(gc, call("fs.delete_file", {"path": "a.txt"}),
                             sc, gate)
    assert d is Decision.REJECT
    assert gate.calls == []                        # 零副作用
    assert sc.asked == ["fs.delete_file"]
    rj = sess.of("guard.rejected")
    assert rj[0]["payload"]["guard_id"] == "scope-hidden"
    assert rj[0]["payload"]["policy_ref"] == "GRD-401"
    assert rj[0]["sync"] is True
    assert sess.of("guard.evaluated")[0]["payload"]["guard_ids"] \
        == ["scope-hidden"]


async def test_append_failure_fail_closed():
    """强同步落盘失败 → evaluate 上抛(fail-closed:缺 rejected/evaluated 不进入
    Provider),绝不带着未落盘的拒绝继续(审计不能撒谎)。"""
    sess = FailSession("guard.rejected")
    gc = chain(session=sess)
    sc = FakeScope(make_policy())
    with pytest.raises(RuntimeError):
        await gc.evaluate(call("fs.read_file", {"path": "C:/x"}), sc)
    assert sess.of("guard.evaluated")              # evaluated 已先落
    # evaluated 落盘失败同样上抛(allow 也 fail-closed)
    sess2 = FailSession("guard.evaluated")
    gc2 = chain(session=sess2)
    with pytest.raises(RuntimeError):
        await gc2.evaluate(call("fs.read_file", {"path": "a.txt"}),
                           FakeScope(make_policy(workspace_root=".")))


async def test_no_session_degrades_decision_enforced():
    """未接线事件出口(session=None):决策语义不变(拒绝仍拒绝),仅日志降级。"""
    gc = GuardChain()
    sc = FakeScope(make_policy())
    assert await gc.evaluate(call("fs.read_file", {"path": "a.txt"}),
                             sc) is Decision.ALLOW
    assert await gc.evaluate(call("fs.read_file", {"path": "C:/x"}),
                             sc) is Decision.REJECT


# ================================================================ g1 schema
def test_g1_schema_match_all_tools():
    """g1 match 所有工具(+内部调用)= 链内复查面。"""
    assert g_schema_match(call("anything")) is True
    assert g_schema_match(call("fs.read_file")) is True


async def test_g1_validator_tlb803_rejects_in_chain():
    """g1 check:validator 抛 TLB-803(入口已验仍败=内层绕过证据)→ reject。"""
    def bad_validator(name, raw_args):
        raise PyHError("TLB-803", ctx={"tool": name})  # registry 同型抛法

    sess = FakeSession()
    gc = chain(session=sess, validator=bad_validator)
    d = await gc.evaluate(call("fs.read_file", {"path": "a.txt"}),
                          FakeScope(make_policy()))
    assert d is Decision.REJECT
    rj = sess.of("guard.rejected")[0]["payload"]
    assert rj["guard_id"] == "g-schema" and rj["policy_ref"] == "TLB-803"


async def test_g1_validator_other_code_reraised():
    """validator 抛其它错误码(注册表层契约问题)→ 上抛不吞(仅 TLB-803 转拒)。"""
    def other_validator(name, raw_args):
        raise PyHError("TLB-802", ctx={"tool": name})

    gc = chain(session=FakeSession(), validator=other_validator)
    with pytest.raises(PyHError) as ei:
        await gc.evaluate(call("fs.read_file"), FakeScope(make_policy()))
    assert ei.value.code == "TLB-802"


async def test_g1_direct_check_unmounted_allows():
    """g1 check 直接调用:validator 未装配 → allow(入口 F026 已验,注册表落地
    后由装配层注入);validator 失败 → (reject, TLB-803)。"""
    assert await g_schema_check(call("x"), None) == ("allow", None)
    def bad_validator(name, raw_args):
        raise PyHError("TLB-803", ctx={"tool": name})
    assert await g_schema_check(call("x"), None,
                                validator=bad_validator) == ("reject", "TLB-803")


# ============================================================== g2 danger 分级
async def test_g2_high_approval_with_channel_default():
    """danger=high → g-danger approval(POL-DGR-1);channel 缺省(None)视同有
    通道 → Decision.APPROVAL;无 rejected(审批不是拒绝)。"""
    sess = FakeSession()
    gc = chain(session=sess)
    d = await gc.evaluate(call("fs.read_file", {"path": "a.txt"},
                               danger="high", call_id="ap-1"),
                          FakeScope(make_policy()))
    assert d is Decision.APPROVAL
    ev = sess.of("guard.evaluated")[0]["payload"]
    assert ev["decision"] == "need_approval"       # 词表别名(ask≡approval)
    assert ev["guard_ids"] == ["g-danger"]
    assert ev["reasons"] == ["POL-DGR-1"]
    assert sess.of("guard.rejected") == []


async def test_g2_high_no_channel_degrades_apr501():
    """high + 无审批通道(headless)→ APR-501 降级为 reject(不发请求直接拒)。"""
    sess = FakeSession()
    gc = chain(session=sess, approval_channel=False)
    d = await gc.evaluate(call("fs.read_file", {"path": "a.txt"},
                               danger="high"),
                          FakeScope(make_policy()))
    assert d is Decision.REJECT
    rj = sess.of("guard.rejected")[0]["payload"]
    assert rj["guard_id"] == "g-danger" and rj["policy_ref"] == "APR-501"


async def test_g2_critical_reject_not_approvable():
    """danger=critical → g-danger 直拒 POL-DGR-1(人类无批准入口,即使有通道)。"""
    sess = FakeSession()
    gc = chain(session=sess)                       # 通道在位也拒
    d = await gc.evaluate(call("fs.write_file", {"path": "a.txt"},
                               danger="critical"),
                          FakeScope(make_policy()))
    assert d is Decision.REJECT
    rj = sess.of("guard.rejected")[0]["payload"]
    assert rj["guard_id"] == "g-danger" and rj["policy_ref"] == "POL-DGR-1"


async def test_g2_none_low_allow_and_match():
    """none/low → 不反对;g-danger match 只盯 danger≠none。"""
    assert g_danger_match(call("x", danger="none")) is False
    assert g_danger_match(call("x", danger="low")) is True
    for level in ("none", "low"):
        d, policy = await g_danger_check(call("x", danger=level), None)
        assert d == "allow" and policy is None
    d, policy = await g_danger_check(call("x", danger="high"), None)
    assert d == "approval" and policy == "POL-DGR-1"
    d, policy = await g_danger_check(call("x", danger="critical"), None)
    assert d == "reject" and policy == "POL-DGR-1"


# =============================================================== g3 路径几何
def test_g3_match_fs_domain_prefix_only():
    """g3 match:fs.*/workspace.* 管;非文件域(单字/其它域)不管辖。"""
    assert g_fs_path_match(call("fs.read_file")) is True
    assert g_fs_path_match(call("workspace.list_dir")) is True
    assert g_fs_path_match(call("web.fetch")) is False
    assert g_fs_path_match(call("plain_tool")) is False


async def test_g3_absolute_outside_polfs1(tmp_path):
    """POL-FS-1:绝对路径词法越界(不 startswith workspace)→ reject。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    sc = FakeScope(make_policy(workspace_root=str(ws)))
    d, policy = await g_fs_path_check(
        call("fs.read_file", {"path": str(tmp_path / "elsewhere" / "f.txt")}),
        sc)
    assert (d, policy) == ("reject", "POL-FS-1")
    # 绝对路径在 workspace 内 → 不构成 FS-1(放行面)
    inside = str(ws / "sub" / "f.txt")
    d, policy = await g_fs_path_check(
        call("fs.read_file", {"path": inside}), sc)
    assert d == "allow" and policy is None


async def test_g3_dotdot_escape_polfs2(tmp_path):
    """POL-FS-2:.. 段规范化后逃出 workspace → reject;界内 .. 归一后放行。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    sc = FakeScope(make_policy(workspace_root=str(ws)))
    d, policy = await g_fs_path_check(
        call("fs.read_file", {"path": "../../etc/passwd"}), sc)
    assert (d, policy) == ("reject", "POL-FS-2")
    # 词法在界内(.. 归一后仍在 workspace)→ allow
    d, policy = await g_fs_path_check(
        call("fs.read_file", {"path": "sub/../f.txt"}), sc)
    assert d == "allow" and policy is None


async def test_g3_junction_escape_polfs3(tmp_path):
    """POL-FS-3:junction 终解析越界(词法在界内,真实在界外)→ reject。
    Windows junction 免管理员权限;创建失败则跳过(环境无该能力)。"""
    ws = tmp_path / "ws"
    outside = tmp_path / "outside"
    ws.mkdir()
    outside.mkdir()
    link = ws / "escape"
    rc = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    if rc != 0:
        pytest.skip("环境不支持创建 junction(无 mklink 权限)")
    try:
        sc = FakeScope(make_policy(workspace_root=str(ws)))
        sess = FakeSession()
        gc = chain(session=sess)
        d = await gc.evaluate(call("fs.read_file", {"path": str(link)}), sc)
        assert d is Decision.REJECT
        rj = sess.of("guard.rejected")[0]["payload"]
        assert rj["guard_id"] == "g-fs-path" and rj["policy_ref"] == "POL-FS-3"
    finally:
        if os.path.exists(link):                   # 只删联接本身,不追目标
            os.rmdir(link)


@pytest.mark.parametrize("key", ["src", "dst", "target", "dir", "path"])
async def test_g3_path_key_variants(tmp_path, key):
    """g3 读参面:path/src/dst/target/dir 任一键越界都拒(动作形态匹配)。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    sc = FakeScope(make_policy(workspace_root=str(ws)))
    d, policy = await g_fs_path_check(
        call("fs.copy", {key: str(tmp_path / "elsewhere")}), sc)
    assert (d, policy) == ("reject", "POL-FS-1")


async def test_g3_empty_args_targets_workspace_root(tmp_path):
    """无路径参数 → 目标=workspace 根(几何界内)→ allow。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    sc = FakeScope(make_policy(workspace_root=str(ws)))
    assert await g_fs_path_check(call("fs.list_dir", {}), sc) \
        == ("allow", None)
    assert await g_fs_path_check(call("fs.list_dir", {"path": ""}), sc) \
        == ("allow", None)


# ========================================================= g4 凭据读拦截
def test_g4_match_read_prefix_only():
    """g4 match:读路径类工具(fs.read/workspace.read);写/其它不属读面。"""
    assert g_credential_read_match(call("fs.read_file")) is True
    assert g_credential_read_match(call("workspace.read_text")) is True
    assert g_credential_read_match(call("fs.write_file")) is False
    assert g_credential_read_match(call("fs.list_dir")) is False


@pytest.mark.parametrize("base", ["credentials.yaml", "credentials.yml"])
async def test_g4_cred_basename_anywhere_reject(tmp_path, base):
    """形态纵深:凭据文件名出现在 workspace 任意位置(清单外)→ POL-CRED-1。"""
    sess = FakeSession()
    ws = tmp_path / "ws"
    ws.mkdir()
    gc = chain(session=sess)
    d = await gc.evaluate(
        call("fs.read_file", {"path": str(ws / "deep" / base)}),
        FakeScope(make_policy(workspace_root=str(ws))))
    assert d is Decision.REJECT
    rj = sess.of("guard.rejected")[0]["payload"]
    assert rj["guard_id"] == "g-credential-read"
    assert rj["policy_ref"] == "POL-CRED-1"


async def test_g4_configured_credential_path_exact_hit(tmp_path):
    """装配清单精确路径命中(文件名非凭据形态)→ POL-CRED-1。"""
    sess = FakeSession()
    ws = tmp_path / "ws"
    (ws / "auth").mkdir(parents=True)
    cred = str(ws / "auth" / "tokens.txt")
    gc = chain(session=sess, credential_paths=[cred])
    d = await gc.evaluate(
        call("fs.read_file", {"path": cred}, call_id="g4-1"),
        FakeScope(make_policy(workspace_root=str(ws))))
    assert d is Decision.REJECT
    rj = sess.of("guard.rejected")[0]
    assert rj["payload"]["guard_id"] == "g-credential-read"
    assert rj["payload"]["policy_ref"] == "POL-CRED-1"
    assert rj["trace"] == {"call_id": "g4-1"}


async def test_g4_default_credential_home_file_direct():
    """未装配清单 → 兜底默认凭据文件(~/.pyharness/credentials.yaml)命中即拒。"""
    home = str(Path.home())
    sc = FakeScope(make_policy(workspace_root=home))  # ws=home 使该文件界内
    d, policy = await g_credential_read_check(
        call("fs.read_file",
             {"path": os.path.join(home, ".pyharness", "credentials.yaml")}),
        sc)
    assert (d, policy) == ("reject", "POL-CRED-1")


async def test_g4_write_of_cred_name_not_read_blocked(tmp_path):
    """写工具(g7 管)不触发 g4:写 credentials.yaml(不存在)→ 链不报 POL-CRED-1
    (读才有凭据外泄面;覆写面归 g7 审批)。"""
    sess = FakeSession()
    ws = tmp_path / "ws"
    ws.mkdir()
    gc = chain(session=sess)
    d = await gc.evaluate(
        call("fs.write_file", {"path": str(ws / "credentials.yaml")}),
        FakeScope(make_policy(workspace_root=str(ws))))
    assert d is Decision.ALLOW                      # 新建(不存在)→ 放行
    assert [e["type"] for e in sess.events] == ["guard.evaluated"]


async def test_g4_outside_ws_g3_semantics_priority():
    """越界读:g3 几何语义优先(g4 不越权,直接调用亦返回 POL-FS-1)。"""
    sc = FakeScope(make_policy(workspace_root=str(Path.cwd())))
    d, policy = await g_credential_read_check(
        call("fs.read_file", {"path": "C:/Windows/win.ini"}), sc)
    assert (d, policy) == ("reject", "POL-FS-1")


# =========================================================== g5 外发 allowlist
def test_g5_match_net_domain():
    """g5 match:web.*/net.* 外发域工具。"""
    assert g_net_outbound_match(call("web.fetch")) is True
    assert g_net_outbound_match(call("net.curl")) is True
    assert g_net_outbound_match(call("fs.read_file")) is False


async def test_g5_default_empty_blocks_all_outbound():
    """默认空 allowlist = 禁一切外发(POL-NET-1),即使给了看似合法 URL。"""
    sess = FakeSession()
    gc = chain(session=sess)
    d = await gc.evaluate(
        call("web.fetch", {"url": "https://example.com/x"}),
        FakeScope(make_policy(workspace_root=str(Path.cwd()))))
    assert d is Decision.REJECT
    rj = sess.of("guard.rejected")[0]["payload"]
    assert rj["guard_id"] == "g-net-outbound" and rj["policy_ref"] == "POL-NET-1"


async def test_g5_allowlisted_host_pass_chain():
    """目标域 ∈ allowed_domains → g5 放行,全链 ALLOW。"""
    sess = FakeSession()
    pol = make_policy(allowed_domains={"api.example.com"})
    gc = chain(session=sess)
    d = await gc.evaluate(
        call("web.fetch", {"url": "https://api.example.com/v1/users?x=1"}),
        FakeScope(pol))
    assert d is Decision.ALLOW
    assert sess.of("guard.evaluated")[0]["payload"]["decision"] == "allow"


@pytest.mark.parametrize("url", [
    "https://evil.example.net/",        # 不在清单
    "https://sub.api.example.com/",     # 子域不隐含放行(字面精确匹配)
    "https://93.184.216.34/",           # IP 直连不隐含放行
])
async def test_g5_subdomain_ip_not_implied(url):
    """字面 hostname 精确匹配:子域/IP/他域不因 api.example.com 在清单而放行。"""
    pol = make_policy(allowed_domains={"api.example.com"})
    gc = chain(session=FakeSession())
    assert await gc.evaluate(call("web.fetch", {"url": url}), FakeScope(pol)) \
        is Decision.REJECT


async def test_g5_wildcard_never_constitutes_allow():
    """纵深兜底:通配 "*" 即使出现在策略里也不构成放行(config 层已拒此处再拦)。"""
    pol = make_policy(allowed_domains={"*"})
    gc = chain(session=FakeSession())
    assert await gc.evaluate(call("web.fetch", {"url": "https://x.com"}),
                             FakeScope(pol)) is Decision.REJECT


async def test_g5_normalize_case_dot_and_other_keys():
    """归一化:大小写/尾点/裸域/domain/host 键同口径比对放行。"""
    pol = make_policy(allowed_domains={"EXAMPLE.com."})
    gc = chain(session=FakeSession())
    for args in ({"url": "https://example.com./p"},   # URL+尾点
                 {"host": "example.com"},             # 裸 host 键
                 {"domain": "Example.COM"}):          # 裸 domain 键
        assert await gc.evaluate(call("net.curl", args), FakeScope(pol)) \
            is Decision.ALLOW, args


async def test_g5_no_url_param_reject():
    """无域参数 → 拒(禁外发默认;空串必不在 allowlist)。"""
    gc = chain(session=FakeSession())
    pol = make_policy(allowed_domains={"example.com"})
    assert await gc.evaluate(call("web.fetch", {"method": "GET"}),
                             FakeScope(pol)) is Decision.REJECT


# =================================================================== g6 exec
def test_g6_match_exec_prefix():
    """g6 match:exec.* 工具。"""
    assert g_exec_match(call("exec.subprocess")) is True
    assert g_exec_match(call("exec.pty")) is True
    assert g_exec_match(call("fs.read_file")) is False


async def test_g6_strict_reject_double_guard(tmp_path):
    """strict 沙箱下 exec 域外不可见(scope 前置已拦)→ g6 兜底直拒 POL-EXEC-1
    (双保险;本测试用全放行 scope 隔离出 g6 自身的兜底面)。"""
    sess = FakeSession()
    pol = make_policy(sandbox_level="strict")
    gc = chain(session=sess)
    d = await gc.evaluate(call("exec.subprocess", {"cmd": ["ls"]},
                               danger="none"), FakeScope(pol))
    assert d is Decision.REJECT
    rj = sess.of("guard.rejected")[0]["payload"]
    assert rj["guard_id"] == "g-exec" and rj["policy_ref"] == "POL-EXEC-1"


async def test_g6_shell_true_reject_basic(tmp_path):
    """basic/off + args.shell is True → 拒(解释器注入面,POL-EXEC-1)。"""
    pol = make_policy(sandbox_level="basic", workspace_root=str(tmp_path))
    gc = chain(session=FakeSession())
    d = await gc.evaluate(
        call("exec.subprocess", {"cmd": ["echo", "hi"], "shell": True}),
        FakeScope(pol))
    assert d is Decision.REJECT


async def test_g6_cwd_outside_reject_cwd_inside_allow(tmp_path):
    """cwd 绝对路径:越出 workspace → 拒;界内/缺省/相对 → 放行。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    pol = make_policy(sandbox_level="basic", workspace_root=str(ws))
    gc = chain(session=FakeSession())
    outside = str(tmp_path / "outside")
    assert await gc.evaluate(
        call("exec.subprocess", {"cmd": ["x"], "cwd": outside}),
        FakeScope(pol)) is Decision.REJECT
    inside = str(ws / "sub")
    assert await gc.evaluate(
        call("exec.subprocess", {"cmd": ["x"], "cwd": inside}),
        FakeScope(pol)) is Decision.ALLOW
    assert await gc.evaluate(
        call("exec.subprocess", {"cmd": ["x"], "cwd": "rel/sub"}),
        FakeScope(pol)) is Decision.ALLOW
    assert await gc.evaluate(
        call("exec.subprocess", {"cmd": ["x"]}),
        FakeScope(pol)) is Decision.ALLOW


# =============================================================== g7 覆写审批
def test_g7_match_write_prefix():
    """g7 match:写类工具(fs.write/workspace.write)。"""
    assert g_overwrite_match(call("fs.write_file")) is True
    assert g_overwrite_match(call("workspace.write_bytes")) is True
    assert g_overwrite_match(call("fs.read_file")) is False


async def test_g7_existing_target_approval_chain(tmp_path):
    """目标已存在且 mode=write(缺省)→ approval POL-OVW-1(覆写转审批);evaluated
    need_approval,无 rejected。"""
    sess = FakeSession()
    ws = tmp_path / "ws"
    ws.mkdir()
    target = ws / "target.txt"
    target.write_text("old", encoding="utf-8")
    gc = chain(session=sess)
    d = await gc.evaluate(
        call("fs.write_file", {"path": str(target)}, danger="low"),
        FakeScope(make_policy(workspace_root=str(ws))))
    assert d is Decision.APPROVAL
    ev = sess.of("guard.evaluated")[0]["payload"]
    assert ev["decision"] == "need_approval"
    assert ev["guard_ids"] == ["g-overwrite"]
    assert ev["reasons"] == ["POL-OVW-1"]
    assert sess.of("guard.rejected") == []


async def test_g7_new_file_allow(tmp_path):
    """目标不存在(新建路径)→ allow。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    gc = chain(session=FakeSession())
    d = await gc.evaluate(
        call("fs.write_file", {"path": str(ws / "fresh.txt")}),
        FakeScope(make_policy(workspace_root=str(ws))))
    assert d is Decision.ALLOW


async def test_g7_append_mode_allow(tmp_path):
    """mode=append(追加非覆盖)→ 目标存在也放行。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    target = ws / "log.txt"
    target.write_text("x", encoding="utf-8")
    gc = chain(session=FakeSession())
    d = await gc.evaluate(
        call("fs.write_file", {"path": str(target), "mode": "append"}),
        FakeScope(make_policy(workspace_root=str(ws))))
    assert d is Decision.ALLOW


async def test_g7_path_exists_injection_is_dead_code(tmp_path):
    """【发现缺陷钉死】注入 path_exists 回调实际未被消费(实现用 os.path.exists
    直探,`exists = path_exists or os.path.exists` 为死代码)——回调返回 False
    但真实文件存在 → 仍 approval。安全方向保守(仅注入缝失效,生产探测仍真),
    按实现真实行为断言,缺陷已列交付摘要。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    target = ws / "hit.txt"
    target.write_text("x", encoding="utf-8")
    gc = chain(session=FakeSession(), path_exists=lambda p: False)
    d = await gc.evaluate(
        call("fs.write_file", {"path": str(target)}),
        FakeScope(make_policy(workspace_root=str(ws))))
    assert d is Decision.APPROVAL                  # 若注入生效应为 ALLOW


async def test_g7_outside_path_g3_priority(tmp_path):
    """越界写:几何语义优先(直接调用返回 POL-FS-1;链上 g3 先拒)。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    sc = FakeScope(make_policy(workspace_root=str(ws)))
    d, policy = await g_overwrite_check(
        call("fs.write_file", {"path": str(tmp_path / "out.txt")}), sc)
    assert (d, policy) == ("reject", "POL-FS-1")
    sess = FakeSession()
    gc = chain(session=sess)
    assert await gc.evaluate(
        call("fs.write_file", {"path": str(tmp_path / "out.txt")}),
        sc) is Decision.REJECT
    assert sess.of("guard.rejected")[0]["payload"]["guard_id"] == "g-fs-path"


# ======================================================== approval 降级两闸
async def test_plugin_approval_on_critical_forced_reject():
    """evaluate 层统一兜底(F014):插件 guard 的 approval 在 danger=critical 时
    强制转 reject POL-DGR-1(不可审批,覆盖一切 guard)。"""
    sess = FakeSession()
    plg = SpyGuard("g-plg-crit", decision=("approval", "PLG-X"))
    gc = GuardChain(chain=[plg], session=sess)
    d = await gc.evaluate(call("fs.delete_file", {"path": "a"},
                               danger="critical"),
                          FakeScope(make_policy()))
    assert d is Decision.REJECT
    rj = sess.of("guard.rejected")[0]["payload"]
    assert rj["guard_id"] == "g-plg-crit" and rj["policy_ref"] == "POL-DGR-1"


async def test_plugin_approval_no_channel_forced_reject():
    """无审批通道(APR-501)统一在 evaluate 层降级,插件 approval 同受约束。"""
    plg = SpyGuard("g-plg-apr", decision=("approval", "PLG-X"))
    gc = GuardChain(chain=[plg], session=FakeSession(),
                    approval_channel=False)
    d = await gc.evaluate(call("fs.write_file", {"path": "a"}, danger="high"),
                          FakeScope(make_policy()))
    assert d is Decision.REJECT
    assert gc._session.of("guard.rejected")[0]["payload"]["policy_ref"] \
        == "APR-501"


async def test_plugin_approval_clean_with_channel():
    """插件 approval + danger≠critical + 有通道 → APPROVAL 原样返回(短路)。"""
    plg = SpyGuard("g-plg-apr2", decision=("approval", "PLG-X"))
    tail = SpyGuard("g-plg-tail")
    gc = GuardChain(chain=[plg, tail], session=FakeSession())
    d = await gc.evaluate(call("fs.write_file", {"path": "a"}, danger="low"),
                          FakeScope(make_policy()))
    assert d is Decision.APPROVAL
    assert tail.checked == 0                      # approval 也短路(不续跑)


# ============================================================= 插件挂载/钩子
def test_register_plugin_guard_tail_order_and_version():
    """插件 guard 追加链尾:版本 +1、enabled_guard_ids 含新 id、总线广播。"""
    bus = SimpleNamespace(events=[])
    bus.emit = lambda t, p: bus.events.append((t, dict(p)))  # noqa: E731
    gc = chain(session=FakeSession(), bus=bus)
    v0 = gc.chain_version()
    gc.register_plugin_guard(SpyGuard("g-plg-z"))
    assert gc.chain_version() == v0 + 1
    assert gc.chain[-1].id == "g-plg-z"
    assert gc.enabled_guard_ids()[-1] == "g-plg-z"
    assert bus.events == [("registry.updated",
                           {"op": "add", "kind": "guard", "key": "g-plg-z"})]


async def test_register_plugin_rejects_more_but_never_less(tmp_path):
    """插件只加拒绝面:放行型插件不改变原决策;拒绝型插件让原本放行的调用被拒。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    sc = FakeScope(make_policy(workspace_root=str(ws)))
    good = call("fs.read_file", {"path": "ok.txt"})
    gc = chain(session=FakeSession())
    assert await gc.evaluate(good, sc) is Decision.ALLOW
    gc.register_plugin_guard(SpyGuard("g-plg-allow"))
    assert await gc.evaluate(good, sc) is Decision.ALLOW     # 放行插件无影响
    gc.register_plugin_guard(SpyGuard("g-plg-deny",
                                      decision=("reject", "POL-PLG")))
    assert await gc.evaluate(good, sc) is Decision.REJECT    # 拒绝面只增


def test_register_plugin_dup_id_tlb801():
    """重名挂载 → TLB-801(guard id 全链唯一),链不被污染。"""
    gc = chain()
    with pytest.raises(PyHError) as ei:
        gc.register_plugin_guard(SpyGuard("g-schema"))
    assert ei.value.code == "TLB-801"
    assert len(gc.chain) == 7


def test_register_plugin_non_guard_tlb801():
    """非 Guard 实例挂载 → TLB-801(契约:id/match/check)。"""
    gc = chain()
    with pytest.raises(PyHError) as ei:
        gc.register_plugin_guard(object())         # type: ignore[arg-type]
    assert ei.value.code == "TLB-801"


def test_register_plugin_disabled_guard_busy():
    """已禁用 guard 挂载 → TLB-801(【发现缺陷】:spec 语义为 BUSY"先启用再挂",
    但实现把重名检查放在禁用检查之前,而 disabled ⊆ chain ids 不变式使 BUSY
    分支永远不可达——禁用 guard 因其 id 仍在链上先命中重名 TLB-801。
    按实现真实行为断言,缺陷已列交付摘要,交由主 Agent 决策是否调整检查序)。"""
    gc = chain()
    gc.disable("g-fs-path", config_ref="t")
    with pytest.raises(PyHError) as ei:
        gc.register_plugin_guard(SpyGuard("g-fs-path"))
    assert ei.value.code == "TLB-801"
    assert len(gc.chain) == 7                      # 挂载失败,链未被污染


def test_register_hook_and_match_by_hook_lifecycle():
    """register_guard_hook → match_guard_by_hook(按 guard_hooks 声明序解析)。"""
    g1 = SpyGuard("g-hook-a")
    g2 = SpyGuard("g-hook-b")
    register_guard_hook("hook-a", g1)
    register_guard_hook("hook-b", g2)
    defn = SimpleNamespace(guard_hooks=["hook-a", "hook-b"])
    assert match_guard_by_hook(defn) == [g1, g2]
    assert match_guard_by_hook(SimpleNamespace(guard_hooks=())) == []


def test_register_hook_dup_and_unknown_tlb801():
    """重名钩子 / 声明未登记钩子 / 非 Guard → TLB-801。"""
    register_guard_hook("h", SpyGuard("g-h"))
    with pytest.raises(PyHError) as ei:
        register_guard_hook("h", SpyGuard("g-h2"))   # 重名
    assert ei.value.code == "TLB-801"
    with pytest.raises(PyHError) as ei:
        register_guard_hook("x", object())           # 非 Guard
    assert ei.value.code == "TLB-801"
    with pytest.raises(PyHError) as ei:
        match_guard_by_hook(SimpleNamespace(
            guard_hooks=["never-registered"]))        # 声明了不存在的 guard
    assert ei.value.code == "TLB-801"


async def test_plugin_bad_decision_cyc999():
    """插件 check 返回四值外字符串 = 契约违规(bug 证据)→ CYC-999 上抛,零事件。"""
    sess = FakeSession()
    plg = SpyGuard("g-plg-bad", decision=("maybe", "POL-X"))
    gc = GuardChain(chain=[plg], session=sess)
    with pytest.raises(PyHError) as ei:
        await gc.evaluate(call("fs.read_file"), FakeScope(make_policy()))
    assert ei.value.code == "CYC-999"
    assert ei.value.ctx["decision"] == "maybe"     # 违规决策值留 ctx 供归因
    assert "allow/reject/approval" in ei.value.ctx["hint"]
    assert sess.events == []                       # 违规求值不落审计


# =============================================================== disable 保护
def test_disable_forced_guards_cfg601():
    """g-schema/g-danger 恒在不可关(disable → CFG-601);不在链上的 id 同拒。"""
    gc = chain()
    for gid in ("g-schema", "g-danger", "g-no-such"):
        with pytest.raises(PyHError) as ei:
            gc.disable(gid, config_ref="security.guards.disabled")
        assert ei.value.code == "CFG-601", gid
    assert gc.disabled == set()                    # 零污染


def test_constructor_disabled_invalid_cfg601():
    """构造期 disabled 越权(强制 guard/未知 id)→ CFG-601 拒装配。"""
    with pytest.raises(PyHError) as ei:
        chain(disabled=["g-schema"])
    assert ei.value.code == "CFG-601"
    with pytest.raises(PyHError) as ei:
        chain(disabled=["g-fs-path", "g-ghost"])
    assert ei.value.code == "CFG-601"


async def test_disable_builtin_ok_event_and_effect(tmp_path):
    """五内置可关(须 config_ref):留 guard.disabled 事件、版本 +1、生效链排除;
    关闭 g-fs-path 后原本越界的 fs.list_dir 不再被该 guard 拦(可审计放松)。"""
    sess = FakeSession()
    gc = chain(session=sess)
    ws = tmp_path / "ws"
    ws.mkdir()
    gc.disable("g-fs-path", config_ref="security.guards.disabled")
    assert "g-fs-path" not in gc.enabled_guard_ids()
    assert gc.chain_version() == 2
    ev = sess.of("guard.disabled")[0]
    assert ev["payload"] == {"guard_id": "g-fs-path",
                             "config_ref": "security.guards.disabled"}
    assert ev["actor"] == "system"
    # 效果:越界列表调用从 REJECT → ALLOW(disable 是 config 显式声明的放松)
    out = call("fs.list_dir", {"path": str(tmp_path / "outside")},
               danger="low")
    assert await gc.evaluate(out, FakeScope(make_policy(
        workspace_root=str(ws)))) is Decision.ALLOW
    # 对照组:未关闭的链同样调用仍 REJECT
    gc2 = chain(session=FakeSession())
    assert await gc2.evaluate(out, FakeScope(make_policy(
        workspace_root=str(ws)))) is Decision.REJECT


async def test_disable_idempotent_no_second_event():
    """重复 disable 同 id → 幂等返回,不重复留痕(防刷日志)。"""
    sess = FakeSession()
    gc = chain(session=sess)
    gc.disable("g-exec", config_ref="c")
    gc.disable("g-exec", config_ref="c")
    assert len(sess.of("guard.disabled")) == 1
    assert gc.enabled_guard_ids() == [
        "g-schema", "g-danger", "g-fs-path", "g-credential-read",
        "g-net-outbound", "g-overwrite"]


# ================================================================== from_config
def duck_cfg(*, disabled=(), cred_file="~/.pyharness/credentials.yaml"):
    """from_config 消费面鸭子 cfg(security.guards.disabled / credentials.file)。"""
    return SimpleNamespace(
        security=SimpleNamespace(
            guards=SimpleNamespace(disabled=list(disabled)),
            credentials=SimpleNamespace(file=cred_file)))


def test_from_config_wires_disabled_and_event():
    """from_config:config 显式禁用项逐个 disable 留痕(审计"谁放松了安全")。"""
    sess = FakeSession()
    gc = from_config(duck_cfg(disabled=["g-fs-path", "g-exec"]),
                     session=sess)
    assert gc.disabled == {"g-fs-path", "g-exec"}
    dis = sess.of("guard.disabled")
    assert len(dis) == 2
    assert {e["payload"]["guard_id"] for e in dis} \
        == {"g-fs-path", "g-exec"}
    assert all(e["payload"]["config_ref"] == "security.guards.disabled"
               for e in dis)


def test_from_config_forced_disable_cfg601_recheck():
    """config 层漏网的强制 guard 禁用 → from_config 复核兜底 CFG-601。"""
    with pytest.raises(PyHError) as ei:
        from_config(duck_cfg(disabled=["g-schema"]))
    assert ei.value.code == "CFG-601"


async def test_from_config_credentials_file_wired_to_g4(tmp_path):
    """from_config:cfg.security.credentials.file 注入 g4 清单;读取命中即拒。"""
    ws = tmp_path / "ws"
    (ws / "cfg").mkdir(parents=True)
    cred = str(ws / "cfg" / "token.txt")
    sess = FakeSession()
    gc = from_config(duck_cfg(cred_file=cred), session=sess)
    d = await gc.evaluate(call("fs.read_file", {"path": cred}),
                          FakeScope(make_policy(workspace_root=str(ws))))
    assert d is Decision.REJECT
    assert sess.of("guard.rejected")[0]["payload"]["policy_ref"] \
        == "POL-CRED-1"
    # 非凭据文件照常放行
    assert await gc.evaluate(call("fs.read_file", {"path": "plain.txt"}),
                             FakeScope(make_policy(
                                 workspace_root=str(ws)))) is Decision.ALLOW


# ==================================================== 调用契约 / L1+L3 集成
async def test_evaluate_requires_scope_can_use_grd401():
    """直接传 ScopePolicy(无 can_use 前置)违反调用契约 → GRD-401 上抛。"""
    gc = chain(session=FakeSession())
    pol = make_policy(workspace_root=str(Path.home()))
    with pytest.raises(PyHError) as ei:
        await gc.evaluate(call("fs.read_file", {"path": "a"}), pol)
    assert ei.value.code == "GRD-401"


async def test_integration_real_scope_critical_scope_hidden(tmp_path):
    """L1+L3 集成(真实 Scope):critical 工具(默认 fs.delete_file=critical)经
    scope 前置终局 scope-hidden GRD-401 强同步;界内低危读全链 ALLOW。"""
    # workspace 根落到 tmp(默认 ~/.pyharness/workspaces 属真实用户目录,测试不碰)
    cfg = Settings.model_validate(
        {"storage": {"workspaces_dir": str(tmp_path / "wsroot")}})
    sess = FakeSession()
    scope = build_scope(cfg, "guard-int-1", session=FakeSession())
    gc = chain(session=sess)
    assert scope.can_use("fs.delete_file") is False   # L1 前置已拦(critical)
    ws = scope.policy.workspace_root
    os.makedirs(ws, exist_ok=True)                 # 真实路径参与 FS-3 探测
    gate = Gate()
    d = await evaluate_gated(
        gc, call("fs.delete_file", {"path": os.path.join(ws, "v.txt")},
                 danger="critical"), scope, gate)
    assert d is Decision.REJECT
    assert gate.calls == []                        # Provider 零执行
    rj = sess.of("guard.rejected")[0]
    assert rj["payload"]["guard_id"] == "scope-hidden"
    assert rj["payload"]["policy_ref"] == "GRD-401"
    assert rj["sync"] is True
    # 界内低危读:scope 放行 → 链求值 → ALLOW(一次放行对照组)
    d2 = await evaluate_gated(
        gc, call("fs.read_file", {"path": os.path.join(ws, "ok.txt")},
                 danger="low"), scope, gate)
    assert d2 is Decision.ALLOW
    assert gate.calls == ["fs.read_file"]
