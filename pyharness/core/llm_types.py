"""Llm types: complete migrated implementation."""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional
from pyharness.errors import raise_code
if TYPE_CHECKING:  # 仅类型标注;装配对象鸭子注入(session/bus 经 ctx)
    pass


_RETRYABLE_CODES = frozenset({"LLM-301", "LLM-303"})

_L1_SETTINGS: Any = None  # L1 兜底配置懒加载缓存(无 ctx.config/无注入 cfg 时)

def _l1_settings() -> Any:
    """L1 默认 Settings(键缺省必可加载;仅无注入配置时兜底,不读写用户密钥)。"""
    global _L1_SETTINGS
    if _L1_SETTINGS is None:
        from pyharness.config import load_settings  # 惰性:保持模块导入零配置依赖
        _L1_SETTINGS = load_settings()
    return _L1_SETTINGS

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
    ``cost_est`` **是**预算硬闸判据之一(与 token 并列):``Scope.budget_state`` 与
    ``BudgetGate._compute_state`` 均按 ``used.cost_est >= limits.max_cost_yuan`` 判
    exhausted。原注释称其"只进事件/报表、不打硬闸(N14)"**与实现相反**(实测:纯成本
    超限即拦),已在 R21 更正。"""

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
    _session_total: Any = field(default=None, repr=False, compare=False)
    _parent: Any = field(default=None, repr=False, compare=False)
    _limits: Any = field(default=None, repr=False, compare=False)
    _descendants: Any = field(default=None, repr=False, compare=False)
    _reserved_out: int = field(default=0, repr=False, compare=False)
    _reserved_in: int = field(default=0, repr=False, compare=False)
    _reserved_cost: float = field(default=0, repr=False, compare=False)

    def new_task(self, limits, *, parent=None):
        """Fresh task usage, separate from session and ancestor totals."""
        return UsageCounters(_session_total=self._session_total or self,
                             _parent=parent, _limits=limits)

    def ancestors(self):
        account = self
        while account is not None:
            yield account
            account = account._parent

    def reserve_output(self, requested, *, input_tokens=0, input_rate=0.0, output_rate=0.0):
        """Atomic on the runtime loop: reserve before awaiting any transport."""
        from pyharness.core.scope import BudgetExhausted
        from pyharness.core.budget_diagnostics import budget_detail
        accounts = list(self.ancestors())
        available = int(requested)
        for account in accounts:
            limits = account._limits
            if limits is None:
                continue
            used = account.task_total()
            owner='task' if account is self else 'parent'
            if used.in_tokens >= limits.max_in_tokens or used.in_tokens + account._reserved_in + input_tokens > limits.max_in_tokens:
                raise BudgetExhausted('Input allowance exhausted', diagnostics=budget_detail('input',
                    limit=limits.max_in_tokens,used=used.in_tokens,reserved=account._reserved_in,
                    requested=input_tokens,account=owner,basis='conservative_utf8_bytes'))
            if used.cost_est >= limits.max_cost_yuan:
                raise BudgetExhausted('Cost allowance exhausted',diagnostics=budget_detail('cost',
                    limit=limits.max_cost_yuan,used=used.cost_est,reserved=account._reserved_cost,
                    requested=input_tokens*input_rate+output_rate,account=owner,basis='estimated_yuan'))
            available = min(available, limits.max_out_tokens-used.out_tokens-account._reserved_out)
            cost_left = limits.max_cost_yuan-used.cost_est-account._reserved_cost-input_tokens*input_rate
            if cost_left < 0:
                raise BudgetExhausted('No unreserved task or parent cost allowance',diagnostics=budget_detail('cost',
                    limit=limits.max_cost_yuan,used=used.cost_est,reserved=account._reserved_cost,
                    requested=input_tokens*input_rate+output_rate,account=owner,basis='estimated_yuan'))
            if output_rate > 0:
                available = min(available, int(max(0,cost_left)/output_rate))
            if available <= 0:
                dimension='cost' if output_rate>0 and cost_left<output_rate else 'output'
                raise BudgetExhausted('No unreserved task or parent output allowance',diagnostics=budget_detail(dimension,
                    limit=limits.max_cost_yuan if dimension=='cost' else limits.max_out_tokens,
                    used=used.cost_est if dimension=='cost' else used.out_tokens,
                    reserved=account._reserved_cost if dimension=='cost' else account._reserved_out,
                    requested=input_tokens*input_rate+output_rate if dimension=='cost' else 1,
                    account=owner,basis='estimated_yuan' if dimension=='cost' else 'tokens'))
        for account in accounts:
            account._reserved_out += available
            account._reserved_in += input_tokens
            account._reserved_cost += input_tokens*input_rate+available*output_rate
        released = False
        def release():
            nonlocal released
            if not released:
                released = True
                for account in accounts:
                    account._reserved_out -= available
                    account._reserved_in -= input_tokens
                    account._reserved_cost = max(0, account._reserved_cost-input_tokens*input_rate-available*output_rate)
        return available, release

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
        self._rate_limit_streak = 0
        if self._session_total is not None:
            self._session_total.task_add(payload)
        parent = self._parent
        while parent is not None:
            # A legacy session counter already received the charge above.
            if parent is not self._session_total:
                if parent._descendants is None:
                    parent._descendants = UsageCounters()
                parent._descendants.task_add(payload)
            parent = parent._parent

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

        Task fields represent own usage; descendants contribute only here.
        The session ledger is independently cumulative across task lifetimes."""
        from pyharness.core.llm_fallback import TaskUsage   # 免顶层环依赖
        child = self._descendants
        return TaskUsage(in_tokens=self.in_tokens+(child.in_tokens if child else 0),
                         out_tokens=self.out_tokens+(child.out_tokens if child else 0),
                         cost_est=round(self.cost_est+(child.cost_est if child else 0), 6))

    def rate_limit_streak(self) -> int:
        """连续 429 计数(协议;事件可重建)。"""
        return self._rate_limit_streak

    def rate_limit_add(self) -> int:
        """429 计数 +1(适配器归一 LLM-303 时调用;返回累计)。"""
        self._rate_limit_streak += 1
        return self._rate_limit_streak

    def degrade_add(self, failed: str, to: str) -> int:
        """会话级降级计数(F013):返回累计次数(超 max_per_session 发告警)。"""
        if self._session_total is not None:
            return self._session_total.degrade_add(failed, to)
        self._degrade_total += 1
        self._degrade_last = {"failed": failed, "to": to}
        return self._degrade_total

