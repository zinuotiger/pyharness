"""Tool definition: complete migrated implementation."""
from __future__ import annotations
import logging
import re
from typing import TYPE_CHECKING, Any, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pyharness.errors import PyHError
if TYPE_CHECKING:  # 仅类型标注;总线实例鸭子注入(可 None = 未接线)
    pass


log = logging.getLogger("pyharness.tools_registry")

NAME_RE = re.compile(r"^[a-z][a-z0-9_.]{1,63}$")

RESERVED: frozenset = frozenset({
    "agent-loop", "tools", "session", "llm", "system-prompt", "system_prompt",
    "scope", "agent", "persistence",
})

INJECTION_MARKERS: tuple[str, ...] = (
    "忽略之前", "忽略以上", "忽略先前", "忽略前面", "忽略所有之前",
    "ignore previous", "ignore all previous", "ignore prior",
)

TOOL_REGISTERED = "tool.registered"    # 总线瞬时类型(77 词表外,见偏离 1)

REGISTRY_UPDATED = "registry.updated"  # F003 瞬时广播(EventBus 预置类型)

DANGER_LEVELS: tuple[str, ...] = ("none", "low", "high", "critical")

def _raise_busy(**ctx: Any) -> None:
    """字面量 BUSY 上抛(BUSY = 无域前缀字面量码,ERR.md §2.11)。

    errors.raise_code 对未登记码兜底改判 CYC-999,故 BUSY 按 spec 伪码
    (raise PyHError("BUSY", ctx=…))直接构造——code 字段保持字面量 BUSY,
    供调用方/测试按 .code 断言(agent.py/scope.py 同款先例)。
    """
    raise PyHError("BUSY", ctx=dict(ctx))

class ToolDefinition(BaseModel):
    """冻结工具契约(五要素 = name/description/schema/danger/owner+provider 载体)。

    注册后不可变(frozen):改工具 = 注销重注册留痕(原则 2)。schema 为 JSON-Schema
    dict(parameters),禁止 pydantic 模型对象混入——注册时编译 pydantic 模型(F026)。
    描述 ≤200 字且禁注入特征(T-07);approval=never 只允许 none/low 危险级
    (矛盾契约构造即拒)。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str                                   # ^[a-z][a-z0-9_.]{1,63}$ 全库唯一
    description: str = Field(min_length=1, max_length=200)   # 给 LLM/人类,≤200 字
    # JSON-Schema(parameters),注册时编译;存储字段用 schema_ 规避 pydantic 对
    # 父类 BaseModel.schema(v1 兼容属性)的同名遮蔽警告,经 validation_alias
    # 收 "schema" 关键字/输入键,对外属性 defn.schema 由 property 提供(偏离 5)。
    schema_: dict[str, Any] = Field(validation_alias="schema")
    output_schema: Optional[dict[str, Any]] = None  # 输出校验声明(executor 关4 用)
    danger: Literal["none", "low", "high", "critical"]  # 危险级,注册后不可变
    guard_hooks: tuple[str, ...] = ()           # 追加 guard 逻辑名(如 "g-exec"),只加严
    approval: Literal["never", "auto", "always"] = "auto"  # auto=danger 分级裁决
    timeout_s: int = 60                         # Provider 执行超时(F017,executor 关3)
    owner: str                                  # 能力/插件 id 或 "builtin"/"spine"
    ctx_path: Optional[str] = None              # 定位器键(如 tools.fs.read)
    version: str = "1.0.0"                      # 能力版本

    @property
    def schema(self) -> dict[str, Any]:
        """JSON-Schema 访问面(spec 字段名;消费方 defn.schema 不变)。"""
        return self.schema_

    @field_validator("name")
    @classmethod
    def _name_shape(cls, v: str) -> str:
        """名称形态在模型构造期即锁死(注册层再验一次,双保险)。"""
        if not isinstance(v, str) or not NAME_RE.fullmatch(v):
            raise ValueError(f"工具名须匹配 {NAME_RE.pattern!r}")
        return v

    @field_validator("description")
    @classmethod
    def _description_clean(cls, v: str) -> str:
        """T-07 防注入:描述含改写指令特征 → 构造即拒(registry 层同谓词)。"""
        low = v.lower()
        hit = next((m for m in INJECTION_MARKERS if m.lower() in low), None)
        if hit is not None:
            raise ValueError(f"description 含注入特征 {hit!r}(T-07 拒)")
        return v

    @model_validator(mode="after")
    def _approval_danger_consistent(self) -> "ToolDefinition":
        """approval=never 只允许 none/low 危险级(矛盾契约构造即拒)。"""
        if self.approval == "never" and self.danger not in ("none", "low"):
            raise ValueError("approval=never 只允许 danger ∈ none/low")
        return self

# Preserve historical import and pickle identities through the compatibility facade.
ToolDefinition.__module__ = 'pyharness.core.tools_registry'
