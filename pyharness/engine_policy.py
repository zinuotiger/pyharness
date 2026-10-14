"""Engine policy: complete migrated implementation."""
from __future__ import annotations
from typing import Any, TYPE_CHECKING
if TYPE_CHECKING:
    pass
from pyharness.core import llm as llm_mod

from pyharness.engine_types import log

def _make_chain(cfg: Any, bus: Any, registry=None) -> Any:
    """FallbackChain 装配(F013/F028):adapters 引用 llm 注册表(后续注册立即可见)。

    config = Settings 权威(chain = llm.model + fallback_models;退避 llm.retry;
    降级 llm.degrade);bus 为告警/通知事件尽力出口。"""
    from pyharness.core.llm_fallback import FallbackChain
    return FallbackChain(adapters=llm_mod.adapters if registry is None else registry, config=cfg, bus=bus)

def _cfg_probe_interval(cfg: Any) -> float:
    """F033 探针周期取值(CFG `llm.probe.interval_s`,缺省 60s)。

    与 ``flush_kwargs_of``/``_cfg_max_arg_failures`` 同款:装配层解析配置后把**标量**
    注入组件,组件不 import config。
    """
    v = getattr(getattr(getattr(cfg, "llm", None), "probe", None), "interval_s", None)
    try:
        return float(v) if v is not None else 60.0
    except (TypeError, ValueError):
        return 60.0

def _policy_preset(cfg: Any) -> str:
    """security.policy.preset 读取(缺省 strict;任意 cfg 形态容错)。"""
    try:
        v = getattr(getattr(getattr(cfg, "security", None), "policy", None),
                    "preset", None)
    except Exception:                                  # noqa: BLE001 非 Settings 对象
        return "strict"
    return str(v) if v in ("locked", "readonly", "standard", "strict") else "strict"

def _cfg_max_arg_failures(cfg: Any) -> int:
    """F026 连败阈值取值(CFG `loop.max_arg_failures_per_round`,缺省由执行器常量兜底)。

    与 ``persistence.flush_kwargs_of`` 同款装配面:装配层解析配置后把**标量**注入
    组件,组件自身不 import config(N1"声明配置必须可达")。
    """
    from pyharness.core.tools_executor import DEFAULT_MAX_ARG_FAILURES
    v = getattr(getattr(cfg, "loop", None), "max_arg_failures_per_round", None)
    try:
        return int(v) if v is not None else DEFAULT_MAX_ARG_FAILURES
    except (TypeError, ValueError):
        return DEFAULT_MAX_ARG_FAILURES

def _apply_preset(cfg: Any, policy: Any, tool_reg: Any,
                  *, scope: Any = None) -> None:
    """权限预设编译(#39):按注册表实名字面量收紧/放宽,策略只紧不松(无 relax)。

    - locked:  全禁(仅对话,零工具)——deny = 全部注册名
    - readonly:保留只读/会话内自管理(fs 读/列表 + goal/todo + spill 读)
    - standard:sandbox_level=basic(放宽域约束;危险级仍由 guard/审批管)
    - strict:  默认最小权限(engine 既有行为,不改动)

    留痕(2026-09-12 修复):策略写入后经 scope.note_tighten 落 scope.updated 事件
    ——装配段是同步函数无法 await,故用同形只读补记,消除"切预设无审计痕"缺口。
    _deny 为空(strict/无注册名)时不留痕(与 tighten 幂等语义一致)。
    """
    def _audit(added: set) -> None:
        if scope is not None and added:
            try:
                scope.note_tighten(added, reason=f"preset:{preset_name}")
            except Exception:                      # noqa: BLE001 留痕失败不阻断装配
                log.warning("preset 留痕失败", exc_info=True)

    preset_name = _policy_preset(cfg)
    # Explicit local prohibitions apply at initial assembly and after preset resets.
    policy.deny_tools.update(getattr(getattr(getattr(cfg, "security", None),
        "policy", None), "deny_tools_extra", None) or [])
    names = [d.name for d in tool_reg.iter_definitions()]
    if preset_name == "locked":
        _deny = set(names)
        policy.deny_tools |= _deny
        _audit(_deny - set())
        return
    if preset_name == "readonly":
        keep = {"fs.read_file", "fs.list_dir", "goal", "todo", "storage.spill"}
        _deny = set(names) - (keep & set(names))
        policy.deny_tools |= _deny
        _audit(_deny)
        return
    if preset_name == "standard":
        policy.sandbox_level = "basic"                 # 放宽仅域约束
        # 同步可见性档:tool_exec 等按 security.sandbox.level 判定(两键脱节
        # 会造成审批已放行但工具 TLB-802——2026-09-08 exec 真链实测暴露)
        cfg.security.sandbox.level = "basic"
        return
