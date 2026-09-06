"""llm 模块单测 — 契约:specs/llm.py.md + PARAMETER-ANCHOR(10s/60s/180s)+ ERR.md(LLM-3xx)

覆盖面(任务要求全项):
    超时映射:TimeoutLimits 档序(违者 CFG-601)、总闸超时 → LLM-301(chat 链路)
    tool_calls 坏 JSON 修复:二次 json.loads 容错(空串/纯空白 → {})、语法错/非对象/
        id 重复 → ToolCallSyntaxError(零猜原则,上层 TLB-803);流式增量分片合并(追加不覆盖)
    错误归一:normalize_exc 五码矩阵(301/302/303/304)+ 非 API 异常原样上抛(不上码);
        HTTP 401/403→302、429/5xx→303、其余 4xx→304
    mock Provider 调用链:替身传输注入 → chat/chat_stream 全生命周期
        (llm.request → 计量 → llm.response;事件序/seq 回填/usage 入账/计数器)
    计量:report_usage/estimate_cost(缓存折扣、缺单价表不炸记账)
    注册表:register_adapter 接口强制(CFG-601)、未注册查询 → LLM-304
    凭据单口:env:/file: 解析、缺失 → CRED-701

铁律 5:本文件零真实网络——传输全部注入替身;真实端点路径(build_client httpx 传输)
只做构造级冒烟,不发请求。
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from pyharness.core.llm import (  # noqa: E501
    LLMClient, LLMResponse, LLMAdapter, OpenAICompatAdapter,
    ProviderProtocolError, ProviderStatusError, TimeoutLimits, ToolCall,
    ToolCallSyntaxError, UsageCounters, accumulate_stream, adapters,
    assistant_and_tool_messages, build_client, estimate_cost, normalize_exc,
    parse_tool_calls, register_adapter, report_usage, require_adapter,
    resolve_secret_ref,
)
from pyharness.errors import ConfigError, CredentialError, PyHError, raise_code
from pyharness.core.session import SessionLog

MODEL = "deepseek-chat"
SID = "s-llm-unit"
PRICES = {  # 与 CFG L1 单价表一致(元/百万 token;只影响估算/报表)
    "deepseek-chat": {"in_per_million": 2.0, "out_per_million": 8.0},
    "qwen-max": {"in_per_million": 4.0, "out_per_million": 12.0},
}


# ===================================================================== 替身
def _llm_cfg(temperature: float = 0.7, max_tokens: int = 4096) -> SimpleNamespace:
    """ctx.config.llm 替身(测试钉死取值,不读环境/文件,保证断言确定)。"""
    return SimpleNamespace(temperature=temperature, max_tokens=max_tokens,
                           usage=SimpleNamespace(unit_price=dict(PRICES)))


def _usage(prompt: int = 20, out: int = 5, *, hit: int = None, miss: int = None,
           prompt_only: bool = False) -> SimpleNamespace:
    """传输层 usage 替身(prompt_only = 无缓存扩展字段的厂商形态)。"""
    kw = {"prompt_tokens": prompt, "completion_tokens": out}
    if not prompt_only:
        if hit is not None:
            kw["prompt_cache_hit_tokens"] = hit
        if miss is not None:
            kw["prompt_cache_miss_tokens"] = miss
    return SimpleNamespace(**kw)


def _tc(tc_id: str, name: str, arguments: str) -> SimpleNamespace:
    """wire tool_call 元素替身(arguments = 字符串内嵌 JSON,非 dict)。"""
    return SimpleNamespace(id=tc_id, type="function",
                           function=SimpleNamespace(name=name, arguments=arguments))


def _raw(content=None, tool_calls=None, *, finish: str = "stop",
         usage=None, model: str = MODEL) -> SimpleNamespace:
    """非流式 raw 响应替身(choices/message/usage 鸭子同构)。"""
    msg = SimpleNamespace(role="assistant", content=content, tool_calls=tool_calls)
    return SimpleNamespace(model=model, usage=usage,
                           choices=[SimpleNamespace(message=msg,
                                                    finish_reason=finish)])


def _chunk(content=None, tool_calls=None, *, finish=None, usage=None) -> SimpleNamespace:
    """SSE chunk 替身(choices 空 = 仅流末 usage 块)。"""
    if usage is not None and not content and not tool_calls and finish is None:
        return SimpleNamespace(choices=[], usage=usage)
    delta = SimpleNamespace(content=content, tool_calls=tool_calls, role=None)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish,
                                                    index=0)],
                           usage=None)


def _dt(index: int, tc_id=None, name: str = "", arguments: str = "") -> SimpleNamespace:
    """流式增量 tool_calls delta 替身(id/name 只在该 index 首块出现)。"""
    fn = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(index=index, id=tc_id, type="function", function=fn)


class FakeTransport:
    """注入式传输替身(零网络):记录 req + 依脚本回放 result/error/chunks。"""

    def __init__(self, result=None, error=None, chunks=None) -> None:
        self.result = result
        self.error = error
        self.chunks = list(chunks or [])
        self.requests: list = []
        self.pings = 0

    async def complete(self, req: dict):
        self.requests.append(req)
        if self.error is not None:
            raise self.error
        return self.result

    def stream(self, req: dict):
        self.requests.append(req)

        async def _gen():
            if self.error is not None:
                raise self.error
            for c in self.chunks:
                yield c

        return _gen()

    async def ping(self) -> float:
        self.pings += 1
        if self.error is not None:
            raise self.error
        return 0.05


class FakeBus:
    """总线替身:记录 emit 事件(llm.chunk 等),供聚合断言。"""

    def __init__(self) -> None:
        self.events: list = []

    async def emit(self, type_: str, payload: dict, **kw) -> dict:
        self.events.append((type_, dict(payload)))
        return {"delivered": 1, "errored": 0}


async def _session() -> SessionLog:
    """纯内存会话引导(首事件 session.created,seq=1;append 校验链全走)。"""
    s = SessionLog(sid=SID)
    await s.append("session.created", {"title": "", "model": MODEL}, actor="system")
    return s


def _ctx(session, *, counters: UsageCounters = None, bus: FakeBus = None) -> SimpleNamespace:
    """chat 消费 ctx 替身(config/session/counters/bus 齐全)。"""
    return SimpleNamespace(config=SimpleNamespace(llm=_llm_cfg()),
                           session=session, counters=counters, bus=bus)


def _adapter(result=None, *, error=None, chunks=None, counters: UsageCounters = None,
             model: str = MODEL) -> OpenAICompatAdapter:
    """注入替身传输的适配器(不触网;limits 锁 10/60/180)。"""
    return OpenAICompatAdapter(model, transport=FakeTransport(result=result, error=error,
                                                              chunks=chunks),
                               limits=TimeoutLimits(10, 60, 180), counters=counters)


def _event_types(s: SessionLog) -> list:
    """会话事件类型序列(含 created;断言全生命周期事件序)。"""
    return [e.type for e in s.events_after(0)]


def _event(s: SessionLog, type_: str):
    """取会话中最后一条指定类型事件(断言 payload 字段用)。"""
    hits = [e for e in s.events_after(0) if e.type == type_]
    assert hits, f"会话日志缺事件 {type_}: {_event_types(s)}"
    return hits[-1]


# ===================================================================== 超时映射
class TestTimeoutLimits:
    """三档超时(PARAMETER-ANCHOR 锁死 10/60/180):默认值/构建/档序强校验。"""

    def test_defaults_anchor(self) -> None:
        t = TimeoutLimits()
        assert (t.connect_s, t.first_token_s, t.total_s) == (10, 60, 180)

    def test_explicit_valid(self) -> None:
        t = TimeoutLimits(5, 30, 120)
        assert (t.connect_s, t.first_token_s, t.total_s) == (5, 30, 120)

    def test_from_cfg(self) -> None:
        t = TimeoutLimits.from_cfg(SimpleNamespace(connect_s=1, first_token_s=2,
                                                   total_s=3))
        assert (t.connect_s, t.first_token_s, t.total_s) == (1, 2, 3)

    @pytest.mark.parametrize("triple", [(10, 10, 180), (10, 60, 60),
                                        (60, 10, 180), (10, 180, 60)])
    def test_order_violation_cfg601(self, triple) -> None:
        """档序违例(total≤first 或 first≤connect)→ CFG-601 启动拒(禁半套超时)。"""
        with pytest.raises(PyHError) as ei:
            TimeoutLimits(*triple)
        assert ei.value.code == "CFG-601"
        assert isinstance(ei.value, ConfigError)


# ===================================================================== 错误归一
class TestNormalizeExc:
    """normalize_exc 五码唯一映射(ADI §7):只按类别/HTTP 状态,不匹配厂商文本。"""

    def test_total_gate_timeout_301(self) -> None:
        assert normalize_exc(asyncio.TimeoutError()) == "LLM-301"

    @pytest.mark.parametrize("status,code", [
        (401, "LLM-302"), (403, "LLM-302"),          # 认证/权限:不重试直接降级
        (429, "LLM-303"), (500, "LLM-303"), (503, "LLM-303"),  # 限流/5xx:可退避
        (400, "LLM-304"), (404, "LLM-304"), (422, "LLM-304"),  # 业务 4xx:不重试不降级
    ])
    def test_http_status_mapping(self, status, code) -> None:
        assert normalize_exc(ProviderStatusError(status)) == code

    def test_protocol_error_304(self) -> None:
        assert normalize_exc(ProviderProtocolError("choices 空")) == "LLM-304"

    def test_httpx_transport_errors_303(self) -> None:
        """断网/连接/读超时(httpx 传输族)→ LLM-303。"""
        httpx = pytest.importorskip("httpx")
        assert normalize_exc(httpx.ConnectError("conn")) == "LLM-303"
        assert normalize_exc(httpx.ReadTimeout("read")) == "LLM-303"
        assert normalize_exc(httpx.ConnectTimeout("ct")) == "LLM-303"

    def test_non_api_exception_reraised(self) -> None:
        """非 API 异常不上 LLM 码(内部缺陷 → CYC-999 兜底;禁伪装模型域错误)。"""
        with pytest.raises(ValueError):
            normalize_exc(ValueError("内部缺陷"))

    def test_unknown_httpx_error_reraised(self) -> None:
        httpx = pytest.importorskip("httpx")
        # HTTPStatusError 亦属 httpx 族:按状态映射(400 → LLM-304)
        req = httpx.Request("POST", "https://x/v1/chat/completions")
        resp = httpx.Response(400, request=req)
        assert normalize_exc(httpx.HTTPStatusError("bad", request=req,
                                                   response=resp)) == "LLM-304"


# ===================================================================== tool_calls 解析
class TestParseToolCalls:
    """wire tool_calls 拆解(ADI §1.4):arguments 二次 json.loads 容错 + 零猜原则。"""

    def _msg(self, calls):
        return SimpleNamespace(content=None, tool_calls=calls)

    def test_multi_calls_parsed_in_order(self) -> None:
        msg = self._msg([
            _tc("call_a", "fs.read_file", '{"path": "C:/x", "offset": 1}'),
            _tc("call_b", "workspace.list_dir", '{"path": "C:/x"}'),
        ])
        out = parse_tool_calls(msg)
        assert [c.id for c in out] == ["call_a", "call_b"]
        assert [c.index for c in out] == [0, 1]          # 数组序 = 执行序
        assert out[0].name == "fs.read_file"
        assert out[0].raw_args == {"path": "C:/x", "offset": 1}
        assert out[0].raw_json == '{"path": "C:/x", "offset": 1}'  # 原文留审计(INV-06)
        assert out[0].call_id == "call_a"                # 工具层兼容别名(偏离 5)

    def test_empty_arguments_tolerated(self) -> None:
        """空串/纯空白/None → {} 容错(无参调用合法,不误伤)。"""
        for raw in ("", "   ", None):
            out = parse_tool_calls(self._msg([_tc("c1", "t", raw)]))
            assert out[0].raw_args == {}
            assert out[0].raw_json == (raw or "")

    def test_non_function_skipped_index_preserved(self) -> None:
        msg = self._msg([
            _tc("call_a", "fs.read_file", '{"path": "1"}'),
            SimpleNamespace(id="c-int", type="code_interpreter", function=None),
            _tc("call_b", "workspace.list_dir", '{"path": "2"}'),
        ])
        out = parse_tool_calls(msg)
        assert [c.id for c in out] == ["call_a", "call_b"]
        assert [c.index for c in out] == [0, 2]          # 跳过非 function,index 保留原数组位

    def test_no_tool_calls(self) -> None:
        assert parse_tool_calls(SimpleNamespace(content="hi", tool_calls=None)) == []
        assert parse_tool_calls(SimpleNamespace(content="hi")) == []  # 缺字段防御

    @pytest.mark.parametrize("raw", ["{not-json", '{"a": 1', "[1,2,3]", '"str"', "42"])
    def test_bad_arguments_raise_syntax_error(self, raw) -> None:
        """语法错/非 JSON 对象 → ToolCallSyntaxError(上层 TLB-803 回喂,零执行)。"""
        with pytest.raises(ToolCallSyntaxError) as ei:
            parse_tool_calls(self._msg([_tc("call_x", "t", raw)]))
        assert ei.value.call_id == "call_x"

    def test_duplicate_id_rejected(self) -> None:
        """同响应 id 重复(配对必乱)→ ToolCallSyntaxError,拒本轮。"""
        with pytest.raises(ToolCallSyntaxError) as ei:
            parse_tool_calls(self._msg([_tc("dup", "t1", "{}"),
                                        _tc("dup", "t2", '{"k": 1}')]))
        assert ei.value.call_id == "dup"


class TestAssistantAndToolMessages:
    """工具轮回填(ADI §1.4.4):arguments 原文回传 + N 条 tool;缺 result 拒发。"""

    def _calls(self):
        return [ToolCall(id="c1", index=0, name="fs.read_file",
                         raw_json='{"path": "a",\n "x": 1}'),   # 带缩进原文(重序列化会丢)
                ToolCall(id="c2", index=1, name="workspace.list_dir", raw_json="{}")]

    def test_assistant_passthrough_raw_json(self) -> None:
        """assistant.tool_calls.arguments 必须等于模型原文(不重序列化,INV-06)。"""
        msgs = assistant_and_tool_messages(self._calls(),
                                           {"c1": SimpleNamespace(summary="ok1"),
                                            "c2": SimpleNamespace(summary="ok2")})
        assert msgs[0]["role"] == "assistant"
        assert msgs[0]["content"] is None                   # 工具轮 content 可 null
        tcs = msgs[0]["tool_calls"]
        assert [t["id"] for t in tcs] == ["c1", "c2"]
        assert tcs[0]["function"]["arguments"] == '{"path": "a",\n "x": 1}'  # 原文
        assert [m["role"] for m in msgs[1:]] == ["tool", "tool"]
        assert msgs[1] == {"role": "tool", "tool_call_id": "c1", "content": "ok1"}
        assert msgs[2]["content"] == "ok2"

    def test_result_dict_form_supported(self) -> None:
        msgs = assistant_and_tool_messages(self._calls(),
                                           {"c1": {"summary": "s1"},
                                            "c2": {"summary": "s2"}})
        assert msgs[1]["content"] == "s1"

    def test_missing_result_rejected_evt101(self) -> None:
        """声明的 call 无对应 result → 拒绝以残缺上下文发下一轮(EVT-101 族)。"""
        with pytest.raises(PyHError) as ei:
            assistant_and_tool_messages(self._calls(), {"c1": SimpleNamespace(summary="ok")})
        assert ei.value.code == "EVT-101"
        assert ei.value.ctx.get("call_id") == "c2"


class TestAccumulateStream:
    """SSE 增量聚合(F027/ADI §5.3):分片追加不覆盖、usage 末块、chunk 只上总线。"""

    async def test_content_and_tool_merge(self) -> None:
        bus = FakeBus()
        usage = _usage(prompt=10, out=3)
        chunks = [
            _chunk(content="你"),                          # 文本分片 1
            _chunk(content="好"),                          # 文本分片 2
            _chunk(tool_calls=[_dt(0, tc_id="c1", name="fs.read_file",
                                   arguments='{"path": ')]),   # 工具分片(首块:id/name)
            _chunk(tool_calls=[_dt(0, arguments='"C:/x"}' )]),  # 工具分片(续块:仅 arguments)
            _chunk(content="!", finish="tool_calls"),
            _chunk(usage=usage),                           # choices=[] 仅末块带 usage
        ]
        text, calls, finish, got_usage = await accumulate_stream(
            FakeTransport(chunks=chunks).stream({}), SimpleNamespace(bus=bus))
        assert text == "你好!"                             # 逐 chunk 拼接
        assert len(calls) == 1
        assert calls[0].id == "c1" and calls[0].name == "fs.read_file"
        assert calls[0].raw_json == '{"path": "C:/x"}'     # 跨块追加,绝不覆盖
        assert calls[0].raw_args == {"path": "C:/x"}
        assert finish == "tool_calls"
        assert got_usage is usage
        # chunk 只上总线(3 条文本 delta:你/好/!),不进日志
        assert [t for t, _ in bus.events] == ["llm.chunk", "llm.chunk", "llm.chunk"]
        assert [p["delta"] for _, p in bus.events] == ["你", "好", "!"]

    async def test_no_bus_skips_emit(self) -> None:
        """ctx 未装配总线:聚合照常,不 emit 不报错(纯内存模式)。"""
        chunks = [_chunk(content="a"), _chunk(content="b", finish="stop")]
        text, calls, finish, usage = await accumulate_stream(
            FakeTransport(chunks=chunks).stream({}), SimpleNamespace(bus=None))
        assert text == "ab" and finish == "stop"

    async def test_broken_merged_json_raises(self) -> None:
        """分片拼完仍半截 JSON → ToolCallSyntaxError(流中断语义,上层整请求重发)。"""
        chunks = [
            _chunk(tool_calls=[_dt(0, tc_id="c1", name="t", arguments='{"a":')]),
            _chunk(tool_calls=[_dt(0, arguments="")]),
        ]
        with pytest.raises(ToolCallSyntaxError) as ei:
            await accumulate_stream(FakeTransport(chunks=chunks).stream({}),
                                    SimpleNamespace(bus=None))
        assert ei.value.call_id == "c1"

    async def test_usage_only_tail_and_empty(self) -> None:
        usage = _usage(prompt=1, out=1)
        text, calls, finish, got = await accumulate_stream(
            FakeTransport(chunks=[_chunk(usage=usage)]).stream({}),
            SimpleNamespace(bus=None))
        assert text == "" and calls == [] and got is usage


# ===================================================================== chat(唯一出口)
class TestChat:
    """chat 非流式唯一出口(F012):mock Provider 全链路(事件序/计量/归一/seq)。"""

    async def test_happy_path_text(self) -> None:
        """纯文本轮:request→usage→response 事件序、seq 回填、usage 入账、计数器更新。"""
        s = await _session()
        counters = UsageCounters()
        tr = FakeTransport(result=_raw(content="你好世界", usage=_usage(prompt=20, out=5)))
        adp = _adapter(result=None, counters=counters)
        adp._transport = tr                                # 替换注入传输(单测可写)
        ctx = _ctx(s, counters=counters)
        resp = await adp.chat([{"role": "user", "content": "hi"}], ctx=ctx)
        # 结构化返回
        assert isinstance(resp, LLMResponse)
        assert resp.content == "你好世界"
        assert not resp.tool_calls
        assert resp.model == MODEL and resp.finish_reason == "stop"
        assert resp.seq == _event(s, "llm.response").seq      # 偏离 1:llm.response seq
        # mock Provider 调用链:req 组装字段(spec 契约)
        assert len(tr.requests) == 1
        req = tr.requests[0]
        assert req["model"] == MODEL
        assert req["messages"] == [{"role": "user", "content": "hi"}]
        assert req["tools"] is None                        # 无工具 = None(整字段省略在传输层)
        assert req["temperature"] == 0.7 and req["max_tokens"] == 4096
        assert "stream" not in req
        # 事件序(llm.request → llm.usage → llm.response)
        assert _event_types(s) == ["session.created", "llm.request", "llm.usage",
                                   "llm.response"]
        req_ev = _event(s, "llm.request").payload
        assert req_ev["model"] == MODEL and req_ev["n_tools"] == 0
        use_ev = _event(s, "llm.usage").payload
        assert use_ev["model"] == MODEL
        assert use_ev["in_tokens"] == 20 and use_ev["out_tokens"] == 5
        assert use_ev["cache_hit"] == 0
        assert use_ev["cost_est"] == pytest.approx(20 / 1e6 * 2.0 + 5 / 1e6 * 8.0)
        resp_ev = _event(s, "llm.response").payload
        assert resp_ev["content"] == "你好世界"
        # 计数器(F029 唯一写入;F032 硬闸读 token)
        snap = counters.snapshot()
        assert snap["requests"] == 1
        assert snap["in_tokens"] == 20 and snap["out_tokens"] == 5
        assert snap["cost_est"] == pytest.approx(use_ev["cost_est"])
        assert MODEL in snap["by_model"]

    async def test_happy_path_with_tools_n_tools(self) -> None:
        s = await _session()
        adp = _adapter(result=_raw(content="用工具", usage=_usage(prompt=1, out=1)))
        tools = [{"type": "function", "function": {"name": "fs.read_file"}}]
        ctx = _ctx(s)
        resp = await adp.chat([{"role": "user", "content": "hi"}], tools=tools, ctx=ctx)
        assert adp._transport.requests[0]["tools"] == tools   # 有工具照传
        assert _event(s, "llm.request").payload["n_tools"] == 1
        assert resp.content == "用工具"

    async def test_tool_round_content_null_normalized(self) -> None:
        """工具轮:content=null → 归一 "";llm.response.tool_calls arguments 原文回填。"""
        s = await _session()
        raw_json = '{\n "path": "C:/x"\n}'                # 带格式原文(须原样回填)
        raw = _raw(content=None, finish="tool_calls", usage=_usage(prompt=30, out=12),
                   tool_calls=[_tc("call_1", "fs.read_file", raw_json),
                               _tc("call_2", "workspace.list_dir", "{ }")])
        adp = _adapter(result=raw)
        ctx = _ctx(s)
        resp = await adp.chat([{"role": "user", "content": "ls"}], ctx=ctx)
        assert resp.content == ""                          # content null → ""
        assert resp.finish_reason == "tool_calls"
        assert resp.tool_calls is not None and len(resp.tool_calls) == 2
        c0 = resp.tool_calls[0]
        assert c0.id == "call_1" and c0.raw_args == {"path": "C:/x"}
        assert c0.raw_json == raw_json                     # 原文留审计(INV-06)
        # llm.response 事件 tool_calls = 原文 arguments(不重序列化)
        ev = _event(s, "llm.response").payload
        assert ev["content"] == ""
        assert ev["tool_calls"][0]["arguments"] == raw_json
        assert [t["id"] for t in ev["tool_calls"]] == ["call_1", "call_2"]

    async def test_total_gate_timeout_301(self) -> None:
        """总闸超时(编排 180s)经归一 → LLM-301,可重试(退避预算归 F028)。"""
        s = await _session()
        adp = _adapter(error=asyncio.TimeoutError())       # 模拟 wait_for 总闸触发
        with pytest.raises(PyHError) as ei:
            await adp.chat([{"role": "user", "content": "x"}], ctx=_ctx(s))
        assert ei.value.code == "LLM-301"
        assert ei.value.ctx.get("retryable") is True
        assert _event_types(s) == ["session.created", "llm.request"]  # 失败不计 F029

    async def test_wait_for_gate_monkeypatched(self) -> None:
        """真实 wait_for 路径:替换为即抛 TimeoutError,验证总闸分支(非仅归一)。"""
        s = await _session()
        adp = _adapter(result=_raw(content="x", usage=_usage()))

        async def _fake_wait_for(coro, timeout):          # 模拟总闸触发(不跑内层协程)
            coro.close()
            raise asyncio.TimeoutError()

        orig = asyncio.wait_for
        asyncio.wait_for = _fake_wait_for                 # noqa: 单测局部替换
        try:
            with pytest.raises(PyHError) as ei:
                await adp.chat([{"role": "user", "content": "x"}], ctx=_ctx(s))
        finally:
            asyncio.wait_for = orig
        assert ei.value.code == "LLM-301"

    @pytest.mark.parametrize("error,code", [
        (ProviderStatusError(401), "LLM-302"),            # 认证:不重试直接降级(F013)
        (ProviderStatusError(403), "LLM-302"),
        (ProviderStatusError(429), "LLM-303"),            # 限流:退避可重试
        (ProviderStatusError(503), "LLM-303"),
        (ProviderStatusError(404), "LLM-304"),            # 业务错:不重试不降级
        (ProviderStatusError(400), "LLM-304"),
        (ProviderProtocolError("bad json"), "LLM-304"),
    ])
    async def test_error_normalization_end_to_end(self, error, code) -> None:
        """传输异常经 chat 链路 → raise_code 归一(错误全走 raise_code,无裸异常外泄)。"""
        s = await _session()
        adp = _adapter(error=error)
        with pytest.raises(PyHError) as ei:
            await adp.chat([{"role": "user", "content": "x"}], ctx=_ctx(s))
        assert ei.value.code == code
        assert ei.value.ctx.get("retryable") == (code in ("LLM-301", "LLM-303"))
        assert _event_types(s) == ["session.created", "llm.request"]   # 失败只留 request

    async def test_non_api_error_not_wrapped(self) -> None:
        """内部缺陷(ValueError)不上 LLM 码,原样上抛(上层 CYC-999 兜底)。"""
        s = await _session()
        adp = _adapter(error=ValueError("内部缺陷"))
        with pytest.raises(ValueError):
            await adp.chat([{"role": "user", "content": "x"}], ctx=_ctx(s))

    async def test_empty_choices_304(self) -> None:
        """choices 空数组(内容过滤)→ LLM-304 业务错,不回空文本冒充成功。"""
        s = await _session()
        adp = _adapter(result=SimpleNamespace(model=MODEL, choices=[], usage=None))
        with pytest.raises(PyHError) as ei:
            await adp.chat([{"role": "user", "content": "x"}], ctx=_ctx(s))
        assert ei.value.code == "LLM-304"

    async def test_double_empty_304(self) -> None:
        """content null 且无 tool_calls = 协议异常 → LLM-304(不冒充成功)。"""
        s = await _session()
        adp = _adapter(result=_raw(content=None, tool_calls=None, usage=None))
        with pytest.raises(PyHError) as ei:
            await adp.chat([{"role": "user", "content": "x"}], ctx=_ctx(s))
        assert ei.value.code == "LLM-304"

    async def test_syntax_error_passthrough(self) -> None:
        """arguments 坏 JSON → ToolCallSyntaxError 原样上抛(上层 TLB-803 回喂,零执行)。"""
        s = await _session()
        raw = _raw(content=None, finish="tool_calls", usage=None,
                   tool_calls=[_tc("c1", "fs.read_file", "{bad")])
        adp = _adapter(result=raw)
        with pytest.raises(ToolCallSyntaxError):
            await adp.chat([{"role": "user", "content": "x"}], ctx=_ctx(s))


class TestChatStream:
    """流式入口(F027):chunk 只上总线不入日志;聚合单条 llm.response;同 chat 型返回。"""

    async def test_stream_text_and_events(self) -> None:
        s = await _session()
        counters = UsageCounters()
        usage = _usage(prompt=11, out=4)
        chunks = [_chunk(content="你"), _chunk(content="好", finish="stop"),
                  _chunk(usage=usage)]
        bus = FakeBus()
        adp = _adapter(chunks=chunks, counters=counters)
        ctx = _ctx(s, counters=counters, bus=bus)
        resp = await adp.chat_stream([{"role": "user", "content": "hi"}], ctx=ctx)
        assert resp.content == "你好"
        assert resp.finish_reason == "stop" and resp.model == MODEL
        assert resp.seq == _event(s, "llm.response").seq
        # chunk 只上总线(2 条文本 delta);日志恰 1 条 llm.response(GWT-L4-04 口径)
        assert [t for t, _ in bus.events] == ["llm.chunk", "llm.chunk"]
        assert len([e for e in s.events_after(0) if e.type == "llm.chunk"]) == 0
        # 事件序 + usage 末块计量
        assert _event_types(s) == ["session.created", "llm.request", "llm.usage",
                                   "llm.response"]
        assert _event(s, "llm.usage").payload["in_tokens"] == 11
        assert _event(s, "llm.response").payload["content"] == "你好"
        assert counters.snapshot()["requests"] == 1

    async def test_stream_tool_round(self) -> None:
        """流式工具轮:增量 tool_calls 分片合并;usage 计费;resp 与 chat 同型。"""
        s = await _session()
        usage = _usage(prompt=5, out=2)
        chunks = [
            _chunk(tool_calls=[_dt(0, tc_id="c1", name="fs.read_file",
                                   arguments='{"path": "')]),
            _chunk(tool_calls=[_dt(0, arguments='a"}')]),
            _chunk(finish="tool_calls"),
            _chunk(usage=usage),
        ]
        adp = _adapter(chunks=chunks)
        resp = await adp.chat_stream([{"role": "user", "content": "read"}],
                                     ctx=_ctx(s, bus=FakeBus()))
        assert resp.content == ""
        assert resp.tool_calls and resp.tool_calls[0].raw_args == {"path": "a"}
        assert resp.finish_reason == "tool_calls"
        assert _event(s, "llm.response").payload["tool_calls"][0]["arguments"] == \
            '{"path": "a"}'

    async def test_stream_error_midway_303(self) -> None:
        """流中断(读超时)→ LLM-303(整请求退避重发,F028;非断点续传)。"""
        s = await _session()
        httpx = pytest.importorskip("httpx")
        adp = _adapter(error=httpx.ReadTimeout("断流"))
        with pytest.raises(PyHError) as ei:
            await adp.chat_stream([{"role": "user", "content": "x"}], ctx=_ctx(s))
        assert ei.value.code == "LLM-303"
        assert _event_types(s) == ["session.created", "llm.request"]

    async def test_stream_total_gate_301(self) -> None:
        s = await _session()
        adp = _adapter(error=asyncio.TimeoutError())
        with pytest.raises(PyHError) as ei:
            await adp.chat_stream([{"role": "user", "content": "x"}], ctx=_ctx(s))
        assert ei.value.code == "LLM-301"

    async def test_empty_stream_304(self) -> None:
        s = await _session()
        adp = _adapter(chunks=[])                          # 零内容零调用:异常
        with pytest.raises(PyHError) as ei:
            await adp.chat_stream([{"role": "user", "content": "x"}], ctx=_ctx(s))
        assert ei.value.code == "LLM-304"


# ===================================================================== 计量
class TestUsage:
    """F029 计量:report_usage/estimate_cost(缓存会计/缺价表容错)。"""

    async def test_report_usage_and_counters(self) -> None:
        s = await _session()
        counters = UsageCounters()
        ctx = _ctx(s, counters=counters)
        usage = _usage(prompt=100, out=50, hit=90, miss=10)
        ev = await report_usage(usage, MODEL, ctx=ctx, counters=counters)
        assert ev.type == "llm.usage"
        p = ev.payload
        assert p["in_tokens"] == 100 and p["out_tokens"] == 50 and p["cache_hit"] == 90
        # cost = (miss 10 + hit 90×1.0)/1e6×2 + 50/1e6×8
        assert p["cost_est"] == pytest.approx(100 / 1e6 * 2.0 + 50 / 1e6 * 8.0)
        assert counters.snapshot()["in_tokens"] == 100
        assert counters.snapshot()["by_model"][MODEL]["cache_hit"] == 90

    async def test_report_usage_no_price_graceful(self) -> None:
        """价格表缺模型单价 → cost_est=None 照常记账(偏离 4;token 计数不炸)。"""
        s = await _session()
        counters = UsageCounters()
        ctx = _ctx(s, counters=counters)
        ev = await report_usage(_usage(prompt=7, out=3), "no-price-model",
                                ctx=ctx, counters=counters)
        assert ev.payload["cost_est"] is None
        assert ev.payload["in_tokens"] == 7
        assert counters.snapshot()["in_tokens"] == 7

    def test_estimate_cost_cache_accounting(self) -> None:
        """缓存会计:命中按折扣(hit_discount),未命中全价;token 计数照算。"""
        usage = SimpleNamespace(prompt_tokens=1000, completion_tokens=500,
                                prompt_cache_hit_tokens=900,
                                prompt_cache_miss_tokens=100)
        cost = estimate_cost(MODEL, usage, PRICES, hit_discount=0.1)
        billed_in = 100 + 900 * 0.1                       # 190
        assert cost == pytest.approx(billed_in / 1e6 * 2.0 + 500 / 1e6 * 8.0)

    def test_estimate_cost_no_cache_fields_conservative(self) -> None:
        """无缓存字段厂商 → prompt 全按未命中(保守全价估算)。"""
        usage = SimpleNamespace(prompt_tokens=1000, completion_tokens=500)
        cost = estimate_cost(MODEL, usage, PRICES)
        assert cost == pytest.approx(1000 / 1e6 * 2.0 + 500 / 1e6 * 8.0)


# ===================================================================== 注册表
class TestRegistry:
    """F030 注册表:接口强制(CFG-601 不落脏表)、未注册查询 → LLM-304。"""

    @pytest.fixture(autouse=True)
    def _cleanup(self):
        names = [n for n in adapters if n.startswith("u-")]
        yield
        for n in [n for n in adapters if n.startswith("u-")]:
            adapters.pop(n, None)
        for n in names:
            adapters.pop(n, None)

    def test_register_and_require(self) -> None:
        inst = register_adapter("u-ok", lambda: OpenAICompatAdapter(
            model="u-ok", transport=FakeTransport(), limits=TimeoutLimits(10, 60, 180)))
        assert adapters["u-ok"] is inst
        assert require_adapter("u-ok") is inst

    def test_register_missing_interface_cfg601(self) -> None:
        """factory 产物缺统一接口方法 → CFG-601,注册失败不落脏表。"""
        with pytest.raises(PyHError) as ei:
            register_adapter("u-bad", lambda: object())
        assert ei.value.code == "CFG-601"
        assert "u-bad" not in adapters

    def test_require_unknown_llm304(self) -> None:
        with pytest.raises(PyHError) as ei:
            require_adapter("u-no-such-model")
        assert ei.value.code == "LLM-304"

    def test_adapter_abstract(self) -> None:
        """LLMAdapter 为 ABC:chat/chat_stream/ping 三接口强制(注册校验依赖接口存在)。"""
        import inspect
        assert inspect.isabstract(LLMAdapter)
        assert LLMAdapter.__abstractmethods__ == frozenset({"chat", "chat_stream", "ping"})

    def test_ping_smoke(self) -> None:
        adp = OpenAICompatAdapter(model="u-ping", transport=FakeTransport(),
                                  limits=TimeoutLimits(10, 60, 180))
        lat = asyncio.run(adp.ping())
        assert lat == 0.05

    def test_ping_failure_303(self) -> None:
        adp = OpenAICompatAdapter(model="u-ping2", transport=FakeTransport(
            error=ProviderStatusError(503)), limits=TimeoutLimits(10, 60, 180))
        with pytest.raises(PyHError) as ei:
            asyncio.run(adp.ping())
        assert ei.value.code == "LLM-303"


class TestLLMClient:
    """会话门面(ctx.llm):模型解析/注册表查询;唯一出口代理。"""

    def test_model_resolution_default(self) -> None:
        assert LLMClient().model == "deepseek-chat"        # L1 主模型
        assert LLMClient(model="qwen-max").model == "qwen-max"
        cfg = SimpleNamespace(llm=SimpleNamespace(model="custom"))
        assert LLMClient(cfg=cfg).model == "custom"

    async def test_unknown_model_llm304(self) -> None:
        """注册表缺该模型 → LLM-304(核对模型名;不自动造适配器)。"""
        client = LLMClient(model="u-no-model", registry={})
        with pytest.raises(PyHError) as ei:
            await client.chat([{"role": "user", "content": "x"}], ctx=_ctx(None))
        assert ei.value.code == "LLM-304"

    async def test_chat_delegates_to_registered_adapter(self) -> None:
        s = await _session()
        register_adapter("u-cli", lambda: OpenAICompatAdapter(
            model="u-cli", transport=FakeTransport(
                result=_raw(content="代理成功", usage=_usage(prompt=2, out=1))),
            limits=TimeoutLimits(10, 60, 180)))
        client = LLMClient(model="u-cli")
        resp = await client.chat([{"role": "user", "content": "hi"}], ctx=_ctx(s))
        assert resp.content == "代理成功"
        assert resp.seq == _event(s, "llm.response").seq


# ===================================================================== 凭据与客户端
class TestSecrets:
    """F016 单口:env:/file: 解析;缺失/非法 → CRED-701(禁字面量密钥)。"""

    def test_env_ref(self, monkeypatch) -> None:
        monkeypatch.setenv("PH_LLM_TEST_KEY", "sk-test-123")
        assert resolve_secret_ref("env:PH_LLM_TEST_KEY") == "sk-test-123"

    def test_env_missing_cred701(self, monkeypatch) -> None:
        monkeypatch.delenv("PH_LLM_NOPE", raising=False)
        with pytest.raises(CredentialError) as ei:
            resolve_secret_ref("env:PH_LLM_NOPE")
        assert ei.value.code == "CRED-701"

    def test_file_ref(self, tmp_path) -> None:
        f = tmp_path / "key.txt"
        f.write_text("sk-file-key-123\n", encoding="utf-8")
        assert resolve_secret_ref(f"file:{f}") == "sk-file-key-123"

    def test_file_missing_cred701(self) -> None:
        with pytest.raises(CredentialError) as ei:
            resolve_secret_ref("file:C:/no/such/key.txt")
        assert ei.value.code == "CRED-701"

    def test_bad_ref_syntax_cred701(self) -> None:
        with pytest.raises(CredentialError):
            resolve_secret_ref("sk-明文密钥绝不允许")


class TestBuildClient:
    """客户端构建(ADI §1.1):零网络构造冒烟 + 三档超时落点 + CRED-701。"""

    def test_build_smoke_and_timeouts(self) -> None:
        from pyharness.core.llm import AdapterTriple
        triple = AdapterTriple(base_url="https://api.example.com/v1/",
                               api_key_ref="env:X", model=MODEL)
        tr = build_client(triple, TimeoutLimits(10, 60, 180),
                          resolver=lambda ref: "sk-key")
        # 传输对象三接口齐(Provider 可注入面)
        assert callable(tr.complete) and callable(tr.stream) and callable(tr.ping)
        # 超时三档落点(连接 10/首 token 60/写与池 = connect)
        t = tr._client.timeout
        assert t.connect == 10 and t.read == 60
        assert t.write == 10 and t.pool == 10
        # base_url 去尾 /(禁写 /chat/completions)
        assert str(tr._client.base_url).rstrip("/") == "https://api.example.com/v1"

    def test_base_url_trailing_slash_trimmed(self) -> None:
        from pyharness.core.llm import AdapterTriple
        tr = build_client(AdapterTriple("https://api.example.com/", "env:X", "m"),
                          TimeoutLimits(10, 60, 180), resolver=lambda ref: "k")
        assert str(tr._client.base_url) == "https://api.example.com"

    def test_missing_key_cred701(self) -> None:
        from pyharness.core.llm import AdapterTriple
        with pytest.raises(CredentialError) as ei:
            build_client(AdapterTriple("https://x", "env:X", "m"),
                         TimeoutLimits(10, 60, 180), resolver=lambda ref: "")
        assert ei.value.code == "CRED-701"
