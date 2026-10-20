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

from pyharness.core.llm_types import (_RETRYABLE_CODES, _L1_SETTINGS, _l1_settings, TimeoutLimits, ToolCall, LLMResponse, UsageCounters, AdapterTriple, ProviderStatusError, ProviderProtocolError, ToolCallSyntaxError, LLMAdapter)
from pyharness.core.llm_protocol import (_sanitize_tool_name, _sanitize_tools, _restore_call_names, _load_arguments, parse_tool_calls, assistant_and_tool_messages, _emit_chunk, _slot_to_call, accumulate_stream, _est_tokens, _http_status_code, _openai_module, normalize_exc)
from pyharness.core.llm_usage import (estimate_cost, _price_table, _llm_cfg_from_ctx, _request_payload, _response_payload, report_usage)
from pyharness.core.llm_runtime import adapters, register_adapter, require_adapter
from pyharness.core.llm_transport import (_OpenAICompatHTTPTransport, _to_ns)

if TYPE_CHECKING:  # 仅类型标注;装配对象鸭子注入(session/bus 经 ctx)
    pass

from pyharness.core.llm_protocol import log

try:  # httpx 为项目硬依赖;极端缺环境下模块仍可导入(纯解析/单测路径可用)
    import httpx
except ImportError:  # pragma: no cover — 缺依赖兜底
    httpx = None  # type: ignore[assignment]

# LLM 错误归一后可重试码(F028 依据;LLM-304 业务错不重试不降级)


# ================================================================ 数据结构


# ================================================================ 传输层异常


# ================================================================ wire 解析层


# ================================================================ 错误归一


# ================================================================ 成本与计量


# ================================================================ 适配器层


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

    @property
    def chain(self) -> Any:
        """降级链只读别名(F013/F028/F033 观测面;None = 未装配链)。

        装配层需要它来启动健康探针(``probe_loop``)并观测 ``idx``/健康状态;
        直接读 ``_chain`` 属跨模块私名依赖,故给只读别名。
        """
        return self._chain

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
        self._egress_guard(method, ctx)
        if ch is not None and callable(getattr(ch, "chat_with_fallback", None)):
            return await ch.chat_with_fallback(messages, tools, ctx=ctx,
                                               method=method)
        inst = self.require()
        if not isinstance(inst, ModelRequests):
            from pyharness.core.context_observation import observe_adapter_request
            await observe_adapter_request(ctx, self.model, method, messages, tools)
        from pyharness.core.product_model_gate import check_model_attempt
        await check_model_attempt(ctx, self.model, method)
        return await getattr(inst, method)(messages, tools, ctx=ctx)

    def _egress_guard(self, method: str, ctx: Any) -> None:
        """LLM 出网治理闸——**统一边界 + 显式 Negative Space**(GAP-1)。

        ``_chat_any`` 是 LLM 出网的**唯一汇点**:``chat`` / ``chat_stream`` /
        ``mini`` / ``summarize`` / ``json_chat`` 五个公开出口全部经它,故治理只
        需在此**一处**加闸——不需要在 8 个出口复制 ``authorize()``(那会造成
        架构碎片化,且多份副本迟早漂移)。

        **为什么边界不是 ``governance.authorize()``**

        ``governance`` 包是**工具调用**授权层:g1–g7 按**工具名**前缀匹配
        (``fs.*`` / ``web.*`` / ``exec.*``),``authorize()`` 收 ``ToolCall``,产出的
        ``decision.issued`` / ``receipt.emitted`` 经 ``call_id`` 串联 ``tool.call`` /
        ``tool.result``。**LLM 请求不是工具调用**:无工具名、无参数集合、无
        Provider 副作用。把它塞进 ``authorize()`` 只能靠伪造 ``ToolCall``,会污染
        工具治理链语义。故**不这么做**(P1:决策权与执行权分离,不因凑指标而破坏)。

        **LLM 出网实际适用的治理面 = 会话级预算硬闸**,本闸把它下沉到唯一汇点:
        ``ctx.scope.check_budget()``(超限抛 ``BudgetExhausted``,与 ``agent_loop``
        轮起点**同源同信号**)。修复前该闸只在轮起点调用,``mini``(自动标题)/
        ``summarize``(压缩)/``json_chat``(计划)三条**轮内**系统出口可绕过。

        **三态语义**

        1. scope 在位且预算 ok ⇒ 放行(正常落 ``llm.request`` / usage);
        2. scope 在位且 paused/exhausted ⇒ **拒**(抛 ``BudgetExhausted``,
           **零出网**:不产生 ``llm.request``);
        3. scope 缺失(纯内存/轻装配/单测替身)⇒ **降级放行**。理由:预算闸是
           **配额面**而非**授权面**,缺配额不等于越权;而事件落点/守卫缺失会让
           审计不成立,那些才必须 fail-closed(见 executor ``_require_wiring``)。

        **Negative Space(显式声明哪些 LLM 出口不经本闸,及理由)**

        - ``OpenAICompatAdapter.chat`` / ``.chat_stream``:适配器层,位于本类之后。
          已核验**全库无生产代码直接调用**(CI 静态闸扫此),唯一路径 = 本类
          ``_chat_any`` ⇒ 已被本闸覆盖。
        - ``OpenAICompatAdapter.ping`` / 降级链健康探针:``ping`` 不产生 tokens、不落
          F029 计量、不进 ``llm.request``;让其受会话预算约束会使"探测可用性"被预算
          拒,语义颠倒。**明确不治理**,并登记于 ``LIMITATIONS.md``。
        - 出网**之后**的审计面(``llm.request`` / ``llm.response`` / ``llm.usage``)
          不属本闸:那是**观测**面,由适配器落事件(唯一真源仍是 append-only 日志)。
        """
        scope = getattr(ctx, "scope", None) if ctx is not None else None
        if scope is None:
            return                                   # 三态 ③:未装配配额面
        check = getattr(scope, "check_budget", None)
        if callable(check):
            check()                                  # 三态 ②:超限即抛,零出网

    async def chat(self, messages: list[dict], tools: Optional[list] = None, *,
                   ctx: Any) -> LLMResponse:
        """唯一出口(代理到适配器/降级链;请求全生命周期单元,事件路径唯一)。"""
        return await self._chat_any("chat", messages, tools, ctx)

    async def chat_stream(self, messages: list[dict], tools: Optional[list] = None, *,
                          ctx: Any) -> LLMResponse:
        """流式出口(代理到适配器/降级链;chunk 上总线不入日志)。"""
        return await self._chat_any("chat_stream", messages, tools, ctx)

    async def mini(self, prompt: str, *, ctx: Any) -> str:
        """系统工具级单次文本出口(F042 auto_title 等消费):单 prompt 进 → 纯文本出。

        与 ``chat`` 属**不同类**:无 messages 列表 / 无 tools 面 / 无对话轮语义 /
        不返回 ``LLMResponse``。``chat``/``chat_stream`` 是 **Agent Loop 对话出口**
        (INV-02:唯一合法调用方 = agent-loop);本出口是 **System Tool LLM 出口**,
        与 ``summarize``/``json_chat`` 同类(同为 ``_chat_any`` 薄包装),差别仅在输出
        形态。超时(F017)/计量(F029)/事件/降级链全部复用既有链,不新增真源。
        """
        resp = await self._chat_any("chat", [{"role": "user", "content": prompt}],
                                    None, ctx)
        return (resp.content or "").strip()

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


