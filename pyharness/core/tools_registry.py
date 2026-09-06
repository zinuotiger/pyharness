"""pyharness/core/tools_registry.py — 工具注册表(契约侧总闸)(specs/tools_registry.py.md 契约)

功能编号:F008(Definition 不可变登记)· F026(schema 编译/先验后跑入口)· F003(与
总线注册表联动)· F007(schemas_for 每轮下发)· F049(toolset 变体子集过滤)。
权威口径:specs/tools_registry.py.md(编码契约)、DIS-CORE §7.2/§7.3.1、
DIS-SEAM §2.1(Definition frozen 契约)/§2.4(announce 第③步 register)、
CONSTRAINTS-03(T-05 五要素/T-07 description 防注入)、ERR.md §2.9(TLB-801/802/803)。

职责一句话:工具唯一登记处——`name → ToolDefinition(不可变契约) + 注册时编译的
pydantic 模型 + Provider(可整体替换)`,负责注册查重(TLB-801/保留名 BUS-002)、
给 LLM 的 schemas_for 列表(scope 可见性 + agent 变体 toolset 过滤、确定性排序)、
以及参数强类型校验的模型供给(F026)。本文件不执行 Provider、不过 guard
(见 tools_executor.py.md/tools_guard.py.md);与 bus/registry.Registry 职责不同——
bus Registry 管 plugin/tool/capability 三类寻址索引,本注册表管 LLM 工具契约
(名称/描述/schema→模型/Provider 绑定),schema 发布给模型消费。

偏离说明(契约=spec;以下为既有实现冲突/空白处的取舍,均列理由):
1. 事件投递用 emit_sync 同步口:register_tool 为同步签名(spec 函数清单 def 非
   async),伪码的 bus.emit 为异步分发口不可直接 await → 采用 bus/registry.py
   Registry(F003)同款 emit_sync 同步投递(瞬时仅内存事件,留痕在同步调用路径上
   确定可见)。“tool.registered” 不在 events 57 词表(EVENT-SCHEMA §1.3:可由
   registry.updated 重算的事实)→ 归为总线瞬时类型,构造时幂等注册(register_type,
   重复构造/共享总线不报错)。
2. RESERVED = spec 内部索引表 8 项 + “system_prompt” 下划线拼写变体
   (bus/registry.py 两拼写同收先例,脊柱保留名口径共享);guard/approval/
   credentials 为插件域子系统保留名(bus Registry),工具名空间与其分离,不入本集。
3. schema→pydantic 编译器支持 OpenAI function parameters 形态子集:顶层 object +
   properties/required;属性 type ∈ string/integer/number/boolean/array/object/null,
   enum/const、anyOf/oneOf 仅“单实型+null”(→Optional)、长度/数值/正则约束、
   嵌套 object、非标识符属性名(create_model 经 **dict 展开,实测可用,原名保留)。
   组合型(多型 anyOf)/allOf/$ref → ValueError 并入 TLB-803 早失败(坏 schema 注册
   期暴露,不静默降级)。所有编译模型一律 extra="forbid”——“多余字段默认拒绝”
   (INV-06),additionalProperties=true 也不例外(安全优先,注册即按严格契约编译)。
4. _validate_contract 在 spec 伪码(名称/approval×danger)外补 T-05 五要素完整性
   (description 非空 ≤200/owner 非空/danger 合法)与 T-07 description 注入特征拒绝
   (tool_fs 规格注明该检查在 registry 层;INJECTION_MARKERS 为最小黑名单)。
5. frozen 赋值异常在 pydantic v2 实为 ValidationError(spec 关联测试注 TypeError;
   冻结语义不变,异常类型以运行库为准);另 ToolDefinition 的 schema 字段以
   schema_ 存储 + validation_alias("schema") + property 暴露——pydantic 父类
   BaseModel 保有 v1 兼容属性 schema,字段同名触发遮蔽 UserWarning(无配置开关),
   存储改名后对外消费面 defn.schema 不变,model_dump() 键为 schema_。
6. scope 为 None(装配期未接线)时 schemas_for 跳过 scope 过滤(日志降级)——与
   scope.py “未接线→日志降级”先例一致;toolset=None = 全量可见。

依赖方向(INV-08):本文件 → errors(raise_code)、events.vocab(summarize_validation)、
pydantic v2(create_model/model_validate);不 import tools_guard/tools_executor
(执行管道阶段后模块,避免环);Provider 以鸭子注入,永不 import Provider 内部符号。
"""
from __future__ import annotations

