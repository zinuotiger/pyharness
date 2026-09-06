"""pyharness/bus/registry.py — 注册表 (specs/bus.py.md Registry 全量契约)

三类索引(plugin/tool/capability)写入/查重/卸载(F003):
- register  : 脊柱八模块 + guard/approval/credentials 子系统名为 plugin 保留名
              → BUS-002;同 kind 同 key 重复 → TLB-801(改 = 注销重注册留痕);
              成功后广播 registry.updated(op=add,瞬时仅内存事件,F003 留痕)。
- lookup    : O(1) 查询;key 不存在不静默返回 None(F003 边界)。
- unregister: 摘除索引并广播 registry.updated(op=del);注销不存在 key 不静默。

错误码说明(偏离点):spec 伪码中未注册 key 的查询/注销指向 BUS-000——该码属
ERR.md §1 预留空洞(BUS-000/001 禁占位登记),errors.py 未登记、表外码即错误;
故统一改用已登记码:tool 查询按 spec 用 TLB-802(工具未注册),plugin/capability
查询与 unregister-unknown 用 TLB-801 + detail 区分(重复/非法注册同域码,结构化
拒绝语义一致,仍不静默返回 None)。kind 越界同样 TLB-801 拒。

registry.updated 经 bus.emit_sync 同步投递(瞬时仅内存广播,订阅者为同步刷新器),
保证 F003 留痕在同步调用路径上确定可见。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pyharness.errors import raise_code

if TYPE_CHECKING:
    from pyharness.bus.event_bus import EventBus

# 保留名:脊柱八模块(PRD §2.2/PARAMETER-ANCHOR,spec 伪码 system-prompt 与
# 锚定文档 system_prompt 两种拼写同收)+ guard/approval/credentials 子系统名
# (spec 异常表明示);kind=plugin 注册冲突 → BUS-002,脊柱不可换(原则 5)。
RESERVED: frozenset = frozenset({
    "agent-loop", "tools", "session", "llm", "system-prompt", "system_prompt",
    "scope", "agent", "persistence",
    "guard", "approval", "credentials",
})


class Registry:
    """注册表:三类索引 + 保留名护栏 + registry.updated 留痕(F003)。"""

    RESERVED = RESERVED
    KINDS: tuple[str, ...] = ("plugin", "tool", "capability")

    def __init__(self, bus: "EventBus"):
        self._index: dict[str, dict[str, Any]] = {k: {} for k in self.KINDS}
        self._bus = bus

    # ------------------------------------------------------------ 内部护栏
    def _check_kind(self, kind: str) -> None:
        """kind 越界 → TLB-801(非法注册),禁 KeyError 裸崩。"""
        if kind not in self.KINDS:
            raise_code("TLB-801", kind=kind, detail="kind 须为 plugin/tool/capability")

    # ------------------------------------------------------------ 写入
    def register(self, kind: str, key: str, obj: Any) -> None:
        """三类索引写入(plugin/tool/capability);留痕 registry.updated。"""
        self._check_kind(kind)
        if kind == "plugin" and key in self.RESERVED:
            raise_code("BUS-002", key=key,
                       hint="脊柱八模块/子系统名为保留名,不可注册为插件(原则 5)")
        if key in self._index[kind]:
            raise_code("TLB-801", kind=kind, key=key,
                       detail="同 kind 同 key 重复注册(改 = 注销重注册,无脏数据)")
        self._index[kind][key] = obj
        self._bus.emit_sync("registry.updated",      # F003 瞬时仅内存留痕
                            {"op": "add", "kind": kind, "key": key})

    def lookup(self, kind: str, key: str) -> Any:
        """O(1) 查询;key 不存在 → 结构化错误(不静默返回 None, F003 边界)。"""
        self._check_kind(kind)
        if key not in self._index[kind]:
            if kind == "tool":
                raise_code("TLB-802", kind=kind, key=key, hint="查工具名拼写与注册表")
            raise_code("TLB-801", kind=kind, key=key,
                       detail="key 未注册/已卸载(lookup),不静默返回 None")
        return self._index[kind][key]

    def unregister(self, kind: str, key: str) -> None:
        """摘除索引并广播 registry.updated(op=del);注销不存在 key 不静默。"""
        self._check_kind(kind)
        if key not in self._index[kind]:
            raise_code("TLB-801", kind=kind, key=key,
                       detail="unregister-unknown:注销不存在的 key 不静默(幂等由调用方先 lookup)")
        del self._index[kind][key]
        self._bus.emit_sync("registry.updated",      # op=del 留痕
                            {"op": "del", "kind": kind, "key": key})

    def __contains__(self, pair: tuple[str, str]) -> bool:
        """(kind, key) 成员判定(内部/测试辅助)。"""
        kind, key = pair
        return kind in self._index and key in self._index[kind]


__all__ = ["Registry"]