class LLMRuntime:
    """Application-owned connections; session counters are never retained here.

    Explicit non-production adapters in the public registry remain a borrowed
    test/plugin seam. New production clients are closed by this owner once.
    """
    def __init__(self):
        self.registry = {key: value for key, value in adapters.items()
                         if not isinstance(value, OpenAICompatAdapter)}
        self.closed = False

    def has_borrowed_adapter(self, name):
        adapter = self.registry.get(name)
        return adapter is not None and not isinstance(adapter, OpenAICompatAdapter)

    async def aclose(self):
        self.closed = True
        task = getattr(self, '_close_task', None)
        if task is None or (task.done() and (task.cancelled() or task.exception() is not None)):
            task = self._close_task = asyncio.create_task(self._close_owned())
        await asyncio.shield(task)

    async def _close_owned(self):
        errors = []
        for adapter in {id(a): a for a in self.registry.values()}.values():
            if isinstance(adapter, OpenAICompatAdapter):
                try:
                    await adapter.aclose()
                except BaseException as exc:
                    errors.append(exc)
        if errors:
            raise BaseExceptionGroup('model transport cleanup failed', errors)


from pyharness.core.llm_requests import ModelRequests

class OpenAICompatAdapter(ModelRequests, LLMAdapter):
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
        self._closed = False
        self._counters = counters if counters is not None else UsageCounters()
        self._counters_injected: bool = counters is not None
        self._deg: Optional[str] = None            # 降级来源(fallback 切链后置位 F013)

    def mark_degraded_from(self, source: Optional[str]) -> None:
        """置位降级来源(Producer:降级链在降级时调用;F013)。

        是 ``_deg`` 的唯一写入口:下一次 ``chat()`` 的 llm.request 据此携带
        ``degraded_from``(EVENT-SCHEMA §3.x / ADI §3.3 / specs/llm.py.md)。
        CND-08:此前 ``_deg`` 无赋值点 → ``degraded_from`` 恒缺失(dead trigger)。
        """
        self._deg = source

    # ------------------------------------------------------ 传输解析
    def _ensure_transport(self) -> Any:
        """惰性取传输:注入优先;否则 build_client(解析 key 需真实凭据,仅真调用路径)。"""
        if self._closed:
            raise_code('CRED-703', reason='model_runtime_closed')
        if self._triple is not None and self._triple.credential_store is not None:
            self._triple.credential_store.validate_binding(
                self._triple.api_key_ref, self._triple.credential_binding)
        if self._transport is None:
            if self._triple is None:
                raise_code("CFG-601", reason="structural", fields=["llm"],
                           detail="适配器缺端点三元组:构造 OpenAICompatAdapter 需 "
                                  "triple 或注入 transport")
            self._transport = build_client(self._triple, self.timeout)
        return self._transport


    # ------------------------------------------------------ 非流式唯一出口


    # ------------------------------------------------------ 流式入口(F027)


    # ------------------------------------------------------ 计量钩子


    # ------------------------------------------------------ F033 探针