import logging
import re
import weakref
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, Literal, Optional

from pydantic import (BaseModel, ConfigDict, Field, ValidationError, create_model,
                      field_validator, model_validator)

from pyharness.errors import PyHError, raise_code
from pyharness.events.vocab import summarize_validation

if TYPE_CHECKING:  # 仅类型标注;总线实例鸭子注入(可 None = 未接线)
    from pyharness.bus.event_bus import EventBus

log = logging.getLogger("pyharness.tools_registry")

# ------------------------------------------------------------------ 常量
NAME_RE = re.compile(r"^[a-z][a-z0-9_.]{1,63}$")
"""工具名锚定:小写字母开头,后续 a-z0-9_. ,总长 2~64(spec 字段表规则)。"""

# 保留名:脊柱八模块(PRD §2.2/PARAMETER-ANCHOR;spec 内部索引表 8 项)+
# system_prompt 下划线拼写变体(bus/registry.py 两拼写同收,口径共享,见偏离 2)。
RESERVED: frozenset = frozenset({
    "agent-loop", "tools", "session", "llm", "system-prompt", "system_prompt",
    "scope", "agent", "persistence",
})

# T-07 description 防注入:能力元数据不得夹带“忽略先前指令”类改写文本(描述与
# 执行同谓词,CONSTRAINTS-03 T-07);匹配为大小写不敏感的子串黑名单。
INJECTION_MARKERS: tuple[str, ...] = (
    "忽略之前", "忽略以上", "忽略先前", "忽略前面", "忽略所有之前",
    "ignore previous", "ignore all previous", "ignore prior",
)

TOOL_REGISTERED = "tool.registered"    # 总线瞬时类型(57 词表外,见偏离 1)
REGISTRY_UPDATED = "registry.updated"  # F003 瞬时广播(EventBus 预置类型)

DANGER_LEVELS: tuple[str, ...] = ("none", "low", "high", "critical")


# ------------------------------------------------------------- 错误辅助
def _raise_busy(**ctx: Any) -> None:
    """字面量 BUSY 上抛(BUSY = 无域前缀字面量码,ERR.md §2.11)。

    errors.raise_code 对未登记码兜底改判 CYC-999,故 BUSY 按 spec 伪码
    (raise PyHError("BUSY", ctx=…))直接构造——code 字段保持字面量 BUSY,
    供调用方/测试按 .code 断言(agent.py/scope.py 同款先例)。
    """
    raise PyHError("BUSY", ctx=dict(ctx))


# ------------------------------------------------------------ ToolDefinition
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


# ---------------------------------------------- schema → pydantic 编译器(F026)
def _sanitize(part: str) -> str:
    """名称片段清洗:非 [A-Za-z0-9_] 字符替换为 _ (防 create_model 名污染)。"""
    return re.sub(r"[^A-Za-z0-9_]", "_", part) or "_"


def _literal_annotation(values: list[Any]) -> Any:
    """动态 Literal 构造(eval 受限命名空间;值不可哈希 → TypeError 上抛)。

    Python 3.13 的 typing.Literal 为特殊形式,无法经 __class_getitem__ 动态
    展开,故把值逐一 repr 成 Python 字面量后在仅含 Literal 的命名空间 eval——
    值来自 JSON-Schema dict,repr 文本不可执行任意代码(白名单命名空间)。
    """
    for v in values:
        hash(v)                     # 不可哈希(如 dict)→ TypeError → TLB-803
    expr = "Literal[" + ", ".join(repr(v) for v in values) + "]"
    return eval(expr, {"Literal": Literal})  # noqa: S307 受限命名空间,见上


