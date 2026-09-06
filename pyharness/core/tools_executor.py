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
  关3 Provider 执行(同步 handler 经 asyncio.to_thread 跑线程池;总超时 =
      defn.timeout_s or 60s(TLB-805,TLB-805 码即默认超时参数锚);wait_for 掐断;
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
   decision=approval(链仍要求人类,而本次调用同参刚获 granted)→ 视为已授权放行;
   decision=allow → 正常执行。GRD-403 语义只落在"新拒"(errors.py 登记:批准后被
   新拒),approval 决策不是拒绝、不存在被"翻回"问题(单调性指拒绝不可被批准覆盖)。
4. tool.error 事件与 payload 模型对齐(extra=forbid 实测超字段 append → EVT-100):
   payload 仅 {name, call_id, code, message}(无 reason/明细字段);解析失败
   (call=None,尚无 ToolCall)时 name/call_id 取 raw 中的尝试名/补生成 id,保证
   事件结构合法可审计(工具名非法/缺失时 name 回落 "?")。tool.result 的 spill_ref
   为完整 spill 引用 dict {ref, chars, lines, preview}(ToolResultPayload 模型锁定),
   ExecResult.spill_ref 同型;spec 伪码以 sp["ref"] 字符串作 spill_ref 弃用。
5. _reject(scope-hidden 等 executor 侧终局拒)summary 统一 "guard 拒绝:…" 前缀
   (DIS-CORE §7.3.2 的 "scope 拒绝" 弃用):F026 连败计数与审计 grep "guard 拒绝"
   同口径,scope 前置拒绝本就属 GRD-401 守卫族。
6. 取消(F025)partial 事件以 ok=False + truncated=True + summary 标注 partial
   表达(ExecResult/ToolResultPayload 均无 partial 字段);EVENT-SCHEMA §3.4.6 注的
   "ok=True,truncated=True" 为另一口径,以 executor spec 职责 4(ok=False,
   partial=true)为准。仅 Provider 已启动(invoke 已进入)才写 partial;取消落在
   执行前则直接 re-raise(无副作用即无事可记)。
7. Provider 调用面:spec 伪码 `provider.handle(args, ctx)` 为同步函数由 to_thread
   承载;落地兼容两形态——同步 handle/裸 callable 经 asyncio.to_thread(线程池),
   async handle 直接协程 wait_for(异步 handler 不应占用线程池)。两者同样受
   timeout 掐断与取消语义。Provider 缺 handle 且不可调用 → TLB-802(契约在但实现
   损坏,拒绝执行)。
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
    纪律);guard 链 reject 与审批重入新拒同样记入 _rejected(职责 6 广义防重放,
    spec 伪码仅 scope-hidden/_reject 路径落记)。
12. 超时/异常消息用动态实际超时值(spec 伪码字面 "60s" 在 timeout_s 覆盖时失真),
   错误文本经 e.to_model_message()(errors.py 落地名;spec 伪码 to_llm_text 为
   ERR.md 文档名,同一函数两处命名,以实现为准)。

依赖方向(单向,INV-08):本文件 → tools_registry(lookup/validate_args/lookup_provider/
register_tool/schemas_for)、tools_guard(ToolCall/Decision)、errors(raise_code)、
events.vocab(summarize_validation);不 import 任何 Provider 内部符号、不 import
approval/agent/llm(审批/消费方经 ctx 注入)。本文件被 tools_guard/tools_registry
反向 import = 环,禁止。
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
import uuid
from dataclasses import dataclass, replace
from typing import Any, Optional

from pydantic import ValidationError

from pyharness.core.tools_guard import Decision, ToolCall
from pyharness.core.tools_registry import (NAME_RE, ToolRegistry,
                                           _compile_model as _registry_compile_model)
from pyharness.errors import PyHError, raise_code
from pyharness.events.vocab import summarize_validation

log = logging.getLogger("pyharness.tools_executor")

