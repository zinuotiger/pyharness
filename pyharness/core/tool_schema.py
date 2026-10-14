"""Tool schema: complete migrated implementation."""
from __future__ import annotations
import re
from typing import TYPE_CHECKING, Any, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, create_model
if TYPE_CHECKING:  # 仅类型标注;总线实例鸭子注入(可 None = 未接线)
    pass


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