def _annotation_from(prop: dict[str, Any], model_name: str, key: str) -> Any:
    """单个属性 schema → typing 注解;不支持形态抛 ValueError(TLB-803 上游收)。

    顺序:const/enum(不依赖 type)→ 标量 → array → object → null → anyOf/oneOf
    (仅“单实型 + null”组合 → Optional);组合型/allOf/$ref → ValueError 早失败,
    不静默降级成宽松类型(坏 schema 注册期即暴露,而非调用期神秘失败)。
    """
    if not isinstance(prop, dict):
        raise ValueError(f"属性 {key!r} 的 schema 须为 dict,"
                         f"实为 {type(prop).__name__}")

    # ---- const:字面量单值
    if "const" in prop:
        v = prop["const"]
        if v is None:
            return Optional[Any]
        return _literal_annotation([v])

    # ---- enum:字面量集合(允许 null 成员 → Optional)
    if "enum" in prop:
        vals = prop["enum"]
        if not isinstance(vals, list) or not vals:
            raise ValueError(f"属性 {key!r} 的 enum 须为非空 list")
        if any(v is None for v in vals):
            non_null = [v for v in vals if v is not None]
            if not non_null:
                return Optional[Any]
            return Optional[_literal_annotation(non_null)]
        return _literal_annotation(vals)

    # ---- anyOf/oneOf:仅“单实型 + null”(OpenAI nullable 惯用形态)
    for comb in ("anyOf", "oneOf"):
        if comb in prop:
            parts = prop[comb]
            if not isinstance(parts, list) or not parts:
                raise ValueError(f"属性 {key!r} 的 {comb} 须为非空 list")
            real = [p for p in parts
                    if not (isinstance(p, dict) and p.get("type") == "null")]
            if len(real) != len(parts) - 1 or len(real) != 1:
                raise ValueError(f"属性 {key!r} 的 {comb} 仅支持单实型+null 组合")
            return Optional[_annotation_from(real[0], model_name, key)]
    if "$ref" in prop or "allOf" in prop:
        raise ValueError(f"属性 {key!r} 含 $ref/allOf,本注册表编译器不支持"
                         "(能力包须内联展开)")

    t = prop.get("type")
    if t is None:
        raise ValueError(f"属性 {key!r} 缺 type(且无 enum/const/组合型)")

    if t == "string":
        return str
    if t == "integer":
        return int
    if t == "number":
        return float
    if t == "boolean":
        return bool
    if t == "null":
        return type(None)

    if t == "array":
        items = prop.get("items")
        if items is None:
            return list[Any]                    # 合法 JSON-Schema:元素任意
        if isinstance(items, dict):
            return list[_annotation_from(items, model_name, f"{key}[]")]
        raise ValueError(f"属性 {key!r} 的 items 仅支持单一 schema dict")

    if t == "object":
        props = prop.get("properties", {})
        if not isinstance(props, dict):
            raise ValueError(f"属性 {key!r} 的 properties 须为 dict")
        return _object_model(f"{model_name}_{_sanitize(key)}", props,
                             prop.get("required"))

    raise ValueError(f"属性 {key!r} 的 type {t!r} 不支持")


def _field_spec(prop: dict[str, Any], ann: Any, *,
                required: bool) -> tuple[Any, Any]:
    """属性约束装配 → (annotation, default);default 为 ... / 具体默认值 / Field。

    映射:str maxLength/minLength → max_length/min_length、pattern;数值
    minimum/maximum/exclusiveMinimum/exclusiveMaximum → ge/le/gt/lt;array
    minItems/maxItems → min_length/max_length。Literal 化字段(enum/const/组合)
    忽略标量约束(Literal 已锁值域)。约束值非法(int()/re.compile 失败)→
    ValueError → TLB-803 早失败,不静默忽略。
    可选字段(非 required)一律 Optional 化 + 缺省 None(schema default 优先):
    LLM 省略可选参数不误伤,显式传 null 亦放行。
    """
    kw: dict[str, Any] = {}
    t = prop.get("type")
    literalized = any(k in prop for k in ("enum", "const", "anyOf", "oneOf"))
    if not literalized:
        if t == "string":
            if "maxLength" in prop:
                kw["max_length"] = int(prop["maxLength"])
            if "minLength" in prop:
                kw["min_length"] = int(prop["minLength"])
            if "pattern" in prop:
                re.compile(str(prop["pattern"]))   # 无效正则早失败(TLB-803)
                kw["pattern"] = str(prop["pattern"])
        elif t in ("integer", "number"):
            for s_key, f_key in (("minimum", "ge"), ("maximum", "le"),
                                 ("exclusiveMinimum", "gt"),
                                 ("exclusiveMaximum", "lt")):
                if s_key in prop:
                    kw[f_key] = prop[s_key]
        elif t == "array":
            if "minItems" in prop:
                kw["min_length"] = int(prop["minItems"])
            if "maxItems" in prop:
                kw["max_length"] = int(prop["maxItems"])

    if not required:
        if ann is not type(None):
            ann = Optional[ann]
        kw["default"] = prop.get("default")      # 可选字段恒有默认(缺省 None)
        return ann, Field(**kw)
    # 必填字段
    if "default" in prop:
        kw["default"] = prop["default"]
        return ann, Field(**kw)
    if kw:
        return ann, Field(**kw)
    return ann, ...


