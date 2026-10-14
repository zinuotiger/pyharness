"""Llm runtime: complete migrated implementation."""
from __future__ import annotations
import logging
log = logging.getLogger("pyharness.llm")
from typing import TYPE_CHECKING, Any
from pyharness.errors import raise_code
if TYPE_CHECKING:  # 仅类型标注;装配对象鸭子注入(session/bus 经 ctx)
    pass

from pyharness.core.llm_types import LLMAdapter

adapters: dict[str, LLMAdapter] = {}


def register_adapter(name: str, factory: Any) -> LLMAdapter:
    """F030 注册:factory 无参实例化即校验统一接口;缺方法 → CFG-601 不落脏表。"""
    inst = factory()
    for m in ("chat", "chat_stream", "ping"):       # 缺方法 → 注册失败,不落脏表
        if not callable(getattr(inst, m, None)):
            raise_code("CFG-601", reason="structural", adapter=name, missing=m,
                       detail="适配器缺统一接口方法,注册失败(不落脏表)")
    adapters[name] = inst                           # deepseek-chat/qwen-max/自定义端点
    log.info("adapter registered name=%s type=%s", name, type(inst).__name__)
    return inst

def require_adapter(name: str) -> LLMAdapter:
    """注册表查询;未注册模型 → LLM-304(advice=核对模型名 F030;不重试)。

    **N8 登记(2026-09-20 M5 复核)**:本函数当前**无生产调用者** —— 全库唯一命中
    是 ``__all__`` 导出与本文件的单元测试;``docs/`` 与规格中**零引用**,故
    **无兼容性承诺**。

    **处置:保留并加注,不删除。** 理由:它在 ``__all__`` 里,属**公共导出面**;
    删除是 H-4(API 契约变更),收益(少一个辅助函数)小于成本。LLM 门面自身的
    等价查询是 ``LLMClient.require()``(生产在用)。若将来确认要删,须走一次显式
    契约变更。
    """
    inst = adapters.get(name)
    if inst is None:
        raise_code("LLM-304", model=name,
                   hint="模型未注册:核对模型名,或 register_adapter 后重试(F030)")
    return inst
