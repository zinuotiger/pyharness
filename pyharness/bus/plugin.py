"""pyharness/bus/plugin.py — 插件宿主与五态生命周期 (specs/bus.py.md PluginManager)

PluginManager:声明式 manifest(id/version/requires[]/api_version)装载——
api_version 校验 → 依赖拓扑(缺失/未激活/循环依赖 → BUS-003)→ 状态收束 →
registry 注册插件记录 → ctx.session.append 写 plugin.installed(F004/F006)。

五态状态机唯一迁移闸 _set_state(spec F006 迁移表):
    absent → installed → activating → active → deactivating → inactive
            ↘(安装即卸)uninstalled ↖(重激活)           ↘ uninstalled
非法迁移 → BUS-003;落定 uninstalled 后插件管理记录摘除(支持热插拔重装,F004)。

偏离点(契约内自洽修正,spec 伪码存在自相矛盾处):
1. spec 伪码 install 未把插件写入 Registry(kind=plugin),但 deactivate/uninstall
   均要求 registry.lookup("plugin", pid)——故 install 落定后注册 PluginRecord;
2. spec 迁移表缺 absent 起始边(否则首次 install 必 BUS-003)与 installed→uninstalled
   (装后未激活即卸载须可执行),_LEGAL 补两边;
3. 宿主能力契约(host.enter/detach,spec 指向 DIS-SEAM 未随文件展开):能力对象
   须含 id(注册 capability 键)/tool_keys+tool(注册 tool 双键)/subscriptions
   [(pattern, handler[, when])](以 owner=plugin_id 装载订阅);detach 逆序摘净;
4. api_version 常量取 "1"(EVENT-SCHEMA §3.5.6 示例值);
5. uninstall/投递中拦截用专类 BusyUninstall(BUSY 字面量语义,码表无 BUSY 条目,
   spec 异常表明示非 NNN 码,故不走 raise_code)。

错误全经 raise_code(F019);能力 enter 半载自回滚,不留脏注册表(TLB-801 纪律)。
"""
from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Optional

from pyharness.errors import raise_code
from pyharness.bus.registry import Registry

if TYPE_CHECKING:
    from pyharness.bus.event_bus import EventBus, Subscription

log = logging.getLogger("pyharness.bus.plugin")

# 框架插件 API 版本(EVENT-SCHEMA §3.5.6 示例值锁定 "1";不匹配 → BUS-003 拒装载)
API_VERSION = "1"


class BusyUninstall(Exception):
    """插件运行中/投递中不可卸载(BUSY 字面量语义,spec 异常表:非 NNN 码)。

    码表无 BUSY 条目(ERR.md §1 禁现场造码),故以专类携带 pid 抛出,不走
    raise_code;正常路径下由状态机前置(active 等)拦截触发。
    """

    def __init__(self, pid: str) -> None:
        self.pid = pid
        super().__init__(f"[BUSY] 插件 {pid} 运行中/投递中,不可卸载(deactivate/uninstall)")


@dataclass
class Capability:
    """能力声明(宿主 enter/detach 的最小契约,见模块 docstring 偏离点 3)。

    tool_keys 为工具 Definition.name 清单;tool 为注册对象(缺省以能力自身
    占位);subscriptions 元素为 (pattern, handler[, when]) 订阅三元/二元组。
    """

    id: str                                        # capability 注册键(ctx 路径式)
    tool_keys: list = field(default_factory=list)
    tool: Any = None
    subscriptions: list = field(default_factory=list)  # (pattern, handler[, when])


def _coerce_cap(cap: Any) -> Capability:
    """dict/鸭子对象 → Capability;缺 id 一律 TLB-801 拒(非法注册)。"""
    if isinstance(cap, Capability):
        return cap
    if isinstance(cap, dict):
        cid = cap.get("id")
        if not cid:
            raise_code("TLB-801", kind="capability", detail="能力声明缺 id 字段")
        return Capability(id=cid,
                          tool_keys=list(cap.get("tool_keys", ())),
                          tool=cap.get("tool"),
                          subscriptions=list(cap.get("subscriptions", ())))
    if not getattr(cap, "id", None):
        raise_code("TLB-801", kind="capability", detail="能力声明缺 id 字段")
    return cap


