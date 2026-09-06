# specs/llm.py.md — 编码规格

> **模块文件**:`pyharness/core/llm.py` | **功能编号**:F012(核心)· F017 · F027 · F029 · F030(F033 探针读数在 llm_fallback.py 编排) | **权威口径**:PRD-Core §5.2 F012/F017、§5.3 F027-F030(功能权威)、DIS-CORE §4(编排伪代码权威)、ADI §1/§2/§4/§5/§7/§8(协议与解析层)、ERR.md §2.4(LLM-3xx 码权威)、EVENT-SCHEMA(llm.* 字段)
> **一句话**:脊柱唯一 LLM 出口——OpenAI 兼容客户端(DeepSeek 主模型)请求组装与发送、tool_calls wire 解析(arguments 二次 json.loads 容错)、流式聚合、错误归一、usage 计量;LLM 零信任(原则 4)的物理闸门,全系统除本模块外不存在"直连模型端点"路径(INV-02)。

## 模块职责

1. **唯一出口 `chat`**(F012):选适配器 → 组请求(四种 role 消息 + tools schema + 温度/输出上限)→ 总超时闸(180s)→ 错误归一 → 计量 → 结构化返回;任何模块禁止绕过 agent-loop 直调模型(INV-02)。
2. **协议与解析层**(ADI §1):`parse_tool_calls` 负责 assistant 消息 tool_calls 拆解——`function.arguments` 是**字符串内嵌 JSON**,必须二次 `json.loads`;失败抛 `ToolCallSyntaxError` → 上层映射 TLB-803 回喂,零执行、零补全(零猜原则)。
3. **流式聚合**(F027):chunk 只经 `bus.emit("llm.chunk")` 给 UI、**不进日志**;增量 tool_calls 按 index 分片合并(跨块追加不覆盖);完成时聚合为单条 `llm.response` 落盘,与逐 chunk 拼接逐字节一致(INV 比对)。
4. **错误归一**(ADI §7):只按异常类别/HTTP 状态判定(ADR-011),绝不匹配厂商错误文本;归一后对外仅 LLM-301/302/303/304 五码,非 API 异常不上 LLM 码(内部缺陷 → CYC-999)。
5. **用量计量**(F029):`report_usage` 每成功请求落 `llm.usage` 事件(model/in_tokens/out_tokens/cost_est)+ 更新 `UsageCounters`;计数器可由事件日志重建(INV-01),预算硬闸只读 token 数(F032)。

## 依赖

- **依赖方向**(§0.3 拓扑):`llm → scope(预算联动,读窗口/预算)`;llm 经 `ctx.session.append` 落事件、经 `bus.emit` 发 chunk;本模块调用 `llm_fallback.chat_with_fallback`(同模块族编排层)与 `scope` 预算读数。
- **消费方**:agent-loop(`chat`/`chat_stream`)、llm_fallback(降级链重试目标)、agent(会话装配)。
- 外部依赖:`openai` SDK(AsyncOpenAI/异常类)、`errors.raise_code`(F020)、`credentials.get_secret`(F016,key 只经单口)、`events`(payload 模型);运行时第三方 ≤15(N9)。

## 数据结构表

| 字段 | 类型 | 规则 |
|---|---|---|
| `LLMResponse` | dataclass | `content/tool_calls/usage/model/finish_reason/raw`;tool_calls 保留 `raw_args` 与 `raw_json` 供审计(F026/INV-06) |
| `ToolCall` | dataclass | `id/index/name/raw_args/raw_json`;raw_json=模型原文(回填 tool 消息时原样带回,ADI §1.4.4) |
| `TimeoutLimits` | dataclass | `connect=10s / first_token=60s / total=180s`(F017);强制 `total > first > connect`,违者 CFG-601 |
| `LLMAdapter(ABC)` | 抽象类 | 统一接口 `chat/chat_stream/ping/model`;实例注入超时/计量钩子(F030) |
| `AdapterRegistry` | dict | `adapters[name]`;换模型=注册+配置(ADR-007),llm 零改动 |
| `UsageCounters` | dataclass | 会话/任务/模型三维聚合 `in/out/cache/cost`;report_usage 唯一写入,scope/agent-loop 只读 |

## 类与函数清单

### `class LLMAdapter(ABC)` + `def register_adapter(name: str, factory) -> None` — 适配器统一接口与注册表(F030)

