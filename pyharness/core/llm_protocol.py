"""Llm protocol: complete migrated implementation."""
from __future__ import annotations
try:
    import httpx
except ImportError:
    httpx = None

import asyncio
import logging
log = logging.getLogger("pyharness.llm")
import json
import re
from typing import TYPE_CHECKING, Any, AsyncIterator, Optional
from pyharness.errors import raise_code
if TYPE_CHECKING:  # 仅类型标注;装配对象鸭子注入(session/bus 经 ctx)
    pass

from pyharness.core.llm_types import ProviderProtocolError, ProviderStatusError, ToolCall, ToolCallSyntaxError

def _sanitize_tool_name(name: str) -> str:
    """DeepSeek 工具名正则 ^[a-zA-Z0-9_-]+$(禁点号);fs.read_file → fs_read_file。

    真链实测:带点工具名 → HTTP 400 'Invalid tools[0].function.name' →
    被归一为 LLM-304。发送前清洗、响应 tool_calls 名还原(见 chat)。
    """
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name)

def _sanitize_tools(tools: Optional[list]) -> tuple:
    """下发 tools 清洗 + 反向映射表。返回 (clean_tools, {sanitized: original})。"""
    if not tools:
        return tools, {}
    rev: dict[str, str] = {}
    clean: list[dict] = []
    for t in tools:
        fn = (t or {}).get("function") or {}
        orig = fn.get("name", "")
        if orig and orig != _sanitize_tool_name(orig):
            rev[_sanitize_tool_name(orig)] = orig
        clean.append(t)
    if not rev:
        return tools, {}
    # 需重建带清洗名的 tools(嵌套 dict 深拷贝改 name)
    import copy as _copy
    out = []
    for t in clean:
        t2 = _copy.deepcopy(t)
        fn = t2.get("function")
        if fn and fn.get("name"):
            fn["name"] = _sanitize_tool_name(str(fn["name"]))
        out.append(t2)
    return out, rev

def _restore_call_names(calls: list, rev: dict) -> None:
    """响应 tool_calls 名还原(sanitized → original,原地改)。"""
    if not rev:
        return
    for c in calls:
        nm = getattr(c, "name", "")
        if nm in rev:
            c.name = rev[nm]

def _load_arguments(raw_json: Optional[str], call_id: Optional[str]) -> dict:
    """arguments 二次 json.loads(空串/纯空白容错为 {});语法错/非对象 → ToolCallSyntaxError。"""
    raw = (raw_json or "").strip()
    if not raw:
        return {}                                  # 空串/纯空白 = 无参数调用(容错)
    try:
        args = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ToolCallSyntaxError(call_id, e) from e
    if not isinstance(args, dict):
        raise ToolCallSyntaxError(call_id, "arguments 非 JSON 对象(零猜原则,拒本轮)")
    return args

def parse_tool_calls(message: Any) -> list[ToolCall]:
    """tool_calls wire 拆解(ADI §1.4):function.arguments 是字符串内嵌 JSON,二次 loads。

    非 function 类型跳过(留 raw 审计);同响应 id 重复 → ToolCallSyntaxError(配对必乱);
    返回按数组序升序 = 执行序。message = 传输层 choices[0].message(鸭子对象)。
    """
    seen: set = set()
    out: list[ToolCall] = []
    for idx, tc in enumerate(getattr(message, "tool_calls", None) or []):
        if getattr(tc, "type", None) not in (None, "function"):
            continue                                # 非 function 不进管道,留 raw 审计
        tc_id = tc.id
        if tc_id in seen:
            raise ToolCallSyntaxError(tc_id, "同响应 id 重复,拒本轮")
        seen.add(tc_id)
        raw_json = (tc.function.arguments or "") if tc.function else ""
        out.append(ToolCall(id=tc_id, index=idx, name=tc.function.name,
                            raw_args=_load_arguments(raw_json, tc_id),
                            raw_json=raw_json))     # 原文留审计(INV-06)
    return out

def assistant_and_tool_messages(calls: list[ToolCall], results: dict) -> list[dict]:
    """工具轮回填消息(ADI §1.4.4):assistant(tool_calls,arguments 原文回传)+ N 条 tool。

    arguments 回传 raw_json 原文(不重序列化);缺 result 的 call → EVT-101 拒发下一轮
    (半截回填多数端点 422,与 EVT-101 同原则:拒绝以残缺上下文发下一轮)。
    """
    msgs: list[dict] = [{
        "role": "assistant", "content": None,      # 工具轮 content 可 null
        "tool_calls": [{"id": c.id, "type": "function",
                        "function": {"name": c.name, "arguments": c.raw_json}}
                       for c in calls]}]
    for c in calls:                                # 顺序 = 声明顺序(数组序)
        r = results.get(c.id)
        if r is None:
            raise_code("EVT-101", call_id=c.id,
                       advice="tool.result 缺失,拒绝以残缺上下文发下一轮")
        if isinstance(r, dict):
            content = r.get("summary", "")
        else:
            content = getattr(r, "summary", "") or ""
        msgs.append({"role": "tool", "tool_call_id": c.id, "content": content})
    return msgs

async def _emit_chunk(ctx: Any, delta: str) -> None:
    """llm.chunk 上总线(瞬时事件,禁 append 入日志;EVENT-SCHEMA §1.3)。"""
    bus = getattr(ctx, "bus", None)
    if bus is None:
        return                                      # 未装配总线:纯内存/单测模式
    try:
        payload = {"delta": delta, "session_id": str(getattr(
            getattr(ctx, "session", None), "sid", "") or "")}
        task_id = getattr(ctx, "task_id", None)
        if task_id:
            payload["task_id"] = str(task_id)
        r = bus.emit("llm.chunk", payload)
        if asyncio.iscoroutine(r):
            await r
    except Exception as exc:                        # noqa: BLE001 UI 通道尽力而为
        log.debug("llm.chunk emit failed: %s", exc)