# ====================================================================== 常量
DEFAULT_TOOL_TIMEOUT: int = 60          # F017 工具默认超时(PARAMETER-ANCHOR 锁定)
SPILL_THRESHOLD: int = 2048             # 结果文本 >2KB → spill(F039,DIS-SEAM §6.1 G1)
SUMMARY_MAX_CHARS: int = 2000           # tool.result summary ≤2KB(payload max 2048)
PARSE_FAIL_NAME: str = "?"              # 解析失败事件 name 回落值(缺工具名时,审计占位)

# 参数摘要化优先展示键(审批展示用:先动作后其余,按风险/动作语义排序)
_SUMMARY_PRIORITY: tuple[str, ...] = (
    "url", "domain", "host", "target", "path", "src", "dst", "dir",
    "mode", "command", "cmd", "shell", "message", "content",
)


# ================================================================ ExecResult
@dataclass(frozen=True)
class ExecResult:
    """单次工具调用结果(spec 数据结构表;F022 批量入口消费)。

    summary ≤2KB(回喂/审计);truncated/spill_ref 由关4 finalize 产出
    (超长 → spill 私有区,大结果不进上下文/日志);elapsed_ms = Provider 实耗。
    """

    ok: bool
    summary: str
    truncated: bool = False
    spill_ref: Optional[dict] = None     # {ref, chars, lines, preview}(事件同型)
    elapsed_ms: float = 0.0


# ================================================================ 文本辅助
def render_result_text(raw: Any) -> str:
    """Provider 原始返回 → 统一文本(dict/list JSON 序列化;str 原样;None → 空)。"""
    if isinstance(raw, str):
        return raw
    if raw is None:
        return ""
    if isinstance(raw, (dict, list)):
        try:
            return json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            return str(raw)
    return str(raw)


def summarize_text(text: str, max_chars: int = SUMMARY_MAX_CHARS) -> str:
    """文本截断摘要(≤max_chars;截断处补 … 记号,防静默丢尾)。"""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


def _short_value(v: Any, width: int = 80) -> str:
    """摘要值短化:dict/list 只报结构;字符串截断;None/bool/int 直写。"""
    if isinstance(v, dict):
        return f"<dict {len(v)}键>"
    if isinstance(v, (list, tuple)):
        return f"<list {len(v)}项>"
    if v is None:
        return "null"
    s = str(v)
    if len(s) > width:
        return s[: width - 1] + "…"
    return s


def summarize(args: Any, *, max_chars: int = 400) -> str:
    """Consumer 层参数摘要化(审批展示用;spec 速览表 def summarize(args))。

    不由 Provider/LLM 生成(防注入操纵摘要,SECURITY §5):只做键值短化 + 动作
    键优先排序;超长截断。敏感值仅截断不语义化(人类审批通道,摘要可完整展示)。
    """
    if not isinstance(args, dict) or not args:
        return "(无参数)"
    ordered = sorted(args, key=lambda k: (_SUMMARY_PRIORITY.index(k)
                                          if k in _SUMMARY_PRIORITY else 99, k))
    parts: list[str] = []
    for k in ordered:
        seg = f"{k}={_short_value(args[k])}"
        if sum(len(p) for p in parts) + len(seg) > max_chars:
            parts.append("…")
            break
        parts.append(seg)
    return ", ".join(parts)