**功能一句话**:抽象基类强制三接口;`register_adapter` 校验接口后入表,未注册模型查询 → LLM-304。

```python
class LLMAdapter(ABC):
    model: str; timeout: TimeoutLimits
    @abstractmethod
    async def chat(self, messages, tools=None, *, ctx) -> LLMResponse: ...
    @abstractmethod
    async def chat_stream(self, messages, tools=None, *, ctx) -> LLMResponse: ...
    @abstractmethod
    async def ping(self) -> float: ...            # F033 探针用;失败→抛 LLM-303

def register_adapter(name, factory):
    inst = factory()                              # 实例化即校验接口
    for m in ("chat", "chat_stream", "ping"):     # 缺方法→注册失败,不落脏表
        if not callable(getattr(inst, m, None)): raise PyHError("CFG-601",
            ctx={"adapter": name, "missing": m})
    adapters[name] = inst                         # deepseek-chat/qwen-max/自定义端点
```

**参数表**:`name`=适配器名(与模型名一致);`factory`=无参工厂(三元组/超时注入点)。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 适配器缺接口 | CFG-601 | 列缺失方法,修复注册 |
| `PyHError` | 查询未注册模型 | LLM-304 | advice=核对模型名(F030);不重试 |

**关联测试**:GWT-L4-01(错误归一无裸异常)、test_f030_adapters(接口强制)。

### `def build_client(triple: AdapterTriple, limits: TimeoutLimits) -> AsyncOpenAI` — 客户端构建(ADI §1.1)

**功能一句话**:按三元组(base_url/api_key/model)构建单客户端;**max_retries=0**(SDK 隐式重试不可审计、不可取消、不参与降级,重试由编排层显式做)。

```python
def build_client(triple, limits):
    base = triple.base_url.rstrip("/")            # 裸域或 /v1 结尾;禁写 /chat/completions
    key = credential_store.resolve(triple.api_key_ref)   # F016 单口;缺失→CRED-701
    return AsyncOpenAI(base_url=base, api_key=key,
        timeout=httpx.Timeout(limits.connect_s,          # connect 档
            read=limits.first_token_s,                   # 首 token 档
            write=limits.connect_s, pool=limits.connect_s),
        max_retries=0)                            # 重试预算全归 F028,单点留痕
```

**参数表**:`triple`=AdapterTriple(base_url/api_key_ref/model);`limits`=三档超时。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | key 解析缺失 | CRED-701 | 配置 `env:NAME` 引用后重启(不热重载) |
| `PyHError` | 时间档序 total≤first≤connect | CFG-601 | 修 config,启动中止 |

**关联测试**:test_f012_llm_client(base_url 形态/401 触发降级)。

### `async def chat(messages: list[dict], tools: list | None = None, *, ctx) -> LLMResponse` — 唯一出口(F012/DIS-CORE §4.3.1)

**功能一句话**:选适配器 → 落 llm.request → 总超时闸内调 create → 归一错误 → 计量 → 结构化返回。

```python
async def chat(self, messages, tools=None, *, ctx):
    model = self.chain.pick()                     # F033 健康择优;down 者跳过(见 llm_fallback)
    req = {"model": model, "messages": messages, "tools": tools or None,
           "temperature": ctx.config.llm.temperature,      # 0-1.5,越窗 CFG-601
           "max_tokens": ctx.config.llm.max_tokens}        # 默认 4096,单次输出帽
    await ctx.session.append("llm.request", {"model": model,
        "degraded_from": self._deg, "n_tools": len(tools or [])}, actor="llm")
    try:
        raw = await asyncio.wait_for(             # 总时长闸(F017);只取消本任务
            self._client.chat.completions.create(**req), timeout=self.limits.total)
    except asyncio.TimeoutError:
        raise PyHError("LLM-301", ctx={"model": model})     # 编排总闸
    except Exception as e:                        # SDK 细分异常统一归一(ADI §7.2)
        raise PyHError(normalize_exc(e), ctx={"model": model, "retryable": True})
    self.report_usage(raw.usage, model)           # F029:落 llm.usage+计数器(见下)
    msg = raw.choices[0].message                  # choices 空数组=内容过滤→业务错
    return LLMResponse(content=msg.content or "",  # 工具轮 content=null→归一 ""
        tool_calls=parse_tool_calls(msg),          # raw_args 原样存(INV-06)
        usage=raw.usage, model=raw.model,
        finish_reason=raw.choices[0].finish_reason, raw=raw)
```

