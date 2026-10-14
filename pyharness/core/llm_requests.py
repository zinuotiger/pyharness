"""ModelRequests operations, separated from facade assembly."""
from __future__ import annotations
import asyncio
import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Optional
from pyharness.errors import raise_code
from pyharness.core.llm_types import _RETRYABLE_CODES, LLMResponse, UsageCounters, ProviderProtocolError
from pyharness.core.llm_protocol import _sanitize_tools, _restore_call_names, parse_tool_calls, accumulate_stream, _est_tokens, normalize_exc
from pyharness.core.llm_usage import _price_table, _llm_cfg_from_ctx, _request_payload, _response_payload, report_usage
if TYPE_CHECKING:  # 仅类型标注;装配对象鸭子注入(session/bus 经 ctx)
    pass
from pyharness.core.llm_protocol import log

class ModelRequests:
    """ModelRequests responsibility; state is owned by the composing facade."""

    async def aclose(self):
        self._closed = True
        task = getattr(self, '_close_task', None)
        if task is None or (task.done() and (task.cancelled() or task.exception() is not None)):
            task = self._close_task = asyncio.create_task(self._close_transport())
        await asyncio.shield(task)

    async def _close_transport(self):
        close = getattr(self._transport, 'aclose', None)
        if close is not None:
            await close()

    def _llm_cfg(self, ctx: Any) -> Any:
        """本请求 llm 配置段(ctx.config.llm → 构造 cfg → L1)。"""
        return _llm_cfg_from_ctx(ctx, self._cfg)

    def _output_limit(self, ctx: Any, configured: int) -> int:
        """Clamp Platform requests to remaining declared output allowance.

        This bounds the submitted generation parameter, not provider billing.
        The existing sequential run owns its counters; other callers retain
        their existing limit and budget gate.
        """
        runtime = getattr(ctx, 'platform_runtime', None)
        if runtime is None:
            return configured
        counters = getattr(ctx, 'counters', None)
        if counters is None:
            from pyharness.errors import raise_code
            raise_code('CFG-601', reason='platform_usage_counter_missing')
        remaining = runtime.definition.budget.output_tokens - counters.task_total().out_tokens
        if remaining <= 0:
            from pyharness.core.scope import BudgetExhausted
            raise BudgetExhausted('Platform output token budget exhausted')
        return min(configured, remaining)

    @staticmethod
    def _first_message(raw: Any) -> tuple:
        """choices[0].message + finish_reason;choices 空数组 = 内容过滤 → LLM-304(不回空)。"""
        choices = getattr(raw, "choices", None) or []
        if not choices:
            raise ProviderProtocolError("Missing choices")
        c0 = choices[0]
        return c0.message, (c0.finish_reason or "unknown")

    async def chat(self, messages: list[dict], tools: Optional[list] = None, *,
                   ctx: Any) -> LLMResponse:
        return await self._budgeted_request(messages, tools, ctx=ctx, streaming=False)

    async def _budgeted_request(self, messages, tools, *, ctx, streaming):
        limit = self._output_limit(ctx, self._llm_cfg(ctx).max_tokens)
        counters = getattr(ctx, 'counters', None)
        release = lambda: None
        if isinstance(counters, UsageCounters):
            config=self._llm_cfg(ctx)
            prices=_price_table(getattr(getattr(config,'usage',None),'unit_price',{}))
            price=prices.get(self.model,{})
            # Conservative byte reservation, including tool schemas/framing.
            # Actual provider usage still settles the ledger; unknown prices
            # cannot provide a monetary guarantee and remain explicitly unpriced.
            input_bound=len(json.dumps([messages,tools],ensure_ascii=False).encode('utf-8'))
            limit, release = counters.reserve_output(limit,input_tokens=input_bound,
                input_rate=price.get('in_per_million',0)/1e6,
                output_rate=price.get('out_per_million',0)/1e6)
        try:
            method = self._chat_stream if streaming else self._chat
            return await method(messages, tools, ctx=ctx, output_limit=limit)
        finally:
            # Includes transport failure, malformed response and cancellation.
            release()

    async def _chat(self, messages: list[dict], tools: Optional[list] = None, *,
                    ctx: Any, output_limit: int) -> LLMResponse:
        """唯一出口(F012):组请求 → llm.request → 总闸 → 归一 → 计量 → 落 llm.response。

        全系统除本模块外不存在直连模型端点路径(INV-02);工具轮 content=null 归一 "";
        双空响应(content null 且无 tool_calls)= 协议异常,不回空文本冒充成功(ADI §1.3)。
        """
        cfg_llm = self._llm_cfg(ctx)
        transport = self._ensure_transport()
        tools_clean, name_map = _sanitize_tools(tools)   # 禁点号工具名清洗(DeepSeek)
        req = {"model": self.model, "messages": list(messages),
               "tools": tools_clean or None,    # 无工具整字段省略在传输层(wire 规则)
               "temperature": cfg_llm.temperature,  # 0-1.5(越窗已在配置层 CFG-601 拒)
               "max_tokens": output_limit}
        request_event = await ctx.session.append("llm.request", _request_payload(self.model, self._deg,
                                                                 tools), actor="llm", task_id=getattr(ctx, "task_id", None))
        from pyharness.core.llm_diagnostics import observed_wait
        observation = {}
        try:
            raw = await observed_wait(           # 总时长闸(F017);只取消本任务
                transport.complete(req), timeout=self.timeout.total_s, observation=observation)
        except Exception as e:                      # noqa: BLE001 传输细分统一归一(ADI §7.2)
            code = normalize_exc(e)                 # 非 API 异常:原样上抛(不上 LLM 码)
            if code == "LLM-303":
                self._note_rate_limit(ctx)          # 归一 303 计入连续限流(降级判定用)
            await self._raise_diagnostic(e, code, ctx, request_event.seq)
        try:
            msg, finish = self._first_message(raw)
        except (ProviderProtocolError, AttributeError, IndexError, TypeError) as exc:
            await self._raise_diagnostic(ProviderProtocolError(), "LLM-304", ctx, request_event.seq, observation=observation)
        content = (msg.content or "") if msg.content is not None else ""
        calls = parse_tool_calls(msg)               # ToolCallSyntaxError 上抛 → TLB-803 回喂
        _restore_call_names(calls, name_map)        # sanitized → fs.read_file 还原
        if not content and not calls:
            await self._raise_diagnostic(ProviderProtocolError(), "LLM-304", ctx, request_event.seq, observation=observation)
        usage = getattr(raw, "usage", None)
        if usage is not None:
            await self.report_usage(usage, self.model, ctx=ctx)   # F029:落 llm.usage+计数器
        env = await ctx.session.append(             # 偏离 1:单条 llm.response 落盘
            "llm.response", _response_payload(self.model, finish, content, calls, request_event.seq),
            actor="llm")
        return LLMResponse(content=content, tool_calls=calls or None, usage=usage,
                           model=self.model, finish_reason=finish, raw=raw, seq=env.seq)

    async def chat_stream(self, messages: list[dict], tools: Optional[list] = None, *,
                          ctx: Any) -> LLMResponse:
        return await self._budgeted_request(messages, tools, ctx=ctx, streaming=True)

    async def _chat_stream(self, messages: list[dict], tools: Optional[list] = None, *,
                           ctx: Any, output_limit: int) -> LLMResponse:
        """流式编排:chunk 实时上总线(accumulate 内,不进日志);聚合后同 chat 型返回。"""
        cfg_llm = self._llm_cfg(ctx)
        transport = self._ensure_transport()
        tools_clean, name_map = _sanitize_tools(tools)   # 禁点号工具名清洗(DeepSeek)
        req = {"model": self.model, "messages": list(messages),
               "tools": tools_clean or None,
               "temperature": cfg_llm.temperature, "max_tokens": output_limit,
               "stream": True, "stream_options": {"include_usage": True}}
        request_event = await ctx.session.append("llm.request", _request_payload(self.model, self._deg,
                                                                 tools), actor="llm", task_id=getattr(ctx, "task_id", None))
        from pyharness.core.llm_diagnostics import observed_wait
        observation = {}
        progress = {}
        try:
            stream = transport.stream(req)          # 返回异步迭代器(鸭子;可注入替身)
            text, calls, finish, usage = await observed_wait(
                accumulate_stream(stream, ctx, progress=progress), timeout=self.timeout.total_s, observation=observation)
        except BaseException as e:                 # Include cancellation after emitted tokens.
            partial = ''.join(progress.get('parts', [])) + ''.join(
                slot['arguments'] for slot in progress.get('slots', {}).values())
            observed_usage = progress.get('usage')
            if observed_usage is not None or partial:
                # Missing provider totals remain unknown. Charge only an output
                # estimate from received bytes, through the same task/ancestor
                # ledger; never invent unobserved input or claim exact billing.
                observed_usage = observed_usage or SimpleNamespace(
                    prompt_tokens=0, completion_tokens=_est_tokens(partial),
                    prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=0,
                    estimated=True, input_unknown=True)
                try:
                    await self.report_usage(observed_usage, self.model, ctx=ctx)
                except Exception:
                    log.warning('interrupted stream usage persistence failed')
            if isinstance(e, asyncio.CancelledError):
                raise
            code = normalize_exc(e)
            if code == "LLM-303":
                self._note_rate_limit(ctx)          # 归一 303 计入连续限流(降级判定用)
            await self._raise_diagnostic(e, code, ctx, request_event.seq)
        _restore_call_names(calls, name_map)        # sanitized → fs.read_file 还原
        if not text and not calls:
            await self._raise_diagnostic(ProviderProtocolError(), "LLM-304", ctx, request_event.seq, observation=observation)
        if usage is not None:                       # 流式计量在 include_usage 末块(F029)
            await self.report_usage(usage, self.model, ctx=ctx)
        elif text or calls:
            # P1-3 盲区修复:端点忽略 include_usage 或断流缺末块 → 整轮流式零入账,
            # 预算闸(F032)对流式全程无感知。按已拼内容兜底估算 out_tokens(仅 output;
            # input 不猜 0,账目保守),杜绝"流式长对话超支无感"。
            fallback_usage = SimpleNamespace(
                prompt_tokens=0, completion_tokens=_est_tokens(text),
                prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=0,
                estimated=True, input_unknown=True)
            await self.report_usage(fallback_usage, self.model, ctx=ctx)
        env = await ctx.session.append(             # 聚合完成落单条 llm.response(F027)
            "llm.response", _response_payload(self.model, finish or "unknown",
                                              text, calls, request_event.seq), actor="llm")
        return LLMResponse(content=text, tool_calls=calls or None, usage=usage,
                           model=self.model, finish_reason=finish or "unknown",
                           raw=None, seq=env.seq)

    async def _raise_diagnostic(self, exc, code, ctx, request_seq, *, observation=None):
        from pyharness.core.llm_diagnostics import diagnostics
        exc.diagnostics = {**(observation or {}), **getattr(exc, "diagnostics", {})}
        d = diagnostics(exc, code=code, model=self.model, request_seq=request_seq,
            endpoint=getattr(self._transport, '_base_url', getattr(self._triple, 'base_url', None)))
        scrub = getattr(self._transport, '_safe_diagnostics', None)
        if callable(scrub):
            d = scrub(d)
        await ctx.session.append('llm.error', {'code': code, 'message': d['summary'],
            'retryable': d['safe_to_retry'] is True, 'attempt': d['attempt'], 'diagnostics': d},
            actor='llm', task_id=getattr(ctx, 'task_id', None), sync=True)
        raise_code(code, model=self.model, retryable=code in _RETRYABLE_CODES, diagnostics=d)

    async def report_usage(self, usage: Any, model: str, *, ctx: Any) -> Any:
        """F029 适配器侧计量(spec chat 伪码 self.report_usage 调用形;实现见模块级函数)。

        计数归属:显式注入(engine 装配共享实例)优先;未注入(桌面/CLI 装配期
        适配器先于 ctx)且 ctx.counters 在岗 → 落 ctx.counters——预算闸与
        llm 计量强制同源(scope._counters == ctx.counters == 本实例)。"""
        cnt = getattr(ctx, 'counters', None) or self._counters
        return await report_usage(usage, model, ctx=ctx, counters=cnt)

    def _note_rate_limit(self, ctx: Any) -> None:
        """LLM-303 归一时计入"连续限流"计数(供 llm_fallback._may_degrade 阈值判定)。

        计数归属与 report_usage 同源(显式注入优先,否则 ctx.counters);计数器缺
        rate_limit_add(测试替身)→ 守卫式跳过。修复:此前 rate_limit_add 全库无调用
        → _rate_limit_streak 恒 0 → "连续限流达阈值降级"分支永不触发。
        已知限制:无"成功重置"点,计数为会话内累计而非严格"连续"。
        """
        cnt = getattr(ctx, 'counters', None) or self._counters
        add = getattr(cnt, "rate_limit_add", None)
        if callable(add):
            add()

    async def ping(self) -> float:
        """健康探针:传输往返秒;失败 → LLM-303(探针语义,不计 F029/不落 llm.retry)。"""
        transport = self._ensure_transport()
        try:
            return await transport.ping()
        except Exception as e:                      # noqa: BLE001
            try:
                code = normalize_exc(e)
            except Exception:                       # noqa: BLE001 非 API 异常:探针统一 303
                code = "LLM-303"
            from pyharness.core.llm_diagnostics import diagnostics, model_call
            with model_call('probe'):
                d = diagnostics(e, code=code, model=self.model,
                    endpoint=getattr(transport, '_base_url', None))
            scrub = getattr(transport, '_safe_diagnostics', None)
            if callable(scrub): d = scrub(d)
            raise_code(code, model=self.model, retryable=True, diagnostics=d)

