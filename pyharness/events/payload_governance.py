"""Payload governance: complete migrated implementation."""
from __future__ import annotations
from typing import Any, Optional
from pydantic import Field, field_validator

from pyharness.events.payload_base import _EVIDENCE_REF_KINDS, _PayloadBase

class ContextCompactedPayload(_PayloadBase):
    """context.compacted(强同步):ranges=折叠闭区间列表,区间合法且互不重叠。"""
    ranges: list[list[int]] = Field(min_length=1)
    summary: str = Field(min_length=1)
    tokens_before: Optional[int] = Field(default=None, ge=0)
    tokens_after: Optional[int] = Field(default=None, ge=0)

    @field_validator("ranges")
    @classmethod
    def _ranges_sane(cls, v: list[list[int]]) -> list[list[int]]:
        """区间必须 [lo, hi](lo≤hi、lo≥1)且两两不重叠。"""
        ordered: list[list[int]] = []
        for pair in v:
            if len(pair) != 2:
                raise ValueError(f"区间须为 [lo, hi] 二元组:{pair}")
            lo, hi = pair
            if lo < 1:
                raise ValueError(f"seq 从 1 起,lo 非法:{pair}")
            if lo > hi:
                raise ValueError(f"lo 不得大于 hi:{pair}")
            ordered.append(pair)
        ordered.sort(key=lambda p: p[0])
        for (_, prev_hi), (cur_lo, _) in zip(ordered, ordered[1:]):
            if cur_lo <= prev_hi:
                raise ValueError("ranges 区间不得重叠")
        return v

class PluginInstalledPayload(_PayloadBase):
    """plugin.installed:api_version 不匹配拒装载(BUS-002 保留名校验在注册层)。"""
    plugin_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    api_version: str = Field(min_length=1)

class PluginUninstalledPayload(_PayloadBase):
    """plugin.uninstalled:非 running 态才可卸载(BusyUninstall)。"""
    plugin_id: str = Field(min_length=1)

class BusBackpressurePayload(_PayloadBase):
    """bus.backpressure:在途 >1000 才发;禁静默丢事件。"""
    sender: str = Field(min_length=1)
    dropped: int = Field(ge=0)
    sample: Optional[list] = Field(default=None, max_length=100)   # ≤100 死信样本

class SystemCancelledPayload(_PayloadBase):
    """system.cancelled:取消审计(防"以为停了还在跑")。"""
    what: str = Field(min_length=1)
    reason: Optional[str] = None

class SystemErrorPayload(_PayloadBase):
    """system.error:code 必注册(F019);hint 人读指引;ctx 结构化现场。"""
    code: str = Field(min_length=1)
    hint: str = Field(min_length=1)
    ctx: Optional[dict] = None

class TodoItem(_PayloadBase):
    """todo 单项:{id, text, done};done 重复幂等由状态层保证。"""
    id: int
    text: str = Field(min_length=1)
    done: bool

class TodoUpdatedPayload(_PayloadBase):
    """todo.updated:每任务 ≤20 项;compaction 清 done 项。"""
    task_id: str = Field(min_length=1)
    todos: list[TodoItem] = Field(default_factory=list, max_length=20)

class SyscheckFailPayload(_PayloadBase):
    """syscheck.fail:自检发现(SEQ-GAP/NO-GUARD-EVENT 等),失败不杀进程提示 repair。"""
    findings: list = Field(min_length=1)
    trigger: Optional[str] = None

class BudgetPausedPayload(_PayloadBase):
    """budget.paused:F032 预算硬闸首达 exhausted 落事件(事件即事实,防刷)。"""
    state: str = Field(min_length=1)
    used: dict = Field(default_factory=dict)

class UserQuestionPayload(_PayloadBase):
    """user.question:#38 agent 反问用户(带选项);ask_id = 本事件 seq。"""
    question: str = Field(min_length=1)
    options: list[str] = Field(default_factory=list, max_length=10)
    ttl_s: int = Field(120, ge=5, le=3600)

class UserAnswerPayload(_PayloadBase):
    """user.answer:人对 user.question 的答复(choice 命中选项/自由文本)。"""
    ask_id: int = Field(ge=1)
    choice: Optional[str] = Field(default=None, max_length=500)
    text: Optional[str] = Field(default=None, max_length=2000)
    by: str = Field(default="user", min_length=1)

class ScopeUpdatedPayload(_PayloadBase):
    """scope.updated:F014 策略单调收紧留痕(op=tighten;只增不改,防刷日志)。"""
    op: str = Field(min_length=1)
    added: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)