**参数表**:`messages`=system-prompt.assemble 产物(四种 role);`tools`=schemas_for(scope) 或 None(**无工具整字段省略**,qwen 对 `[]` 语义不稳)。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `asyncio.TimeoutError` | 总闸 180s | LLM-301 | R·退避≤4(落 llm.retry)→耗尽降级 |
| `AuthenticationError` 等 | 401/403 | LLM-302 | 不重试直接降级,带 degraded_from |
| `RateLimit/断网/SDK 超时/5xx` | 429/网络 | LLM-303 | R·退避≤4→降级 |
| 其余 4xx | 400/404/422 | LLM-304 | 不重试不降级,上抛 agent-loop reason=error |
| `PyHError` | choices 空/模型未注册 | LLM-304 | 不回空文本冒充成功 |

**关联测试**:GWT-L4-01(归一:AuthenticationError/Timeout/RateLimit → 302/301/303 无裸异常外泄)、GWT-L4-03(usage 入账)。

### `def parse_tool_calls(message) -> list[ToolCall]` — tool_calls wire 解析(ADI §1.4)

**功能一句话**:拆解 assistant 消息 tool_calls 数组;`arguments` 字符串**二次 json.loads**(容错空串/纯空白视为 `{}`);语法错/非对象 → ToolCallSyntaxError,零执行。

```python
class ToolCallSyntaxError(Exception):            # 上层映射 TLB-803 回喂修正
    """arguments 非合法 JSON / 非对象 / 同响应 id 重复 → 零执行、不落脏状态。"""

def parse_tool_calls(message) -> list[ToolCall]:
    seen = set(); out = []
    for idx, tc in enumerate(message.tool_calls or []):
        if tc.type not in (None, "function"):
            continue                              # 非 function 不进管道,留 raw 审计
        if tc.id in seen:
            raise ToolCallSyntaxError(tc.id, "同响应 id 重复,拒本轮")   # 配对必乱
        seen.add(tc.id)
        raw_json = tc.function.arguments or ""    # 字符串内嵌 JSON,不是 dict
        try:
            args = json.loads(raw_json) if raw_json.strip() else {}
        except json.JSONDecodeError as e:
            raise ToolCallSyntaxError(tc.id, e) from e     # → TLB-803 回喂
        if not isinstance(args, dict):
            raise ToolCallSyntaxError(tc.id, "arguments 非 JSON 对象")   # 零猜原则
        out.append(ToolCall(id=tc.id, index=idx, name=tc.function.name,
                            raw_args=args, raw_json=raw_json))   # 原文留审计
    return out                                   # 按 index 升序=执行序
```

**参数表**:`message`=wire choices[0].message。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `ToolCallSyntaxError` | arguments JSON 语法错/非对象/id 重复 | TLB-803(上层映射) | 回喂明细零执行;连败 2 次终止轮(F026) |
| `ToolCallSyntaxError` | finish_reason=length 且 arguments 残缺 | TLB-803 | 同上;提示减小单次参数体积 |
| — | name 未注册 | TLB-802(执行期) | 解析通过,执行期回喂自查(F022) |

**关联测试**:INV-06(畸形 30 例零执行、执行 args=日志 args)、test_f022_tool_calls。

### `def assistant_and_tool_messages(calls: list[ToolCall], results: dict) -> list[dict]` — 工具轮回填消息(ADI §1.4.4)

**功能一句话**:上轮 N 条 tool_calls → assistant(tool_calls,arguments 原文回传)+ N 条 role=tool 回填;缺 result 的 call → 拒绝以残缺上下文发下一轮(与 EVT-101 同原则)。

```python
def assistant_and_tool_messages(calls, results):
    msgs = [{"role": "assistant", "content": None,      # 工具轮 content 可 null
             "tool_calls": [{"id": c.id, "type": "function",
                "function": {"name": c.name,
                             "arguments": c.raw_json}}   # 原文回传,不重序列化
                            for c in calls]}]
    for c in calls:                              # 顺序=声明顺序(数组序)
        r = results.get(c.id)                    # 半截回填(缺 1 条)→多数端点 422
        if r is None: raise PyHError("EVT-101", ctx={"call_id": c.id,
            "advice": "tool.result 缺失,拒绝以残缺上下文发下一轮"})
        msgs.append({"role": "tool", "tool_call_id": c.id, "content": r.summary})
    return msgs                                  # content 与 tool_calls 并存:两路都保留
```

