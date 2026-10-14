"""Guard descriptors: complete migrated implementation."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

from pyharness.core.guard_models import FORCED_GUARDS
from pyharness.core.guard_builtin import build_builtin_chain

_RULE_POLICY_REFS: dict[str, tuple[str, ...]] = {
    "g-schema": ("TLB-803",),
    "g-fs-path": ("POL-FS-1", "POL-FS-2", "POL-FS-3"),
    "g-credential-read": ("POL-CRED-1",),
    "g-exec": ("POL-EXEC-1",),
    "g-overwrite": ("POL-OVW-1",),
    "g-net-outbound": ("POL-NET-1",),
    "g-danger": ("POL-DGR-1",),
}

@dataclass(frozen=True)
class RuleDescriptor:
    """内置规则(g1-g7)的**声明式描述**(只读)。

    供治理层构造 Policy 用(治理层不 import 本模块,只按属性读取
    ``rule_id``/``policy_refs``/``forced``/``match``/``check``)。``match``/``check``
    是既有 guard 可调用面的**引用**,判定逻辑仍在 Guard 内——本类不含判定。
    """

    rule_id: str
    policy_refs: tuple[str, ...]
    forced: bool
    match: Callable[[Any], bool]
    check: Callable[[Any, Any], Any]

def describe_rules(*, credential_paths: Optional[Iterable[str]] = None,
                   validator: Optional[Callable[..., dict]] = None,
                   approval_channel: Optional[bool] = None,
                   path_exists: Optional[Callable[[str], bool]] = None,
                   link_resolver: Optional[Callable[[str], str]] = None
                   ) -> tuple[RuleDescriptor, ...]:
    """内置 g1-g7 的声明式描述(求值序 = ``_BUILTIN_IDS``;纯只读)。

    经既有 ``build_builtin_chain`` 取规则实例(注入参数语义与装配完全一致),
    再投影为纯描述——**不构造 GuardChain、不改动任何判定逻辑**。
    由**装配层**调用并把结果传给 ``PolicyEngine.from_config(rules=...)``;
    治理层不 import 本函数(依赖方向单向,ADR-018)。
    """
    guards = build_builtin_chain(credential_paths=credential_paths,
                                 validator=validator,
                                 approval_channel=approval_channel,
                                 path_exists=path_exists,
                                 link_resolver=link_resolver)
    return tuple(
        RuleDescriptor(rule_id=g.id,
                       policy_refs=_RULE_POLICY_REFS.get(g.id, ()),
                       forced=g.id in FORCED_GUARDS,
                       match=g.match, check=g.check)
        for g in guards)

# Preserve historical import and pickle identities through the compatibility facade.
RuleDescriptor.__module__ = 'pyharness.core.tools_guard'
