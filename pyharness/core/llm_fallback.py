"""pyharness/core/llm_fallback.py — 降级链 + 指数退避 + 健康探针 + 预算闸 (specs/llm_fallback.py.md)

功能编号:F013(核心)· F028 · F033 · F032(预算联动前置闸)。
权威口径:specs/llm_fallback.py.md(编码契约)、DIS-CORE §4.3/§4.4(降级/退避/状态机
伪代码)、ADI §3(触发判据/切换粒度/回切)、ERR.md §2.4/§5.2(LLM 错误码)、
CFG.md §3.1/§3.5(llm.retry/llm.degrade/llm.probe/budget.task.* 配置键)。

职责一句话:主模型失败时按链切备用(qwen-max)——请求级粒度、idx 前移对后续请求生效、
降级留痕、探针回切(3 败 down / 2 好回)、BudgetGuard 守住"发请求前/每次重试前"的
单任务预算前置检查;LLM-304 业务错不降级直抛;全链败 → LLM-310 终态(不无限降级)。

依赖方向:llm_fallback 属 llm 模块族,经 ctx 消费 config/session/counters(注入);
经 ctx.session.append 落 llm.retry 事件、尽力而为 bus 通知发告警。llm.py(adapters
注册表/LLMResponse)阶段未落地——本模块不 import 之,适配器经构造注入(测试全 mock)。

偏离说明(相对 spec 伪码;契约=spec,偏离均列理由):
1. 注入式装配:伪码引用 llm.py 全局 adapters 注册表与全局 bus;llm.py 未落地,
   FallbackChain 构造注入 adapters(名称→适配器)/config/chain/bus(任务要求主备
   Provider 注入式)。chain 缺省由 config.llm.model + fallback_models 拼装。
2. BudgetGuard.check 为 async:伪码同步 def 与内部 await ctx.session.append 自相矛盾
   (SessionLog.append 为 async,落盘须 await),调用点相应 await。
3. BUDGET-EXHAUSTED 为终态信号码(未入 ERR 登记册):raise_code 会把未登记码改写为
   CYC-999,故直接构造 PyHError——与 agent_loop.BUSY 同款先例(见 agent_loop.py
   偏离说明 5)。
4. 探针回切 idx 归零:DIS-CORE §4.4 状态机要求"恢复自动回切",但伪码未给 idx 归零点;
   本实现由 probe_loop 检测主模型 down→healthy(2 好回切判据)时置 idx=0 并尽力发
   llm.recovered 事件。degraded→healthy(单次成功)只更新状态不归零——回切须 F033
   的 2 好判据,防抖动。
5. 链尾降级收敛 LLM-310:伪码在链尾仍执行 _record_degradation(..., chain[i+1])/
   idx=i+1 会 IndexError 或越过链长;本实现仅当存在下一适配器才降级,链尾 302/303
   (含降级关关闭)自然落到 LLM-310(GWT-L4-02 双败→LLM-310 口径,err_hist 携带)。
6. AdapterHealth.degraded 单次 ok 回 healthy:伪码 degraded 态永不回 healthy 属缺口
   (DIS 4.4 线性状态机 healthy→degraded→down→healthy 语义);llm.recovered 通知由
   probe_loop 按状态翻转发出(mark 保持纯状态机无副作用、无 bus 句柄依赖)。
7. 探针 ping 超时(5s wait_for 的 TimeoutError)与非 PyHError 未预期异常同判失败
   (伪码异常表只列 PyHError);探针尽力而为,失败不落 llm.retry、不计 F029 用量。
8. 降级/预算通知事件(system.error/budget.warn/llm.recovered)为尽力而为出口:
   EventBus.emit 同步返回分发协程,同步决策点 fire-and-forget(create_task 排程,
   失败只记日志不阻断主路径);budget.paused 强留痕走 session.append(async 路径)。
9. llm.retry.attempts=0 视同 1(CFG 取值范围含 0,但至少应发出一次请求)。
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import random
from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Protocol

from pyharness.config import load_settings
from pyharness.errors import PyHError, raise_code

log = logging.getLogger("pyharness.llm_fallback")

# ------------------------------------------------------------------ 码集常量
# 决策只看码(ERR.md §2.4/§5.2):302 认证直降;303 可重试(429/5xx/断网/超时);
# 304 业务错不降级直抛;301 超时只在退避内消化。
_DEGRADE_CODES: tuple[str, ...] = ("LLM-302", "LLM-303")
_RETRYABLE_CODES: tuple[str, ...] = ("LLM-301", "LLM-303")


# ------------------------------------------------------------------ 类型别名
BudgetState = Literal["ok", "warn", "paused", "exhausted"]
"""预算状态字面量(F032):exhausted → agent-loop 强制终态 reason=budget。"""

HealthTable = dict[str, "AdapterHealth"]
"""探针健康表:每 interval_s 更新;pick()/降级跳过判据的数据源。"""


# ---------------------------------------------------------------- TaskUsage
@dataclass
class TaskUsage:
    """单任务用量快照(BudgetGuard 只读;真源 = scope.report_usage 事件,可重建)。

    与 scope.budget_state 同一数据源口径(INV-01):BudgetGuard 不持有第二份累计,
    每次 check 都现读 counters.task_total()。
    """

    in_tokens: int = 0
    out_tokens: int = 0
    cost_est: float = 0.0

    def model_dump(self) -> dict:
        """事件/错误上下文载荷序列化(pydantic 同款方法名,scope 落地可无缝替换)。"""
        return {"in_tokens": self.in_tokens, "out_tokens": self.out_tokens,
                "cost_est": self.cost_est}


# -------------------------------------------------------------- 注入式协议
class LLMAdapter(Protocol):
    """适配器最小契约(llm.py F030 注册表条目;本模块按鸭子类型消费,不 import llm.py)。

    chat: 返回 LLMResponse 型对象(content/tool_calls/model/…,由 llm.py 定义);
    ping: 探针可达性延迟探测,成功返回秒数(float),失败抛 PyHError/超时。
    """

    async def chat(self, messages, tools=None, *, ctx) -> Any:  # pragma: no cover
        ...

    async def ping(self) -> float:  # pragma: no cover
        ...


class UsageCounters(Protocol):
    """会话/任务用量计数器最小契约(scope 模块落地前注入;report_usage 唯一写入)。

    task_total(): 本任务累计用量(读任务级聚合);rate_limit_streak(): 连续限流
    (429)计数,事件可重建;degrade_add(): 会话级降级计数,返回累计次数(可重建)。
    """

    def task_total(self) -> TaskUsage:  # pragma: no cover
        ...

    def rate_limit_streak(self) -> int:  # pragma: no cover
        ...

    def degrade_add(self, failed: str, to: str) -> int:  # pragma: no cover
        ...


# ------------------------------------------------------------ AdapterHealth
@dataclass
class AdapterHealth:
    """单适配器健康状态机(F033):healthy → 1 败 degraded → 连 3 败 down → 连 2 好 healthy。

    mark(ok) 由探针 ping 结果驱动,纯状态机、无副作用(不计数、不发事件、不落日志)
    ——llm.recovered 等通知由 probe_loop 依据状态翻转发出。防抖动:down 需连 3 败,
    回切 healthy 需连 2 好(瞬时 1 败仅 degraded;degraded 单次成功即解除,见偏离 6)。
    """

    state: str = "healthy"          # healthy / degraded / down
    fail_streak: int = 0
    ok_streak: int = 0

    def mark(self, ok: bool) -> None:
        """按一次探针结果推进状态机。ok=True=ping 成功,ok=False=ping 失败。"""
        if ok:
            self.fail_streak = 0
            self.ok_streak += 1
            if self.state == "down":
                if self.ok_streak >= 2:      # 连 2 好 → 回切(防抖动)
                    self.state = "healthy"
            elif self.state == "degraded":
                self.state = "healthy"       # 瞬时单败解除(偏离 6)
        else:
            self.ok_streak = 0
            self.fail_streak += 1
            if self.state == "healthy" and self.fail_streak == 1:
                self.state = "degraded"      # 1 败仅 degraded,不 down
            if self.fail_streak >= 3:
                self.state = "down"          # 连 3 败 down

    def is_down(self) -> bool:
        """down 态判定(down 者被 pick()/降级循环跳过)。"""
        return self.state == "down"


# ------------------------------------------------------------ 事件尽力出口
def _fire_emit(bus: Any, type_: str, payload: dict) -> None:
    """尽力而为总线出口:bus 未接线/emit 抛错只记日志,不阻断调用方主路径。

    EventBus.emit(type, payload) 同步返回分发协程(见 event_bus.py emit 偏离点);
    运行中事件循环存在时 create_task 排程投递,否则降级为日志(config.py _emit_event
    同款 fire-and-forget 语义;告警/通知不阻塞降级主路径)。
    """
    if bus is None:
        return
    try:
        r = bus.emit(type_, payload)
        if inspect.isawaitable(r):
            try:
                asyncio.get_running_loop().create_task(r)
            except RuntimeError:             # 无运行中事件循环:同步/未装配上下文
                log.warning("llm_fallback: 无运行中事件循环,%s 事件未投递", type_)
    except Exception as exc:                 # noqa: BLE001 EVT-102 未注册类型等
        log.warning("llm_fallback emit %s 失败: %s", type_, exc)


# ------------------------------------------------------------- FallbackChain
@dataclass
class FallbackChain:
    """链式降级编排(F013/DIS-CORE §4.3.2):chain[idx:] 逐适配器经 _retry_adapter
    尝试;可重试码耗尽且满足降级条件才前移 idx(对后续请求生效);LLM-304 业务错上抛;
    全链败 → LLM-310 终态。F033 探针/择优/回切与 F028 退避亦在本类内聚。

    spec FallbackChain.__init__() 无参;按"主备 Provider 注入式"(偏离 1):
        adapters = {适配器名: LLMAdapter} 注册表(llm.py F030 落地前由装配层注入)
        config   = Settings 或 None(L1 load_settings;ctx.config 优先于本字段)
        chain    = 显式链或 None(config.llm.model + llm.fallback_models 拼装)
        bus      = 告警/通知事件尽力出口(可 None)
    """

    adapters: Optional[dict[str, Any]] = None
    config: Any = None
    chain: Optional[list[str]] = None
    bus: Any = None
    idx: int = 0                              # 降级结果前移,对后续请求生效
    health: HealthTable = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        if self.config is None:
            self.config = load_settings()     # L1 默认(键缺省必可加载)
        if self.chain is None:
            # CFG llm.fallback_models 注入:主模型 + 备用链(默认 ["deepseek-chat", "qwen-max"])
            self.chain = [self.config.llm.model] + list(self.config.llm.fallback_models)
        self.chain = list(self.chain)
        if not (0 <= self.idx < len(self.chain)):
            self.idx = 0                      # 链收缩防御:越界回起点
        self.health = {n: AdapterHealth() for n in self.chain}

    # ====================================================== 链式降级编排(F013)
    async def chat_with_fallback(self, messages, tools=None, *, ctx) -> Any:
        """从 chain[idx:] 起逐适配器经 _retry_adapter 尝试,返回 LLMResponse。

        每适配器进入前过 BudgetGuard(预算前置闸);302/303 且满足降级条件 → 留痕 +
        idx 前移,本轮请求继续下一适配器;304 等业务错不降级直接上抛;链尾 302/303
        (或 degrade.enabled=false 主模型败)→ LLM-310 终态(err_hist 携带排查)。
        """
        err_hist: list[tuple[str, str]] = []
        enabled = bool(ctx.config.llm.degrade.enabled)
        # 降级总开关(CFG llm.degrade.enabled):关 = 只走当前 idx 适配器,败即 LLM-310
        end = len(self.chain) if enabled else min(self.idx + 1, len(self.chain))
        start = self.idx
        for i in range(start, end):
            name = self.chain[i]
            # 探针 down → 跳过(仅跳过循环起始位之后的;起始位不跳:仍尝试以测恢复,
            # 与 pick() 全 down 保底一致——主模型 down 不跳,见 spec 注释)
            if self.health[name].is_down() and i > start:
                continue
            await BudgetGuard.check(ctx)      # 每请求前置预算闸(F032)
            try:
                return await self._retry_adapter(name, messages, tools, ctx)
            except PyHError as e:
                err_hist.append((name, e.code))
                if (e.code in _DEGRADE_CODES and enabled
                        and i + 1 < len(self.chain)
                        and self._may_degrade(e, ctx)):
                    # 降级:留痕 + idx 前移(单调;对后续请求生效),本轮继续下一适配器
                    self._record_degradation(name, self.chain[i + 1], ctx)
                    self.idx = i + 1
                    continue
                if e.code in _DEGRADE_CODES:
                    break                     # 链尾/降级关:全链耗尽 → LLM-310(偏离 5)
                raise                         # LLM-304 业务错等:不降级直接上抛
        raise_code("LLM-310", err_hist=err_hist,
                   advice="全链失败,任务终止(不无限降级)")

    # ====================================================== 指数退避(F028)
    async def _retry_adapter(self, name: str, messages, tools, ctx) -> Any:
        """单适配器内重试循环:只重试 LLM-301/303;基数 1s×2 递增、上限 attempts
        (默认 4)、±jitter(默认 30%)抖动;每次等待落 llm.retry 事件(sleep 可被取消
        F025);退避耗尽 → LLM-303(exhausted)交 chat_with_fallback 降级决策。
        """
        cfg = ctx.config.llm.retry
        n_attempts = max(1, int(cfg.attempts))        # 0 视同 1(偏离 9)
        delay = float(cfg.base_delay_s)               # 基数 1s ×2(CFG llm.retry)
        jitter = float(getattr(cfg, "jitter", 0.3))
        for attempt in range(n_attempts):
            await BudgetGuard.check(ctx)              # 每次重试前再查预算(防烧钱)
            try:
                return await self.adapters[name].chat(messages, tools, ctx=ctx)
            except PyHError as e:
                if e.code not in _RETRYABLE_CODES:
                    raise                             # LLM-302/304:不重试
                if attempt == n_attempts - 1:
                    # 退避预算耗尽:交 chat_with_fallback 降级决策
                    raise_code("LLM-303", model=name, exhausted=True)
                await ctx.session.append(
                    "llm.retry",
                    {"model": name, "attempt": attempt,
                     "delay_ms": int(delay * 1000)},
                    actor="llm")
                # sleep 可被取消(F025);总重试时长计入请求预算
                await asyncio.sleep(delay * random.uniform(1 - jitter, 1 + jitter))
                delay *= 2

    # ====================================================== 降级条件判定(ADI §3.3)
    def _may_degrade(self, e: PyHError, ctx) -> bool:
        """按码判定:LLM-302 无条件降;LLM-303 exhausted 或连续限流达阈值(默认 2)
        降;304 永不降级(降级只救"上游坏",不救"我们错")。"""
        if e.code == "LLM-302":                       # 认证错:不重试直接降级
            return True
        if e.code == "LLM-303":                       # 429/5xx/断网/超时
            if e.ctx.get("exhausted"):
                return True                           # 退避耗尽(attempts 次)后降
            consec = ctx.counters.rate_limit_streak()  # 连续 429 计数(事件可重建)
            return consec >= int(
                ctx.config.llm.degrade.rate_limit_consecutive)  # 默认 2
        return False                                  # LLM-301/304:301 已在退避内消化

    # ====================================================== 降级留痕与告警(ADI §3.4)
    def _record_degradation(self, failed: str, to: str, ctx) -> None:
        """每次实际降级计数;超过 max_per_session(默认 5)发 system.error 告警事件。

        idx 前移已在 chat_with_fallback 完成;llm.request 的 degraded_from 由下一次
        chat() 落(见 llm.py 职责)——此处只计数留痕,事件尽力而为(偏离 8)。
        """
        n = ctx.counters.degrade_add(failed, to)      # 会话级降级计数(可重建)
        if n > int(ctx.config.llm.degrade.max_per_session):
            _fire_emit(self.bus, "system.error", {
                "code": "LLM-310",
                "advice": f"单会话降级 {n} 次,查备用模型配额/上游状态"})

    # ====================================================== 周期健康探针(F033)
    async def probe_loop(self, adapters: Optional[dict[str, Any]] = None,
                         interval_s: float = 60.0) -> None:
        """进程生命周期协程:每 interval_s 对全部适配器 ping()(5s 超时),结果 mark()。

        探针成功不计 F029 用量、失败不落 llm.retry——只判可达性,真实请求仍按错误码
        走降级(ADI §3.5);主模型 down→healthy(2 好回切)时 idx 归零自动回切主模型
        (偏离 4)。取消即退出(不吞 CancelledError,F025)。
        """
        reg = self.adapters if adapters is None else adapters
        if not reg:
            log.warning("probe_loop: 无适配器注册表,探针空转退出")
            return
        while True:
            for name, adp in reg.items():
                prev = self.health[name].state
                try:
                    await asyncio.wait_for(adp.ping(), timeout=5.0)
                    self.health[name].mark(True)
                except (PyHError, asyncio.TimeoutError):
                    self.health[name].mark(False)
                except Exception:                     # noqa: BLE001 未预期同判失败
                    log.debug("probe %s 未预期异常", name, exc_info=True)
                    self.health[name].mark(False)
                # 2 好回切(down→healthy):通知 + 主模型 idx 归零自动回切
                if (prev == "down"
                        and self.health[name].state == "healthy"):
                    _fire_emit(self.bus, "llm.recovered", {"model": name})
                    if name == self.chain[0]:
                        self.idx = 0
            await asyncio.sleep(interval_s)

    # ====================================================== 适配器择优(F033)
    def pick(self) -> str:
        """返回 chain 中从 idx 起第一个非 down 适配器;全部 down → 返回 idx 当前
        元素(交由降级流程暴露 LLM-310)。主模型 down 不跳过(保底仍尝试,以测恢复)。"""
        for m in self.chain[self.idx:]:
            if not self.health[m].is_down():
                return m
        return self.chain[self.idx]


# -------------------------------------------------------------- BudgetGuard
class BudgetGuard:
    """单任务预算闸(F032 前置;CONSTRAINTS-07 §10):发请求前与每次重试前读
    UsageCounters(与 scope.budget_state 同源,report_usage 唯一写入)与 BudgetLimits
    (ctx.config.budget.task),返回 ok/warn;超任一硬闸 → budget.paused(exhausted)
    事件 + 抛 BUDGET-EXHAUSTED(agent-loop 据此强制终态 reason=budget)。

    偏离说明:check 为 async(spec 伪码同步签名与内部 await session.append 矛盾,
    见偏离 2);paused/exhausted 态由 agent-loop 的 scope.budget_state 三闸强制,
    本闸只做前置拦截,返回面为 ok/warn + raise。
    """

    # 硬闸:输出/输入 token 或估算成本任一超限 → 终态(判定以 token 为准,F032)
    @staticmethod
    async def check(ctx) -> BudgetState:
        """前置检查:超限 raise BUDGET-EXHAUSTED;达 warn_ratio(默认 80%)返回
        "warn";否则 "ok"。"""
        used = ctx.counters.task_total()      # 与 scope.budget_state 同一数据源
        lim = ctx.config.budget.task          # in≤200万 / out≤5万 / cost≤1 元(F032)
        if (used.out_tokens >= lim.max_out_tokens
                or used.in_tokens >= lim.max_in_tokens
                or used.cost_est >= lim.max_cost_yuan):
            await ctx.session.append(
                "budget.paused", {"state": "exhausted",
                                  "used": used.model_dump()}, actor="system")
            raise PyHError("BUDGET-EXHAUSTED",
                           ctx={"used": used.model_dump()})   # 偏离 3:未登记码直构
        warn_ratio = float(getattr(ctx.config.budget, "warn_ratio", 0.8))
        if used.out_tokens >= warn_ratio * lim.max_out_tokens:  # warn=80% 提醒
            _fire_emit(getattr(ctx, "bus", None), "budget.warn",
                       {"used_out": used.out_tokens})
            return "warn"
        return "ok"

    @staticmethod
    def report(ctx) -> dict:
        """预算读数汇总(/cost 命令与审计数据源,CONSTRAINTS-07 §9;纯只读)。"""
        used = ctx.counters.task_total()
        lim = ctx.config.budget.task
        return {"used": used.model_dump(), "limit": lim.model_dump(),
                "pct_out": used.out_tokens / lim.max_out_tokens * 100}


__all__ = [
    "AdapterHealth", "BudgetGuard", "BudgetState", "FallbackChain",
    "HealthTable", "LLMAdapter", "TaskUsage", "UsageCounters",
]