**参数表**:`calls`=上轮 parse_tool_calls 产物;`results`=call_id → tool.result payload(summary 已由 F039 spill 截断先行)。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 声明的 call 无对应 result | EVT-101 族 | 拒发本轮;查丢失的 tool.result(F022) |

**关联测试**:GWT-L4-02 配套(test_f013_fallback)、test_f022_tool_calls(trace.parent_seq 关联)。

### `async def accumulate_stream(stream, ctx) -> tuple[str, list[ToolCall], str, usage]` — SSE 增量聚合(ADI §5.3)

**功能一句话**:逐 chunk 拼 content、tool_calls 按 index 定位增量槽、跨块 arguments **追加不覆盖**;usage 只出现在 include_usage 末块;聚合后统一走 parse_tool_calls 校验。

```python
async def accumulate_stream(stream, ctx):
    parts, slots, finish, usage = [], {}, None, None
    async for chunk in stream:
        if not chunk.choices:                    # choices=[]+usage:仅流末块
            usage = chunk.usage; continue
        d = chunk.choices[0].delta
        if d.content: parts.append(d.content)    # 增量文本
        for dt in (d.tool_calls or []):          # id/name 只在该 index 首块
            s = slots.setdefault(dt.index, {"id": "", "name": "", "arguments": ""})
            if dt.id: s["id"] = dt.id
            if dt.function and dt.function.name: s["name"] = dt.function.name
            if dt.function and dt.function.arguments:
                s["arguments"] += dt.function.arguments   # 跨块追加,绝不覆盖
        if chunk.choices[0].finish_reason: finish = chunk.choices[0].finish_reason
    calls = [ToolCall(id=s["id"], index=i, name=s["name"], raw_json=s["arguments"])
             for i, s in sorted(slots.items())]
    if calls: parse_tool_calls({"tool_calls": calls})    # 拼完仍可能半截 JSON→TLB-803
    return "".join(parts), calls, finish, usage   # 逐 chunk 拼接==整段文本(INV)
```

**参数表**:`stream`=SDK 流式响应迭代器。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `ToolCallSyntaxError` | 增量拼完 arguments 残缺 | TLB-803 | 回喂;流中断→F028 整请求重发 |
| 流断连/未收 [DONE] | 断流 | LLM-303 | 退避重发,UI 标"续写"(F027) |

**关联测试**:GWT-L4-04(5 chunk → 总线 5 条 llm.chunk、日志恰 1 条 llm.response)。

### `async def chat_stream(messages, tools=None, *, ctx) -> LLMResponse` — 流式入口(F027)

**功能一句话**:SSE 流式编排——chunk 只上总线不进日志;聚合结果以与 chat 同型返回并落单条 llm.response。

```python
async def chat_stream(self, messages, tools=None, *, ctx):
    model = self.chain.pick()
    stream = await self._client.chat.completions.create(
        model=model, messages=messages, tools=tools or None,
        stream=True, stream_options={"include_usage": True})
    text, calls, finish, usage = await accumulate_stream(stream, ctx)
    # chunk 已实时上总线(accumulate 内不留痕);此处只落最终事实
    if usage: self.report_usage(usage, model)     # 流式计量在末块(F029)
    resp = LLMResponse(content=text, tool_calls=calls, usage=usage,
                       model=model, finish_reason=finish, raw=None)
    await ctx.session.append("llm.response", {"model": model,
        "finish_reason": finish, "content": text}, actor="llm")
    return resp
```

**参数表**:同 `chat`。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| 断流/总闸超时 | 流中 HTTP 断连 | LLM-301/303 | 整请求退避重发(非断点续传) |
| `asyncio.CancelledError` | 用户取消(F025) | system.cancelled | 即停不重试;半截文本不当 llm.response 落日志 |
| usage 未达 | 取消/断流 | — | 不计 F029,留取消痕迹(ADI §8.5) |

**关联测试**:GWT-L4-04、test_f027_stream(chunk 只上总线/聚合一致)。

