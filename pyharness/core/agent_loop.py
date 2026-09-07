"""pyharness/core/agent_loop.py — Agent 主循环三态机驱动器(specs/agent_loop.py.md 契约;阶段 1 核心)

F007 主循环:消费 user.message(已由外壳强同步落盘)→ 每轮从会话日志现派生上下文
(INV-01)→ llm.chat → 纯文本即终态 / tool_calls 逐工具步执行后进入下一轮。轮数
/收敛/取消/预算四类终态闸在此强制;本模块是全框架唯一允许调 ``llm.chat`` 的入口
(INV-02)——LLM 经注入的 ctx.llm 调用,绝不 import 真实 API;工具执行一律经注入的
ctx.tools.execute(校验/guard/审批/Provider 全在工具层内聚),本模块不 import 外围
能力(INV-08 单向:agent_loop → {agent, llm, session, scope})。

三态机:顶层三态 idle / running / terminated(PRD F007);paused / stopping 为
running 的受控子态(本阶段以字段位表达:state 字面量 + asyncio.Lock 双保险,同会话
同时仅一个 running)。终态收尾分工(DIS §0.2.6):session.finished 只由 agent.close
写(EVT-104);本模块 _finish 只返回 RunResult(reason),不写 finished。

轮口径(DIS §0.2):run = 外壳一次 submit 触发的整段运行(本实现含队内排水,见
run() 偏离说明);轮 = run 内一次 LLM 调用及其全部工具;RunContext.turn 计已完成的
工具轮(与 DIS/伪码 run.turn += 1 语义一致,文本轮天然终态不占轮数)。

偏离说明(相对 specs/agent_loop.py.md 伪码):
1. run() = 排水监督器:伪码 run() 主体(while True + 三闸 + 异常分级)落在私有
   _run_engine(ctx, run) 中(单输入引擎);公开 run(ctx) 在 state 恒为 running 的
   前提下逐件消费 pending(状态表 running→running"取下一 pending,新 RunContext")
   并返回最后一件的 RunResult。理由:伪码 run 返回即回 idle,与自身状态表及
   wake()"当前 run 结束后自动取下一件"的 FIFO 口径矛盾;无排水层则并发 submit
   会乱序插队。排水在 run.cancelled/budget 终态时停止(用户/资源边界,剩余队内
   输入保留不静默丢,待下次 wake 按 FIFO 继续消费)。
2. BudgetExhausted / MaxTurnsExceeded 为循环控制信号异常,spec 未给归属模块
   (scope/llm 未实现),本模块本地定义;scope.py 落地后按 INV-08(agent_loop →
   scope 允许)改为 from pyharness.core.scope import ...。catch 仅为防御:主终止
   路径是 _must_stop 三闸声明式判定。
3. ctx 绑定:loop 与会话 1:1(一会话一 agent,ctx.loop),ctx 一经 run(ctx)/wake
   (env, ctx) 绑定即终身有效(cancel()/队列满留痕等无参路径经 self._ctx 引用);
   伪码 cancel(reason) 体内引用 ctx 却无参即因此成立。wake(env, ctx=None) 比伪码
   wake(env) 多一个可选 ctx:伪码 idle 分支"create_task(run)"同样没有 ctx 来源,
   该参数供外壳/测试在 loop 尚未绑定 ctx 时显式注入(已绑定时可省略)。
4. 事件 payload 字段以 EVENT-SCHEMA 模型为准(伪码已过时):system.error 载荷用
   {code, hint, ctx}(模型无 message 键,extra=forbid);system.cancelled 载荷用
   {what, reason}(模型必填 what,伪码的 run_id 键会 EVT-100 拒写);hint 文本取自
   PyHError.to_model_message()(errors.py 无 to_llm_text 旧名)。
5. BUSY 为命名状态字面量(ERR.md §2.11,未注册码),直接构造 PyHError("BUSY")——
   raise_code 会对未登记码改写为 CYC-999,语义不符。
6. 指纹采用"本轮新增 tool.result 事件序列",经 session.events_after(工具步前
   日志水位)派生——无第二份状态(INV-01);连续空指纹轮(全工具失败)计为同指纹,
   空转防烧钱语义一致。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Literal, Optional

from pyharness.config import load_settings
from pyharness.errors import PyHError, raise_code
from pyharness.events import Envelope

log = logging.getLogger("pyharness.agent_loop")

# 顶层三态 + running 受控子态(DIS §1.4:PAUSED/STOPPING 不改顶层三态语义)
LoopState = Literal["idle", "running", "paused", "stopping", "terminated"]

# RunResult.reason 终态枚举(与 close 枚举对齐,DIS §1.2)
_REASONS = ("complete", "max_turns", "budget", "cancelled", "stall",
            "timeout", "error", "close")


# ------------------------------------------------------------ 循环信号异常
class BudgetExhausted(Exception):
    """F032 预算硬闸信号:scope.check_budget() 超限抛出 → 终态 reason=budget。

    scope/llm 模块落地前本地定义(偏离说明 2);不得被 PyHError 域捕获逻辑吞掉,
    故不继承 PyHError,按伪码顺序在 except PyHError 之前捕获。
    """


class MaxTurnsExceeded(Exception):
    """轮数闸信号:防御性捕获 → 终态 reason=max_turns(主路径走 _must_stop 闸1)。

    正常情况下本模块不会抛出(轮数由闸前置判定);llm/工具层防绕行兜底用。
    """


# ------------------------------------------------------------ 关键数据结构
@dataclass
class RunContext:
    """单 run 运行态(伪码字段级;input_seq = 触发本 run 的 user.message seq)。

    last_fp 为实现字段(DIS 伪码 prev/last 双槽的等价:收敛比较只需"与上一轮
    是否相同"),可弃重建,不构成第二份消息状态。
    """

    run_id: str
    input_seq: int
    turn: int = 0                     # 已完成工具轮数(文本轮天然终态,不计数)
    reason: Optional[str] = None      # 终态原因(_finish 写入)
    stall_streak: int = 0             # 连续同指纹轮数(收敛闸)
    cancelled: bool = False           # 取消置位(声明式;闸2 下一轮触发)
    last_fp: Optional[tuple] = None   # 上一轮收敛指纹(实现字段)


@dataclass
class RunResult:
    """run 结果汇总(_finish 产出;不写 session.finished)。"""

    run_id: str
    reason: str
    turns: int
    last_seq: int                     # 收尾时日志当前最大 seq(派生事实)


# ------------------------------------------------------------ 主循环类
class AgentLoop:
    """Agent 主循环三态机驱动器(F007;阶段 1 最核心模块)。

    构造:cfg = Settings(读 loop.max_turns / loop.convergence_rounds /
    loop.input_queue_max);显式 kwargs 优先于 cfg 优先于 L1 默认
    (max_turns=30 / stall_limit=3 / queue_limit=10,权威默认见
    PARAMETER-ANCHOR 与 specs/agent_loop.py.md F007:默认 30,非 10)。
    headless = 外壳注入:run 结束后自动关会话(由 agent 层 close,本模块不写)。
    """

    def __init__(self, cfg: Any = None, *,
                 max_turns: Optional[int] = None,
                 stall_limit: Optional[int] = None,
                 queue_limit: Optional[int] = None,
                 headless: bool = False) -> None:
        if cfg is None:
            cfg = load_settings()          # L1 默认加载(键缺省必可加载)
        self.max_turns: int = max_turns if max_turns is not None \
            else int(cfg.loop.max_turns)               # 轮数上限(默认 30)
        self.stall_limit: int = stall_limit if stall_limit is not None \
            else int(cfg.loop.convergence_rounds)      # 收敛阈值(默认 3)
        self.queue_limit: int = queue_limit if queue_limit is not None \
            else int(cfg.loop.input_queue_max)         # 队深上限(默认 10)
        self.headless: bool = headless
        self.state: LoopState = "idle"
        self.pending: Deque[Envelope] = deque()        # running 期输入队列(FIFO)
        self.current: Optional[RunContext] = None      # 执行中 run;idle 为 None
        self.lock = asyncio.Lock()                     # 单 running 双保险之一
        self._child_tasks: set[asyncio.Task] = set()   # run 派生工具子任务登记
        self._ctx: Any = None                          # run 期 ctx 绑定(cancel/wake)
        self._last_reason: Optional[str] = None        # 最近一次终态原因(审计查询)

    # ======================================================== 主循环入口
    async def run(self, ctx: Any) -> Optional[RunResult]:
        """F007 主循环入口:wake 拉起;断言 idle + 拿锁防重入;队内排水监督器。

        每件 pending 输入 = 独立 RunContext(轮数/取消按单输入计),逐件交
        _run_engine 驱动;输入终态后按 FIFO 取下一件(状态表 running→running),
        直至队空或遇 cancelled/budget 边界终态;返回最后一件的 RunResult。
        异常分级收尾在 _run_engine;本层 finally 无条件回 idle、清 current/ctx
        (协作式取消路径的 CancelledError 不在此吞,沿任务传播由外层 close 收尾)。
        """
        async with self.lock:
            if self.state != "idle":                  # 锁前置:非 idle 拒入
                raise PyHError("BUSY", ctx={"advice": "loop 忙,输入已入队或被拒"})
            self._ctx = ctx                           # 1:1 绑定:本 run 期间 cancel
            self.state = "running"                    #    /队列满留痕有 ctx 可依
        try:
            last: Optional[RunResult] = None
            while True:
                env = self._pop_pending()             # None → 队空
                if env is None:
                    return last                       # 无输入可处理(空排水)
                run = RunContext(run_id=uuid.uuid4().hex, input_seq=env.seq)
                self.current = run
                try:
                    last = await self._run_engine(ctx, run)   # 伪码 run() 主体
                finally:
                    self.current = None               # 单输入收尾(与伪码逐件一致)
                # 边界终态(取消/预算)后不再自动取队内下一件:用户/资源边界,
                # 剩余输入保留在队(不静默丢),下次 wake 按 FIFO 继续消费。
                if last is not None and last.reason in ("cancelled", "budget"):
                    return last
        finally:
            self.current = None
            self.state = "idle"                       # 回 idle 等下一输入/队内下一件
            # 注:_ctx 不清零——loop 与会话 1:1,绑定终身有效(偏离说明 3)

    async def _run_engine(self, ctx: Any, run: RunContext) -> RunResult:
        """单输入 run 引擎 = specs/agent_loop.py.md run() 伪码主体。

        逐轮三闸前置判定 → scope.check_budget(F032)→ run_turn(单轮);轮内异常
        分级收尾:BudgetExhausted → budget、MaxTurnsExceeded → max_turns、
        PyHError(LLM-310 等)→ system.error 留痕 → error、未预期 → CYC-999 →
        error;CancelledError 不吞(置 cancelled 后 re-raise,外层收尾)。
        """
        try:
            while True:                               # 轮 = 一次 LLM 调用及其全部工具
                if (r := self._must_stop(ctx, run)) is not None:   # 三闸前置
                    return await self._finish(ctx, run, r)
                ctx.scope.check_budget()              # F032:超限抛 BudgetExhausted
                outcome = await self.run_turn(ctx, run)             # 单轮(含工具步)
                if outcome is not None:
                    return await self._finish(ctx, run, outcome)
        except asyncio.CancelledError:                # F025:不吞;外层(close/cancel)收尾
            run.cancelled = True
            raise
        except BudgetExhausted:                       # 预算硬闸:无"再给一次机会"
            return await self._finish(ctx, run, "budget")
        except MaxTurnsExceeded:                      # 轮数闸(防御路径)
            return await self._finish(ctx, run, "max_turns")
        except PyHError as e:                         # LLM-310 等结构化错误
            await ctx.session.append(
                "system.error",
                {"code": e.code, "hint": e.to_model_message(), "ctx": e.ctx},
                actor="system")
            return await self._finish(ctx, run, "error")
        except Exception as e:                        # 未预期兜底:CYC-999,堆栈仅本地
            log.exception("loop fatal run_id=%s", run.run_id)
            await ctx.session.append(
                "system.error",
                {"code": "CYC-999", "hint": f"{type(e).__name__}: {e}"[:2000]},
                actor="system")
            return await self._finish(ctx, run, "error")

    # ======================================================== 单轮驱动器
    async def run_turn(self, ctx: Any, run: RunContext) -> Optional[str]:
        """单轮:派生历史 → llm.chat → 纯文本直接终态 / tool_calls 逐工具步执行
        → 轮计数 +1 → 收敛指纹判定。返回 None = 继续下一轮;终态原因串即收尾。

        LLM 经注入 ctx.llm.chat 调用(INV-02 唯一合法入口);三档超时/退避/降级
        由 llm 内部消化;llm.request/usage 事件由 llm 层落日志,本函数不越权。
        """
        hist = ctx.session.derive_messages(ctx.scope.window_tokens)  # 日志现派生(INV-01)
        resp = await ctx.llm.chat(hist, tools=ctx.tools.schemas_for(ctx.scope),
                                  ctx=ctx)          # llm.chat 须 ctx(落 request/usage 事件)
        if not resp.tool_calls:                       # 纯文本 → 自然终态
            content = resp.content or ""
            if not content:                           # llm 双空响应:模型域未预期
                raise_code("LLM-399", hint="llm.chat 返回空 content 且无 tool_calls")
            await ctx.session.append(
                "agent.message", {"content": content}, actor="agent",
                trace={"parent_seq": resp.seq} if getattr(resp, "seq", None) else None)
            return "complete"                         # finished 由外壳 close(reason) 落
        # 工具轮:校验/guard/审批/Provider 全在 tools.execute 内;单步失败→事件化
        # 回喂(tool.error/guard.rejected),不中断本轮(见 run_step)
        start_seq = ctx.session.stats()["seq"]        # 工具步前日志水位(指纹基)
        for call in resp.tool_calls:
            await self.run_step(ctx, run, call)       # 逐工具步执行(顺序,阶段1)
        run.turn += 1
        fp = self._outcome_fingerprint(ctx, start_seq)  # 指纹=(name,summary,ok)序列
        run.stall_streak = run.stall_streak + 1 if fp == run.last_fp else 0
        run.last_fp = fp
        if run.stall_streak >= self.stall_limit:
            return "stall"                            # 连续 N 轮无新信息 → 空转封顶
        return None                                   # 下一轮

    # ======================================================== 单工具步
    async def run_step(self, ctx: Any, run: RunContext, call: Any) -> None:
        """单工具步:登记子任务执行 ctx.tools.execute(call, ctx)。

        call = llm 响应解析出的 ToolCall(name/raw_args/call_id);TLB-802/803/805
        等失败已由 tools.execute 捕获写 tool.error 回喂 LLM,不向主循环抛——本轮
        继续。协作式取消:子任务被 cancel() 取消时置 run.cancelled 并 re-raise,
        已开始工具如实留 partial(副作用不假装回滚)。
        """
        t = asyncio.create_task(ctx.tools.execute(call, ctx))
        self._child_tasks.add(t)                      # 登记:取消可传播(F025)
        try:
            await t                                   # 60s 工具超时在 tools 内部(F017)
        except asyncio.CancelledError:
            run.cancelled = True                      # 协作式取消:不吞,继续传播
            raise
        finally:
            self._child_tasks.discard(t)

    # ======================================================== 三闸只读判定
    def _must_stop(self, ctx: Any, run: RunContext) -> Optional[str]:
        """每轮起点三闸(轮数/取消/预算)只读判定;不写事件、不收尾——判定与执行
        分离保证终态无旁路(预算事件在 scope 内落)。
        """
        if run.turn >= self.max_turns:                # 闸1 轮数(默认 30)
            return "max_turns"
        if run.cancelled:                             # 闸2 取消(声明式置位,F025)
            return "cancelled"
        if ctx.scope.budget_state() in ("paused", "exhausted"):  # 闸3 预算(F032)
            return "budget"
        return None

    # ======================================================== 收敛指纹
    @staticmethod
    def _outcome_fingerprint(ctx: Any, start_seq: int) -> tuple:
        """本轮工具结果指纹 = (name, summary, ok) 元组序列(seq 序,派生事实)。

        只取本轮新增日志(events_after(start_seq) 排他下界)中的 tool.result;
        连续轮指纹相同 → 收敛闸计数。空指纹(整轮工具全败)与上轮空指纹相等,
        空转同样被封顶——防烧钱语义一致(偏离说明 6)。
        """
        return tuple(
            (e.payload["name"], e.payload["summary"], e.payload["ok"])
            for e in ctx.session.events_after(start_seq)
            if e.type == "tool.result")

    # ======================================================== 收尾(不写 finished)
    async def _finish(self, ctx: Any, run: RunContext, reason: str) -> RunResult:
        """汇总 RunResult(run_id/reason/turns/last_seq);不写 session.finished
        ——finished 唯一归属 agent.close(EVT-104);headless 由外壳在 run 返回后
        close(reason)。"""
        run.reason = reason
        self._last_reason = reason
        last_seq = ctx.session.stats()["seq"]         # 日志当前最大 seq(派生事实)
        log.info("run finished run_id=%s reason=%s turns=%d last_seq=%s",
                 run.run_id, reason, run.turn, last_seq)
        return RunResult(run_id=run.run_id, reason=reason,
                         turns=run.turn, last_seq=last_seq)

    # ======================================================== 取消传播(F025)
    async def cancel(self, reason: str = "cancelled") -> None:
        """声明式取消:置位 → 强同步留痕(system.cancelled)→ 取消子任务 →
        gather 等待收尾 → 清登记。不吞 CancelledError;append 失败不被日志故障
        阻塞(记日志继续)。reason ∈ {cancelled, timeout, close}。
        """
        run = self.current
        if run is None:                               # 无在途 run:无事可取消
            return
        ctx = self._ctx
        run.cancelled = True                          # 置位:闸2 下一轮即触发
        if ctx is not None:
            try:
                await ctx.session.append(
                    "system.cancelled",
                    {"what": f"run:{run.run_id}", "reason": reason},
                    actor="system", sync=True)        # 强同步:取消审计可重建
            except PyHError as e:                     # 强同步失败:PERS-202 等
                log.error("cancel log failed code=%s", e.code)  # 取消不被日志故障阻塞
        for t in self._child_tasks:                   # 传播:取消派生子任务
            if not t.done():
                t.cancel()
        await asyncio.gather(*self._child_tasks, return_exceptions=True)
        self._child_tasks.clear()                     # 已开始工具如实留 partial

    # ======================================================== 输入唤醒/入队
    async def wake(self, env: Envelope, ctx: Any = None) -> Optional[RunResult]:
        """外壳(agent.submit)在 user.message 强同步落盘后调用。

        idle → 入队并拉起排水 run(await 至队空/边界终态,返回 RunResult);
        running → 入队(FIFO),返回 None,当前 run 结束后自动取下一件;
        队满(>queue_limit)→ system.error[BUSY] 强同步 + 拒新(BUSY);
        stopping/terminated → BUSY 拒(会话正在关闭/已结束)。
        ctx:本 loop 尚未经 run() 绑定时显式注入(1:1 绑定后可不传,偏离说明 3)。
        """
        if self.state in ("stopping", "terminated"):
            raise PyHError("BUSY", ctx={"advice": "会话正在关闭/已结束,请新开会话"})
        if ctx is None:
            ctx = self._ctx                           # 已绑定(1:1)则复用
        if self.state == "running" and len(self.pending) >= self.queue_limit:
            # 队满:显式拒新,不静默丢(先事件留痕再抛)
            if ctx is not None:
                try:
                    await ctx.session.append(
                        "system.error",
                        {"code": "BUSY",
                         "hint": f"输入队列已满(>{self.queue_limit}),请稍后重试"},
                        actor="system", sync=True)
                except PyHError as e:                 # 留痕失败不阻断拒新
                    log.error("busy log failed code=%s", e.code)
            raise PyHError("BUSY", ctx={"advice": f"队列已满(>{self.queue_limit})"})
        self.pending.append(env)                      # 空闲或 running:统一入队保 FIFO
        if self.state == "idle":                      # 空闲:拉起排水 run
            if ctx is None:
                raise PyHError("CYC-999",
                               ctx={"hint": "wake 拉起 run 前 loop 未绑定 ctx"
                                            "(请先经 run(ctx) 或 wake(env, ctx))"})
            return await self.run(ctx)
        return None                                   # running:当前 run 结束自动取下一件

    def _pop_pending(self) -> Optional[Envelope]:
        """取队首输入(FIFO);队空返回 None。"""
        return self.pending.popleft() if self.pending else None

    # ======================================================== 只读状态查询
    def state_snapshot(self) -> dict:
        """外壳/审计只读句柄;禁止由此路径修改状态。"""
        return {
            "state": self.state,
            "current_run_id": self.current.run_id if self.current else None,
            "turn": self.current.turn if self.current else 0,
            "queue_len": len(self.pending),
            "pending_inputs": [
                {"seq": env.seq,
                 "content": (env.payload or {}).get("content", "")}
                for env in self.pending],
            "last_reason": self._last_reason,
        }


__all__ = [
    "AgentLoop", "RunContext", "RunResult",
    "BudgetExhausted", "MaxTurnsExceeded",
]