def _slot_to_call(index: int, slot: dict) -> ToolCall:
    """流式增量槽 → ToolCall(合并完仍二次校验:半截 JSON → ToolCallSyntaxError→TLB-803)。"""
    raw_json = slot["arguments"]
    return ToolCall(id=slot["id"], index=index, name=slot["name"] or None,
                    raw_args=_load_arguments(raw_json, slot["id"]),
                    raw_json=raw_json)

async def accumulate_stream(stream: AsyncIterator[Any], ctx: Any, *, progress=None) -> tuple:
    """SSE 增量聚合(ADI §5.3):content 拼接、tool_calls 按 index 分片合并(追加不覆盖)。

    返回 (text, calls, finish_reason, usage);chunk 只经总线给 UI、不进日志;usage 只出现在
    include_usage 末块(choices=[] 仅该块);聚合结果与逐 chunk 拼接逐字节一致(INV 比对)。
    """
    parts: list[str] = []
    slots: dict[int, dict] = {}
    finish: Optional[str] = None
    usage: Any = None
    if progress is not None:
        progress.update(parts=parts, slots=slots, usage=None)
    async for chunk in stream:
        choices = getattr(chunk, "choices", None) or []
        if not choices:                            # choices=[] + usage:仅流末块
            u = getattr(chunk, "usage", None)
            if u is not None:
                usage = u
                if progress is not None:
                    progress['usage'] = u
            continue
        d = choices[0].delta
        content = getattr(d, "content", None)
        if content:                                # 增量文本:拼 + 上总线(不留日志)
            parts.append(content)
            await _emit_chunk(ctx, content)
        for dt in (getattr(d, "tool_calls", None) or []):   # id/name 只在该 index 首块
            slot = slots.setdefault(dt.index, {"id": None, "name": "", "arguments": ""})
            if dt.id:
                slot["id"] = dt.id
            fn = getattr(dt, "function", None)
            if fn is not None:
                if fn.name:
                    slot["name"] = fn.name
                if fn.arguments:
                    slot["arguments"] += fn.arguments    # 跨块追加,绝不覆盖
        fr = choices[0].finish_reason
        if fr:
            finish = fr
    calls = [_slot_to_call(i, slots[i]) for i in sorted(slots)]
    return "".join(parts), calls, finish, usage

def _est_tokens(text: str) -> int:
    """输出 token 粗估(流式计量盲区兜底;与 session._estimate_tokens 同启发式)。

    CJK 逐字 1 token;ASCII 词按 4 字符 ≈ 1;+4 结构开销。仅用于 usage 缺失时
    让 F029 账目与预算闸至少计入 output 量级,不构成权威计数。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    words = sum(max(1, (len(w) + 3) // 4)
                for w in re.findall(r"[A-Za-z0-9_./\\@:+-]+", text))
    return cjk + words + 4

def _http_status_code(status: int) -> str:
    """HTTP 状态 → 码(401/403→302;429/≥500→303;其余 4xx→304;只按状态不按文本)。"""
    if status in (401, 403):
        return "LLM-302"                           # 认证/权限:不重试直接降级
    if status == 429 or status >= 500:
        return "LLM-303"                           # 限流/上游 5xx:退避可重试
    return "LLM-304"                               # 其余 4xx 业务错:不重试不降级

def _openai_module() -> Any:
    """openai SDK 惰性取用(非本阶段硬依赖;装后异常类归一自动生效,e2e/未来阶段)。"""
    try:
        import openai
        return openai
    except Exception:  # noqa: BLE001 ImportError/装配缺依赖
        return None

def normalize_exc(e: Exception) -> str:
    """异常类别/HTTP 状态 → LLM-3xx 唯一映射(ADI §7);绝不匹配厂商错误文本(ADR-011)。

    非 API 异常原样上抛(不上 LLM 码——内部缺陷 → CYC-999 兜底,禁伪装模型域错误)。
    """
    if isinstance(e, asyncio.TimeoutError):
        return "LLM-301"                           # 编排总闸(180s)
    if isinstance(e, ProviderStatusError) or (httpx is not None
                                              and isinstance(e, httpx.HTTPStatusError)):
        return _http_status_code(int(getattr(e, "status_code", getattr(getattr(e, "response", None), "status_code", 0))))
    if isinstance(e, ProviderProtocolError):
        return "LLM-304"                           # 响应结构非法(内容过滤/坏 JSON)
    oa = _openai_module()
    if oa is not None:                             # openai SDK 异常类(映射矩阵 ADI §7.2)
        if isinstance(e, (oa.AuthenticationError, oa.PermissionDeniedError)):
            return "LLM-302"
        if isinstance(e, (oa.RateLimitError, oa.APIConnectionError,
                          oa.APITimeoutError, oa.InternalServerError)):
            return "LLM-303"
        if isinstance(e, (oa.BadRequestError, oa.NotFoundError,
                          oa.UnprocessableEntityError, oa.APIStatusError, oa.APIError)):
            return "LLM-304"
    if httpx is not None and isinstance(e, httpx.TransportError):
        return "LLM-303"                           # 断网/连接/读超时(httpx 传输族)
    raise e                                        # 非 API 异常不上 LLM 码(→CYC-999)