# ================================================================ 注册表
# AdapterRegistry(F030):name(= 模型名) → 适配器实例;换模型 = 注册 + 配置,llm 零改动


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
        # CRED-702(2026-09-21 R27 接线):凭据文件**权限过宽** ⇒ 拒载,不静默读。
        # 该码此前**已登记但全库从不抛出**(TS-06 实测:43 码中唯一无测试者)——
        # 文档承诺的"权限过宽(>600)→ 启动拒载"在实现里根本不存在。POSIX 查 mode
        # 的 group/other 位;Windows 的 mode 位是合成的、无安全含义(仓库既有口径:
        # ACL 尽力而为),故 Windows 上跳过该检查。
        if os.name != "nt":
            try:
                mode = os.stat(path).st_mode & 0o777
            except OSError:
                mode = None
            if mode is not None and mode & 0o077:
                raise_code("CRED-702", ref=ref, path=path, mode=oct(mode),
                           hint="凭据文件权限过宽(>600):chmod 600 后重试")
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


def build_client(triple: AdapterTriple, limits: TimeoutLimits, *,
                 resolver: Any = None) -> _OpenAICompatHTTPTransport:
    """客户端构建(ADI §1.1):按三元组建传输客户端;key 经 resolver(F016 单口)缺失 → CRED-701。

    偏离 2:spec 返回 openai.AsyncOpenAI——openai SDK 非本阶段运行依赖(真实调用留 e2e),
    此处返回 httpx OpenAI 兼容传输(complete/stream/ping,语义与 create 对齐),可整体注入。
    """
    if triple.credential_store is not None:
        resolver = lambda ref: triple.credential_store.resolve_ref(ref, binding=triple.credential_binding)
    else:
        resolver = resolver or resolve_secret_ref
    key = resolver(triple.api_key_ref)              # 缺失 → CRED-701(绝不空串/None 续跑)
    if not key:
        raise_code("CRED-701", ref=triple.api_key_ref,
                   hint="凭据解析为空,拒绝构建客户端(绝不空串续跑)")
    base = triple.base_url.rstrip("/")              # 裸域或 /v1 结尾;禁写 /chat/completions
    timeout = httpx.Timeout(limits.connect_s,       # 连接/写/池 档
                            read=limits.first_token_s,   # 首 token 档(60s)
                            write=limits.connect_s, pool=limits.connect_s)
    transport = _OpenAICompatHTTPTransport(base_url=base, api_key=key, timeout=timeout)
    if triple.credential_store is not None:
        transport._validate = lambda: triple.credential_store.validate_binding(
            triple.api_key_ref, triple.credential_binding)
    return transport


# ================================================================ 会话门面



def model_configuration_admission(actual,expected,registry,model):
    from pyharness.core.model_connection_identity import connection_registry_key
    actual_keys=[actual.model,*(actual.fallback_models or [])]
    wanted=[expected.model,*(expected.fallback_models or [])]
    if len(actual_keys)!=len(wanted):return False,False
    limits=TimeoutLimits.from_cfg(expected.timeout)
    for key,name in zip(actual_keys,wanted):
        if key==name:continue  # old explicit borrowed-adapter configuration
        adapter=registry.get(key)
        if type(adapter) is not OpenAICompatAdapter or adapter._closed:return False,False
        triple=adapter._triple
        if (triple is None or adapter.model!=name or triple.model!=name or
                triple.api_key_ref!=expected.api_key or triple.base_url!=expected.base_url or
                adapter.timeout!=limits):return False,False
        store=triple.credential_store;binding=triple.credential_binding
        if store is not None:
            # Original store validates the exact generation; no secret is read.
            store.validate_binding(triple.api_key_ref,binding)
        derived=connection_registry_key(expected.api_key,expected.base_url,name,limits,binding,
            str(store.root.resolve()) if store is not None else '')
        if key!=derived:return False,False
    value=actual.model_dump();value['model']=expected.model;value['fallback_models']=expected.fallback_models
    return value==expected.model_dump(),model in [*actual_keys,*wanted]


__all__ = [
    "model_configuration_admission",
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
