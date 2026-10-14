"""Subagent provider: complete migrated implementation."""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from pyharness.core.subagent import SubagentManager
import asyncio
from typing import Any
from pyharness.core.subagent_models import DEFAULT_BUDGET_RATIO, SubagentSpec


class SubagentSpawnProvider:
    """subagent.spawn 工具 Provider 适配器(DIS-SEAM §2.3 Consumer 管道内执行)。

    handle = spawn + join:LLM 一次工具调用即"派发并回收",返回 ≤2KB 摘要——摘要经
    executor 关4 的 tool.result 管道以 tool 数据身份进入主会话派生历史(偏离 8);
    子任务失败(join 上抛原 PyHError)→ executor 落 tool.error 回喂,不中断主循环。
    深度由装配层按 manager 层级推(depth = manager.depth+1),不入 LLM 参数(防谎报
    层级绕过递归闸,见 definition)。
    """

    def __init__(self, manager: SubagentManager) -> None:
        self._mgr = manager

    async def handle(self, args: dict, ctx: Any = None) -> str:
        """Provider 执行面(async;executor 关3 直接协程 wait_for)。"""
        # 递归深度自装配层经 ctx.subagent_depth 传入(见 orchestration):子会话携带
        # 自身层级 d → 此处计算 d+1,使嵌套 spawn 逐层递增至 ≤3 的硬上限;无该字段
        # (交互主会话/未装配)= 顶层 d=0 → depth=1。LLM 无法自报 depth 绕过闸门。
        base = self._mgr._depth
        if ctx is not None:
            base = int(getattr(ctx, "subagent_depth", base))
        spec = SubagentSpec(
            task=str(args.get("task") or ""),
            tools_subset=args.get("tools_subset"),
            deny_extra=list(args.get("deny_extra") or []),
            budget_ratio=float(args.get("budget_ratio")
                               or DEFAULT_BUDGET_RATIO),
            depth=int(args.get("depth") or base + 1),
            creds_allowed=list(args.get("creds_allowed") or []),
            # N11:JSON-Schema 的 ``notify`` 默认值是 ``null``,经 pydantic 校验后
            # ``args["notify"]`` **存在但为 None** ⇒ ``args.get("notify", True)`` 拿到
            # None(默认值 True 永不生效)→ ``bool(None)=False`` ⇒ LLM 工具路径上
            # ``subagent.joined`` **永不发出**(父会话丢失回收事实)。此处把"缺省/显式
            # None"统一收敛为 True(契约默认),只有显式传 False 才关闭。
            notify=(True if args.get("notify") is None
                    else bool(args.get("notify"))))
        sub_id = await self._mgr.spawn(spec, ctx)       # 四道闸内聚(spawn 校验)
        try:
            return await self._mgr.join(sub_id, ctx=ctx)
        except asyncio.CancelledError:
            # This provider owns its synchronous child. Independent join timeouts
            # still leave detached work alive; cancellation must not orphan it.
            await self._mgr.cancel(sub_id, reason='parent-cancelled')
            raise

# Preserve historical import and pickle identities through the compatibility facade.
SubagentSpawnProvider.__module__ = 'pyharness.core.subagent'