def _object_model(model_name: str, props: dict[str, Any],
                  required: Any) -> type[BaseModel]:
    """properties dict → pydantic 模型(extra=forbid,恒 populate_by_name)。

    属性名可为任意字符串(非标识符亦可,create_model 经 **dict 展开,实测可用;
    model_dump 输出原名 == schema 属性名,INV-06 可逐字段比对)。required 引用
    未知属性 / 非 list → ValueError(TLB-803 上游收)。
    """
    if not isinstance(props, dict):
        raise ValueError("object 的 properties 须为 dict")
    req = required or []
    if not isinstance(req, list) or not all(isinstance(r, str) for r in req):
        raise ValueError("required 须为 str 元素 list")
    missing = [r for r in req if r not in props]
    if missing:
        raise ValueError(f"required 引用未知属性: {missing}")

    fields: dict[str, Any] = {}
    for key, prop in props.items():
        ann = _annotation_from(prop, model_name, key)
        fields[key] = _field_spec(prop, ann, required=key in req)
    return create_model(model_name,
                        __config__=ConfigDict(extra="forbid",
                                              populate_by_name=True),
                        **fields)


def _compile_model(tool_name: str, schema: Any) -> type[BaseModel]:
    """工具注册时的 schema 编译入口(F026 早失败;失败 ValueError → TLB-803)。"""
    if isinstance(schema, BaseModel):
        raise ValueError("禁止 pydantic 模型对象混入 schema(须 JSON-Schema dict)")
    if not isinstance(schema, dict):
        raise ValueError(f"schema 须为 JSON-Schema dict,实为 {type(schema).__name__}")
    t = schema.get("type")
    if t not in (None, "object"):
        raise ValueError(f"顶层 type 须为 object(或省略),实为 {t!r}")
    if t is None and "properties" not in schema and "required" not in schema:
        # 顶层既无 type 也无 properties/required(如 {"path": …} 裸键)→ 非
        # object 参数形态,拒(空参数工具应显式写 {"type": "object"})
        raise ValueError("顶层缺 type/properties,非 object 参数形态")
    props = schema.get("properties", {})
    return _object_model(f"ToolArgs_{_sanitize(tool_name)}", props,
                         schema.get("required"))