class PolicyUpdatedPayload(_PayloadBase):
    """policy.updated:治理层策略集变化留痕(ADR-020)。

    op ∈ {add, enable, disable}——**tighten 归 scope.updated**,禁止两事件表达同一种
    策略变化;`guard.disabled` 不单独登记,关闭 guard 统一经 op="disable"。
    config_ref 经 Envelope.trace 携带(ADR-020 Δ-6),**不入本载荷**——与
    tools_guard 的 call_id / approval 的 channel 同款"传输元数据走 trace"先例。

    六字段与 governance.policy.POLICY_UPDATED_FIELDS 一致(S2-1 声明,S2-2 对齐);
    op 用 str 而非 Literal:值域校验在治理层(避免 events 反向依赖 governance,
    也避免复制 POLICY_OPS 成第二真源);参照 ScopeUpdatedPayload 同型。
    """
    policy_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    fingerprint: str = Field(min_length=1)
    op: str = Field(min_length=1)
    added: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)

class DecisionIssuedPayload(_PayloadBase):
    """decision.issued:治理层决策事件(ADR-015;M3/S3-2-2)。

    与 ``guard.evaluated``(**规则级**:哪些规则参与求值)并存,本事件是
    **治理级**:谁在什么策略下作出什么决定、依据哪些策略引用、针对哪份输入。
    两者经 ``Envelope.trace.call_id`` 双向关联(与 tool.call 同源)。

    - ``verdict`` ∈ ``allow|reject|approval``——**不得**使用 ``guard.evaluated``
      载荷的审计别名(``deny`` / ``need_approval``);用 ``str`` 而非 ``Literal``:
      值域校验在治理层,避免 events 反向依赖 governance 或复制值域成第二真源
      (与 ``PolicyUpdatedPayload.op`` 同因)。
    - ``principal_*`` **扁平三字段**:沿用本文件平坦字段风格,不引入嵌套模型,
      避免复制新的嵌套 schema(嵌套 identity 对象留 S4 Receipt 再议)。
    - ``call_id`` 走 ``Envelope.trace``(**不入本载荷**)——"传输元数据走 trace"
      既有先例。
    - 字段与 ``governance.decision.Decision`` 一一对应;``supersedes`` 表达审批
      重入时后一决策对前一决策的替代关系(无前序为空)。
    """
    decision_id: str = Field(min_length=1)
    verdict: str = Field(min_length=1)
    tool: str = ""
    guard_ids: list[str] = Field(default_factory=list)
    policy_refs: list[str] = Field(default_factory=list)
    policy_fingerprint: str = ""
    inputs_digest: str = ""
    principal_kind: str = Field(min_length=1)
    principal_id: str = Field(min_length=1)
    principal_channel: Optional[str] = None
    ts: str = ""
    approval_ref: Optional[int] = None
    supersedes: Optional[str] = None

class ReceiptEmittedPayload(_PayloadBase):
    """receipt.emitted:治理凭证生成事实(设计 §3.4;M4)。

    **薄引用 + 完整性链**——只承载凭证自身的定位与防篡改信息,不复制 Decision
    字段(违 INV-G4"只含引用与哈希"):

    - ``receipt_id`` / ``decision_id``:凭证主键与所绑定的决策(外键);
    - ``kind`` ∈ ``decision|approval``;
    - ``digest`` = **`Receipt.content_hash`**(R-1(a) 冻结:与相邻 ``prev_hash``
      同属完整性链;**不是** ``inputs_digest``——后者表达"本次授权针对什么 inputs");
    - ``prev_hash`` = 前一条 receipt 的 ``content_hash``(创世为 ``None``)。

    完整的 ``DecisionReceipt`` 字段由 ``decision_id`` 指向的 ``decision.issued``
    **派生**,故事件日志仍为唯一真源。强同步(与 decision.issued 同族)。
    """
    receipt_id: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    digest: str = Field(min_length=1)
    prev_hash: Optional[str] = None