### `def report_usage(usage, model: str, *, ctx) -> Envelope` — token 计量(F029/DIS-CORE §4.3.4)

**功能一句话**:每成功请求落 `llm.usage`(in/out/cost_est)+ 更新计数器;usage 事件即事实,计数器可重建。

```python
def report_usage(self, usage, model):
    ev = ctx.session.append("llm.usage", {"model": model,
        "in_tokens": usage.prompt_tokens,
        "out_tokens": usage.completion_tokens,
        "cache_hit": getattr(usage, "prompt_cache_hit_tokens", 0) or 0,
        "cost_est": estimate_cost(model, usage, ctx.config.llm.usage.unit_price)},
        actor="llm")                              # 单价表来自 config(仅报表,N14)
    ctx.counters.task_add(ev.payload)             # 预算硬闸读 token(F032)
    return ev
```

**参数表**:`usage`=SDK usage 对象(prompt/completion/cache 扩展);`model`=**实际服务模型**(降级后记备用模型,不记主名)。**异常表**:无(纯记账;缺失缓存字段按 0,不做厂商猜)。**关联测试**:GWT-L4-03、test_f029_usage(三维聚合)。

### `def normalize_exc(e: Exception) -> str` — 错误归一(ADI §7.2)

**功能一句话**:异常类别/HTTP 状态 → LLM-3xx 唯一映射;只按类别不匹配文本;非 API 异常原样上抛(不上 LLM 码)。

```python
def normalize_exc(e) -> str:
    if isinstance(e, asyncio.TimeoutError): return "LLM-301"     # 编排总闸
    if isinstance(e, (AuthenticationError, PermissionDeniedError)):
        return "LLM-302"                                          # 401/403
    if isinstance(e, (RateLimitError, APIConnectionError,
                      APITimeoutError, InternalServerError)):
        return "LLM-303"                                          # 429/断网/超时/5xx
    if isinstance(e, (BadRequestError, NotFoundError,
                      UnprocessableEntityError, APIStatusError, APIError)):
        return "LLM-304"                                          # 4xx 业务/未知
    raise e                                  # 非 API 异常不上 LLM 码(→CYC-999 兜底)
```

**参数表**:`e`=SDK/httpx 异常。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| 非 API 异常 | 内部缺陷 | 不上码(上层 CYC-999) | 本地堆栈排查;禁伪装 LLM 码 |

**关联测试**:GWT-L4-01、GWT-ERR-05(归一+降级对齐)。

### `def estimate_cost(model, usage, prices: dict, hit_discount: float = 1.0) -> float` — 成本估算(ADI §8.2)

**功能一句话**:元/百万单价表 ×(未命中输入 + 命中输入×折扣 + 输出)估算成本;只进事件/报表,不打预算硬闸(N14)。

```python
def estimate_cost(model, usage, prices, hit_discount=1.0):
    p = prices[model]                            # {in_per_million, out_per_million}
    hit = getattr(usage, "prompt_cache_hit_tokens", 0) or 0
    miss = getattr(usage, "prompt_cache_miss_tokens", 0) or 0
    if not (hit or miss): miss = getattr(usage, "prompt_tokens", 0) or 0  # 无缓存字段厂商
    billed_in = miss + hit * hit_discount        # 命中按折扣价(官方约 1/10)
    return (billed_in / 1e6 * p["in_per_million"]
            + usage.completion_tokens / 1e6 * p["out_per_million"])
```

**参数表**:`usage` 无缓存字段时按全价估算(保守)。**异常表**:无。**关联测试**:test_f029_usage(缓存会计:token 计数照算,折扣只影响 cost_est)。

## 关联文档

| 文档 | 章节 | 关系 |
|---|---|---|
| PRD-Core.md | §5.2 F012/F017、§5.3 F027-F030 | 功能与边界权威 |
| DIS-CORE.md | §4(4.3.1/4.3.4) | 编排伪代码权威(本文件落地) |
| ADI.md | §1/§2/§4/§5/§7/§8 | 协议/解析层细则唯一来源 |
| ERR.md | §2.4 LLM-3xx、§4-⑤、§5.2 | 错误码与模型失败链 |
| EVENT-SCHEMA.md | llm.* 事件 | payload 字段级权威 |
| CONSTRAINTS-07 | §10 | 成本计量验收口径 |