# ------------------------------------------------------------- ToolRegistry
class ToolRegistry:
    """工具注册表:契约/模型/Provider 三索引 + 保留名护栏 + 事件留痕(F008)。

    内部索引(spec):_tools 契约(不可变)、_models 注册时编译缓存(F026)、
    _providers 实现(可整体替换)、_running 在途调用名(注销闸)。
    RESERVED 为脊柱八模块名(BUS-002 语义,与 bus.Registry 共享口径)。

    查询语义:查不到一律结构化错误(TLB-802,不静默返回 None);has()/names()/
    count()/snapshot() 为只读防抖/审计面(不抛错)。bus 可 None(未接线 → 事件
    日志降级),与 scope.py 注入式装配先例一致。
    """

    RESERVED: frozenset = RESERVED

    def __init__(self, bus: Optional["EventBus"] = None) -> None:
        self._tools: dict[str, Any] = {}                  # name → ToolDefinition
        self._models: dict[str, type[BaseModel]] = {}     # name → 编译模型(F026)
        self._providers: dict[str, Any] = {}              # name → Provider 实现
        self._running: set[str] = set()                   # 在途调用名(注销闸)
        self._bus: Optional["EventBus"] = bus
        if bus is not None:
            self._register_internal_types(bus)

    # ------------------------------------------------------------ 内部护栏
    _registered_buses: "weakref.WeakKeyDictionary[Any, None]" = weakref.WeakKeyDictionary()

    @classmethod
    def _register_internal_types(cls, bus: "EventBus") -> None:
        """tool.registered 为词表外瞬时事件(见模块 docstring 偏离 1)。

        构造时注册使 emit_sync 通过 EVT-102 前置类型闸;同一总线重复构造幂等
        且零噪音——已注册总线记入弱引用表直接跳过(register_type 二次注册会
        抛 EVT-102 并经 raise_code 打 error 日志,不宜走异常路径)。
        """
        try:
            if bus in cls._registered_buses:
                return
        except TypeError:                       # 不可弱引用对象:退化为异常吞并路径
            pass
        try:
            bus.register_type(TOOL_REGISTERED, None)
        except PyHError as e:
            if e.code != "EVT-102":
                raise
        try:
            cls._registered_buses[bus] = None
        except TypeError:                       # 同上:仅本次尝试,不阻断构造
            pass

    def _emit(self, type_: str, payload: dict) -> None:
        """事件出口:emit_sync 同步投递(瞬时仅内存,同 bus.Registry F003 先例)。

        未接线 bus → 日志降级;投递异常(如订阅者外的总线层错误)记日志,
        不阻断注册/注销主路径(留痕尽力而为)。
        """
        bus = self._bus
        if bus is None:
            log.debug("tools_registry 未接线 bus,%s 未广播", type_)
            return
        try:
            bus.emit_sync(type_, payload)
        except PyHError as e:
            log.warning("tools_registry emit %s 失败 code=%s", type_, e.code)

    def _validate_contract(self, defn: Any) -> None:
        """注册前置契约校验(T-05 五要素/T-07;失败 → TLB-801)。

        覆盖伪码之外的字段级规则:description 非空 ≤200 且无注入特征(T-07)、
        owner 必填、danger 合法;名称 regex 与 approval×danger 一致性也在
        register_tool 内直查(伪码顺序),此处为集中收口(鸭子 defn 与
        ToolDefinition 实例同谓词)。
        """
        name = getattr(defn, "name", None)
        danger = getattr(defn, "danger", None)
        desc = getattr(defn, "description", None)
        owner = getattr(defn, "owner", None)
        approval = getattr(defn, "approval", "auto")

        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            raise_code("TLB-801", tool=name, rule=NAME_RE.pattern,
                       why="名称非法(小写字母开头,仅 a-z0-9_. ,长 2~64)")
        if danger not in DANGER_LEVELS:
            raise_code("TLB-801", tool=name, danger=danger,
                       why="缺 danger 或非法危险级(T-05 五要素之一)")
        if approval == "never" and danger not in ("none", "low"):
            raise_code("TLB-801", tool=name,
                       why="approval=never 只允许 danger ∈ none/low")
        if not isinstance(desc, str) or not desc.strip():
            raise_code("TLB-801", tool=name, why="缺 description(T-05 五要素之一)")
        if len(desc) > 200:
            raise_code("TLB-801", tool=name, length=len(desc),
                       why="description 超 200 字上限")
        low = desc.lower()
        hit = next((m for m in INJECTION_MARKERS if m.lower() in low), None)
        if hit is not None:
            raise_code("TLB-801", tool=name, marker=hit,
                       why="description 含注入特征(T-07 拒)")
        if not isinstance(owner, str) or not owner.strip():
            raise_code("TLB-801", tool=name, why="缺 owner(能力/插件 id 必填)")

    # ------------------------------------------------------------ 登记入口
    def register_tool(self, defn: Any, *, provider: Any = None) -> str:
        """工具登记(F008):查重 → 契约校验 → schema 编译(TLB-803 早失败)→ 入表
        → 绑 Provider → 事件留痕。返回工具名。

        拒绝路径(重名/保留名/非法名/矛盾契约/坏 schema)注册表零变更、不发
        tool.registered(GWT-T7-01:无脏数据)。Definition 入表即不可变(frozen)。
        """
        name = getattr(defn, "name", "")
        if name in self._tools or name in self.RESERVED:
            raise_code("TLB-801", tool=name,
                       advice="重名或脊柱保留名(BUS-002);注销旧定义后重注册")
        self._validate_contract(defn)             # T-05/T-07 集中收口

        schema = getattr(defn, "schema", None)
        try:
            model = _compile_model(name, schema)  # F026 编译期校验(早失败)
        except Exception as e:                    # noqa: BLE001 编译器内聚,
            log.debug("tools_registry schema 编译失败 name=%s: %s", name, e)
            raise_code("TLB-803", tool=name, why="schema 不可编译")
        self._tools[name] = defn                  # Definition frozen,入表不可变
        self._models[name] = model
        if provider is not None:
            self._providers[name] = provider      # 绑定实现(可后补/换绑)
        self._emit(TOOL_REGISTERED, {
            "name": name, "danger": getattr(defn, "danger", None),
            "owner": getattr(defn, "owner", None),
            "hooks": list(getattr(defn, "guard_hooks", ()))})
        self._emit(REGISTRY_UPDATED, {"op": "add", "kind": "tool", "key": name})
        return name

    def register_definition(self, defn: Any) -> str:
        """能力 Definition 登记别名(agent.announce 消费面;= register_tool)。"""
        return self.register_tool(defn)

    def unregister_definition(self, name: str) -> None:
        """能力摘除别名(agent.detach_capability 消费面;= unregister)。"""
        return self.unregister(name)

    # ------------------------------------------------------------ 只读查询
    def lookup(self, name: str) -> Any:
        """查契约(executor 关1);不存在 → TLB-802(不静默,防幻觉工具名)。"""
        defn = self._tools.get(name)
        if defn is None:
            raise_code("TLB-802", tool=name,
                       advice="工具不存在或不可用;查注册表与拼写")
        return defn

    def lookup_provider(self, name: str) -> Any:
        """查实现(executor 关3);未绑定/不存在 → TLB-802。

        Consumer 只经本函数取实现,永不 import Provider 内部符号(原则 2)。
        """
        if name not in self._tools:
            raise_code("TLB-802", tool=name,
                       advice="工具不存在或不可用;查注册表与拼写")
        provider = self._providers.get(name)
        if provider is None:
            raise_code("TLB-802", tool=name,
                       advice="契约已注册但未绑定 Provider;能力激活未完成或已解绑")
        return provider

    def bind_provider(self, name: str, provider: Any) -> None:
        """契约已注册后补绑/换绑实现;契约不存在 → TLB-802。

        Provider 可整体替换而不触碰 Consumer(seam 测试);传 None = 解绑
        (解除实现绑定,契约保留)。
        """
        if name not in self._tools:
            raise_code("TLB-802", tool=name,
                       advice="契约未注册;先 register_tool 再绑 Provider")
        if provider is None:
            self._providers.pop(name, None)
        else:
            self._providers[name] = provider

    def get_model(self, name: str) -> type[BaseModel]:
        """取注册时编译的 pydantic 模型;未注册 → TLB-802(模块职责 5)。"""
        model = self._models.get(name)
        if model is None:
            raise_code("TLB-802", tool=name,
                       advice="工具不存在或不可用;查注册表与拼写")
        return model

    def has(self, name: str) -> bool:
        """契约是否存在(防抖查询,不抛错)。"""
        return name in self._tools

    def names(self) -> list[str]:
        """全部已注册工具名(排序;健康摘要/审计)。"""
        return sorted(self._tools)

    def iter_definitions(self) -> Iterator[Any]:
        """只读遍历契约(按名排序;Definition frozen,遍历不产出修改面)。"""
        for name in sorted(self._tools):
            yield self._tools[name]

    def count(self) -> int:
        """注册工具数(健康摘要 N 能力)。"""
        return len(self._tools)

    def snapshot(self) -> dict:
        """名称+版本+danger 只读快照(fork/审计/自检 F031 用)。"""
        return {name: {"version": str(getattr(d, "version", "")),
                       "danger": getattr(d, "danger", None)}
                for name, d in sorted(self._tools.items())}

    # ------------------------------------------------------------ 注销闸
    def mark_running(self, name: str) -> None:
        """在途登记(executor 关3 Provider 执行前调用;注销闸判定源)。"""
        self._running.add(name)

    def mark_idle(self, name: str) -> None:
        """在途解除(executor 关3 finally 调用;幂等)。"""
        self._running.discard(name)

    def unregister(self, name: str) -> None:
        """注销(卸插件/能力 detach):摘三索引并 registry.updated(op=del) 留痕。

        在途调用中的工具拒绝注销(BUSY)——防“执行到一半 Provider 没了”
        (executor 关3 前后 mark_running/mark_idle 联动)。不存在 → TLB-802。
        """
        if name not in self._tools:
            raise_code("TLB-802", tool=name, advice="不存在,无需注销")
        if name in self._running:
            _raise_busy(tool=name, advice="有在途调用,稍后重试")
        del self._tools[name]
        del self._models[name]
        self._providers.pop(name, None)
        self._emit(REGISTRY_UPDATED, {"op": "del", "kind": "tool", "key": name})

    # ------------------------------------------------------------ 校验与发布
    def validate_args(self, name: str, raw_args: dict) -> dict:
        """参数强类型校验(F026 调用期入口;先验后跑物理闸门)。

        raw_args(模型原话)→ 注册时编译模型 model_validate:强类型转换 + 多余
        字段拒(extra=forbid,防 {"path": 123} 被隐式 str() 转换执行)+ 约束
        复查(maxLength 等,超限零执行);失败抛 TLB-803 附字段级明细回喂 LLM
        修正,**绝不执行 Provider(INV-06)**。返回强类型化参数字典
        (真实函数收到的 == 日志 args,INV-06)。
        """
        model = self._models.get(name)
        if model is None:
            raise_code("TLB-802", tool=name,
                       advice="工具不存在或不可用;查注册表与拼写")
        try:
            typed = model.model_validate(raw_args)
            return typed.model_dump()             # 原名输出(含非标识符属性)
        except ValidationError as e:
            raise_code("TLB-803", tool=name,
                       detail=summarize_validation(e),
                       advice="按明细修正参数;未执行任何操作")

    def schemas_for(self, scope: Any = None, *,
                    toolset: Optional[set[str]] = None) -> list[dict]:
        """给 LLM 的 tools 数组(F007 每轮取用;OpenAI function 列表)。

        按 scope 可见性过滤(scope.can_use(name)==False 者不下发——LLM 拿到的
        是当前 scope/变体下注定能过 scope 关的集合,减少注定被拒的无效调用)
        + toolset 子集过滤(子 agent 变体 F049)+ 确定性排序(同输入同输出,
        可缓存可测)。scope=None(装配期未接线)→ 跳过 scope 过滤(偏离 6)。
        """
        out: list[dict] = []
        for name in sorted(self._tools):          # 确定性排序
            if scope is not None and not scope.can_use(name):
                continue                          # scope 不可见 → 不下发
            if toolset is not None and name not in toolset:
                continue                          # agent 变体子集外
            out.append(self.to_openai_schema(self._tools[name]))
        return out

    def to_openai_schema(self, defn: Any) -> dict:
        """Definition → OpenAI function schema(描述/参数;不泄露 danger/
        guard_hooks/approval 给 LLM——安全元数据只在管道内消费)。"""
        return {"type": "function",
                "function": {"name": defn.name,
                             "description": defn.description,
                             "parameters": defn.schema}}


__all__ = ["ToolDefinition", "ToolRegistry", "RESERVED", "NAME_RE",
           "INJECTION_MARKERS", "TOOL_REGISTERED", "REGISTRY_UPDATED"]
