"""ToolInvocation operations, separated from facade assembly."""
from __future__ import annotations
import asyncio
import inspect
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from pydantic import ValidationError
from pyharness.core.tools_guard import ToolCall
from pyharness.core.tools_registry import _compile_model as _registry_compile_model
from pyharness.errors import PyHError, raise_code
from pyharness.events.vocab import summarize_validation
from pyharness.core.result_text import render_result_text
from pyharness.core.execution_result import SPILL_THRESHOLD, _ZOMBIE_POOL_LIMIT, ExecResult, summarize_text

class ToolInvocation:
    """ToolInvocation responsibility; state is owned by the composing facade."""


    async def _run_provider(self, defn: Any, args: dict, ctx: Any) -> Any:
        """Provider 执行(runner):async handle 直接协程;同步 handle 经独立单工线程池。

        同步函数用**每次调用独立**的 ``ThreadPoolExecutor(max_workers=1)`` 承载
        (不阻塞事件循环,spec 关3);async handler 走事件循环协作(不占线程池,偏离 7)。

        超时(外层 wait_for 掐断)/取消掐断 → 内部 await 抛 CancelledError → 本函数
        以 ``shutdown(wait=False)`` **驱逐**该池(线程把副作用在后台跑完),并保留引用
        防 GC 半途杀线程——该池绝不被复用于后续调用,消除"超时线程回池 + mark_idle
        后并发重入 → 重叠副作用"(Python 无法安全 kill 运行中线程,只能不复用)。
        正常完成则 ``shutdown(wait=True)`` 立即收口(线程已结束)。见偏离说明 13。
        """
        runtime = getattr(ctx, 'platform_runtime', None)
        if runtime is not None:
            async def original():
                return await self._run_original_provider(defn, args, ctx)
            return await runtime.execute(defn.name, args, ctx, original)
        return await self._run_original_provider(defn, args, ctx)

    async def _run_original_provider(self, defn: Any, args: dict, ctx: Any) -> Any:
        handle = self._provider_handle(defn.name)
        if inspect.iscoroutinefunction(handle):
            return await handle(args, ctx)              # wait_for 在外层掐断
        loop = asyncio.get_running_loop()
        pool = self._new_provider_pool(defn.name)
        try:
            result = await loop.run_in_executor(pool, handle, args, ctx)
        except BaseException:                           # 超时/取消/异常:驱逐不复用
            pool.shutdown(wait=False, cancel_futures=True)
            self._zombie_pools.add(pool)
            self._prune_zombies()
            raise
        else:
            pool.shutdown(wait=True)                    # 正常完成:线程已结束
            self._zombie_pools.discard(pool)
            return result

    def _prune_zombies(self) -> None:
        """僵尸池裁剪:超时产生的池,线程跑完后自会空转结束;仅当僵尸池累积超过
        上限时,丢弃其中线程已全部结束的池(仍在跑的保留引用,防 GC 半途回收)。"""
        if len(self._zombie_pools) < _ZOMBIE_POOL_LIMIT:
            return
        keep: set[ThreadPoolExecutor] = set()
        for pool in self._zombie_pools:
            try:
                threads = getattr(pool, "_threads", ())
                alive = any(getattr(t, "is_alive", lambda: False)()
                            for t in threads)
            except Exception:                           # noqa: BLE001 线程查询异常:保守保留
                alive = True
            if alive:
                keep.add(pool)
        self._zombie_pools = keep

    def _invoke(self, defn: Any, args: dict, ctx: Any) -> Any:
        """spec 速览 _invoke:Provider 实际调用同步入口(to_thread 承载面)。

        保留为可整体替换的薄层:替换 _run_provider 即可换执行策略;同步调用
        经本函数时按 handle(args, ctx) 契约执行。
        """
        handle = self._provider_handle(defn.name)
        return handle(args, ctx)

    async def cancel_current(self) -> bool:
        """取消在途 Provider 任务(协作式,F025):掐断 wait_for → execute 内
        CancelledError 分支写 partial + re-raise(已发生副作用不假装回滚)。

        无在途任务 → 返回 False(幂等)。partial 事件由 execute 的取消处理分支
        落盘,本函数只负责触发取消(单事件循环单在途,阶段 1 agent-loop 串行)。
        """
        fut = self._active
        if fut is None or fut.done() or fut.cancelling():
            return False
        fut.cancel()
        return True

    def _validate_output(self, defn: Any, raw: Any) -> None:
        """输出契约单点校验(spec 速览表):output_schema 缺失 = 跳过;声明则
        编译(缓存)+ model_validate。失败抛 ValidationError/ValueError,
        _finalize 收口转 tool.error(TLB-803,"输出校验失败:")。
        """
        schema = getattr(defn, "output_schema", None)
        if not schema:
            return
        model = self._out_models.get(id(defn))
        if model is None:
            try:
                model = _registry_compile_model(
                    f"{defn.name}__out", schema)        # 编译器同源(偏离 8)
            except Exception as e:                      # noqa: BLE001 坏契约
                raise ValueError(f"输出契约不可编译:{e}") from e
            self._out_models[id(defn)] = model
        model.model_validate(raw)                       # ValidationError 上抛

    async def _spill_put(self, ctx: Any, text: str, kind: str) -> dict:
        """ctx.storage.spill.put 封装:未接线 → PERS-221(结构化);异常收敛。"""
        spill = getattr(getattr(ctx, "storage", None), "spill", None)
        if spill is None:
            raise_code("PERS-221",
                       hint="storage.spill 未接线(能力未激活),超长输出无法归档")
        r = spill.put(text, kind=kind)
        if inspect.isawaitable(r):
            r = await r
        if not isinstance(r, dict):
            raise_code("PERS-221", hint="storage.spill.put 返回非 dict(契约违约)")
        return r

    async def _finalize(self, ctx: Any, call: ToolCall, defn: Any, raw: Any, *,
                        elapsed_ms: float = 0.0) -> ExecResult:
        """关4 结果检查与落盘(spec 伪代码权威;偏离 4:spill_ref 全 dict)。

        输出 schema 校验(声明才做;失败 → tool.error TLB-803)→ 超长转 spill
        (F039,原文落私有区,上下文只留 ≤2KB 摘要)→ tool.result(ok/summary/
        truncated/spill_ref,trace.parent=父 response)。spill 写失败/超限
        (PERS-221/223)同样收敛为单条 tool.error——每调用恰一条终局事件。
        """
        if getattr(defn, "output_schema", None):
            try:
                self._validate_output(defn, raw)
            except (ValidationError, ValueError) as e:
                detail = summarize_validation(e) if isinstance(e, ValidationError) \
                    else str(e)
                return await self._on_error(ctx, call, "TLB-803",
                                            f"输出校验失败:{detail}")
        from pyharness.core.provider_result import ProviderOutcome
        ok = bool(raw.get('ok')) if isinstance(raw, ProviderOutcome) else True
        text = render_result_text(raw)
        truncated, spill_ref = False, None
        if len(text) > SPILL_THRESHOLD:
            try:
                sp = await self._spill_put(ctx, text, kind=call.name)
            except PyHError as e:                       # PERS-221/223 → 回喂
                return await self._on_error(ctx, call, e.code,
                                            e.to_model_message())
            truncated = True
            spill_ref = sp                              # {ref,chars,lines,preview}
            preview = str(sp.get("preview") or text[:500])[:500]
            chars = int(sp.get("chars", len(text)))
            lines = int(sp.get("lines", text.count("\n") + 1))
            ref = sp.get("ref", "?")
            summary = f"{preview}…(共{chars}字符/{lines}行,spill:{ref})"
        else:
            summary = summarize_text(text)
        await self._append(ctx, "tool.result",
                           {"name": call.name, "call_id": call.call_id,
                            "ok": ok, "summary": summary,
                            "truncated": truncated, "spill_ref": spill_ref},
                           actor="tool", trace=self._trace(call))
        return ExecResult(ok=ok, summary=summary, truncated=truncated,
                          spill_ref=spill_ref, elapsed_ms=elapsed_ms)

