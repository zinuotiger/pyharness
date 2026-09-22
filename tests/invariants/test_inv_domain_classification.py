"""不变量:Registered Tool Is Classified（已注册工具必须被归类）—— N2-a。

**性质**:注册表中每个工具的名称前缀(域)必须落在四张**显式**白名单之一:
``SELF_DOMAINS`` / ``WORKSPACE_DOMAINS`` / ``ALLOWLIST_DOMAINS`` / ``RESTRICTED_DOMAINS``。

**机制**:域分类必须**完备** —— 修复前该分类是"隐式"的:任何未被三张白名单覆盖的域
都会**自动落入收紧面**,于是"漏配"与"有意收紧"无法区分。``subagent`` 就是这样被
静默隐藏的(实测:strict 档 ``can_use("subagent.spawn")=False`` ⇒ 整个子代理能力
从 LLM 视角不可达),且这是 **session / schedule 之后的第三次**同类缺陷。

**证据**:本文件即运行时证据 —— 枚举**真实装配**出的注册表逐项判定。

**负向**:从 ``SELF_DOMAINS`` 移除任一"零外部副作用"域 ⇒ 本文件必须 RED。
"""
from __future__ import annotations

import pathlib


from pyharness.config import load_settings
from pyharness.core.scope import (ALLOWLIST_DOMAINS, RESTRICTED_DOMAINS,
                                  SELF_DOMAINS, WORKSPACE_DOMAINS)

CLASSIFIED = (SELF_DOMAINS | WORKSPACE_DOMAINS | ALLOWLIST_DOMAINS
              | RESTRICTED_DOMAINS)


def _domain(tool_name: str) -> str:
    return tool_name.split(".", 1)[0] if "." in tool_name else tool_name


async def _assembled_tools(tmp_path):
    """真实装配(含 orchestration announce)后的注册表快照。"""
    from pyharness.core.session import open_session
    from pyharness.engine import activate_orchestration, build_runner_components
    from pyharness.persistence import open_store

    cfg = load_settings()
    root = pathlib.Path(tmp_path)
    cfg.storage.root = str(root)
    cfg.storage.sessions_dir = str(root / "s")
    cfg.storage.workspaces_dir = str(root / "w")
    cfg.storage.spill_dir = str(root / "sp")
    cfg.storage.db_path = str(root / "i.db")
    store = open_store("s-invdom-000001", dir=root / "s")
    log_ = await open_session("s-invdom-000001", store)
    spine = build_runner_components(cfg, log_=log_, bus=None,
                                    sessions_dir=root / "s", store=store,
                                    attach_persistence=False, channel="desktop")
    await activate_orchestration(spine)
    try:
        return sorted(d.name for d in spine.tool_registry.iter_definitions()), spine
    finally:
        try:
            await spine.close()
        except Exception:                        # noqa: BLE001 收尾尽力
            pass


async def test_every_registered_tool_domain_is_classified(tmp_path):
    """**核心断言**:注册表内每个工具的域都必须被显式归类。"""
    names, _spine = await _assembled_tools(tmp_path)
    assert names, "注册表不应为空(装配失效?)"
    unclassified = sorted({_domain(n) for n in names
                           if _domain(n) not in CLASSIFIED})
    assert not unclassified, (
        f"未归类域(会被**静默隐藏**于 strict 档):{unclassified}；"
        f"注册工具={names}")


async def test_subagent_domain_is_self_managed(tmp_path):
    """``subagent`` 必须属自管理域(零外部副作用 ⇒ strict 档恒可见)。"""
    names, _spine = await _assembled_tools(tmp_path)
    assert "subagent.spawn" in names, "subagent.spawn 应已由 orchestration announce 注册"
    assert "subagent" in SELF_DOMAINS


async def test_subagent_spawn_reachable_under_default_strict(tmp_path):
    """**N2-a 回归锚**:默认 strict 档下 ``can_use("subagent.spawn") == True``。

    修复前实测为 False ⇒ GRD-401 ⇒ 整个子代理能力不可达。
    """
    _names, spine = await _assembled_tools(tmp_path)
    assert spine.scope.policy.sandbox_level == "strict", "默认档应为 strict"
    assert spine.scope.can_use("subagent.spawn") is True, \
        "默认 strict 档下 subagent.spawn 必须可见(否则子代理不可达)"


async def test_restricted_domains_are_still_restricted(tmp_path):
    """不因本次修复放宽收紧域:``exec.*`` / ``proc.*`` 在 strict 档仍不可见。"""
    _names, spine = await _assembled_tools(tmp_path)
    for t in ("exec.shell_run", "exec.python_run", "proc.start"):
        assert spine.scope.can_use(t) is False, f"{t} 应仍被收紧(strict 档)"


async def test_scope_matrix_is_stable(tmp_path):
    """作用域矩阵(strict vs standard):仅收紧域可见性变化,自管理/workspace 域不变。"""
    _names, spine = await _assembled_tools(tmp_path)
    sc = spine.scope
    strict = {t: sc.can_use(t) for t in (
        "goal", "todo", "fs.read_file", "storage.kv",
        "subagent.spawn", "exec.shell_run", "web.search")}
    sc.policy.sandbox_level = "basic"          # standard 档
    basic = {t: sc.can_use(t) for t in strict}
    assert strict["goal"] is basic["goal"] is True
    assert strict["fs.read_file"] is basic["fs.read_file"] is True
    assert strict["subagent.spawn"] is basic["subagent.spawn"] is True
    assert strict["exec.shell_run"] is False and basic["exec.shell_run"] is True
    assert strict["web.search"] is False, "无 allowlist 域名 ⇒ strict 下仍不可见"


def test_domain_sets_are_disjoint():
    """四张域白名单必须两两不交(否则"归类"有歧义)。"""
    sets = {"SELF": SELF_DOMAINS, "WORKSPACE": WORKSPACE_DOMAINS,
            "ALLOWLIST": ALLOWLIST_DOMAINS, "RESTRICTED": RESTRICTED_DOMAINS}
    keys = list(sets)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = keys[i], keys[j]
            assert not (sets[a] & sets[b]), f"{a} 与 {b} 相交:{sets[a] & sets[b]}"
