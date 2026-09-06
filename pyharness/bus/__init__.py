"""pyharness/bus — 事件总线 + 注册表 + 插件宿主 (specs/bus.py.md)

单进程内唯一通信通道(阶段 0 最后一个模块):
- EventBus   :精确/通配订阅 + 三模式分发(sequential/waterfall/parallel)
              + per-sender FIFO 背压(默认 1000 在途,F005)+ 强同步恒 sequential;
              事件类型先注册才能 emit(EVT-102,与 events.vocab 词表联动)。
- Registry   :plugin/tool/capability 三类索引;脊柱八模块 + guard/approval/
              credentials 保留名(BUS-002);重复注册 TLB-801;registry.updated
              瞬时留痕(F003)。
- PluginManager:五态生命周期(installed→activating→active→deactivating→inactive
              →uninstalled)与热插拔(F004/F006);非法迁移 BUS-003。

边界:总线只中转不落盘——payload 不校验、不写会话日志;落盘由日志订阅者负责。
错误全经 raise_code(F019),订阅者异常 EVT-103 隔离不外抛。
"""
from pyharness.bus.event_bus import EventBus, STOP, Subscription
from pyharness.bus.registry import Registry
from pyharness.bus.plugin import (API_VERSION, BusyUninstall, Capability,
                                  PluginHost, PluginManager, PluginRecord)

__all__ = [
    # 订阅与总线
    "EventBus", "Subscription", "STOP",
    # 注册表
    "Registry",
    # 插件宿主与能力契约
    "PluginManager", "PluginHost", "PluginRecord", "Capability",
    "BusyUninstall", "API_VERSION",
]