# ================================================================ ToolExecutor
class ToolExecutor:
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

    def __init__(self, registry: Optional[ToolRegistry] = None) -> None:
        self._r: ToolRegistry = registry if registry is not None else ToolRegistry()
        self._rejected: set[str] = set()        # GRD-402 防重放(拒绝裁决记忆)
        self._fail: dict[str, int] = {}         # F026 轮内连败计数
        self._out_models: dict[int, Any] = {}   # 输出契约编译缓存(id(defn) 键)
        self._active: Optional[asyncio.Future] = None   # cancel_current 在途句柄

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
        return self._r.schemas_for(scope, toolset=toolset)

    # ------------------------------------------------------ F026 轮内连败计数
    def reset_turn_failures(self) -> None:
        """每轮起点清零(spec: 轮内连败计数每轮重置)。"""
        self._fail.clear()

    def mark_turn_failure(self, name: str) -> int:
        """同工具失败一次并返回累计次数(≥2 由批量入口终止该轮,F026)。"""
        key = str(name)
        n = self._fail.get(key, 0) + 1
        self._fail[key] = n
        return n

    def _check_fail_streak(self, name: str) -> bool:
        """轮内同工具连败 ≥2 判定(spec 速览表;调用方终止该轮)。"""
        return self._fail.get(str(name), 0) >= 2

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
        # ---- 关2a scope 前置:不可见 → 终局拒(GRD-401;events 由 _reject 自写)
        if not ctx.scope.can_use(call.name):
            return await self._reject(ctx, call, "scope-hidden", "GRD-401")
        # ---- 关2b guard 单调链(F014;reject 已强同步 guard.rejected,零副作用)
        d = str(await ctx.guard.evaluate(call, ctx.scope))
        if d == "reject":
            self._rejected.add(call.call_id)            # 广义防重放(偏离 11)
            return ExecResult(ok=False, summary="guard 拒绝,未执行")
        # ---- 关2.5 审批(F015;danger≥high 由链出 approval 决策)
        if d == "approval":
            d = await self._approval_round(ctx, call, args)
            if d != "allow":
                return ExecResult(ok=False, summary=d)  # 非放行 → 不执行
        # ---- 关3 Provider 执行(线程池 + 超时掐断 TLB-805)
        timeout = int(defn.timeout_s) or DEFAULT_TOOL_TIMEOUT
        started = False
        elapsed_ms = 0.0
        try:
            self._r.mark_running(call.name)             # registry 注销闸联动
            t0 = time.perf_counter()
            fut = asyncio.wait_for(
                self._run_provider(defn, args, ctx), timeout=timeout)
            self._active = fut
            started = True                          # Provider 已进入 await 点
            try:
                raw = await fut
            finally:
                self._active = None
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
        except asyncio.TimeoutError:                    # 掐断;进程类杀树归
            return await self._on_error(                # Provider(TO-301),见 docstring
                ctx, call, "TLB-805", f"执行超时({timeout}s)")
        except asyncio.CancelledError:                  # F025:取消不吞
            if started:                                 # 已发生副作用如实写 partial
                await self._append(ctx, "tool.result",
                                   {"name": call.name, "call_id": call.call_id,
                                    "ok": False, "truncated": True,
                                    "summary": "已取消:执行被中断,"
                                               "已发生副作用如实记录(partial)"},
                                   actor="tool", trace=self._trace(call))
            raise                                       # 按取消协议 re-raise
        except PyHError as e:                           # Provider 结构化错误
            return await self._on_error(ctx, call, e.code, e.to_model_message())
        except Exception as e:                          # noqa: BLE001 Provider 运行异常
            return await self._on_error(
                ctx, call, "TLB-805",
                f"Provider 运行异常:{type(e).__name__}:{str(e)[:200]}")
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

    async def _approval_round(self, ctx: Any, call: ToolCall,
                              args: dict) -> str:
        """关2.5 审批编排:request → granted 重入链起点 → 放行/拒绝裁决。

        返回 "allow" = 可执行;否则返回 ExecResult 用 summary 文本(调用方原样
        返回)。denied/timeout = 不执行;APR-501(headless 运行时无通道)→ 记
        rejected id 后拒;granted 后重入 guard 链:reject → GRD-403(批准作废,
        单调性高于人类即时意志);approval(重入仍要审批)== 本调用同参刚获批准
        → 视为已授权(偏离 3);allow → 放行。
        """
        ap = getattr(ctx, "approval", None)
        if ap is None:
            raise_code("CYC-999", module="tools_executor",
                       hint="ctx.approval 未接线:guard 判 need_approval 但审批"
                            "服务缺失;拒绝执行(fail-closed)")
        try:
            verdict = await ap.request(call, summarize(args), ctx)
        except PyHError as e:
            if e.code == "APR-501":                     # headless 无通道:直接拒
                self._rejected.add(call.call_id)
                return "审批不可用(APR-501),未执行"
            raise                                       # 其余错误:装配/落盘故障上抛
        if verdict != "granted":                        # denied/timeout = 不执行
            return f"审批{verdict},未执行"
        d = str(await ctx.guard.evaluate(call, ctx.scope))  # granted≠放行:重入
        if d == "reject":                               # 批准时策略收紧 → 作废
            self._rejected.add(call.call_id)
            return "审批后 guard 重入拒绝(GRD-403 语义)"
        return "allow"      # allow 放行;approval(同参已批)视为已授权(偏离 3)

    async def _reject(self, ctx: Any, call: ToolCall, guard_id: str,
                      policy_ref: str) -> ExecResult:
        """终局拒(executor 侧,如 scope-hidden):guard.evaluated + guard.rejected
        (sync=True,强同步)+ 记 _rejected call_id(GRD-401 单调语义,INV-05)。
        """
        policy = policy_ref or "GRD-401"
        await self._append(ctx, "guard.evaluated",
                           {"tool": call.name, "decision": "deny",
                            "guard_ids": [guard_id], "reasons": [policy]},
                           actor="tool", trace={"call_id": call.call_id})
        await self._append(ctx, "guard.rejected",
                           {"tool": call.name, "guard_id": guard_id,
                            "reason": policy, "policy_ref": policy},
                           actor="tool", sync=True, trace={"call_id": call.call_id})
        self._rejected.add(call.call_id)
        return ExecResult(ok=False,
                          summary=f"guard 拒绝:{guard_id}({policy}),未执行")

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

    # ------------------------------------------------------------ Provider 调用
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

    async def _run_provider(self, defn: Any, args: dict, ctx: Any) -> Any:
        """Provider 执行(runner):async handle 直接协程;同步 handle 经线程池。

        同步函数由 asyncio.to_thread 承载(不阻塞事件循环,spec 关3);async
        handler 走事件循环协作(占用线程池反伤吞吐,偏离 7)。超时掐断/取消由
        execute 的 wait_for 统一施加。
        """
        handle = self._provider_handle(defn.name)
        if inspect.iscoroutinefunction(handle):
            return await handle(args, ctx)              # wait_for 在外层掐断
        return await asyncio.to_thread(handle, args, ctx)

    def _invoke(self, defn: Any, args: dict, ctx: Any) -> Any:
        """spec 速览 _invoke:Provider 实际调用同步入口(to_thread 承载面)。

        保留为可整体替换的薄层:替换 _run_provider 即可换执行策略;同步调用
        经本函数时按 handle(args, ctx) 契约执行。
        """
        handle = self._provider_handle(defn.name)
        return handle(args, ctx)

    # ------------------------------------------------------------ 取消(F025)
    async def cancel_current(self) -> bool:
        """取消在途 Provider 任务(协作式,F025):掐断 wait_for → execute 内
        CancelledError 分支写 partial + re-raise(已发生副作用不假装回滚)。

        无在途任务 → 返回 False(幂等)。partial 事件由 execute 的取消处理分支
        落盘,本函数只负责触发取消(单事件循环单在途,阶段 1 agent-loop 串行)。
        """
        fut = self._active
        if fut is None or fut.done():
            return False
        fut.cancel()
        return True

    # ------------------------------------------------------------ 关4 finalize
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
                            "ok": True, "summary": summary,
                            "truncated": truncated, "spill_ref": spill_ref},
                           actor="tool", trace=self._trace(call))
        return ExecResult(ok=True, summary=summary, truncated=truncated,
                          spill_ref=spill_ref, elapsed_ms=elapsed_ms)


__all__ = [
    "ToolExecutor", "ExecResult", "ToolCall", "Decision",
    "DEFAULT_TOOL_TIMEOUT", "SPILL_THRESHOLD", "SUMMARY_MAX_CHARS",
    "render_result_text", "summarize_text", "summarize",
]
