"""pyharness/core/tools_executor.py — 工具执行管道总闸(四关强制)(specs/tools_executor.py.md 契约)

功能编号:F022(调用解析回填)· F026(先验后跑)· F014/F015(guard/审批编排)·
F017(超时)· F025(取消)· F039(结果 spill)联动。
权威口径:specs/tools_executor.py.md(编码契约)+ DIS-CORE §7.3.2(执行管道伪代码)
+ EVENT-SCHEMA §3.4(事件字段级);冲突以 PRD-Core 为准。本文件是 tools 脊柱三文件
之一,为统一 Consumer(DIS-SEAM §2.3):消费 tools_registry(契约/模型/Provider)与
tools_guard(链),一切工具调用必经 execute——无旁路(INV-04)。

四关强制(顺序不可换,DIS-CORE §7.3.2):
  关1 契约校验(lookup,TLB-802)+ 参数 pydantic 先验后跑(validate_args,TLB-803,
      失败 = Provider 零调用,INV-06)→ 落 tool.call(args+raw_args 双份存档)
  关2 scope 前置(GRD-401)→ guard 单调链 evaluate(F014;reject 终局零副作用,
      guard.rejected 强同步)→ 审批(F015,granted 重入链起点,GRD-403 语义)
  关3 Provider 执行(同步 handler 经**每次调用独立**单工线程池承载;总超时 =
      defn.timeout_s or 60s(TLB-805,TLB-805 码即默认超时参数锚);wait_for 掐断;
      超时/取消后该线程池被驱逐不复用(线程后台跑完,见偏离 13);
      CancelledError 不吞——已发生副作用如实写 partial 后 re-raise(F025))
  关4 结果检查 finalize(输出 schema 校验 → 超长转 spill(F039)→ ≤2KB 摘要 →
      tool.result;每调用恰一条 result 或 error,trace.parent_seq 关联父响应)

安全铁律:TLB-803 参数失败=真实函数零调用(INV-06);guard reject=零副作用
(INV-05);审批 denied/timeout=不执行;GRD-402 防重放(已裁决拒绝的 call_id 再执行
尝试 → 拒绝上抛);错误全走 raise_code;失败写 tool.error 回喂 LLM 不中断循环。

偏离说明(契约=spec;以下为与既有实现/落地事件模型的冲突取舍,均列理由):
1. tool.call 事件 trace 键用 parent_seq(agent_loop.py 同款 repo 惯例,EVENT-SCHEMA
   §2.4 建议形态 {"parent_seq": int});spec 伪码的 trace={"parent": …} 键名弃用。
   actor 恒 "tool"(spec 伪码;EVENT-SCHEMA §3.4.1 表注 agent 为 F022 语义归属,
   以本模块伪码为准)。
2. TLB-802/803(关1)失败在 execute() 内部即转 tool.error + ExecResult——spec 伪码
   注释称"executor 上层转"但 agent-loop 单步路径(await ctx.tools.execute(call, ctx))
   无批量上层包装,不转换则畸形参数会以 system.error 而非 tool.error 回喂,违反
   职责 2 回喂语义;批量入口 execute_tool_calls 亦依赖 r.summary 前缀计数,故关1
   失败收敛在 execute 内(与状态机"parsed→errored 回喂"一致,事件序语义不变)。
   参数失败 summary 前缀 "参数校验失败"(F026 连败计数锚),guard 拒绝前缀 "guard"。
3. 审批 granted 后重入链的裁决语义细化:伪码 `if d != "allow" → GRD-403` 字面直译
   将使 danger=high 工具(链上 g-danger 恒判 approval)与覆写转审批工具(g-overwrite
   目标仍存在恒判 approval)在人类批准后永远无法执行——F015 核心用途失效。落地为:
   重入 decision=reject(新拒/策略收紧)→ GRD-403(记录 rejected id,批准作废);
   decision=approval(链仍要求人类,而本次调用同参刚获 granted)→ 经绑定指纹校验
   (本轮参数指纹 == approval 提供器为该 call_id 记录的 binding)一致才放行,不一致
   = 批准针对异参数(篡改/复用)→ GRD-403 拒;decision=allow → 正常执行。GRD-403
   语义只落在"新拒"(errors.py 登记:批准后被新拒),approval 决策不是拒绝、不存在
   被"翻回"问题(单调性指拒绝不可被批准覆盖)。
4. tool.error 事件与 payload 模型对齐(extra=forbid 实测超字段 append → EVT-100):
   payload 仅 {name, call_id, code, message}(无 reason/明细字段);解析失败
   (call=None,尚无 ToolCall)时 name/call_id 取 raw 中的尝试名/补生成 id,保证
   事件结构合法可审计(工具名非法/缺失时 name 回落 "?")。tool.result 的 spill_ref
   为完整 spill 引用 dict {ref, chars, lines, preview}(ToolResultPayload 模型锁定),
   ExecResult.spill_ref 同型;spec 伪码以 sp["ref"] 字符串作 spill_ref 弃用。
5. 终局拒的 summary 统一 "guard 拒绝:…" 前缀(DIS-CORE §7.3.2 的 "scope 拒绝"
   弃用):F026 连败计数与审计 grep "guard 拒绝" 同口径,scope 前置拒绝本就属
   GRD-401 守卫族。
   **该路径的唯一所有者是 ``GuardChain._evaluate_full``**(S3-2-2 上提)。executor
   侧原有的 ``_reject()`` 实现已于 2026-09-20 **删除**(GAP-12):它零调用点,且
   保留会让 ``guard.rejected`` 出现第二个发射点,违反 INV-05 的唯一出口要求。
6. 取消(F025)partial 事件以 ok=False + truncated=True + summary 标注 partial
   表达(ExecResult/ToolResultPayload 均无 partial 字段);EVENT-SCHEMA §3.4.6 注的
   "ok=True,truncated=True" 为另一口径,以 executor spec 职责 4(ok=False,
   partial=true)为准。仅 Provider 已启动(invoke 已进入)才写 partial;取消落在
   执行前则直接 re-raise(无副作用即无事可记)。
7. Provider 调用面:spec 伪码 `provider.handle(args, ctx)` 为同步函数由线程池
   承载;落地兼容两形态——同步 handle/裸 callable 经**每次调用独立**的
   ThreadPoolExecutor(单工),async handle 直接协程 wait_for(异步 handler 不应
   占用线程池)。两者同样受 timeout 掐断与取消语义。Provider 缺 handle 且不可
   调用 → TLB-802(契约在但实现损坏,拒绝执行)。
8. 输出契约编译复用 tools_registry 的 schema→pydantic 编译器(私有 _compile_model
   同包借用,单向依赖不变;避免复制第二套编译器造成双源漂移)。_finalize 对
   compile/校验失败(ValidationError/ValueError)统一转 tool.error(TLB-803,
   "输出校验失败:");spec 伪码仅覆盖 ValidationError,ValueError(坏输出契约)一并收。
9. F026 轮内连败以 execute_tool_calls 内 break 表达(spec 伪代码权威),结构表所述
   "抛 TurnToolFailures" 的异常类不落地——批量 break 不打断 agent-loop 单步执行,
   语义等价(≥2 终止本轮不再尝试)。
10. spill 阈值 SPILL_THRESHOLD = 2048 字符(DIS-SEAM §6.1 G1:5KB>2KB → spill;
    PARAMETER-ANCHOR 的 64KB 行指 F034 读入截断/事件 payload 安全线 N3,非 executor
    摘要阈值);summary 上限 2000 字符(ToolResultPayload max_length=2048 留余量)。
11. GRD-402 用 raise_code 上抛(spec 伪码直接构造 PyHError;码已登记,统一抛出入口
    纪律);guard 链 reject 与审批重入新拒同样记入 _rejected(职责 6 广义防重放)。
12. 超时/异常消息用动态实际超时值(spec 伪码字面 "60s" 在 timeout_s 覆盖时失真),
   错误文本经 e.to_model_message()(errors.py 落地名;spec 伪码 to_llm_text 为
   ERR.md 文档名,同一函数两处命名,以实现为准)。
13. 超时"杀"同步 Provider 线程的落地语义:Python 无法安全终止运行中线程,故不做
   真杀,改为**驱逐不复用**——同步 Provider 经每次调用独立单工线程池执行,超时/取消
   掐断后线程在后台把副作用跑完(已发生副作用仍如实 error/partial 标注,不假装回滚),
   但该线程池 `shutdown(wait=False)` 且不入共享池,绝不被复用于后续调用。此举消除
   "超时线程回共享池 → mark_idle 后新调用复用到同一物理线程 → 重叠副作用"的竞态;
   驱逐池保留引用(防 GC 半途回收线程),超过 _ZOMBIE_POOL_LIMIT 时裁剪已空闲池。

依赖方向(单向,INV-08):本文件 → tools_registry(lookup/validate_args/lookup_provider/
register_tool/schemas_for)、tools_guard(ToolCall/Decision)、errors(raise_code)、
events.vocab(summarize_validation);不 import 任何 Provider 内部符号、不 import
approval/agent/llm(审批/消费方经 ctx 注入)。本文件被 tools_guard/tools_registry
反向 import = 环,禁止。
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from typing import Any, Optional

from pydantic import ValidationError

from pyharness.core.tools_guard import Decision, ToolCall
from pyharness.core.tools_registry import (NAME_RE, ToolRegistry,
                                           _compile_model as _registry_compile_model)
from pyharness.errors import PyHError, raise_code
from pyharness.events.vocab import summarize_validation


# ====================================================================== 常量
# F026 轮内同工具连败终止阈值(CFG `loop.max_arg_failures_per_round` 默认 2;装配层注入,
# 未注入时用本常量——保持"未装配=默认"语义,与 flush_interval_s 同款)

# 参数摘要化优先展示键(审批展示用:先动作后其余,按风险/动作语义排序)

# 动作/内容承载键:审批展示时头尾双展示(防恶意尾部被截断隐藏——人类只看到
# >80 字命令的开头就批准,危险命令段若在尾部则被省略号吞掉)


# ================================================================ ExecResult


# ========================================================= ApprovalRoundResult


# ================================================================ 文本辅助
from pyharness.core.result_text import render_result_text


# ================================================================ ToolExecutor


from pyharness.core.execution_result import (log, DEFAULT_TOOL_TIMEOUT, SPILL_THRESHOLD, SUMMARY_MAX_CHARS, DEFAULT_MAX_ARG_FAILURES, PARSE_FAIL_NAME, _ZOMBIE_POOL_LIMIT, _SUMMARY_PRIORITY, _SUMMARY_TAIL_KEYS, _TAIL_KEEP, ExecResult, ApprovalRoundResult, summarize_text, _short_value, summarize, _approval_binding)

from pyharness.core.tool_invocation import ToolInvocation
from pyharness.core.tool_approval import ToolApproval

async def _authorize_governance(ctx, call, **kwargs):
    """Single governance funnel, including approval re-entry."""
    return await ctx.governance.authorize(call, ctx, **kwargs)


class ToolExecutor(ToolInvocation, ToolApproval):
    """工具执行管道总闸(统一 Consumer;DIS-SEAM §2.3)。

    构造注入 registry(契约/模型/Provider 索引;可 None = 自建空注册表)。
    scope/guard/approval/session/storage 一律经 execute 的 ctx 参数注入
    (ctx.session/ctx.scope/ctx.guard/ctx.approval/ctx.storage.spill)——与
    scope/guard/approval 模块"注入式装配"先例一致,装配层接线而非本模块持依赖。

    实例状态(均会话内单调/轮内可重置,单事件循环无需锁):
      _rejected  set[call_id]  已裁决(拒绝)调用(GRD-402 防重放,单调只增)
      _fail      dict[name,int] 轮内同工具连败计数(每轮起点 reset_turn_failures)
      _out_models dict[id(defn), model] 输出契约编译缓存(Definition 不可变,
                  对象身份即版本;注销重注册 = 新对象 → 新编译,不串味)
    registry 的 mark_running/mark_idle(注销 BUSY 闸)由关3 调用,执行器不另持
    在途镜像(registry._running 是唯一真源,spec 结构表 _Running 由此兑现)。
    """

    def __init__(self, registry: Optional[ToolRegistry] = None, *,
                 max_arg_failures: int = DEFAULT_MAX_ARG_FAILURES) -> None:
        self._r: ToolRegistry = registry if registry is not None else ToolRegistry()
        self._rejected: set[str] = set()        # GRD-402 防重放(拒绝裁决记忆)
        self._fail: dict[str, int] = {}         # F026 轮内连败计数
        # F026 终止阈值:装配层从 cfg.loop.max_arg_failures_per_round 注入
        # (2026-09-21 修:此前恒硬编码 2,该配置键声明可调却零读取者)
        self._max_arg_failures: int = max(1, int(max_arg_failures))
        self._out_models: dict[int, Any] = {}   # 输出契约编译缓存(id(defn) 键)
        self._active: Optional[asyncio.Future] = None   # cancel_current 在途句柄
        # 超时/取消后被驱逐的同步 Provider 线程池(线程仍在后台跑完副作用);
        # 保留引用防 GC 半途回收线程,超过上限时裁剪已空闲池(见 _prune_zombies)
        self._zombie_pools: set[ThreadPoolExecutor] = set()

    # ------------------------------------------------------------ 门面透传
    def register(self, defn: Any, *, provider: Any = None) -> str:
        """门面对 registry 的透传(能力 announce 第③步;spec register 签名)。"""
        return self._r.register_tool(defn, provider=provider)

    def register_definition(self, defn: Any) -> str:
        """agent.announce 消费别名(= register_tool;与 agent.py 装配面兼容)。"""
        return self._r.register_tool(defn)

    def unregister_definition(self, name: str) -> None:
        """agent.detach_capability 消费别名(= unregister,在途 BUSY 闸同语义)。"""
        self._r.unregister(name)

    def unregister(self, name: str) -> None:
        """注销透传(卸插件/能力 detach;运行中不可卸 → BUSY)。"""
        self._r.unregister(name)

    def schemas_for(self, scope: Any = None, *,
                    toolset: Optional[set[str]] = None) -> list[dict]:
        """门面对 registry.schemas_for 透传(agent-loop 每轮下发 F007)。"""
        schemas=self._r.schemas_for(scope, toolset=toolset)
        hidden=getattr(self,'hidden_tools',set())
        return [schema for schema in schemas if schema['function']['name'] not in hidden]

    # ------------------------------------------------------ F026 轮内连败计数
    def reset_turn_failures(self) -> None:
        """每轮起点清零(spec: 轮内连败计数每轮重置)。"""
        self._fail.clear()

    def mark_turn_failure(self, name: str) -> int:
        """同工具失败一次并返回累计次数(达阈值由批量入口终止该轮,F026)。"""
        key = str(name)
        n = self._fail.get(key, 0) + 1
        self._fail[key] = n
        return n

    def _check_fail_streak(self, name: str) -> bool:
        """轮内同工具连败达阈值判定(F026;调用方终止该轮)。

        阈值来自装配层注入(CFG `loop.max_arg_failures_per_round`,缺省 2)。
        """
        return self._fail.get(str(name), 0) >= self._max_arg_failures

    def rejected_ids(self) -> set[str]:
        """已裁决(拒绝)call_id 集(F031 自检/审计;GRD-402 防重放)。"""
        return set(self._rejected)

    # ------------------------------------------------------------ 解析入口
    def parse_tool_call(self, raw: Any, parent_seq: Optional[int] = None) -> ToolCall:
        """LLM 原始 tool_calls 单条 → ToolCall(spec 伪代码权威)。

        缺 id 补生成(uuid4 前 8 位,审计仍可关联);name 非法/缺失 → TLB-802
        (幻觉名入口拦截,NAME_RE 锚定);arguments 非 JSON/非对象 → TLB-803
        (解析失败不猜测、不透传执行)。兼容 OpenAI 嵌套(function.name)与
        llm.response 事件扁平(id/name/arguments)两种 wire 形态。
        """
        if not isinstance(raw, dict):
            raise_code("TLB-803", tool=PARSE_FAIL_NAME,
                       detail=f"tool_calls 元素须为对象,实为 {type(raw).__name__}",
                       advice="按响应结构修正重发;零执行(INV-06)")
        tid = raw.get("id") or uuid.uuid4().hex[:8]
        fn = raw.get("function") or {}
        if not isinstance(fn, dict):
            fn = {}
        name = fn.get("name") or raw.get("name")
        if not isinstance(name, str) or not name or not NAME_RE.fullmatch(name):
            raise_code("TLB-802", tool=name, rule=NAME_RE.pattern,
                       advice="工具名非法/缺失(幻觉名入口拦截);查 schemas_for 名单")
        args_raw = fn.get("arguments") if fn.get("arguments") is not None \
            else raw.get("arguments")
        try:
            args = json.loads(args_raw if isinstance(args_raw, str) and args_raw.strip()
                              else args_raw or "{}")
        except (json.JSONDecodeError, TypeError) as e:
            raise_code("TLB-803", tool=name,
                       detail=f"arguments 非法 JSON:{e}",
                       advice="按 JSON 格式修正;未执行任何操作")
        if not isinstance(args, dict):
            raise_code("TLB-803", tool=name,
                       detail=f"arguments 须为 JSON 对象,实为 {type(args).__name__}",
                       advice="按参数对象格式修正;未执行任何操作")
        return ToolCall(name=name, raw_args=args, call_id=tid, parent_seq=parent_seq)

    @staticmethod
    def _attempted_name(raw: Any) -> str:
        """raw 中尝试的工具名(解析失败计数/审计用;缺失 → 占位 "?")。"""
        if isinstance(raw, dict):
            fn = raw.get("function")
            if isinstance(fn, dict) and isinstance(fn.get("name"), str) \
                    and fn["name"]:
                return fn["name"]
            if isinstance(raw.get("name"), str) and raw["name"]:
                return raw["name"]
        return PARSE_FAIL_NAME

    def _parse_fail_message(self, e: PyHError, name: str) -> str:
        """解析失败 → tool.error message(带上尝试名便于 LLM 自查)。"""
        tip = e.ctx.get("detail") or e.ctx.get("hint") or ""
        base = f"tool_calls 解析失败(tool={name or '?'})"
        return f"{base}:{tip}" if tip else base

    # -------------------------------------------------------- F022 批量串行入口
    async def execute_tool_calls(self, calls: list, ctx: Any, *,
                                 parent_seq: Optional[int] = None) -> list[ExecResult]:
        """每轮 LLM 返回的多个 tool_calls 逐条解析执行(spec 伪代码权威)。

        默认串行(保序可审计);解析失败/参数校验失败/guard 拒绝按工具名计连败,
        ≥2 → 终止本轮后续调用(F026);单条失败各自落 tool.error/guard 事件回喂,
        不抛到循环(本函数只返回结果列表)。
        """
        self.reset_turn_failures()
        results: list[ExecResult] = []
        for raw in calls:
            try:
                call = self.parse_tool_call(raw, parent_seq)
            except PyHError as e:                       # 解析失败:连败计数
                name = self._attempted_name(raw)
                self.mark_turn_failure(name)
                tid = raw.get("id") if isinstance(raw, dict) else None
                results.append(await self._on_error(
                    ctx, None, e.code, self._parse_fail_message(e, name),
                    name=name, call_id=tid or uuid.uuid4().hex[:8],
                    parent_seq=parent_seq))
                if self._check_fail_streak(name):
                    break                               # 连败 2 次终止该轮(F026)
                continue
            r = await self.execute(call, ctx)           # 全管道纪律在 execute 内
            results.append(r)
            key = call.name
            if not r.ok and r.summary.startswith(("参数校验", "guard")):
                self.mark_turn_failure(key)
                if self._check_fail_streak(key):
                    break                               # TLB-803/GRD 连败 2 → 终止轮
        return results

    # ------------------------------------------------------------ 事件出口
    @staticmethod
    def _trace(call: Any) -> Optional[dict]:
        """trace 语义父关联:parent_seq(父 llm.response;EVENT-SCHEMA §2.4)。"""
        seq = getattr(call, "parent_seq", None)
        return {"parent_seq": seq} if seq is not None else None

    @staticmethod
    async def _append(ctx: Any, type_: str, payload: dict, *, actor: str,
                      sync: bool = False,
                      trace: Optional[dict] = None) -> Any:
        """session.append 统一封装(兼容异步 SessionLog/同步替身;guard 同款)。"""
        sess = ctx.session
        r = sess.append(type_, payload, actor=actor, sync=sync, trace=trace)
        if inspect.isawaitable(r):
            return await r
        return r

    # ------------------------------------------------------------ 执行管道主函数
    async def execute(self, call: Any, ctx: Any) -> ExecResult:
        """四关强制编排(spec 伪代码权威;详见模块 docstring 四关表)。

        任何失败收敛为 tool.error 回喂(ExecResult.ok=False),成功恰一条
        tool.result;guard/审批拒绝走各自事件不留 result(拒绝零副作用,审计以
        guard.rejected/approval.* 为证)。
        """
        call = self._coerce_call(call)                  # 鸭子 → guard.ToolCall
        if call.call_id in self._rejected:              # GRD-402:已裁决禁重放
            raise_code("GRD-402", call_id=call.call_id,
                       advice="该调用已裁决(拒绝),禁止重复执行")
        self._require_wiring(ctx)                       # session/scope/guard 必接线

        # ---- 关1a 契约(TLB-802 → tool.error 回喂,零执行)
        try:
            defn = self._r.lookup(call.name)
        except PyHError as e:
            return await self._on_error(ctx, call, e.code, e.to_model_message())
        call = replace(call, defn=defn)                 # 挂契约(guard 读 danger 面)
        runtime = getattr(ctx, 'platform_runtime', None)
        validate_path = getattr(runtime, 'validate_file_call', None)
        if callable(validate_path):
            error = validate_path(call.name, call.raw_args, ctx)
            if error:
                result = await self._on_error(ctx, call, 'TLB-803', json.dumps(error, ensure_ascii=False))
                if not error['recoverable']:
                    raise PyHError('platform_workspace_path_denied')
                return result
        # ---- 关1b 先验后跑(TLB-803 → 参数校验失败 summary;本函数立即返回,
        # 后续任何一行不执行 —— 畸形参数零调用,INV-06)
        try:
            args = self._r.validate_args(call.name, call.raw_args)
        except PyHError as e:
            detail = e.ctx.get("detail") or e.ctx.get("advice") or e.message
            return await self._on_error(
                ctx, call, e.code,
                f"参数校验失败(TLB-803):{detail}"
                if e.code == "TLB-803" else e.to_model_message())
        call = replace(call, args=args)                 # guard/审批读校验后实参

        # ---- tool.call 双份存档(raw_args 模型原话 vs args 实际执行;INV-06)
        await self._append(ctx, "tool.call",
                           {"name": call.name, "args": dict(args),
                            "raw_args": dict(call.raw_args or {}),
                            "call_id": call.call_id},
                           actor="tool", trace=self._trace(call))
        # ---- 关2 治理授权(S3-2-2:唯一治理入口):scope 前置 + guard 单调链 +
        # Decision + decision.issued。scope 前置的运行时**唯一所有者**是
        # GuardChain._evaluate_full(职责上提,非重复计算);executor 不再自算
        # scope.can_use,也不再直呼 ctx.guard.evaluate。
        # S4-P1-1:授权↔执行绑定摘要(既有 `_approval_binding` 语义,**复用不重写**)
        # 注入为 `Decision.inputs_digest`。治理层不得 import 本模块(ADR-018:308),
        # 故由执行侧计算后经 `authorize(inputs_digest=...)` 传入——与 `chain_factory`
        # 注入先例同型(装配/执行侧适配,治理层只持有)。全流程只算一次。
        digest = _approval_binding(call, args)
        d = await _authorize_governance(ctx, call, inputs_digest=digest)
        if d == "reject":                               # 终局拒(含 scope-hidden)
            self._rejected.add(call.call_id)            # 广义防重放(偏离 11)
            return ExecResult(ok=False, summary="guard 拒绝,未执行")
        # ---- 关2.5 审批(F015;danger≥high 由链出 approval 决策)
        if d == "approval":
            round_ = await self._approval_round(ctx, call, args, prior=d,
                                                binding=digest)
            if not round_.executed:
                return ExecResult(ok=False, summary=round_.message)  # 非放行 → 不执行
        # ---- 关3 Provider 执行(线程池 + 超时掐断 TLB-805)
        timeout = int(defn.timeout_s) or DEFAULT_TOOL_TIMEOUT
        started = False
        elapsed_ms = 0.0
        try:
            self._r.mark_running(call.name)             # registry 注销闸联动
            t0 = time.perf_counter()
            provider_task = asyncio.create_task(self._run_provider(defn,args,ctx))
            fut = asyncio.create_task(asyncio.wait_for(provider_task, timeout=timeout))
            self._active = fut
            started = True                          # Provider 已进入 await 点
            try:
                raw = await fut
            finally:
                # Cancellation may arrive before wait_for first runs. The owned
                # provider Task must still be cancelled and awaited in that window.
                if not provider_task.done():
                    provider_task.cancel()
                    await asyncio.gather(provider_task,return_exceptions=True)
                if self._active is fut:
                    self._active = None
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
        except asyncio.TimeoutError:                    # 掐断;进程类杀树归
            return await self._on_error(                # Provider(TO-301),见 docstring
                ctx, call, "TLB-805", f"执行超时({timeout}s):已停止等待;"
                "工具可能仍在运行,已发生副作用未撤销")
        except asyncio.CancelledError:                  # F025:取消不吞
            if started:                                 # 已发生副作用如实写 partial
                await self._append(ctx, "tool.result",
                                   {"name": call.name, "call_id": call.call_id,
                                    "ok": False, "truncated": True,
                                    "summary": "已取消等待:工具可能仍在运行,"
                                               "已发生副作用未撤销(partial)"},
                                   actor="tool", trace=self._trace(call))
            raise                                       # 按取消协议 re-raise
        except PyHError as e:                           # Provider 结构化错误
            return await self._on_error(ctx, call, e.code, e.to_model_message())
        except Exception as e:                          # noqa: BLE001 Provider 运行异常
            # 回喂 LLM 的 tool.error 不含异常原文(str(e) 可能带内部路径/实现细节,
            # P2 收紧):只报类型名判断失败性质;完整堆栈留本地日志,审计以日志为准。
            log.exception("tool provider failed name=%s call_id=%s",
                          call.name, call.call_id)
            return await self._on_error(
                ctx, call, "TLB-805", f"Provider 运行异常:{type(e).__name__}")
        finally:
            self._r.mark_idle(call.name)
        # ---- 关4 结果检查 + finalize(输出 schema/spill/摘要 → tool.result)
        return await self._finalize(ctx, call, defn, raw, elapsed_ms=elapsed_ms)

    # ------------------------------------------------------------ 内部编排件
    def _coerce_call(self, call: Any) -> ToolCall:
        """鸭子 ToolCall(llm.ToolCall/SimpleNamespace)→ guard.ToolCall 规范化。

        call_id 缺省(uuid4 前 8 位)与解析层补 id 同语义——事件配对/审计仍可关联;
        名字缺失/非 str 不在此拦(registry lookup 会 TLB-802,事件 name 回落 "?")。
        """
        if isinstance(call, ToolCall):
            return call
        name = getattr(call, "name", None)
        raw_args = getattr(call, "raw_args", None)
        cid = getattr(call, "call_id", None) or getattr(call, "id", None)
        return ToolCall(
            name=name if isinstance(name, str) else str(name) if name else "",
            raw_args=dict(raw_args) if isinstance(raw_args, dict) else {},
            call_id=str(cid) if cid else uuid.uuid4().hex[:8],
            parent_seq=getattr(call, "parent_seq", None),
            defn=getattr(call, "defn", None),
            args=getattr(call, "args", None))

    def _require_wiring(self, ctx: Any) -> None:
        """执行前置装配检查(fail-closed):无 session/scope/guard = 非法执行路径。

        缺任一 → CYC-999 上抛(装配期 bug 证据),绝不带缺件执行(INV-04:无
        guard.evaluated 的执行非法;事件留痕是审计硬前提)。
        """
        if getattr(ctx, "session", None) is None:
            raise_code("CYC-999", module="tools_executor",
                       hint="ctx.session 未接线:事件落点缺失,拒绝执行(INV-04)")
        if getattr(ctx, "scope", None) is None:
            raise_code("CYC-999", module="tools_executor",
                       hint="ctx.scope 未接线:scope 前置/guard 求值不可执行")
        if getattr(ctx, "guard", None) is None:
            raise_code("CYC-999", module="tools_executor",
                       hint="ctx.guard 未接线:无 guard.evaluated 的执行非法"
                            "(F031 自检兜底)")
        if getattr(ctx, "governance", None) is None:
            raise_code("CYC-999", module="tools_executor",
                       hint="ctx.governance 未接线:关2 唯一治理入口缺失,"
                            "拒绝执行(S3-2-2)")


    # ------------------------------------------------------------ 已删除:_reject
    # GAP-12(2026-09-20):原 ``_reject()`` 已删除。取证口径:
    #   ① 全库**零调用点**(仅有自身定义与三处说明性引用);
    #   ② 它的语义(scope-hidden 终局拒 → guard.evaluated + guard.rejected +
    #      记 _rejected)自 S3-2-2 起**已整体上移**到
    #      ``GuardChain._evaluate_full``,由关 2 的唯一治理入口
    #      ``authorize()`` 触发;
    #   ③ 保留它的**风险**大于价值:它使 ``guard.rejected`` 存在**第二个发射点**,
    #      而 INV-05 要求拒绝留痕只有一个出口;一旦有人"顺手接线",就会造出
    #      绕过 ``decision.issued`` 的第二条拒绝路径(与 P2/P3 冲突)。
    # 删除后 ``guard.rejected`` 的生产发射点**唯一** = ``tools_guard._append_rejected``,
    # 由 ``tests/invariants/test_inv_context_and_paths.py`` 中的结构断言钉死。

    async def _on_error(self, ctx: Any, call: Any, code: str, message: str, *,
                        name: Optional[str] = None, call_id: Optional[str] = None,
                        parent_seq: Optional[int] = None) -> ExecResult:
        """统一错误出口:append tool.error(code/message ≤2000)并回 ExecResult。

        code 原样透传(ERR §5.2);call=None(解析失败尚无 ToolCall)时 name/
        call_id 取实参或回落占位(偏离 4),保证事件结构合法可审计。失败即
        单条 tool.error——与 tool.result 互斥,每调用恰一条终局事件。
        """
        ename = name if name is not None else (getattr(call, "name", None)
                                               if call is not None else None)
        ecid = call_id if call_id is not None else (getattr(call, "call_id", None)
                                                    if call is not None else None)
        if not isinstance(ename, str) or not ename:
            ename = PARSE_FAIL_NAME
        if not isinstance(ecid, str) or not ecid:
            ecid = uuid.uuid4().hex[:8]
        msg = str(message or code or "工具执行失败")[:2000]
        seq = parent_seq if parent_seq is not None else (
            getattr(call, "parent_seq", None) if call is not None else None)
        trace = {"parent_seq": seq} if seq is not None else None
        await self._append(ctx, "tool.error",
                           {"name": ename, "call_id": ecid,
                            "code": code or "CYC-999", "message": msg},
                           actor="tool", trace=trace)
        return ExecResult(ok=False, summary=msg)

    def _provider_handle(self, name: str) -> Any:
        """解析 Provider 可调用面:provider.handle(args, ctx) 或裸 callable。"""
        provider = self._r.lookup_provider(name)        # 未绑定 → TLB-802
        handle = getattr(provider, "handle", None)
        if callable(handle):
            return handle
        if callable(provider):                          # 兼容裸函数 Provider
            return provider
        raise_code("TLB-802", tool=name,
                   advice="Provider 缺 handle(args, ctx) 且不可调用:"
                          "能力实现损坏或未激活")

    async def _authorize_governance(self, ctx, call, **kwargs):
        return await _authorize_governance(ctx, call, **kwargs)

    def _new_provider_pool(self, name):
        return ThreadPoolExecutor(max_workers=1,
            thread_name_prefix=f"tool:{str(name)[:16]}")


    # ------------------------------------------------------------ Provider 调用


    # ------------------------------------------------------------ 取消(F025)

    # ------------------------------------------------------------ 关4 finalize


__all__ = [
    "ToolExecutor", "ExecResult", "ToolCall", "Decision",
    "DEFAULT_TOOL_TIMEOUT", "SPILL_THRESHOLD", "SUMMARY_MAX_CHARS",
    "render_result_text", "summarize_text", "summarize",
]