@dataclass(frozen=True)
class AdapterTriple:
    """端点三元组(ADI §0.1 全局不变量①:模型差异=(base_url, api_key_ref, model))。"""

    base_url: str        # 裸域或 /v1 结尾(禁写 /chat/completions)
    api_key_ref: str     # env:NAME / file:PATH 秘密引用(F016 单口)
    model: str
    credential_store: Any = field(default=None, repr=False, compare=False)
    credential_binding: Any = field(default=None, repr=False, compare=False)

class ProviderStatusError(Exception):
    """内置传输层 HTTP 状态错(类别判定用,ADI §7;body 文案不上行 ERR §5.1)。"""

    def __init__(self, status_code: int, detail: str = "", *, diagnostics=None) -> None:
        self.status_code = int(status_code)
        self.diagnostics = dict(diagnostics or {})
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

# Preserve historical import and pickle identities through the compatibility facade.
TimeoutLimits.__module__ = 'pyharness.core.llm'
ToolCall.__module__ = 'pyharness.core.llm'
LLMResponse.__module__ = 'pyharness.core.llm'
UsageCounters.__module__ = 'pyharness.core.llm'
AdapterTriple.__module__ = 'pyharness.core.llm'
ProviderStatusError.__module__ = 'pyharness.core.llm'
ProviderProtocolError.__module__ = 'pyharness.core.llm'
ToolCallSyntaxError.__module__ = 'pyharness.core.llm'
LLMAdapter.__module__ = 'pyharness.core.llm'
