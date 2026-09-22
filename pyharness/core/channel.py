"""pyharness/core/channel.py — 通道解析唯一实现(Channel Contract;N5)。

**为什么要这一层**:修复前"通道"在 6 层被**独立解释**了 5 次
(Service 的 ``or`` 合并、Engine 的硬编码默认、Approval 的**精确名**匹配 + 回落、
Governance 的 ``hasattr`` 哨兵 + ``or`` 合并),导致同一 ctx 状态在不同层得到
**互相矛盾**的判定。实测反例:``"acp:client-7"`` 在 ``approval._ensure_channel``
被判为 headless,而在同一文件的 ``_require_human`` 被接受;``""`` 在治理层静默
降级为 ``system``。

本模块给出**唯一**的"原始值 → 语义"映射。核心层(approval/service/engine)直接
消费它;治理层因 ADR-018:308 依赖方向禁令**不得** import ``pyharness.core.*``,
故经其**已有的** ``Principal.from_legacy_by`` 分类消费(该函数已按前缀正确识别
``acp:<id>``),并由 ``tests/invariants`` 的真值表断言二者**逐状态一致**
(沿用 ``decision.HUMAN_CHANNELS`` 的"受守卫的重复"先例)。

**五种状态的冻结语义**(任何改动都是契约变更):

===========  ==========================================  ==========================
状态          判定条件                                    语义
===========  ==========================================  ==========================
MISSING      ``ctx`` 无 ``channel`` 属性(从未声明)          **fail-closed(APR-503)**
HEADLESS     ``ctx.channel is None``(显式声明无人类通道)    合法:SYSTEM 主体,无审批通道
INTERACTIVE  ``"<name>"`` 或 ``"<name>:<id>"``,             合法:人类通道,可发起审批
             ``name ∈ HUMAN_CHANNELS``
INVALID      非空字符串但 ``name ∉ HUMAN_CHANNELS``        **fail-closed(APR-503)**
UNKNOWN      空串 ``""``(或非字符串)                      **fail-closed(APR-503)**
===========  ==========================================  ==========================

**归一化规则(关键)**::``"acp:client-7"`` → ``name="acp"``, ``ident="client-7"``
—— **保留 ACP 身份**,既**不降级为 desktop**,也**不降级为 headless**。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Optional

from pyharness.errors import raise_code

# 人类交互通道白名单(与 ``core/approval.CHANNELS``、``governance.decision.HUMAN_CHANNELS``
# 同源;三处相等由一致性测试断言)。
HUMAN_CHANNELS: tuple[str, ...] = ("cli", "web", "acp", "desktop")

# 无人类通道时的框架主体(沿用 approval 侧 ``by="system"`` 既有语义,非新规则)。
SYSTEM_BY: str = "system"


class ChannelKind(StrEnum):
    """通道原始值的五分类(见模块 docstring 的冻结语义表)。"""

    MISSING = "missing"          # 属性缺失 ⇒ fail-closed
    HEADLESS = "headless"        # 显式 None ⇒ 合法无通道
    INTERACTIVE = "interactive"  # 合法人类通道
    INVALID = "invalid"          # 非空串且前缀不在白名单 ⇒ fail-closed
    UNKNOWN = "unknown"          # 空串/非字符串 ⇒ fail-closed


@dataclass(frozen=True)
class ChannelState:
    """解析结果(不可变)。``raw`` 保留原值以便报错时如实呈现。"""

    kind: ChannelKind
    name: Optional[str] = None    # 归一化通道名(INTERACTIVE 时非 None)
    ident: Optional[str] = None   # 主体 id(``acp:c7`` → ``"c7"``;裸名 → 同 name)
    raw: Any = None

    @property
    def is_interactive(self) -> bool:
        return self.kind is ChannelKind.INTERACTIVE

    @property
    def is_headless(self) -> bool:
        return self.kind is ChannelKind.HEADLESS

    @property
    def is_acceptable(self) -> bool:
        """可接受 = 合法状态(HEADLESS 或 INTERACTIVE)。其余三种 fail-closed。"""
        return self.kind in (ChannelKind.HEADLESS, ChannelKind.INTERACTIVE)


def normalize_channel(raw: Any) -> ChannelState:
    """原始值 → ``ChannelState``(**纯函数,无副作用,不抛**)。

    调用方按 ``kind`` 自行决定 fail-closed,使本函数可在需要"分类而非拒绝"的
    场景(如装配期留痕)复用。
    """
    if raw is None:
        return ChannelState(ChannelKind.HEADLESS, raw=None)
    if not isinstance(raw, str):
        return ChannelState(ChannelKind.UNKNOWN, raw=raw)
    if raw == "":
        return ChannelState(ChannelKind.UNKNOWN, raw=raw)
    name, sep, ident = raw.partition(":")
    if name not in HUMAN_CHANNELS:
        return ChannelState(ChannelKind.INVALID, raw=raw)
    if sep and ident == "":
        # ``"desktop:"`` —— 前缀后**缺 id**:与 ``governance.decision.Principal.
        # from_legacy_by`` 的判据一致(它对此抛 APR-503)。不对称会让同一 ctx 状态
        # 在核心层与治理层得到相反判定,正是本契约要消除的分歧。
        return ChannelState(ChannelKind.INVALID, raw=raw)
    # ``"acp:client-7"`` → name="acp", ident="client-7";裸 ``"cli"`` → ident=name
    return ChannelState(ChannelKind.INTERACTIVE, name=name,
                        ident=(ident if sep else name), raw=raw)


def resolve_channel(ctx: Any) -> ChannelState:
    """由 ``ctx`` 解析通道状态(处理"属性缺失"这一维)。"""
    if not hasattr(ctx, "channel"):
        return ChannelState(ChannelKind.MISSING, raw=None)
    return normalize_channel(getattr(ctx, "channel", None))


def require_channel(ctx: Any, *, module: str, field: str = "channel") -> ChannelState:
    """解析并**对不可接受状态 fail-closed**(MISSING / INVALID / UNKNOWN → APR-503)。

    合法状态原样返回:``HEADLESS``(显式无通道)与 ``INTERACTIVE``(人类通道)。
    """
    st = resolve_channel(ctx)
    if st.is_acceptable:
        return st
    why = {
        ChannelKind.MISSING: "ctx.channel 未声明:通道身份缺失,禁止静默降级为 system"
                             "(fail-closed)。外壳须显式声明 "
                             "channel='cli'|'web'|'acp:<id>'|'desktop',或显式声明 "
                             "headless(channel=None)。",
        ChannelKind.INVALID: f"通道名非法(非白名单前缀):{st.raw!r};"
                             f"合法前缀 {HUMAN_CHANNELS}(fail-closed,不静默降级)",
        ChannelKind.UNKNOWN: "通道值为空串/非字符串:视为未知通道,"
                             "禁止 falsy 回退改写声明语义(fail-closed)",
    }[st.kind]
    raise_code("APR-503", module=module, field=field, got=repr(st.raw), why=why)
    raise AssertionError("unreachable")          # raise_code 恒抛;供类型检查收敛


def default_channel_of(value: Any) -> Optional[str]:
    """装配期"缺省通道"归一化(provider 级默认,**非** ctx 回退)。

    ``"acp:<id>"`` → ``"acp"``;``None``/非法/未知 → ``None``。
    用途:``ApprovalProvider`` 的 ``user_choice``/信任名单按"本 provider 服务哪个
    交互外壳"判定。**不得**把它当作 ctx 未声明通道时的回退源(那正是修复前的缺陷)。
    """
    st = normalize_channel(value)
    return st.name if st.is_interactive else None


__all__ = [
    "HUMAN_CHANNELS", "SYSTEM_BY", "ChannelKind", "ChannelState",
    "normalize_channel", "resolve_channel", "require_channel",
    "default_channel_of",
]
