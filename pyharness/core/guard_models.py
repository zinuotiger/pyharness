"""Guard models: complete migrated implementation."""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Optional


log = logging.getLogger("pyharness.tools_guard")

_DECISIONS = ("allow", "reject", "approval")

_AUDIT_WORD = {"allow": "allow", "reject": "deny", "approval": "need_approval"}

DANGER_LEVELS: tuple[str, ...] = ("none", "low", "high", "critical")

FORCED_GUARDS: frozenset = frozenset({"g-schema", "g-danger"})

FS_DOMAINS: frozenset = frozenset({"fs", "workspace"})

NET_DOMAINS: frozenset = frozenset({"web", "net"})

_READ_PREFIXES: tuple[str, ...] = ("fs.read", "workspace.read")

_WRITE_PREFIXES: tuple[str, ...] = ("fs.write", "workspace.write")

_EXEC_PREFIXES: tuple[str, ...] = ("exec.",)

DEFAULT_CREDENTIAL_FILES: tuple[str, ...] = ("~/.pyharness/credentials.yaml",)

_CRED_BASENAMES: tuple[str, ...] = ("credentials.yaml", "credentials.yml")

class Decision(StrEnum):
    """guard 决策三值(原则 3;无 bypass 值)。

    StrEnum:Decision.REJECT == "reject" 成立(executor 伪码 `d == "reject"`
    直接比较可用,无需 .value)。语义:
        ALLOW    放行,继续下一 guard 或执行(仅"本 guard 不反对")
        REJECT   终局拒绝(guard.rejected 强同步;零副作用;无续跑 API)
        APPROVAL 请求人类裁决(仅 danger=high 且有通道;critical 强制转 reject)
    """

    ALLOW = "allow"
    REJECT = "reject"
    APPROVAL = "approval"

def danger_default_policy(danger: str, *, has_channel: bool) -> Decision:
    """danger 分级默认策略机器表达(SECURITY §4.2;headless=R8)。

    none/low → allow;high → (has_channel ? approval : reject);critical → reject
    (不可审批,无审批通道是刻意设计——人类真想删须下调沙箱留事件而非弹窗点
    同意,POL-DGR-1)。供 g-danger/装配层/测试消费;危险级非法值按 none 处理。
    """
    if danger == "high":
        return Decision.APPROVAL if has_channel else Decision.REJECT
    if danger == "critical":
        return Decision.REJECT
    return Decision.ALLOW  # none/low/未知 → 放行(分级信息由 match 层过滤)

@dataclass(frozen=True)
class ToolCall:
    """待裁决工具调用(最小契约;与 tools_executor.parse_tool_call 同构)。

    字段:name = 工具名;raw_args = LLM 原始参数(审计/复查用);call_id = 调用
    id(关联 tool.call/result);parent_seq = 触发它的 llm.response seq;
    defn = 工具契约引用(只读 danger/guard_hooks 等;tools_registry 落地前可为
    任意含 danger 属性的鸭子对象/None);args = F026 校验后实参(executor 填入;
    未填时回落 raw_args,guard 按动作形态读路径/域名/shell 开关)。
    """

    name: str
    raw_args: dict = field(default_factory=dict)
    call_id: str = ""
    parent_seq: Optional[int] = None
    defn: Any = None
    args: Optional[dict] = None

    @property
    def safe_args(self) -> dict:
        """guard 读参面:F026 后实参优先,未校验回落 raw_args(只读不写)。"""
        return self.args if isinstance(self.args, dict) else self.raw_args

class Guard:
    """Guard 契约基类(DIS-SEAM §5.2;插件 guard 继承本类,只能加拒绝面)。

    id    事件里出现的 guard_id(全链唯一,如 "g-fs-path")
    match(call) -> bool                 管不管该工具(按名前缀/Definition 结构,
                                        不信任 description 文本——防"低危声明+
                                        高危实现"伪装工具)
    check(call, scope) -> (decision, policy_ref)
                                        判定;decision ∈ allow/reject/approval;
                                        policy_ref 如 POL-FS-1(审计 reasons 用);
                                        不读 description
    """

    id: str = "guard"

    def match(self, call: Any) -> bool:
        """是否管辖该调用;缺省不管辖(插件须显式实现)。"""
        return False

    async def check(self, call: Any, scope: Any) -> tuple[str, Optional[str]]:
        """判定;缺省不反对(子类覆盖;返回 (allow, None))。"""
        return ("allow", None)

    # 便于单测/插件断言契约的只读名(防把 Guard 当 bypass 门)
    @property
    def allows_nothing_extra(self) -> bool:
        """只拒绝不放行标记:guard 无任何"放行某调用"的旁路能力(恒 True)。"""
        return True

def _policy_of(scope: Any) -> Any:
    """取策略面:Scope 实例回落 .policy,ScopePolicy 原样(鸭子兼容)。"""
    return getattr(scope, "policy", None) or scope

def _danger_of(call: Any) -> str:
    """读工具契约 danger(五级枚举;defn 缺失/非法 → none,按最安全读取)。"""
    d = getattr(getattr(call, "defn", None), "danger", None)
    return d if d in DANGER_LEVELS else "none"

def _is_domain(prefix: str, name: str) -> bool:
    """工具名前缀域判定(fs./web./exec.…;无点 = 单字工具不入文件/网络域)。"""
    return name.split(".", 1)[0] == prefix if "." in name else False

# Preserve historical import and pickle identities through the compatibility facade.
Decision.__module__ = 'pyharness.core.tools_guard'
ToolCall.__module__ = 'pyharness.core.tools_guard'
Guard.__module__ = 'pyharness.core.tools_guard'
