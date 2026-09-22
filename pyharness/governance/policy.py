"""pyharness/governance/policy.py — 治理层策略一等对象(ADR-018;S2-1 骨架)。

一句话职责:把"规则集 + 求值序 + policy_refs + 声明式参数"提为 Policy 一等对象,
算出内容哈希指纹、管理策略版本、发 policy.updated 事件——**只描述策略,不做决策、
不执行**(G-4/INV-G5;关 2 出口升格属 S3)。

依赖方向(ADR-018:308,强制):本模块**只允许** import ``pyharness.events`` 与
``pyharness.errors``;**禁止** import ``pyharness.core.*``(tools_guard/executor/
scope/llm)。规则的 ``match``/``check`` 可调用对象由装配层注入,本层只**持有**、
不探测来源(Δ-4,见 S2-1_GOVERNANCE_SKELETON_DESIGN.md §1)。类型标注若需引用
``ToolCall``/``Scope`` 一律用 ``Any``——不得用 TYPE_CHECKING 变通(保留 import
即保留方向依赖)。

契约增量(2026-09-14 S2-1 人工裁定;设计与本实现的差异均列于此):
1. **op 取值 = {add, enable, disable}**(Δ-1):``tighten`` 归 ``scope.updated``
   ——禁止 ``policy.updated`` 与 ``scope.updated`` 表达同一种策略变化。
2. **无 ``with_tightened``**(Δ-2):运行时收紧属 ``Scope``;治理动作为
   ``with_rule_enabled`` / ``with_rule_disabled``,禁用必须携带 ``config_ref``。
3. **INV-G6**(Δ-3):策略状态变化必经显式治理动作;任何放宽/禁用必须
   **有 ``policy.updated`` 事件 + 有 ``config_ref`` + 可审计追踪**;禁止静默改变。
4. **``from_config`` 注入式**(Δ-4):``chain_factory`` 与 ``rules`` 由装配层传入,
   治理层不 import ``tools_guard``。
5. **``authorize()`` 不在本步声明**(Δ-5):属 S3 Decision 模型。
6. **``config_ref`` 经 ``Envelope.trace`` 携带**(Δ-6,本步实现选择):沿用
   ``tools_guard``/``approval`` 的"传输元数据走 trace"先例,不扩 payload 模型。
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional

from pyharness.errors import raise_code

log = logging.getLogger("pyharness.governance.policy")

# policy.updated 的唯一合法 op 集(Δ-1:去 tighten、加 enable)
POLICY_OPS: frozenset = frozenset({"add", "enable", "disable"})

# 事件类型名(冻结名:ADR-018 §0.1/契约冻结清单)。词表注册属 S2-2;
# 注册前 emit_updated 按仓库惯例降级(见 PolicyRegistry.emit_updated)。
EVENT_POLICY_UPDATED: str = "policy.updated"

# policy.updated payload 的字段契约(冻结设计 §2.3;S2-2 的 payload 模型须与之一致)
POLICY_UPDATED_FIELDS: tuple[str, ...] = (
    "policy_id", "version", "fingerprint", "op", "added", "reason")


def _is_callable(v: Any) -> bool:
    """函数对象判定(指纹排除面;见 compute_fingerprint)。"""
    return callable(v)


def _canonical_params(params: Optional[Mapping[str, Any]]) -> dict:
    """声明式参数规范化 + 边界校验:含 callable 一律拒(防指纹失稳,R-A)。

    list/tuple/set 归一为**有序**字符串列表——集合序不稳定会让同一配置算出不同
    指纹,直接破坏 M2 判据 ①。
    """
    out: dict = {}
    for k, v in (params or {}).items():
        if _is_callable(v):
            raise_code("CYC-999", module="governance.policy", field=str(k),
                       hint="policy params 只允许声明式值(函数对象不入指纹)")
        if isinstance(v, (list, tuple, set, frozenset)):
            out[str(k)] = sorted(str(x) for x in v)
        else:
            out[str(k)] = v
    return out


def compute_fingerprint(*, policy_id: str, version: str,
                        rules: Iterable["PolicyRule"],
                        disabled: Iterable[str],
                        params: Optional[Mapping[str, Any]]) -> str:
    """策略内容哈希 = sha256(规范化 JSON)。

    ★ **只含声明式内容**:policy_id / version / **元组序** rule_id / policy_refs /
      forced / disabled / params。
    ★ **排除 match/check**(函数对象):否则每次装配指纹都变,M2 判据 ①
      ("指纹随规则内容变化")会退化为"每次都变"(R-A)。
    """
    payload = {
        "policy_id": str(policy_id),
        "version": str(version),
        "rules": [{"rule_id": r.rule_id,
                   "policy_refs": list(r.policy_refs),
                   "forced": bool(r.forced)} for r in rules],
        "disabled": sorted(str(x) for x in disabled),
        "params": _canonical_params(params),
    }
    canon = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":"), default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _disabled_from_cfg(cfg: Any) -> tuple[str, ...]:
    """读 ``cfg.security.guards.disabled``(声明的初始禁用面;只读,容错)。

    与既有 ``tools_guard.from_config`` 同源键面(ADR-019 的 W1 修复面),
    此处仅为策略指纹提供"初始 disabled 集",**不做装配**(装配在 S2-3)。
    """
    node = getattr(getattr(cfg, "security", None), "guards", None)
    vals = getattr(node, "disabled", None) if node is not None else None
    return tuple(str(x) for x in (vals or ()))


@dataclass(frozen=True)
class PolicyRule:
    """单条策略规则描述(``match``/``check`` 由装配层注入,本层只持有)。

    ``rule_id`` 全链唯一(重名注册被拒 TLB-801);``policy_refs`` 是该规则可能产出的
    策略引用(如 ("POL-FS-1","POL-FS-2","POL-FS-3")),供审计与凭证引用;
    ``forced`` 对应 ``tools_guard.FORCED_GUARDS``(恒在不可禁)。
    """

    rule_id: str
    policy_refs: tuple[str, ...] = ()
    match: Any = None
    check: Any = None
    forced: bool = False

    @classmethod
    def from_descriptor(cls, desc: Any) -> "PolicyRule":
        """鸭子描述符(如 ``tools_guard.RuleDescriptor``)→ PolicyRule。

        只按属性读取,**不 import 描述符来源模块**(Δ-4:治理层不得反向依赖 core);
        缺 ``rule_id`` 视为非法描述 → TLB-801。
        """
        rid = getattr(desc, "rule_id", None)
        if not isinstance(rid, str) or not rid:
            raise_code("TLB-801", rule=type(desc).__name__,
                       advice="规则描述符缺 rule_id(非法描述);须含 "
                              "rule_id/policy_refs/forced/match/check")
        refs = tuple(str(r) for r in (getattr(desc, "policy_refs", ()) or ()))
        return cls(rule_id=rid, policy_refs=refs,
                   match=getattr(desc, "match", None),
                   check=getattr(desc, "check", None),
                   forced=bool(getattr(desc, "forced", False)))


@dataclass(frozen=True)
class Policy:
    """策略集**一等对象**(不可变):规则 + 求值序 + 禁用面 + 声明式参数 + 指纹。

    ``rules`` 的**元组序即求值序**(对齐 ``tools_guard._BUILTIN_IDS``);
    ``fingerprint`` 在构造期算定(见 ``compute_fingerprint``),内容变则指纹变。
    派生(禁用/启用)只经 ``with_rule_*`` 返回**新对象**,不改本对象。
    """

    policy_id: str
    version: str
    rules: tuple[PolicyRule, ...] = ()
    disabled: frozenset = frozenset()
    params: Mapping[str, Any] = field(default_factory=dict)
    fingerprint: str = ""

    def __post_init__(self) -> None:
        if not self.fingerprint:                     # 构造即算(不可变对象)
            object.__setattr__(self, "fingerprint", compute_fingerprint(
                policy_id=self.policy_id, version=self.version,
                rules=self.rules, disabled=self.disabled, params=self.params))

    def enabled_rules(self) -> tuple[PolicyRule, ...]:
        """当前生效规则(排除禁用项);求值序保持。"""
        return tuple(r for r in self.rules if r.rule_id not in self.disabled)

    def rule_of(self, rule_id: str) -> Optional[PolicyRule]:
        return next((r for r in self.rules if r.rule_id == rule_id), None)

    def _derive(self, *, disabled: frozenset) -> "Policy":
        """派生新 Policy(重算指纹;原对象不变)。"""
        return Policy(policy_id=self.policy_id, version=self.version,
                      rules=self.rules, disabled=disabled, params=self.params)

    def with_rule_disabled(self, rule_id: str, *, config_ref: str) -> "Policy":
        """禁用一条规则(治理动作,Δ-2)。

        必填 ``config_ref``(谁在何处声明了这次放宽)——缺则 CFG-601 拒;
        规则不在策略内 → CFG-601;``forced`` 规则恒在不可禁 → CFG-601
        (与既有 ``GuardChain.disable`` 的 FORCED_GUARDS 复核同源)。
        幂等:已禁用 → 原样返回(不产生新对象,不重复留痕)。
        """
        if not str(config_ref or "").strip():
            raise_code("CFG-601", reason="越权", fields=["config_ref"],
                       detail="禁用规则必须携带 config_ref(治理动作须可审计,"
                              "INV-G6)")
        rule = self.rule_of(rule_id)
        if rule is None:
            raise_code("CFG-601", guard=rule_id,
                       why="规则不在当前策略内(禁用无从谈起)")
        if rule.forced:
            raise_code("CFG-601", guard=rule_id,
                       why="forced 规则恒在不可禁(对应 FORCED_GUARDS)")
        if rule_id in self.disabled:
            return self                              # 幂等:不重复留痕
        return self._derive(disabled=self.disabled | {rule_id})

    def with_rule_enabled(self, rule_id: str) -> "Policy":
        """重新启用一条规则(治理动作,Δ-2)。幂等:未禁用 → 原样返回。"""
        if rule_id not in self.disabled:
            return self
        return self._derive(disabled=self.disabled - {rule_id})


class PolicyRegistry:
    """规则注册表(只增链尾,延续 ``register_plugin_guard`` 单调性)。

    重名注册被拒(TLB-801);无移除/重排 API——规则面的变更只经 Policy 的
    治理动作表达,并落 ``policy.updated``。
    """

    def __init__(self) -> None:
        self._rules: list[PolicyRule] = []

    def register_rule(self, rule: Any) -> None:
        """登记一条规则(``PolicyRule`` 或同形描述符);重名 → TLB-801。"""
        if not isinstance(rule, PolicyRule):
            rule = PolicyRule.from_descriptor(rule)
        if any(r.rule_id == rule.rule_id for r in self._rules):
            raise_code("TLB-801", rule=rule.rule_id,
                       advice="rule_id 全链唯一(重复注册被拒)")
        self._rules.append(rule)

    def rules(self) -> tuple[PolicyRule, ...]:
        """已登记规则(登记序 = 求值序)。"""
        return tuple(self._rules)

    async def emit_updated(self, policy: Policy, op: str, *, reason: str,
                           config_ref: Optional[str] = None,
                           session: Any = None) -> bool:
        """发 ``policy.updated``(强同步,Q3)。返回 True=已落盘,False=降级未发。

        - **op 白名单**:非 ``POLICY_OPS`` → CYC-999 拒(Δ-1);
        - **op=disable 必带 config_ref** → 缺则 CFG-601 拒(Δ-3:禁止静默放宽);
        - **词表未注册时降级**(S2-1 阶段常态):记日志、不抛——与
          ``approval._emit_trust``/``tools_guard._record`` 同款惯例;S2-2 注册
          ``policy.updated`` 后**同路径自动生效,不需改调用方**;
        - ``config_ref`` 经 ``Envelope.trace`` 携带(Δ-6)。
        """
        if op not in POLICY_OPS:
            raise_code("CYC-999", module="governance.policy", op=str(op),
                       hint=f"policy.updated 的 op 必须 ∈ {sorted(POLICY_OPS)}"
                            "(tighten 归 scope.updated)")
        if op == "disable" and not str(config_ref or "").strip():
            raise_code("CFG-601", reason="越权", fields=["config_ref"],
                       detail="op=disable 必须携带 config_ref(禁止静默放宽)")
        from pyharness.events.vocab import is_registered   # 允许:events(ADR-018)
        if not is_registered(EVENT_POLICY_UPDATED):
            log.debug("policy.updated 未入词表(S2-2 注册),降级未发 op=%s", op)
            return False
        if session is None:
            log.warning("policy.updated 未接线 session,降级未发 op=%s", op)
            return False
        payload = {
            "policy_id": policy.policy_id,
            "version": policy.version,
            "fingerprint": policy.fingerprint,
            "op": op,
            "added": sorted(policy.disabled) if op == "disable" else [],
            "reason": str(reason),
        }
        trace = {"config_ref": str(config_ref)} if config_ref else None
        await session.append(EVENT_POLICY_UPDATED, payload, actor="system",
                             sync=True, trace=trace)
        return True


class PolicyEngine:
    """策略的装配、解析与版本治理。**只提供策略,不做决策、不执行**。

    构造经 ``from_config``(Δ-4 注入式);执行对象(``GuardChain``)由装配层经
    ``chain_factory`` 注入——治理层只持有引用,``chain`` 为 None 时表示"尚未接线
    (S2-1 常态)"。
    """

    def __init__(self, *, policy: Policy, registry: PolicyRegistry,
                 session: Any = None, bus: Any = None, chain: Any = None,
                 chain_factory: Any = None) -> None:
        self._policy: Policy = policy
        self._registry: PolicyRegistry = registry
        self._session: Any = session
        self._bus: Any = bus
        self._chain: Any = chain
        self._chain_factory: Any = chain_factory

    # ------------------------------------------------------------ 装配
    @classmethod
    def from_config(cls, cfg: Any, *, session: Any = None, bus: Any = None,
                    rules: Iterable[Any] = (), params: Optional[Mapping] = None,
                    chain_factory: Any = None,
                    validator: Any = None, credential_paths: Any = None,
                    path_exists: Any = None, link_resolver: Any = None,
                    approval_channel: Optional[bool] = None,
                    policy_id: str = "builtin:v1",
                    version: str = "1.0.0") -> "PolicyEngine":
        """装配策略引擎(**注入式**,Δ-4)。

        - ``rules``:规则描述(``PolicyRule`` 或鸭子描述符,如
          ``tools_guard.describe_rules()`` 的产物)→ 登记进取注册表,登记序 = 求值序;
        - ``chain_factory``:执行对象的构造入口,由装配层传入
          (生产值 = ``tools_guard.from_config``);**治理层不 import 它**;
        - ``validator``/``credential_paths``/``path_exists``/``link_resolver``/
          ``approval_channel``:透传给 ``chain_factory``,并把**其声明式形态**
          (布尔/清单/注入存在性)纳入指纹 params——函数对象本身不入指纹;
        - 初始禁用面读 ``cfg.security.guards.disabled``(只读)。
        """
        registry = PolicyRegistry()
        for r in rules:
            registry.register_rule(r)
        p = _canonical_params(params)
        if approval_channel is not None:
            p["approval_channel"] = bool(approval_channel)
        if credential_paths is not None:
            p["credential_paths"] = sorted(str(x) for x in credential_paths)
        p["injected"] = sorted(n for n, v in (("validator", validator),
                                              ("path_exists", path_exists),
                                              ("link_resolver", link_resolver))
                               if v is not None)
        policy = Policy(policy_id=policy_id, version=version,
                        rules=registry.rules(),
                        disabled=frozenset(_disabled_from_cfg(cfg)),
                        params=p)
        chain = None
        if callable(chain_factory):
            chain = chain_factory(cfg, session=session, bus=bus,
                                  validator=validator,
                                  credential_paths=credential_paths,
                                  path_exists=path_exists,
                                  link_resolver=link_resolver,
                                  approval_channel=approval_channel)
        return cls(policy=policy, registry=registry, session=session, bus=bus,
                   chain=chain, chain_factory=chain_factory)

    # ------------------------------------------------------------ 只读解析
    def current(self, scope: Any = None) -> Policy:
        """会话当前绑定的策略(纯只读)。M2 单策略集 → 返回本引擎的 Policy。"""
        return self._policy

    def resolve(self, call: Any, scope: Any = None) -> Policy:
        """解析调用命中的策略(纯只读)。M2:恒为 ``current()``——
        规则级命中在 S3 的 ``DecisionEngine`` 中展开(本步不做决策)。"""
        return self.current(scope)

    def fingerprint(self, policy: Optional[Policy] = None) -> str:
        """策略指纹(内容哈希);缺省取当前策略。"""
        return (policy or self._policy).fingerprint

    def registry(self) -> PolicyRegistry:
        return self._registry

    @property
    def policy(self) -> Policy:
        return self._policy

    @property
    def chain(self) -> Any:
        """执行对象(未接线 → None;S2-1 常态)。治理层不执行它。"""
        return self._chain

    # ------------------------------------------------------------ 治理动作
    async def disable_rule(self, rule_id: str, *, config_ref: str,
                           reason: str = "guard-disabled") -> Policy:
        """禁用一条规则并留痕(Δ-2/Δ-3):Policy 派生 + 发 policy.updated(op=disable)。

        返回新 Policy(已禁用);事件未注册/未接线时 ``emit_updated`` 降级返回
        False,**策略状态仍已派生**——留痕缺口由 S2-2 注册后消除(此前为降级态)。
        """
        new_policy = self._policy.with_rule_disabled(rule_id,
                                                     config_ref=config_ref)
        if new_policy is not self._policy:
            self._policy = new_policy
            await self._registry.emit_updated(new_policy, "disable",
                                              reason=reason,
                                              config_ref=config_ref,
                                              session=self._session)
        return self._policy

    async def enable_rule(self, rule_id: str, *,
                          reason: str = "guard-enabled") -> Policy:
        """重新启用一条规则并留痕:Policy 派生 + 发 policy.updated(op=enable)。"""
        new_policy = self._policy.with_rule_enabled(rule_id)
        if new_policy is not self._policy:
            self._policy = new_policy
            await self._registry.emit_updated(new_policy, "enable",
                                              reason=reason,
                                              session=self._session)
        return self._policy

    async def emit_assembled(self, *, reason: str = "assembly") -> bool:
        """装配期留痕(op=add;S2-3 装配后调用)。未注册/未接线 → 降级 False。"""
        return await self._registry.emit_updated(self._policy, "add",
                                                 reason=reason,
                                                 session=self._session)


__all__ = [
    "POLICY_OPS", "EVENT_POLICY_UPDATED", "POLICY_UPDATED_FIELDS",
    "PolicyRule", "Policy", "PolicyRegistry", "PolicyEngine",
    "compute_fingerprint",
]
