"""demo_skills_plugins.py — 插件系统(装载/HMR/卸载)+ 技能系统一键跑通演示。

用法: python scripts/demo_skills_plugins.py(离线,零网络,零 key)

Part A 插件:装载仓库示例插件 examples/plugins/hello_time → util.now 工具注册 →
执行拿到真实时间 → 热改插件代码 reload(HMR)→ 再执行看到 v2 行为 → 卸载注销。
Part B 技能:扫描仓库 skills/ → 目录 → skill.load 装载 interview-pitch(正文回喂
+ skill.used 审计)→ skill.list → 未知名 SKL-901 自愈提示。
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from pyharness.bus.plugin import PluginManager            # noqa: E402
from pyharness.core.plugin_loader import (load_plugin, load_spec,  # noqa: E402
                                          reload_plugin, unload_plugin)
from pyharness.core.tools_registry import ToolRegistry    # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[1]
HELLO = REPO / "examples" / "plugins" / "hello_time"


class _SessionStub:
    """事件留痕最小 session(替代真会话:类型+载荷记录)。"""

    def __init__(self) -> None:
        self.notes: list = []

    async def append(self, type_, payload=None, **kw) -> None:
        self.notes.append((type_, payload))


class _Ctx:
    def __init__(self) -> None:
        self.session = _SessionStub()


async def _run_provider(reg, name: str, args: dict, ctx) -> str:
    prov = reg.lookup_provider(name)
    if prov is None:
        raise RuntimeError(f"provider 缺失:{name}")
    fn = getattr(prov, "handle", None)
    if callable(fn):
        return await fn(args, ctx) if fn is not None else str(fn)
    return await prov(args, ctx)


def _line(t: str) -> None:
    print(f"  {t}")


async def part_a_plugin() -> int:
    print("=" * 64)
    print("Part A · 插件系统:装载 → 执行 → HMR 热更 → 卸载")
    print("=" * 64)
    assert HELLO.exists(), "示例插件缺失"
    mgr = PluginManager()
    ctx = _Ctx()
    reg = ToolRegistry()
    state: dict = {}
    spec = load_spec(HELLO)
    _line(f"[1] 发现插件: {spec.manifest}")
    await load_plugin(mgr, spec, ctx, reg, state=state)
    _line(f"[2] 状态: {mgr.state('hello_time')}(应 active)")
    assert mgr.state("hello_time") == "active"
    assert reg.lookup("util.now") is not None, "util.now 应已注册"
    events = [t for t, _ in ctx.session.notes]
    _line(f"[3] 事件留痕: {events}")
    out1 = await _run_provider(reg, "util.now", {}, ctx)
    _line(f"[4] 调用 util.now → {out1}")

    # ---- HMR:热改版本号与输出,reload 后行为即变 ----
    original = (HELLO / "plugin.py").read_text(encoding="utf-8")
    try:
        v2 = original.replace('"version": "1.0.0"', '"version": "2.0.0"')
        v2 = v2.replace('datetime.now()', 'datetime.now()  # v2-hot')
        (HELLO / "plugin.py").write_text(v2, encoding="utf-8")
        await reload_plugin(mgr, spec, ctx, reg, state=state, pkg_dir=HELLO)
        _line(f"[5] HMR reload 后状态: {mgr.state('hello_time')},"
              f"版本: {state['hello_time']['spec'].manifest['version']}")
        assert state["hello_time"]["spec"].manifest["version"] == "2.0.0"
        defn = reg.lookup("util.now")
        _line(f"[6] 注册面刷新: defn.version = {getattr(defn, 'version', '?')}"
              f"(应 2.0.0)")
        assert getattr(defn, "version", "") == "2.0.0"
    finally:
        (HELLO / "plugin.py").write_text(original, encoding="utf-8")
    # 还原后无需 reload:进程退出即回原版(下次启动读磁盘原版)

    await unload_plugin(mgr, spec, ctx, reg, state=state)
    _line(f"[7] 卸载后状态: {mgr.state('hello_time')}(应 absent)")
    assert mgr.state("hello_time") == "absent"
    try:
        reg.lookup("util.now")
        _line("[8] 卸载失败:util.now 仍可查!")
        return 1
    except Exception:
        _line("[8] util.now 已注销(装载器卸载干净)")
    events2 = [t for t, _ in ctx.session.notes]
    _line(f"[9] 生命周期事件: {events2}")
    print("Part A: PASS ✓\n")
    return 0


async def part_b_skill() -> int:
    print("=" * 64)
    print("Part B · 技能系统:目录 → skill.load 装载 → 审计 → 未知名自愈")
    print("=" * 64)
    from pyharness.core.skill import SkillManager
    from pyharness.core.tool_skill import _SkillHandle

    ctx = _Ctx()
    mgr = SkillManager([REPO / "skills"], session=ctx.session)
    ctx.skills = mgr                      # 模拟 engine 装配:agent ctx.skills 绑定
    cat = mgr.render_catalog()
    _line("[1] 技能库扫描完成,技能目录段(注入系统提示词的就是这段):")
    for ln in cat.splitlines():
        _line(ln)
    assert "interview-pitch" in cat and "demo-script" in cat

    handle = _SkillHandle()
    _line("[2] skill.load('interview-pitch') 装载:")
    text = await handle.handle({"name": "interview-pitch"}, ctx)
    for ln in text.splitlines()[:10]:
        _line(ln)
    types = [t for t, _ in ctx.session.notes]
    _line(f"[3] 审计事件: {types}(应含 skill.used)")
    assert "skill.used" in types

    _line("[4] skill.list:")
    lst = await handle.handle({"_op": "list"}, ctx)
    for ln in lst.splitlines()[:4]:
        _line(ln)

    _line("[5] 未知名技能(自愈路径):")
    try:
        await handle.handle({"name": "no-such-skill"}, ctx)
        _line("  未抛错?异常")
        return 1
    except Exception as e:
        _line(f"  → {str(e)[:80]}(回喂 LLM 后它会改调 skill.list)")
    print("Part B: PASS ✓\n")
    return 0


async def main() -> int:
    rc = await part_a_plugin()
    rc += await part_b_skill()
    print("=" * 64)
    print("DEMO:", "ALL PASS ✓" if rc == 0 else f"FAIL rc={rc}")
    print("=" * 64)
    return rc


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(130)
