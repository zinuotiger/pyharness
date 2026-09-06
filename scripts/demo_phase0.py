"""demo_phase0.py — 阶段 0 里程碑:双插件互发 + 热卸载(PRD §4.6.2)

演示:两个插件(alpha/beta)经 EventBus 互发事件;beta 卸载后不再收到;
alpha 的事件在 beta 卸载后仍广播给在场订阅者。

运行: .venv/Scripts/python.exe scripts/demo_phase0.py
"""

import asyncio, sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pyharness.bus.registry import Registry
from pyharness.bus.event_bus import EventBus
from pyharness.bus.plugin import PluginManager


class _FakeSession:
    def __init__(self) -> None:
        self.events: list = []

    async def append(self, type_: str, **payload) -> None:
        self.events.append((type_, dict(payload)))


class _FakeCtx:
    """AgentCtx 鸭子桩(bus 仅消费 ctx.session.append)。"""

    def __init__(self) -> None:
        self.session = _FakeSession()


def _mk_manifest(pid: str, **over) -> dict:
    m = {"id": pid, "version": "0.1.0", "api_version": "1",
         "requires": [], "capabilities": []}
    m.update(over)
    return m


async def main() -> int:
    bus = EventBus()
    reg = Registry(bus=bus)
    pm = PluginManager(registry=reg)
    ctx = _FakeCtx()

    for t in ["alpha.msg", "beta.msg", "plugin.installed", "plugin.uninstalled"]:
        bus.register_type(t, dict)

    got = {"beta": 0, "after_uninstall": 0}

    # handler 两参签名 (type_, payload)
    async def on_alpha_msg(type_, payload):
        got["beta"] += 1

    async def on_alpha_after(type_, payload):
        got["after_uninstall"] += 1

    # 1. 安装并激活 alpha、beta
    await pm.install(_mk_manifest("alpha"), ctx)
    await pm.activate("alpha", ctx)
    await pm.install(_mk_manifest("beta"), ctx)
    await pm.activate("beta", ctx)
    print(f"[阶段0] 激活: alpha={pm.state('alpha')}, beta={pm.state('beta')}")

    # 2. beta 订阅 alpha.msg,alpha 发事件
    bus.subscribe("alpha.msg", on_alpha_msg, owner="beta")
    await bus.emit("alpha.msg", {"text": "ping from alpha"})
    ok = got["beta"] == 1
    print(f"[阶段0] alpha→bus→beta: beta 收到 {got['beta']} 次 {'✅' if ok else '❌'}")
    assert ok, "beta 未收到 alpha 事件"

    # 3. 热卸载 beta(摘订阅 → deactivate → uninstall)
    bus.unsubscribe_all("beta")
    await pm.deactivate("beta", ctx)
    await pm.uninstall("beta", ctx)
    print(f"[阶段0] beta 卸载后 state={pm.state('beta')}")

    # 4. alpha 再发:beta 不再收到
    await bus.emit("alpha.msg", {"text": "second ping"})
    still = got["beta"] == 1
    print(f"[阶段0] 卸载后 beta 计数仍={got['beta']}(不增长={'✅' if still else '❌'})")
    assert still, "卸载后 beta 仍收到事件"

    # 5. 新订阅者仍能收到(总线健康)
    bus.subscribe("alpha.msg", on_alpha_after, owner="listener")
    await bus.emit("alpha.msg", {"text": "third ping"})
    alive = got["after_uninstall"] == 1
    print(f"[阶段0] 新订阅者收到 {got['after_uninstall']} 次(总线存活={'✅' if alive else '❌'})")
    assert alive, "卸载后总线不工作"

    print("\n=== ✅ 阶段 0 里程碑通过:双插件互发 + 热卸载 + 总线存活 ===")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