class PluginHost:
    """能力宿主:enter = 注册 capability/tool 双键 + 装载订阅(F003 双键+F002);
    detach = 摘订阅(owner 精确)+ 注销工具/能力(逆序,后装先卸,F004)。"""

    def __init__(self, registry: Registry, bus: "EventBus", owner: str) -> None:
        self.registry = registry
        self._bus = bus
        self.owner = owner          # 插件 id:订阅 owner,uninstall 按此摘除

    async def enter(self, cap: Any, ctx: Any) -> None:
        """能力装载:announce 注册 + 订阅装载;半载异常自回滚,无脏数据。"""
        cap = _coerce_cap(cap)
        registered_tools: list[str] = []
        try:
            self.registry.register("capability", cap.id, cap)
            for name in cap.tool_keys:              # tool/capability 双键
                self.registry.register("tool", name,
                                       cap.tool if cap.tool is not None else cap)
                registered_tools.append(name)
            for item in cap.subscriptions:          # 订阅装载(owner = 插件 id)
                pattern, handler, *rest = item
                self._bus.subscribe(pattern, handler,
                                    owner=self.owner,
                                    when=rest[0] if rest else None)
        except Exception:
            # 半载回滚:摘已注册键 + 本插件订阅(禁留脏注册表)
            for name in registered_tools:
                try:
                    self.registry.unregister("tool", name)
                except Exception as exc:            # noqa: BLE001 回滚尽力而为
                    log.warning("host.enter 回滚摘工具失败 tool=%s: %s", name, exc)
            try:
                self.registry.unregister("capability", cap.id)
            except Exception as exc:                # noqa: BLE001
                log.warning("host.enter 回滚摘能力失败 cap=%s: %s", cap.id, exc)
            self._bus.unsubscribe_all(self.owner)
            raise

    async def detach(self, cap: Any, ctx: Any) -> None:
        """能力摘除:摘订阅 → 注销工具 → 注销能力(detach 内含 unsubscribe_all)。"""
        cap = _coerce_cap(cap)
        self._bus.unsubscribe_all(self.owner)
        for name in cap.tool_keys:
            self.registry.unregister("tool", name)
        self.registry.unregister("capability", cap.id)


@dataclass
class PluginRecord:
    """Registry 中 kind=plugin 的注册对象:宿主查询卸载/摘除的事实源。"""

    id: str
    manifest: dict
    state: str = "installed"
    capabilities: list = field(default_factory=list)   # 已 enter 的能力(逆序 detach)
    tool_keys: list = field(default_factory=list)      # 已注册工具键(权威清单)


