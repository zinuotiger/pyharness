"""pyharness/core/plugin_loader.py — 插件目录装载器 + 热重载(#50/#51/#52 Consumer 面)。

契约(单文件插件):cfg.plugins.dir/<pid>/plugin.py,模块内声明:
    MANIFEST = {"id","version","api_version":"1","requires":[]}
    async def register_tools(registry) -> list[str]   # 可选:桥接 core ToolRegistry
PluginManager(bus Registry)管五态生命周期 + plugin.installed/uninstalled 留痕;
register_tools 把可执行工具注册进 engine 的 core ToolRegistry(executor 消费面)
——两套注册表语义桥(总线索引 vs 执行契约)。

热重载(HMR,#52):reload = 卸载(工具注销+deactivate+uninstall)→ 重载模块
(importlib 重新 exec,sys.modules 驱逐)→ install/activate/register;loader 级
热更(进程内模块级),非全框架热插拔(诚实口径)。
"""
from __future__ import annotations

import importlib.util
import inspect
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

from pyharness.errors import raise_code

log = logging.getLogger("pyharness.plugin_loader")

_MODULE_NS = "pyh_plugin"


def discover_plugins(plugins_dir: str | Path) -> list[Path]:
    """扫描插件目录:含 plugin.py 的子目录即一个插件包。"""
    root = Path(plugins_dir).expanduser()
    if not root.exists():
        return []
    return [d for d in sorted(root.iterdir())
            if d.is_dir() and (d / "plugin.py").exists()]


def load_spec(pkg_dir: Path) -> SimpleNamespace:
    """装载单插件模块(独立命名空间,防 sys.modules 污染主命名)。

    mod_name 带 nonce:同秒重写文件时不命中 __pycache__/sys.modules 旧缓存
    (HMR 确定性——代码热更必须真加载新字节码)。"""
    pid = pkg_dir.name
    py = pkg_dir / "plugin.py"
    # pyc 按源路径缓存(mtime 同秒会命中旧字节码,nonce 名绕不过)→ 装载前清缓存
    pycache = pkg_dir / "__pycache__"
    if pycache.exists():
        import shutil
        shutil.rmtree(pycache, ignore_errors=True)
    nonce = f"{id(pkg_dir):x}{__import__('time').time_ns() & 0xffff:x}"
    mod_name = f"{_MODULE_NS}.{pid}.{nonce}"
    loaded = False
    try:
        try:
            spec = importlib.util.spec_from_file_location(mod_name, py)
            if spec is None or spec.loader is None:
                raise_code("BUS-003", pid=pid, detail="plugin.py 不可装载(spec 为空)")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)
        except PyHError:
            raise
        except Exception as e:                         # noqa: BLE001 模块级错误定位
            raise_code("BUS-003", pid=pid,
                       detail=f"plugin.py 导入失败:{type(e).__name__}:{e}")
        manifest = dict(getattr(mod, "MANIFEST", {}) or {})
        manifest_id = str(manifest.get("id") or pid)
        if manifest_id != pid:
            raise_code("BUS-003", pid=pid, manifest_id=manifest_id,
                       detail="MANIFEST.id 必须与插件目录名一致")
        manifest["id"] = pid
        manifest.setdefault("version", "0.1.0")
        manifest.setdefault("api_version", "1")
        loaded = True
        return SimpleNamespace(id=pid, module=mod, manifest=manifest)
    finally:
        if not loaded:
            sys.modules.pop(mod_name, None)


def _tool_reg_fn(spec: SimpleNamespace) -> Optional[Any]:
    return getattr(spec.module, "register_tools", None)


async def load_plugin(manager: Any, spec: SimpleNamespace, ctx: Any,
                      tool_reg: Any, *, state: dict) -> None:
    """安装 + 激活 + 桥接工具(幂等前提:同 pid 已存在则先卸载)。"""
    pid = spec.id
    prev = state.get(pid)
    if prev is not None:
        await unload_plugin(manager, spec, ctx, tool_reg, state=state)
    names: list[str] = []
    before_names = set(tool_reg.names()) if callable(getattr(tool_reg, "names", None)) else set()
    installed = False
    active = False
    try:
        await manager.install(spec.manifest, ctx)
        installed = True
        await manager.activate(pid, ctx)
        active = True
        fn = _tool_reg_fn(spec)
        if callable(fn):
            ret = fn(tool_reg)
            if inspect.isawaitable(ret):
                ret = await ret
            names = list(ret or [])
        state[pid] = {"spec": spec, "tool_names": names,
                      "path": str(spec.module.__file__ or "")}
    except Exception:
        after_names = set(tool_reg.names()) if callable(getattr(tool_reg, "names", None)) else set()
        for name in set(names) | (after_names - before_names):
            try:
                tool_reg.unregister_definition(name)
            except Exception:                    # noqa: BLE001 回滚尽力
                log.debug("plugin rollback unregister miss %s", name)
        try:
            if active:
                await manager.deactivate(pid, ctx)
            if installed:
                await manager.uninstall(pid, ctx)
        except Exception:                        # noqa: BLE001 不掩盖原始错误
            log.warning("plugin rollback cleanup failed pid=%s", pid, exc_info=True)
        state.pop(pid, None)
        raise


async def unload_plugin(manager: Any, spec: SimpleNamespace, ctx: Any,
                        tool_reg: Any, *, state: dict) -> None:
    """热卸载(工具注销 → deactivate → uninstall → 模块驱逐)。"""
    pid = spec.id
    rec = state.pop(pid, None)
    if rec is not None:
        for name in rec.get("tool_names", ()):
            try:
                tool_reg.unregister_definition(name)
            except Exception:                            # noqa: BLE001 注销尽力
                log.debug("plugin tool unregister miss %s", name)
    try:
        st = manager.state(pid)
        if st in ("active", "inactive"):
            if st == "active":
                await manager.deactivate(pid, ctx)
            await manager.uninstall(pid, ctx)
    except Exception:                                    # noqa: BLE001 热卸尽力
        log.warning("plugin unload cleanup failed pid=%s", pid)
    _evict(spec)


async def reload_plugin(manager: Any, spec: SimpleNamespace, ctx: Any,
                        tool_reg: Any, *, state: dict,
                        pkg_dir: Path) -> None:
    """HMR:卸旧载新(importlib 重新 exec,进程内模块级热更)。"""
    await unload_plugin(manager, spec, ctx, tool_reg, state=state)
    fresh = load_spec(pkg_dir)                     # 新代码重新装载
    await load_plugin(manager, fresh, ctx, tool_reg, state=state)


def _evict(spec: SimpleNamespace) -> None:
    """sys.modules 驱逐(防 reload 拿到旧模块缓存;nonce 名一并摘除)。"""
    for name in (f"{_MODULE_NS}.{spec.id}",
                 getattr(spec.module, "__name__", "")):
        if name:
            sys.modules.pop(name, None)


def manager_state(manager: Any, pid: str) -> str:
    """插件状态(absent/installed/active/inactive…)。"""
    try:
        return manager.state(pid)
    except Exception:                                  # noqa: BLE001
        return "absent"


__all__ = ["discover_plugins", "load_spec", "load_plugin", "unload_plugin",
           "reload_plugin", "manager_state"]
