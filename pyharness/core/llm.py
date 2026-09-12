"""pyharness/core/llm.py — LLM 唯一出口模块(specs/llm.py.md 契约;阶段 1 核心)

F012/F017/F027/F029/F030:脊柱唯一 LLM 出口——请求组装与发送、tool_calls wire 解析
(arguments 二次 json.loads 容错)、流式聚合、错误归一(五码)、usage 计量;LLM 零信任
(原则 4)的物理闸门。全系统除本模块外不存在"直连模型端点"路径(INV-02),消费方
agent-loop 唯一经注入的 ctx.llm.chat / ctx.llm.chat_stream 出网。

三层超时(PARAMETER-ANCHOR 锁死 10s/60s/180s):连接/写池 = connect_s(httpx 层)、
首 token = first_token_s(httpx read 档)、总闸 = total_s(asyncio.wait_for 编排闸)。
错误全走 errors.raise_code(LLM-301~304 等;LLM-305/310 归 llm_fallback 编排层),
禁裸 raise str、禁匹配厂商错误文本(ADR-011,只按异常类别/HTTP 状态)。

HTTP 客户端注入式(可测性铁律 5):本模块不硬编码 SDK 调用细节——OpenAICompatAdapter
只依赖传输协议 {complete/stream/ping}(与 chat.completions.create 语义同构),单测注入
替身传输零网络;真实端点由 build_client(httpx OpenAI 兼容)构造,仅 e2e(-m e2e)触碰
网络。API key 只经 config 的 env:/file: 秘密引用(F016),模块内零字面量密钥。

偏离说明(契约以 specs/llm.py.md 伪码为准,以下为与已落地模块冲突处的取舍):
1. chat/chat_stream 除 spec 伪码的 llm.request 外,额外落单条 llm.response 事件并回填
   LLMResponse.seq:agent_loop 已实现契约要求(trace.parent_seq=resp.seq;test_agent_loop
   FakeLLM 注释明示"真实 llm 层职责");且 session 派生折叠(INV-01)以 llm.response 为
   assistant 消息唯一事实源,chat 不落则文本轮历史断裂。流式与 chat 同型返回(GWT-L4-04:
   日志恰 1 条 llm.response)。
2. build_client 返回 httpx OpenAI 兼容传输而非 spec 的 openai.AsyncOpenAI:openai SDK 非
   本阶段运行依赖(pyproject deps 无 openai,阶段约束"真实 API 调用留 e2e");传输对象暴露
   complete/stream/ping 三接口,Provider 可整体注入。SDK 装后其异常类经 normalize_exc
   惰性 import 自动生效(e2e/未来阶段),映射矩阵与 ADI §7 一致。
3. normalize_exc 在 SDK 矩阵之外增补内置传输异常类:ProviderStatusError(HTTP 状态,401/403
   →302、429/≥500→303、其余 4xx→304)、ProviderProtocolError(响应结构非法→304)、
   httpx.TransportError(断网/连接/读超时→303)——类别判定同 ADI §7,不匹配厂商文本。
4. report_usage 对价格表缺模型单价 → cost_est=None 照常记账(spec estimate_cost 内
   prices[model] 直取,缺价表会 KeyError 击穿"纯记账不抛"纪律);estimate_cost 本函数
   保持 spec 语义(直取,调用方先查表)。
5. LLMResponse 增 seq 字段(偏离 1);ToolCall 增 call_id 别名属性(=id,工具层按 call_id
   配对 tool.result 的消费契约)。两处均为字段级扩展,不破坏 spec 命名。
6. resolve_secret_ref = F016 最小落地(env:NAME/file:PATH 解析,缺失 CRED-701):credentials
   模块(真 F016,TTL/600 权限)未在本阶段实现,凭据模块落地后由装配层以 resolver 注入
   build_client 替换,本函数即默认 resolver。
7. 单适配器 = 请求全生命周期单元(llm.request/总闸/归一/计量/llm.response 一次请求一
   套);降级链/退避/健康探针(F013/F028/F033)属 llm_fallback 编排层(下一阶段),按
   adapters[name].chat 复用同一事件路径,llm 零改动。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, AsyncIterator, Optional

from pyharness.errors import raise_code

if TYPE_CHECKING:  # 仅类型标注;装配对象鸭子注入(session/bus 经 ctx)
    from pyharness.config import Settings
    from pyharness.events import Envelope

log = logging.getLogger("pyharness.llm")

try:  # httpx 为项目硬依赖;极端缺环境下模块仍可导入(纯解析/单测路径可用)
    import httpx
except ImportError:  # pragma: no cover — 缺依赖兜底
    httpx = None  # type: ignore[assignment]

# LLM 错误归一后可重试码(F028 依据;LLM-304 业务错不重试不降级)
_RETRYABLE_CODES = frozenset({"LLM-301", "LLM-303"})

_L1_SETTINGS: Any = None  # L1 兜底配置懒加载缓存(无 ctx.config/无注入 cfg 时)


def _l1_settings() -> Any:
    """L1 默认 Settings(键缺省必可加载;仅无注入配置时兜底,不读写用户密钥)。"""
    global _L1_SETTINGS
    if _L1_SETTINGS is None:
        from pyharness.config import load_settings  # 惰性:保持模块导入零配置依赖
        _L1_SETTINGS = load_settings()
    return _L1_SETTINGS


# ================================================================ 数据结构
@dataclass
class TimeoutLimits:
    """三档超时(F017/PARAMETER-ANCHOR 锁死 10/60/180);强制 total>first>connect,违者 CFG-601。"""

    connect_s: int = 10        # 连接/写/池 档(httpx)
    first_token_s: int = 60    # 首 token 档(httpx read)
    total_s: int = 180         # 总闸档(asyncio.wait_for 编排)

    def __post_init__(self) -> None:
        if not (self.connect_s < self.first_token_s < self.total_s):
            raise_code("CFG-601", reason="structural", fields=["llm.timeout.*"],
                       detail=f"超时档序须 connect<first<total,实得 "
                              f"{self.connect_s}/{self.first_token_s}/{self.total_s}")

    @classmethod
    def from_cfg(cls, t: Any) -> "TimeoutLimits":
        """自配置段构建(settings.llm.timeout;顺序/范围已由 config.validate_rules 复核)。"""
        return cls(int(t.connect_s), int(t.first_token_s), int(t.total_s))


@dataclass
class ToolCall:
    """解析后的工具调用(id/index/name/raw_args/raw_json;ADI §1.4.4)。

    raw_json = arguments 模型原文(字符串,可能含换行/缩进),回填 tool 消息时原样带回
    (不重序列化,INV-06 审计口径);raw_args = 二次 json.loads 后的 dict(强校验输入)。
    """

    id: Optional[str]
    index: int
    name: Optional[str] = None
    raw_args: dict = field(default_factory=dict)
    raw_json: str = ""

    @property
    def call_id(self) -> Optional[str]:
        """工具层兼容别名(偏离 5):tools.execute 按 call_id 配对 tool.result,= id。"""
        return self.id


@dataclass
class LLMResponse:
    """chat/chat_stream 同型结构化返回(content/tool_calls/usage/model/finish_reason/raw)。

    tool_calls 保留 raw_args 与 raw_json 双轨供审计(F026/INV-06);seq 为 llm.response
    事件日志 seq(偏离 5,agent_loop trace 父关联用);raw = 传输层原始响应(留审计,流式 None)。
    """

    content: str = ""
    tool_calls: Optional[list] = None      # list[ToolCall];None = 纯文本轮
    usage: Any = None                      # 传输层 usage(prompt/completion/cache 扩展)
    model: str = ""
    finish_reason: str = ""
    raw: Any = None
    seq: Optional[int] = None              # 偏离 1:llm.response 事件 seq


@dataclass
class UsageCounters:
    """F029 token 计量计数器(report_usage 唯一写入;scope/agent-loop 只读,F032 硬闸)。

    会话级总量 + by_model 模型细分二维(任务维可经事件日志按 task_id 重建,INV-01);
    cost_est 只进事件/报表,不打预算硬闸(N14)。"""

    in_tokens: int = 0
    out_tokens: int = 0
    cache_hit: int = 0
    cost_est: float = 0.0
    requests: int = 0
    by_model: dict[str, "UsageCounters"] = field(default_factory=dict)
    # llm_fallback 编排面私有计数(协议:task_total/rate_limit_streak/degrade_add;
    # 非事件字段——可整体重建,不进 snapshot,防审计口径漂移)
    _rate_limit_streak: int = field(default=0, repr=False, compare=False)
    _degrade_total: int = field(default=0, repr=False, compare=False)
    _degrade_last: dict = field(default_factory=dict, repr=False, compare=False)

    def task_add(self, payload: dict) -> None:
        """按 llm.usage 事件 payload 入账(事件即事实;计数器可整体重建)。"""
        model = payload.get("model") or "?"
        m = self.by_model.setdefault(model, UsageCounters())
        self.requests += 1
        m.requests += 1
        self.in_tokens += int(payload.get("in_tokens") or 0)
        self.out_tokens += int(payload.get("out_tokens") or 0)
        self.cache_hit += int(payload.get("cache_hit") or 0)
        self.cost_est += float(payload.get("cost_est") or 0.0)
        # 模型细分按各自增量累计(与总量并行,互不覆盖)
        m.in_tokens += int(payload.get("in_tokens") or 0)
        m.out_tokens += int(payload.get("out_tokens") or 0)
        m.cache_hit += int(payload.get("cache_hit") or 0)
        m.cost_est += float(payload.get("cost_est") or 0.0)

    def snapshot(self) -> dict:
        """只读快照(审计/报表;禁止由此路径改写计数)。"""
        return {
            "requests": self.requests,
            "in_tokens": self.in_tokens,
            "out_tokens": self.out_tokens,
            "cache_hit": self.cache_hit,
            "cost_est": round(self.cost_est, 6),
            "by_model": {k: v.snapshot() for k, v in sorted(self.by_model.items())},
        }

    def task_total(self) -> Any:
        """llm_fallback.UsageCounters 协议:TaskUsage 型读数(F032 预算闸同源)。

        scope.budget_state/BudgetGuard 每轮现读本方法;in/out/cost 与会话级
        总量同字段(单任务会话语义,1 会话 = 1 agent = 1 任务)。"""
        from pyharness.core.llm_fallback import TaskUsage   # 免顶层环依赖
        return TaskUsage(in_tokens=self.in_tokens,
                         out_tokens=self.out_tokens,
                         cost_est=round(self.cost_est, 6))

    def rate_limit_streak(self) -> int:
        """连续 429 计数(协议;事件可重建)。"""
        return self._rate_limit_streak

    def rate_limit_add(self) -> int:
        """429 计数 +1(适配器归一 LLM-303 时调用;返回累计)。"""
        self._rate_limit_streak += 1
        return self._rate_limit_streak

    def degrade_add(self, failed: str, to: str) -> int:
        """会话级降级计数(F013):返回累计次数(超 max_per_session 发告警)。"""
        self._degrade_total += 1
        self._degrade_last = {"failed": failed, "to": to}
        return self._degrade_total


@dataclass(frozen=True)
class AdapterTriple:
    """端点三元组(ADI §0.1 全局不变量①:模型差异=(base_url, api_key_ref, model))。"""

    base_url: str        # 裸域或 /v1 结尾(禁写 /chat/completions)
    api_key_ref: str     # env:NAME / file:PATH 秘密引用(F016 单口)
    model: str


# ================================================================ 传输层异常
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
class ProviderStatusError(Exception):
    """内置传输层 HTTP 状态错(类别判定用,ADI §7;body 文案不上行 ERR §5.1)。"""

    def __init__(self, status_code: int, detail: str = "") -> None:
        self.status_code = int(status_code)
        self.detail = detail[:200]
        super().__init__(f"HTTP {self.status_code}")


class ProviderProtocolError(Exception):
    """传输层协议/结构错(响应非 JSON/choices 空/缺 message)→ LLM-304 语义。"""


class ToolCallSyntaxError(Exception):
    """arguments 非合法 JSON / 非对象 / 同响应 id 重复 → 上层映射 TLB-803 回喂,零执行。

    零猜原则(ADI §1.4.3):语法错绝不在解析层补全/猜测,原样上抛由 agent-loop 回喂。
    """

    def __init__(self, call_id: Optional[str], cause: Any) -> None:
        self.call_id = call_id
        self.cause = cause
        super().__init__(f"tool_calls arguments 非法:call_id={call_id!r} 原因={cause!r}")


# ================================================================ wire 解析层
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


async def accumulate_stream(stream: AsyncIterator[Any], ctx: Any) -> tuple:
    """SSE 增量聚合(ADI §5.3):content 拼接、tool_calls 按 index 分片合并(追加不覆盖)。

    返回 (text, calls, finish_reason, usage);chunk 只经总线给 UI、不进日志;usage 只出现在
    include_usage 末块(choices=[] 仅该块);聚合结果与逐 chunk 拼接逐字节一致(INV 比对)。
    """
    parts: list[str] = []
    slots: dict[int, dict] = {}
    finish: Optional[str] = None
    usage: Any = None
    async for chunk in stream:
        choices = getattr(chunk, "choices", None) or []
        if not choices:                            # choices=[] + usage:仅流末块
            u = getattr(chunk, "usage", None)
            if u is not None:
                usage = u
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


# ================================================================ 错误归一
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
        return _http_status_code(int(getattr(e, "status_code", 0)))
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


# ================================================================ 成本与计量
def estimate_cost(model: str, usage: Any, prices: dict, hit_discount: float = 1.0) -> float:
    """成本估算(ADI §8.2,元/百万):未命中输入 + 命中输入×折扣 + 输出;只进事件/报表(N14)。

    usage 无缓存字段厂商按全价估算(保守);prices 缺模型直取 KeyError 属调用方先查表(偏离 4)。
    """
    p = prices[model]                              # {in_per_million, out_per_million}
    hit = getattr(usage, "prompt_cache_hit_tokens", 0) or 0
    miss = getattr(usage, "prompt_cache_miss_tokens", 0) or 0
    if not (hit or miss):                          # 无缓存字段厂商:prompt 全按未命中
        miss = getattr(usage, "prompt_tokens", 0) or 0
    billed_in = miss + hit * hit_discount          # 命中按折扣价(官方约 1/10)
    out = getattr(usage, "completion_tokens", 0) or 0
    return billed_in / 1e6 * p["in_per_million"] + out / 1e6 * p["out_per_million"]


def _price_table(unit_price: Any) -> dict:
    """config 单价表(pydantic UnitPriceCfg 或 dict)→ 纯 dict {模型: {in,out}}。"""
    out: dict = {}
    for name, p in (unit_price or {}).items():
        if isinstance(p, dict):
            out[name] = {"in_per_million": float(p["in_per_million"]),
                         "out_per_million": float(p["out_per_million"])}
        else:                                      # pydantic 模型:attr 取数
            out[name] = {"in_per_million": float(p.in_per_million),
                         "out_per_million": float(p.out_per_million)}
    return out


def _llm_cfg_from_ctx(ctx: Any, fallback: Any = None) -> Any:
    """逐级解析 llm 配置段:ctx.config.llm → 注入 cfg.llm → L1 默认(键缺省必可加载)。"""
    cfg = getattr(ctx, "config", None)
    llm = getattr(cfg, "llm", None) if cfg is not None else None
    if llm is None and fallback is not None:
        llm = getattr(fallback, "llm", None)
    if llm is None:
        llm = _l1_settings().llm
    return llm


def _request_payload(model: str, degraded_from: Optional[str], tools: Any) -> dict:
    """llm.request payload(EVENT-SCHEMA 字段级;降级来源非空才带)。"""
    p: dict = {"model": model, "n_tools": len(tools or [])}
    if degraded_from:
        p["degraded_from"] = degraded_from
    return p


def _response_payload(model: str, finish_reason: str, content: str,
                      calls: list[ToolCall]) -> dict:
    """llm.response payload:content 与 tool_calls 至少其一;arguments 原文回填(INV-06)。"""
    p: dict = {"model": model, "finish_reason": finish_reason, "content": content}
    if calls:
        p["tool_calls"] = [{"id": c.id, "name": c.name, "arguments": c.raw_json}
                           for c in calls]
    return p


async def report_usage(usage: Any, model: str, *, ctx: Any,
                       counters: Optional[UsageCounters] = None) -> Any:
    """F029 token 计量:每成功请求落 llm.usage 事件 + 更新 UsageCounters(F029 唯一写入)。

    usage 事件即事实,计数器可整体重建(INV-01);预算硬闸只读 token 数(F032);cost_est
    价格表缺模型 → None 照常记账(偏离 4);缺失缓存字段按 0,不做厂商猜。
    """
    llm_cfg = _llm_cfg_from_ctx(ctx)
    unit_price = getattr(getattr(llm_cfg, "usage", None), "unit_price", None) or {}
    prices = _price_table(unit_price)
    cost: Optional[float] = None
    if model in prices:                            # 缺单价表不炸记账(仅报表,偏离 4)
        cost = estimate_cost(model, usage, prices)
    payload = {
        "model": model,                            # 实际服务模型(降级后记备用,不记主名)
        "in_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "out_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        "cache_hit": int(getattr(usage, "prompt_cache_hit_tokens", 0) or 0),
        "cost_est": cost,
    }
    ev = await ctx.session.append("llm.usage", payload, actor="llm")
    cnt = counters if counters is not None else getattr(ctx, "counters", None)
    if cnt is not None:
        cnt.task_add(ev.payload)                   # 预算硬闸读 token(F032)
    return ev


# ================================================================ 适配器层
class LLMAdapter(ABC):
    """统一适配器接口(F030):三接口 + model/timeout;实例注入超时/计量钩子。"""

    model: str
    timeout: TimeoutLimits

    @abstractmethod
    async def chat(self, messages: list[dict], tools: Optional[list] = None, *,
                   ctx: Any) -> LLMResponse:
        """非流式唯一出口(每次调用 = 一次请求全生命周期:事件/总闸/归一/计量/解析)。"""

    @abstractmethod
    async def chat_stream(self, messages: list[dict], tools: Optional[list] = None, *,
                          ctx: Any) -> LLMResponse:
        """流式入口(F027):chunk 只上总线不入日志;与 chat 同型返回。"""

    @abstractmethod
    async def ping(self) -> float:
        """F033 探针:返回往返秒;失败 → LLM-303(不计 F029)。"""


class OpenAICompatAdapter(LLMAdapter):
    """OpenAI 兼容端点默认适配器(ADR-007/ADI §1):spec llm.py.md chat 主体实现。

    传输层注入式:transport(缺省经 build_client 由 httpx 构造)只暴露 complete/stream/ping;
    单测注入替身传输即零网络覆盖全链路(可测性铁律 5);真实调用仅 e2e。
    """

    def __init__(self, model: str, triple: Optional[AdapterTriple] = None,
                 limits: Optional[TimeoutLimits] = None, *, cfg: Any = None,
                 transport: Any = None,
                 counters: Optional[UsageCounters] = None) -> None:
        """model=适配器名(与模型名一致,注册表键);triple/transport 二选一(注入则无需网络)。"""
        if limits is None:
            llm = getattr(cfg, "llm", None) if cfg is not None else None
            limits = TimeoutLimits.from_cfg(llm.timeout) if llm is not None \
                else TimeoutLimits()
        self.model: str = model
        self.timeout: TimeoutLimits = limits
        self._triple = triple
        self._cfg = cfg
        self._transport = transport
        self._counters = counters if counters is not None else UsageCounters()
        self._counters_injected: bool = counters is not None
        self._deg: Optional[str] = None            # 降级来源(fallback 切链后置位 F013)

    # ------------------------------------------------------ 传输解析
    def _ensure_transport(self) -> Any:
        """惰性取传输:注入优先;否则 build_client(解析 key 需真实凭据,仅真调用路径)。"""
        if self._transport is None:
            if self._triple is None:
                raise_code("CFG-601", reason="structural", fields=["llm"],
                           detail="适配器缺端点三元组:构造 OpenAICompatAdapter 需 "
                                  "triple 或注入 transport")
            self._transport = build_client(self._triple, self.timeout)
        return self._transport

    def _llm_cfg(self, ctx: Any) -> Any:
        """本请求 llm 配置段(ctx.config.llm → 构造 cfg → L1)。"""
        return _llm_cfg_from_ctx(ctx, self._cfg)

    @staticmethod
    def _first_message(raw: Any) -> tuple:
        """choices[0].message + finish_reason;choices 空数组 = 内容过滤 → LLM-304(不回空)。"""
        choices = getattr(raw, "choices", None) or []
        if not choices:
            raise_code("LLM-304",
                       hint="choices 空数组(内容过滤/协议异常),不回空文本冒充成功")
        c0 = choices[0]
        return c0.message, (c0.finish_reason or "unknown")

    # ------------------------------------------------------ 非流式唯一出口
    async def chat(self, messages: list[dict], tools: Optional[list] = None, *,
                   ctx: Any) -> LLMResponse:
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
               "max_tokens": cfg_llm.max_tokens}    # 单次输出上限(默认 4096)
        await ctx.session.append("llm.request", _request_payload(self.model, self._deg,
                                                                 tools), actor="llm")
        try:
            raw = await asyncio.wait_for(           # 总时长闸(F017);只取消本任务
                transport.complete(req), timeout=self.timeout.total_s)
        except Exception as e:                      # noqa: BLE001 传输细分统一归一(ADI §7.2)
            code = normalize_exc(e)                 # 非 API 异常:原样上抛(不上 LLM 码)
            raise_code(code, model=self.model, retryable=code in _RETRYABLE_CODES)
        msg, finish = self._first_message(raw)
        content = (msg.content or "") if msg.content is not None else ""
        calls = parse_tool_calls(msg)               # ToolCallSyntaxError 上抛 → TLB-803 回喂
        _restore_call_names(calls, name_map)        # sanitized → fs.read_file 还原
        if not content and not calls:
            raise_code("LLM-304", model=self.model,
                       hint="content null 且无 tool_calls(协议异常,不回空文本冒充成功)")
        usage = getattr(raw, "usage", None)
        if usage is not None:
            await self.report_usage(usage, self.model, ctx=ctx)   # F029:落 llm.usage+计数器
        env = await ctx.session.append(             # 偏离 1:单条 llm.response 落盘
            "llm.response", _response_payload(self.model, finish, content, calls),
            actor="llm")
        return LLMResponse(content=content, tool_calls=calls or None, usage=usage,
                           model=self.model, finish_reason=finish, raw=raw, seq=env.seq)

    # ------------------------------------------------------ 流式入口(F027)
    async def chat_stream(self, messages: list[dict], tools: Optional[list] = None, *,
                          ctx: Any) -> LLMResponse:
        """流式编排:chunk 实时上总线(accumulate 内,不进日志);聚合后同 chat 型返回。"""
        cfg_llm = self._llm_cfg(ctx)
        transport = self._ensure_transport()
        tools_clean, name_map = _sanitize_tools(tools)   # 禁点号工具名清洗(DeepSeek)
        req = {"model": self.model, "messages": list(messages),
               "tools": tools_clean or None,
               "temperature": cfg_llm.temperature, "max_tokens": cfg_llm.max_tokens,
               "stream": True, "stream_options": {"include_usage": True}}
        await ctx.session.append("llm.request", _request_payload(self.model, self._deg,
                                                                 tools), actor="llm")
        try:
            stream = transport.stream(req)          # 返回异步迭代器(鸭子;可注入替身)
            text, calls, finish, usage = await asyncio.wait_for(
                accumulate_stream(stream, ctx), timeout=self.timeout.total_s)
        except Exception as e:                      # noqa: BLE001 断流/总闸统一归一
            code = normalize_exc(e)
            raise_code(code, model=self.model, retryable=code in _RETRYABLE_CODES)
        _restore_call_names(calls, name_map)        # sanitized → fs.read_file 还原
        if not text and not calls:
            raise_code("LLM-304", model=self.model,
                       hint="流式聚合为空(断流/零内容),不回空文本冒充成功")
        if usage is not None:                       # 流式计量在 include_usage 末块(F029)
            await self.report_usage(usage, self.model, ctx=ctx)
        env = await ctx.session.append(             # 聚合完成落单条 llm.response(F027)
            "llm.response", _response_payload(self.model, finish or "unknown",
                                              text, calls), actor="llm")
        return LLMResponse(content=text, tool_calls=calls or None, usage=usage,
                           model=self.model, finish_reason=finish or "unknown",
                           raw=None, seq=env.seq)

    # ------------------------------------------------------ 计量钩子
    async def report_usage(self, usage: Any, model: str, *, ctx: Any) -> Any:
        """F029 适配器侧计量(spec chat 伪码 self.report_usage 调用形;实现见模块级函数)。

        计数归属:显式注入(engine 装配共享实例)优先;未注入(桌面/CLI 装配期
        适配器先于 ctx)且 ctx.counters 在岗 → 落 ctx.counters——预算闸与
        llm 计量强制同源(scope._counters == ctx.counters == 本实例)。"""
        cnt = self._counters
        if not self._counters_injected:
            cnt = getattr(ctx, "counters", None) or self._counters
        return await report_usage(usage, model, ctx=ctx, counters=cnt)

    # ------------------------------------------------------ F033 探针
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
            raise_code(code, model=self.model, retryable=True)


# ================================================================ 注册表
# AdapterRegistry(F030):name(= 模型名) → 适配器实例;换模型 = 注册 + 配置,llm 零改动
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
    """注册表查询;未注册模型 → LLM-304(advice=核对模型名 F030;不重试)。"""
    inst = adapters.get(name)
    if inst is None:
        raise_code("LLM-304", model=name,
                   hint="模型未注册:核对模型名,或 register_adapter 后重试(F030)")
    return inst


# ================================================================ 凭据与客户端
def resolve_secret_ref(ref: str) -> str:
    """F016 单口最小落地:env/file/tenant 引用。缺失 → CRED-701。

    偏离 6:credentials 模块(真 F016)未在本阶段实现,此为默认 resolver;凭据模块落地后
    由装配层以 resolver 注入 build_client 替换。禁字面量密钥(配置层已拒载,CFG §7.1)。
    """
    val: Optional[str] = None
    if ref.startswith("tenant:"):
        from pyharness.core.tenant_settings import resolve_tenant_secret
        return resolve_tenant_secret(ref)
    if ref.startswith("env:"):
        val = os.environ.get(ref[4:].strip())
    elif ref.startswith("file:"):
        path = os.path.expanduser(ref[5:].strip())
        try:
            with open(path, "r", encoding="utf-8") as fh:
                val = (fh.readline().strip() or None)   # 首行;空文件 = 缺失
        except OSError as e:
            raise_code("CRED-701", ref=ref, reason=type(e).__name__,
                       hint="凭据缺失:检查 file:PATH 与权限(600)")
    else:
        raise_code("CRED-701", ref=ref, reason="secret_ref 语法非法",
                   hint="凭据只接受 env:NAME / file:PATH / tenant:租户:档案 引用")
    if not val:
        raise_code("CRED-701", ref=ref,
                   hint="凭据缺失:配置 env/file/tenant 引用后重试")
    return val


class _OpenAICompatHTTPTransport:
    """httpx OpenAI 兼容传输(仅 e2e 真实网络路径;unit 注入替身,绝不触网)。

    协议:POST {base}/chat/completions(请求线 ADI §1.1);body 剔除 None 字段(无工具整字段
    省略,wire 规则 §1.2);超时 = 三档 httpx.Timeout(连接=connect_s/读=first_token_s);
    max_retries 语义 = 0(零隐式重试,重试预算全归 F028 编排层,单点留痕)。
    """

    def __init__(self, base_url: str, api_key: str, timeout: Any) -> None:
        if httpx is None:
            raise_code("CYC-999", module="llm.transport", detail="httpx 缺失,无法构建客户端")
        self._base_url = base_url.rstrip("/")       # 裸域或 /v1 结尾(禁写 /chat/completions)
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {api_key}",   # 鉴权只走 header,禁拼 URL
                     # #23 请求归属:每请求带本框架标识(单机自用,审计友好)
                     "X-PyHarness-Agent": "pyharness/1.0"},
            timeout=timeout, max_redirects=2,
            trust_env=False)   # 禁读 env 代理:DeepSeek 直连;应用层不随 HTTP_PROXY 劫持
                                # (桌面 exe 曾因此卡死在 Clash 转发上,180s 才超时)
        self._req_path = "/chat/completions"

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _body(req: dict) -> dict:
        """请求体组装:None 字段剔除(tools 无则整字段省略,qwen 对 [] 语义不稳)。"""
        return {k: v for k, v in req.items() if v is not None}

    async def complete(self, req: dict) -> Any:
        """非流式 POST → raw 命名空间对象(choices/message/tool_calls/usage 鸭子同构)。"""
        # 断网/连接/读超时等 httpx.HTTPError 直传(归一 303);HTTP ≥400 → 状态错(按状态映射)
        resp = await self._client.post(self._req_path, json=self._body(req))
        if resp.status_code >= 400:
            raise ProviderStatusError(resp.status_code)
        try:
            data = resp.json()
        except ValueError as e:
            raise ProviderProtocolError("响应体非 JSON") from e
        if not isinstance(data, dict) or not data.get("choices"):
            raise ProviderProtocolError("choices 空/响应结构非法")
        return _to_ns(data)

    async def stream(self, req: dict) -> AsyncIterator[Any]:
        """SSE 流式(include_usage):逐行 data: 块 → raw chunk 命名空间;断流异常直传。"""
        body = self._body(req)
        async with self._client.stream("POST", self._req_path, json=body) as resp:
            if resp.status_code >= 400:
                raise ProviderStatusError(resp.status_code)
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload:
                    continue
                if payload == "[DONE]":
                    break
                try:
                    data = json.loads(payload)
                except ValueError as e:
                    raise ProviderProtocolError("SSE 块非 JSON") from e
                yield _to_ns(data)

    async def ping(self) -> float:
        """F033 探针:GET /models 往返秒(OpenAI 兼容端点均实现);≥400 → 状态错。"""
        t0 = time.perf_counter()
        resp = await self._client.get("/models")
        if resp.status_code >= 400:
            raise ProviderStatusError(resp.status_code)
        return time.perf_counter() - t0


def _to_ns(data: Any) -> Any:
    """dict → SimpleNamespace(递归;list 逐元素;原子值原样)——SDK 对象形态鸭子替身。"""
    if isinstance(data, dict):
        return SimpleNamespace(**{k: _to_ns(v) for k, v in data.items()})
    if isinstance(data, list):
        return [_to_ns(x) for x in data]
    return data


def build_client(triple: AdapterTriple, limits: TimeoutLimits, *,
                 resolver: Any = None) -> _OpenAICompatHTTPTransport:
    """客户端构建(ADI §1.1):按三元组建传输客户端;key 经 resolver(F016 单口)缺失 → CRED-701。

    偏离 2:spec 返回 openai.AsyncOpenAI——openai SDK 非本阶段运行依赖(真实调用留 e2e),
    此处返回 httpx OpenAI 兼容传输(complete/stream/ping,语义与 create 对齐),可整体注入。
    """
    resolver = resolver or resolve_secret_ref
    key = resolver(triple.api_key_ref)              # 缺失 → CRED-701(绝不空串/None 续跑)
    if not key:
        raise_code("CRED-701", ref=triple.api_key_ref,
                   hint="凭据解析为空,拒绝构建客户端(绝不空串续跑)")
    base = triple.base_url.rstrip("/")              # 裸域或 /v1 结尾;禁写 /chat/completions
    timeout = httpx.Timeout(limits.connect_s,       # 连接/写/池 档
                            read=limits.first_token_s,   # 首 token 档(60s)
                            write=limits.connect_s, pool=limits.connect_s)
    return _OpenAICompatHTTPTransport(base_url=base, api_key=key, timeout=timeout)


# ================================================================ 会话门面
class LLMClient:
    """会话级 LLM 门面(装配后 ctx.llm;agent-loop 唯一经此出网)。

    模型解析:显式 model → cfg.llm.model → L1 deepseek-chat;注册表缺该模型 → LLM-304
    (核对模型名 F030,不自动造适配器——装配层 register_adapter 显式登记,禁隐式端点)。
    降级链/退避(F013/F028):chain 注入后本类代理到 FallbackChain.chat_with_fallback
    (指数退避 + 单调降级 + BudgetGuard 前置;链缺位 = 直连适配器,行为同旧版)。
    """

    def __init__(self, cfg: Any = None, *, model: Optional[str] = None,
                 registry: Optional[dict] = None,
                 chain: Any = None) -> None:
        llm = getattr(cfg, "llm", None) if cfg is not None else None
        self.model: str = model or (llm.model if llm is not None else "deepseek-chat")
        self.registry: dict = registry if registry is not None else adapters
        self._chain: Any = chain                 # FallbackChain(F013/F028;可 None)

    def require(self) -> LLMAdapter:
        """解析当前模型适配器(未注册 → LLM-304)。"""
        inst = self.registry.get(self.model)
        if inst is None:
            raise_code("LLM-304", model=self.model,
                       hint="模型未注册:核对模型名,或 register_adapter 后重试(F030)")
        return inst

    async def _chat_any(self, method: str, messages: list[dict],
                        tools: Optional[list], ctx: Any) -> LLMResponse:
        """统一路由:chain 在岗(engine 装配)走降级编排;否则直连适配器。"""
        ch = self._chain
        if ch is not None and callable(getattr(ch, "chat_with_fallback", None)):
            return await ch.chat_with_fallback(messages, tools, ctx=ctx,
                                               method=method)
        inst = self.require()
        return await getattr(inst, method)(messages, tools, ctx=ctx)

    async def chat(self, messages: list[dict], tools: Optional[list] = None, *,
                   ctx: Any) -> LLMResponse:
        """唯一出口(代理到适配器/降级链;请求全生命周期单元,事件路径唯一)。"""
        return await self._chat_any("chat", messages, tools, ctx)

    async def chat_stream(self, messages: list[dict], tools: Optional[list] = None, *,
                          ctx: Any) -> LLMResponse:
        """流式出口(代理到适配器/降级链;chunk 上总线不入日志)。"""
        return await self._chat_any("chat_stream", messages, tools, ctx)

    async def summarize(self, prompt: str, *, budget: int = 400,
                        ctx: Any = None) -> str:
        """压缩摘要出口(F058 Consumer):单次 chat 取 content;同走降级链。

        compaction._call_summarize 经 ctx.llm.summarize 消费;未装配该面时
        compaction 走规则降级(LLM-399 → degrade_summary,不炸)。"""
        messages = [{"role": "user", "content": prompt}]
        resp = await self._chat_any("chat", messages, None, ctx)
        return (resp.content or "").strip()

    async def json_chat(self, prompt: str, **kw: Any) -> Any:
        """JSON 强约束出口(plan_mode 等编排消费):单次 chat 后稳健抽取 JSON。

        只负责把模型文本解析成 dict/list;结构校验由消费方(PlanManager)完成。
        代码块围栏/前后叙述裁剪,坏 JSON → LLM-304(可重试错误,不静默伪装)。
        """
        goal = str(kw.get("goal") or "")
        max_steps = int(kw.get("max_steps") or 8)
        ctx = kw.get("ctx")
        user = f"目标: {goal}\n最多 {max_steps} 步" if goal else \
            f"最多 {max_steps} 步"
        messages = [{"role": "system", "content": prompt},
                    {"role": "user", "content": user}]
        resp = await self._chat_any("chat", messages, None, ctx)
        text = (resp.content or "").strip()
        try:
            return _extract_json_text(text)
        except ValueError as e:
            raise_code("LLM-304", hint=f"json_chat 输出解析失败: {e}",
                       advice="提示模型只回 JSON;重试")


def _extract_json_text(text: str) -> Any:
    """裁剪 ```json 围栏后抽取首个完整 JSON 对象/数组。"""
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.I)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        lo, hi = cleaned.find(open_ch), cleaned.rfind(close_ch)
        if lo != -1 and hi > lo:
            return json.loads(cleaned[lo:hi + 1])
    raise ValueError("模型输出不含 JSON 对象/数组")


__all__ = [
    # 数据结构
    "TimeoutLimits", "ToolCall", "LLMResponse", "UsageCounters", "AdapterTriple",
    # 异常
    "ToolCallSyntaxError", "ProviderStatusError", "ProviderProtocolError",
    # wire 解析层
    "parse_tool_calls", "assistant_and_tool_messages", "accumulate_stream",
    # 错误归一/成本/计量
    "normalize_exc", "estimate_cost", "report_usage",
    # 适配器与注册表(F030)
    "LLMAdapter", "OpenAICompatAdapter", "adapters", "register_adapter", "require_adapter",
    # 客户端构建(ADI §1.1)/凭据单口(F016 占位)
    "build_client", "resolve_secret_ref",
    # 会话门面(ctx.llm)
    "LLMClient",
]