class EvidenceArchivedPayload(_PayloadBase):
    """evidence.archived:治理证据归档事实(M6;S5-1)。

    **只存引用,不复制事件内容**(INV-G4 / INV-E1):

    - ``evidence_id``:工件主键;
    - ``claim``:被支持的断言;
    - ``refs``:指向**真源**的引用列表,每项 ``{"kind": <enum>, "locator": str}``。
      顶层 ``extra="forbid"`` 不约束嵌套 dict,故此处用 ``field_validator`` 把
      内层严格性补回(键集恰为两键 + kind 值域 + locator 非空);
    - ``artifact_path``:可选**外部附件**引用——**不承载治理事实**(不得作为
      治理判定输入;缺失/损坏不影响任何不变量)。

    **通道 = 普通(攒批),非强同步**(EVENT-SCHEMA §3.6 冻结):证据是**索引**,
    丢失可由既有事件**再派生**(INV-E3)。
    """
    evidence_id: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    refs: list = Field(default_factory=list)
    artifact_path: Optional[str] = None

    @field_validator("refs")
    @classmethod
    def _check_refs(cls, v: Any) -> Any:
        for item in v or ():
            if not isinstance(item, dict) or set(item) != {"kind", "locator"}:
                raise ValueError("refs 每项必须是 {kind, locator} 且仅此两键")
            if item.get("kind") not in _EVIDENCE_REF_KINDS:
                raise ValueError(f"refs.kind 必须是 {_EVIDENCE_REF_KINDS} 之一")
            loc = item.get("locator")
            if not isinstance(loc, str) or not loc:
                raise ValueError("refs.locator 必须为非空字符串")
        return v

class SkillInstalledPayload(_PayloadBase):
    """skill.installed: remote skill safely verified and activated."""
    name: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=64)
    sha256: str = Field(min_length=64, max_length=64)
    source: str = Field(default="", max_length=2000)
    approved_by: str = Field(default="user", min_length=1, max_length=64)

class SkillRemovedPayload(_PayloadBase):
    """skill.removed: installed skill removed from active root."""
    name: str = Field(min_length=1, max_length=64)
    version: str = Field(default="", max_length=64)
    approved_by: str = Field(default="user", min_length=1, max_length=64)

class SkillRolledBackPayload(_PayloadBase):
    """skill.rollback: active skill switched to a previous verified version."""
    name: str = Field(min_length=1, max_length=64)
    from_version: str = Field(default="", max_length=64)
    to_version: str = Field(min_length=1, max_length=64)
    approved_by: str = Field(default="user", min_length=1, max_length=64)

class SkillUsedPayload(_PayloadBase):
    """skill.used:F073 技能装载审计(谁在何时用了哪个技能;body 不进日志防刷)。"""
    name: str = Field(min_length=1, max_length=64)

class ConfigUpdatedPayload(_PayloadBase):
    """config.updated(瞬时,仅总线):热更审计,值已脱敏,防刷屏限长。"""
    key: str = Field(min_length=1, max_length=128)
    old: str = Field(default="", max_length=2000)
    new: str = Field(default="", max_length=2000)
    by: str = Field(default="system", min_length=1, max_length=64)

# Preserve historical import and pickle identities through the compatibility facade.
ContextCompactedPayload.__module__ = 'pyharness.events.payload'
PluginInstalledPayload.__module__ = 'pyharness.events.payload'
PluginUninstalledPayload.__module__ = 'pyharness.events.payload'
BusBackpressurePayload.__module__ = 'pyharness.events.payload'
SystemCancelledPayload.__module__ = 'pyharness.events.payload'
SystemErrorPayload.__module__ = 'pyharness.events.payload'
TodoItem.__module__ = 'pyharness.events.payload'
TodoUpdatedPayload.__module__ = 'pyharness.events.payload'
SyscheckFailPayload.__module__ = 'pyharness.events.payload'
BudgetPausedPayload.__module__ = 'pyharness.events.payload'
UserQuestionPayload.__module__ = 'pyharness.events.payload'
UserAnswerPayload.__module__ = 'pyharness.events.payload'
ScopeUpdatedPayload.__module__ = 'pyharness.events.payload'
PolicyUpdatedPayload.__module__ = 'pyharness.events.payload'
DecisionIssuedPayload.__module__ = 'pyharness.events.payload'
ReceiptEmittedPayload.__module__ = 'pyharness.events.payload'
EvidenceArchivedPayload.__module__ = 'pyharness.events.payload'
SkillInstalledPayload.__module__ = 'pyharness.events.payload'
SkillRemovedPayload.__module__ = 'pyharness.events.payload'
SkillRolledBackPayload.__module__ = 'pyharness.events.payload'
SkillUsedPayload.__module__ = 'pyharness.events.payload'
ConfigUpdatedPayload.__module__ = 'pyharness.events.payload'