class PluginManager:
    """插件宿主:五态生命周期 + 热插拔(F004/F006,契约见模块 docstring)。"""

    # F006 五态迁移表(absent→installed 为首次安装入口;installed→uninstalled
    # 支持"装后未激活即卸载";uninstalled 落定后记录摘除 → 可重装)
    _LEGAL: dict[str, tuple[str, ...]] = {
        "absent": ("installed",),
        "installed": ("activating", "uninstalled"),
        "activating": ("active",),
        "active": ("deactivating",),
        "deactivating": ("inactive",),
        "inactive": ("uninstalled", "activating"),
    }

    def __init__(self, registry: Optional[Registry] = None,
                 host_factory: Optional[Callable[[str], PluginHost]] = None,
                 api_version: str = API_VERSION) -> None:
        """registry 缺省自建(EventBus+Registry 自洽实例);host_factory 缺省按
        (registry, bus, pid) 构建默认 PluginHost(owner=pid)。"""
        if registry is None:
            from pyharness.bus.event_bus import EventBus
            bus: EventBus = EventBus()
            registry = Registry(bus)
        self.registry = registry
        self._bus: EventBus = registry._bus
        self._host_factory = host_factory
        self.api_version = api_version
        self._state: dict[str, str] = {}       # pid → 五态(absent 缺省)
        self._manifest: dict[str, dict] = {}   # pid → manifest(卸载重装事实源)

    # ------------------------------------------------------------ 查询
    def state(self, pid: str) -> str:
        """当前状态;未安装 → absent。"""
        return self._state.get(pid, "absent")

    # ------------------------------------------------------------ 状态机闸
    def _set_state(self, pid: str, target: str) -> None:
        """五态状态机唯一迁移闸;非法迁移 → BUS-003 拒绝(F006)。"""
        cur = self.state(pid)
        if target not in self._LEGAL.get(cur, ()):
            raise_code("BUS-003", pid=pid, detail=f"{cur}→{target} 非法(F006 迁移表)")
        self._state[pid] = target

    # ------------------------------------------------------------ 依赖拓扑
    def _dep_closure(self, pid: str, requires: list) -> list[str]:
        """传递依赖闭包(拓扑序,依赖在前);循环/缺失 → BUS-003(F006)。"""
        closure: list[str] = []
        seen: set[str] = set()

        def visit(node: str, path: tuple) -> None:
            """递归收集 node 的已装传递依赖;环与缺失在此检出。"""
            for dep in self._manifest.get(node, {}).get("requires", ()):
                if dep in path:                  # 循环依赖检出失败
                    raise_code("BUS-003", pid=pid,
                               detail=f"循环依赖:{'->'.join(path + (dep,))}")
                if self.state(dep) == "absent":
                    raise_code("BUS-003", pid=pid,
                               detail=f"依赖 {dep} 缺失或未安装(先装依赖)")
                visit(dep, path + (dep,))
            if node not in seen:
                seen.add(node)
                closure.append(node)

        for dep in requires:                     # 根 requires(含 pid 自环)
            if dep in (pid,):
                raise_code("BUS-003", pid=pid, detail=f"循环依赖:{pid}->{dep}(自依赖)")
            if self.state(dep) == "absent":
                raise_code("BUS-003", pid=pid,
                           detail=f"依赖 {dep} 缺失或未安装(先装依赖)")
            visit(dep, (pid, dep))
        return closure

    # ------------------------------------------------------------ 安装
    async def install(self, manifest: dict, ctx: Any) -> None:
        """声明式装载:api_version → 依赖拓扑 → 状态 installed + registry 注册
        + ctx.session.append("plugin.installed") 留痕(F006/F004)。"""
        pid = manifest.get("id")
        if not pid:
            raise_code("TLB-801", kind="plugin", detail="manifest 缺 id(非法注册)")
        if pid in self.registry.RESERVED:
            raise_code("BUS-002", key=pid, hint="脊柱名/子系统名不可装为插件(原则 5)")
        if manifest.get("api_version") != self.api_version:
            raise_code("BUS-003", pid=pid,
                       detail=f"api_version={manifest.get('api_version')} 不匹配 "
                              f"(框架 API_VERSION={self.api_version})")
        closure = self._dep_closure(pid, list(manifest.get("requires", ())))
        for dep in closure:                      # 依拓扑序校验依赖已激活
            if self.state(dep) != "active":
                raise_code("BUS-003", pid=pid, detail=f"依赖 {dep} 未激活(先 activate)")
        self._set_state(pid, "installed")
        record = PluginRecord(id=pid, manifest=manifest, state="installed")
        self._manifest[pid] = manifest
        self.registry.register("plugin", pid, record)
        try:
            await ctx.session.append("plugin.installed",
                                     {"plugin_id": pid,
                                      "version": manifest["version"],
                                      "api_version": manifest["api_version"]},
                                     actor="plugin")
        except Exception:                        # 留痕失败:回滚,不留半装态
            self.registry.unregister("plugin", pid)
            self._manifest.pop(pid, None)
            self._state.pop(pid, None)
            raise

    # ------------------------------------------------------------ 激活
    def _load_caps(self, manifest: dict) -> list:
        """能力清单:entry 模块的 mod.capabilities(importlib 导入);否则取
        manifest["capabilities"](声明式,供无模块插件/单测注入)。"""
        entry = manifest.get("entry")
        if entry:
            mod = importlib.import_module(entry)
            return list(getattr(mod, "capabilities", ()))
        return list(manifest.get("capabilities", ()))

    def _host_for(self, pid: str) -> PluginHost:
        """取插件宿主实例(owner=pid);host_factory 可注入定制宿主。"""
        if self._host_factory is None:
            return PluginHost(self.registry, self._bus, pid)
        if isinstance(self._host_factory, PluginHost):
            return self._host_factory            # 共享宿主实例(owner 由调用方定)
        return self._host_factory(pid)

    async def activate(self, pid: str, ctx: Any) -> None:
        """activate:导入模块 → 逐能力 enter(注册+订阅)→ active;失败回滚到
        installed,不留半激活态(F006)。"""
        self._set_state(pid, "activating")       # absent/非法前驱 → BUS-003
        record = self.registry.lookup("plugin", pid)
        host = self._host_for(pid)
        entered: list = []
        try:
            for cap in self._load_caps(record.manifest):
                cap = _coerce_cap(cap)
                await host.enter(cap, ctx)       # announce:tool/capability 双键
                entered.append(cap)
                record.capabilities.append(cap)
                for name in cap.tool_keys:
                    if name not in record.tool_keys:
                        record.tool_keys.append(name)
            self._set_state(pid, "active")
            record.state = "active"
        except Exception:
            # 失败回滚:状态归 installed,已 enter 能力逆序 detach 摘净
            self._state[pid] = "installed"
            record.state = "installed"
            record.capabilities = []
            record.tool_keys = []
            for cap in reversed(entered):
                try:
                    await host.detach(cap, ctx)
                except Exception as exc:         # noqa: BLE001 回滚尽力而为
                    log.warning("activate 回滚 detach 失败 cap=%s: %s",
                                getattr(cap, "id", "?"), exc)
            raise

    async def deactivate(self, pid: str, ctx: Any) -> None:
        """deactivate:逆序 detach 能力(摘订阅+注销工具/能力),插件记录保留在
        Registry(仅摘订阅不注销注册表,F004);投递中(BUSY)拒绝。"""
        record = self.registry.lookup("plugin", pid)
        if pid in self._bus._inflight:
            raise BusyUninstall(pid)             # 投递中不可卸(BUSY 语义)
        self._set_state(pid, "deactivating")     # 非 active 前驱 → BUS-003
        host = self._host_for(pid)
        try:
            for cap in reversed(list(record.capabilities)):  # 后装先卸
                await host.detach(cap, ctx)
        except Exception:
            self._state[pid] = "active"          # 失败回滚,不留半失活态
            record.state = "active"
            raise
        record.capabilities = []
        record.tool_keys = []
        self._set_state(pid, "inactive")
        record.state = "inactive"

    async def uninstall(self, plugin_id: str, ctx: Any) -> None:
        """热卸载:运行/迁移中拒绝(BusyUninstall,BUSY 语义)→ 摘剩余工具/能力
        → registry 摘 plugin → plugin.uninstalled 留痕;落定后管理记录摘除,
        支持重装(F004)。不回溯已发生事件。"""
        record = self.registry.lookup("plugin", plugin_id)  # 不存在 → 结构化错误
        st = self.state(plugin_id)
        if st in ("active", "activating", "deactivating"):
            raise BusyUninstall(plugin_id)       # running 拒绝(状态机前置)
        host = self._host_for(plugin_id)
        for cap in reversed(list(record.capabilities)):    # 通常已在 deactivate 摘净
            await host.detach(cap, ctx)
        record.capabilities = []
        record.tool_keys = []
        self.registry.unregister("plugin", plugin_id)
        self._set_state(plugin_id, "uninstalled")
        record.state = "uninstalled"
        self._manifest.pop(plugin_id, None)      # 记录摘除 → state(pid) 回落 absent
        self._state.pop(plugin_id, None)
        try:
            await ctx.session.append("plugin.uninstalled",
                                     {"plugin_id": plugin_id}, actor="plugin")
        except Exception as exc:                 # 卸载已完成,留痕失败告警不阻断
            log.error("plugin.uninstalled 留痕失败 plugin_id=%s: %s",
                      plugin_id, exc)

    # ------------------------------------------------------------ 插件发事件
    async def emit_as(self, plugin_id: str, type_: str, payload: dict) -> dict:
        """插件以自身身份发事件:自动补 origin=plugin:<id>;类型未注册 → EVT-102
        (先 register_type;命名空间 plugin.<id>.<name>)。会话事件须经 session.append
        打 seq,插件不可自报 seq(INV-01)。"""
        if not self._bus._type_registered(type_):
            raise_code("EVT-102", type_=type_,
                       hint="插件事件命名空间 plugin.<id>.<name>,先 register_type")
        return await self._bus.emit(type_, {**payload, "origin": f"plugin:{plugin_id}"})


__all__ = ["API_VERSION", "BusyUninstall", "Capability", "PluginHost",
           "PluginManager", "PluginRecord"]
