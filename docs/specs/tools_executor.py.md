# specs/tools_executor.py.md — 编码规格

> **模块文件**:`pyharness/core/tools_executor.py` | **功能编号**:F022(调用解析回填)· F026(先验后跑)· F014/F015(guard/审批编排)· F017(超时)· F039(结果 spill)联动 | **权威口径**:PRD-Core §2.4(数据流步 8b)/§4.4(原则 4 零信任)/§5.2 F022/F026、DIS-CORE §7.3.2(执行管道伪代码权威);冲突以 PRD-Core 为准
> **一句话**:工具执行管道总闸——**四关强制**:①契约校验(查 Definition,TLB-802)+ 参数 pydantic 先验后跑(TLB-803,**失败 = Provider 零调用**,INV-06)→ ②guard 单调链(F014,拒绝终局零副作用 GRD-401)→ 审批(F015,granted **重入链起点**)→ ③Provider 执行(线程池 + 超时掐断 TLB-805,同步 handler 不阻塞事件循环)→ ④结果检查与 finalize(输出 schema 校验 + spill/摘要截断 → `tool.result` 事件);全程事件留痕、call_id 关联,无旁路(INV-04)。
> **代码目录**:tools 脊柱模块三文件之一;本文件为统一 Consumer(DIS-SEAM §2.3),经 ctx 消费 tools_registry(契约/模型/Provider)与 tools_guard(链)。

## 模块职责

1. **管道唯一执行点(INV-04)**:全系统工具调用必经 `execute`——不存在 Provider 自执行/低层 API 直调旁路;guard.evaluated 缺失的执行 = 非法(F031 自检兜底)。校验/guard/审批/计量/结果检查都在消费协议里,Provider 不重复实现(原则 2)。
2. **关1 契约+先验后跑(F026/R1)**:raw_args 过 Definition.schema 编译的 pydantic 模型强校验(strict 拒多余字段);失败 → `tool.error(TLB-803)` 附明细回喂 LLM 修正,**任何分支都不执行 Provider**(防 `str(123)` 隐式类型转换执行);raw_args(模型原话)与 args(实际执行)双份落 `tool.call`(INV-06 逐字段比对依据);同工具本**轮**连败 2 次 → 终止该轮不再尝试。
3. **关2 scope + guard + 审批(F014/F015)**:scope 不可见 → 终局拒(GRD-401);guard 链 reject → 强同步 guard.rejected + 零副作用;approval → 交 approval.py,denied/timeout = 不执行,**granted ≠ 放行——重入 guard 链起点**,重入被新拒 → GRD-403 语义(批准作废,单调性高于人类即时意志)。
4. **关3 Provider 执行(F017/F025)**:同步 handler 经 `asyncio.to_thread` 跑线程池(默认 4);总超时 = `defn.timeout_s or 60s`,`asyncio.wait_for` 掐断,超时 → `tool.error(TLB-805)` + 进程类工具杀树(TO-301);取消(CancelledError)不吞——已发生副作用如实写 `tool.result(ok=False, partial=true)`(F025 声明式取消)。
5. **关4 结果检查 + finalize(F039)**:输出按 `defn.output_schema` 校验(失败 → tool.error);超长输出(超事件 payload 安全线)落 spill 私有区,`tool.result` 只带 ≤2KB summary + truncated + spill_ref(大结果不进上下文/日志);每调用恰好一条 result 或 error(事件配对,trace.parent_seq 关联父 llm.response)。
6. **GRD-402 防重放**:已裁决(拒绝)的 call_id 再次执行尝试 → 拒绝 + system.error,防审计流里"拒了又跑"的矛盾记录。

## 依赖

- **单向依赖**:本文件 → tools_registry(lookup/get_model/validate_args/lookup_provider)、tools_guard(evaluate)、approval(request,经注入 ctx.approval)、session.append(tool.call/result/error)、spill(ctx.storage.spill,F039)、errors.raise_code;不 import 任何 Provider 内部符号。
- **消费方**:agent-loop(F007 步 8b `await ctx.tools.execute(call)`)、F022(批量串行执行)、斜杠/内部调用(同管道,无特权路径)。
- **外部**:asyncio(线程池/to_thread/wait_for)、pydantic v2。

## 数据结构表

| 结构 | 字段 | 规则 |
|---|---|---|
| `ToolCall` | name/raw_args/call_id/parent_seq/defn? | raw_args 原样存(审计);call_id 关联 result/error;parent_seq=trace 父 llm.response |
| `ExecResult` | ok/summary/truncated/spill_ref/elapsed_ms | summary ≤2KB;truncated/spill_ref 由关4 产出 |
| `_TurnFail` | tool→连续失败次数 | 每轮重置;≥2 抛 TurnToolFailures 终止该轮 |
| `_RejectedIds` | set[call_id] | 拒绝裁决记录;重放尝试 → GRD-402 |
| `_Running` | set[name] | 在途工具名(registry 注销闸联动) |

**四关总表(权威顺序,DIS-CORE §7.3.2)**:

| 关 | 检查 | 失败处置 | 码/事件 |
|---|---|---|---|
| 1 | 契约存在 + 参数强类型 | tool.error 回喂,零执行;连败 2 次终止轮 | TLB-802/803 |
| 2 | scope 前置 + guard 链 | 终局拒;guard.rejected 强同步;零副作用 | GRD-401 |
| 2.5 | danger≥high 人类审批 | denied/timeout 不执行;granted 重入关2 | APR-5xx/GRD-403 |
| 3 | Provider 执行(线程池+超时) | tool.error 回喂;重试性由 LLM 判断 | TLB-805/TO-301 |
| 4 | 输出 schema 校验 + spill/摘要 | 超长转 spill;校验失败 tool.error | PERS-221~223/tool.result |

## 类与函数清单

### `async def execute(call: ToolCall, ctx) -> ExecResult` — 执行管道主函数(F022 全链)

**功能**:四关强制编排:查契约(TLB-802)→ 先验后跑(TLB-803,零执行)→ 落 `tool.call`(args+raw_args 双份)→ scope/guard → 审批(重入)→ Provider(超时掐断)→ 结果检查 finalize;任何失败收敛为 tool.error 回喂 LLM,成功恰一条 tool.result。

```python
async def execute(self, call, ctx):
    if call.call_id in self._rejected:              # GRD-402:已裁决调用禁重放
        raise PyHError("GRD-402", ctx={"call_id": call.call_id,
            "advice": "该调用已裁决,禁止重复执行"})
    defn = self._r.lookup(call.name)                # 关1a 契约;TLB-802 → 回喂
    args = self._r.validate_args(call.name, call.raw_args)  # 关1b 先验后跑
    # 失败在 validate_args 内已抛 TLB-803 → executor 上层转 tool.error,
    # 本函数后续任何一行都不执行(INV-06:畸形参数零调用)
    await ctx.session.append("tool.call", {"name": call.name, "args": args,
        "raw_args": call.raw_args, "call_id": call.id}, actor="tool",
        trace={"parent": call.parent_seq})          # 双份存档(INV-06)
    if not ctx.scope.can_use(call.name):            # 关2a scope 前置
        return await self._reject(ctx, call, "scope-hidden", "GRD-401")
    d = await ctx.guard.evaluate(call, ctx.scope)   # 关2b 单调链
    if d == "reject":                               # guard.rejected 已强同步
        return ExecResult(ok=False, summary="guard 拒绝,未执行")
    if d == "approval":                             # 关2.5 danger≥high
        verdict = await ctx.approval.request(call, summarize(args), ctx)
        if verdict != "granted":                    # denied/timeout/headless=R8
            return ExecResult(ok=False, summary=f"审批{verdict},未执行")
        d = await ctx.guard.evaluate(call, ctx.scope)  # granted≠放行:重入链起点
        if d != "allow":                            # 批准时策略已收紧
            return ExecResult(ok=False, summary="审批后 guard 重入拒绝(GRD-403 语义)")
    try:                                            # 关3 Provider(线程池+超时)
        self._running.add(call.name)
        raw = await asyncio.wait_for(asyncio.to_thread(
                self._invoke, defn, args, ctx), timeout=defn.timeout_s or 60)
    except asyncio.TimeoutError:                    # 掐断;进程类须已杀树(F052)
        return await self._on_error(ctx, call, "TLB-805", "执行超时(60s)")
    except PyHError as e:                           # Provider 结构化错误
        return await self._on_error(ctx, call, e.code, e.to_llm_text())
    finally:
        self._running.discard(call.name)
    return await self._finalize(ctx, call, defn, raw)   # 关4 结果检查+tool.result
```

**参数表**:`call` = 已解析 ToolCall(含 parent_seq);`ctx` = 会话门面(scope/guard/approval/session/spill)。**返回**:`ExecResult`。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 工具不存在(幻觉名/已卸载) | TLB-802 | tool.error 回喂 LLM 自查;查 schemas_for 名单 |
| `PyHError` | 参数校验失败(畸形/多余字段/非 JSON) | TLB-803 | tool.error 附明细;修正重发;连败 2 次终止轮 |
| `PyHError` | 已拒 call_id 重放 | GRD-402 | system.error;不重复审计 |
| `PyHError` | 重入后新拒(策略收紧) | GRD-403 | 非错误;两条 evaluated 可证 |
| `PyHError` | Provider 超时/运行异常 | TLB-805 | tool.error 回喂;LLM 判重试 |
| `asyncio.CancelledError` | 用户取消(F025) | system.cancelled | 已发生副作用写 partial=true;re-raise |

**关联测试**:GWT-T7-02(事件序 tool.call→guard.evaluated(allow)→tool.result;Provider 实参==日志 args)、GWT-T7-03/04(拒绝零副作用/审批重入)、T-SEC-08(30 例畸形零执行)。

### `async def execute_tool_calls(calls: list[dict], ctx, *, parent_seq: int | None = None) -> list[ExecResult]` — F022 批量串行入口

**功能**:agent-loop 每轮把 LLM 返回的多个 tool_calls 逐条解析执行(默认串行,可配并发 ≤3);记录同工具连败,≥2 终止本轮后续调用;结果/错误事件各自落盘并 trace 关联父 llm.response(§3.5 配对)。

```python
async def execute_tool_calls(self, calls, ctx, *, parent_seq=None):
    results, fail = [], {}
    for raw in calls:                               # 默认串行(保序可审计)
        try:
            call = self.parse_tool_call(raw, parent_seq)   # 非 JSON → TLB-803 回喂
        except PyHError as e:                       # 解析失败:连败计数
            fail[raw.get("name", "?")] = fail.get(raw.get("name", "?"), 0) + 1
            results.append(await self._on_error(ctx, None, e.code,
                "tool_calls 解析失败:非法 JSON/缺字段"))
            if fail[raw.get("name", "?")] >= 2: break   # 连败 2 次终止该轮(F026)
            continue
        r = await self.execute(call, ctx)           # 全管道纪律在内部
        results.append(r)
        key = call.name
        if not r.ok and r.summary.startswith(("参数校验", "guard")):   # 失败分级计数
            fail[key] = fail.get(key, 0) + 1
            if fail[key] >= 2: break                # TLB-803 连败 2 → 终止轮
    return results
```

**参数表**:`calls` = LLMResponse.tool_calls 原始数组;`parent_seq` = 触发响应 seq。**异常表**:同 execute(解析失败 → TLB-803 回喂而非抛到 loop)。**关联测试**:GWT-L1-02(每轮工具多调用串行)、test_f022_tool_calls.py(trace 关联)。

### `def parse_tool_call(raw: dict, parent_seq) -> ToolCall` — 单条解析

**功能**:LLM 原始 tool_calls → ToolCall(name/raw_args/call_id);`id/name/arguments` 缺失或 arguments 非 JSON → TLB-803 回喂(**解析失败不猜测、不透传执行**)。

```python
def parse_tool_call(self, raw, parent_seq=None):
    tid = raw.get("id") or uuid4().hex[:8]          # 缺 id 补生成(审计仍可关联)
    fn = raw.get("function") or {}
    name = fn.get("name")
    if not name or not NAME_RE.match(name):         # 幻觉名入口拦截
        raise PyHError("TLB-802", ctx={"tool": name, "advice": "查 schemas_for 名单"})
    try:
        args = json.loads(fn.get("arguments") or "{}")
    except json.JSONDecodeError as e:               # 非法 JSON → 回喂修正
        raise PyHError("TLB-803", ctx={"tool": name, "why": f"arguments 非法 JSON: {e}"})
    return ToolCall(name=name, raw_args=args, call_id=tid, parent_seq=parent_seq)
```

**异常表**:TLB-802(名非法)、TLB-803(arguments 非 JSON)。**关联测试**:GWT-ERR-03(畸形参数族)、T-SEC-08。

### `async def _finalize(ctx, call, defn, raw) -> ExecResult` — 关4 结果检查与落盘

**功能**:输出 schema 校验(声明了 output_schema 才做,失败 → tool.error)→ 超长转 spill → 摘要化 → `tool.result`(ok/summary/truncated/spill_ref,trace.parent=父 response);事件 payload 严守 ≤64KB(N3)。

```python
async def _finalize(self, ctx, call, defn, raw):
    if defn.output_schema:                          # 输出契约校验(声明时)
        try: validate_output(defn.output_schema, raw)
        except ValidationError as e:
            return await self._on_error(ctx, call, "TLB-803",
                f"输出校验失败:{summarize_validation(e)}")
    text = render_result_text(raw)                  # 大结果 dict/str 统一文本化
    truncated, spill_ref = False, None
    if len(text) > SPILL_THRESHOLD:                 # 超限 → spill(F039),上下文只留摘要
        sp = await ctx.storage.spill.put(text, kind=call.name)
        truncated, spill_ref = True, sp["ref"]
        summary = sp["preview"][:500] + f"…(共{sp['chars']}字符/{sp['lines']}行,spill:{sp['ref']})"
    else:
        summary = summarize_text(text, max_chars=2000)   # ≤2KB 摘要
    ev = await ctx.session.append("tool.result", {"name": call.name,
        "call_id": call.call_id, "ok": True, "summary": summary,
        "truncated": truncated, "spill_ref": spill_ref},
        actor="tool", trace={"parent": call.parent_seq})
    return ExecResult(ok=True, summary=summary,
        truncated=truncated, spill_ref=spill_ref)
```

**异常表**:PERS-221(spill 写失败)、PERS-223(>10MB 拒写)——tool.error 回喂。**关联测试**:F039 G1(输出 5KB>2KB → truncated+spill_ref 且上下文 ≤2KB)、GWT-T7-02。

### 其余函数速览

| 函数 | 功能一句话 | 备注 |
|---|---|---|
| `async def _reject(ctx, call, guard_id, policy_ref) -> ExecResult` | 终局拒:guard.evaluated+guard.rejected(sync=True)+记 _rejected call_id | GRD-401 单调语义 |
| `async def _on_error(ctx, call, code, message) -> ExecResult` | 统一错误出口:append tool.error(code/message ≤2000)并回 ExecResult(ok=False) | code 原样透传(ERR §5.2) |
| `def _invoke(defn, args, ctx)` | Provider 实际调用:`provider.handle(args, ctx)`(同步函数直接执行,由 to_thread 承载) | Provider 可整体替换 |
| `def summarize(args) -> str` | Consumer 层参数摘要化(工具名/动作类型/路径/风险)——**不由 Provider/LLM 生成**(防注入操纵摘要,SECURITY §5) | approval 展示用 |
| `def mark_turn_failure(name) / reset_turn_failures()` | 轮内连败计数(≥2 终止轮,F026 边界) | 每轮起点 reset |
| `def rejected_ids() -> set[str]` | 已裁决 call_id 集(F031 自检/审计) | 防重放 GRD-402 |
| `def register(defn, *, provider=None) -> str` | 门面对 registry 的透传(能力 announce 第③步) | ctx.tools.register |
| `def schemas_for(scope, *, toolset=None) -> list[dict]` | 门面对 registry.schemas_for 透传(agent-loop 每轮) | ctx.tools.schemas_for |
| `async def cancel_current()` | 取消在途 Provider 任务(F025):置 partial=true、写系统事件、re-raise | 协作式取消 |
| `def _check_fail_streak(name) -> bool` | 轮内同工具连败 ≥2 判定(≥2 返回 True,调用方终止该轮) | F026 边界;每轮起点清零 |
| `def _validate_output(defn, raw) -> None` | 输出契约单点校验:output_schema 缺失 = 跳过;失败抛 TLB-803 | _finalize 关4 前置 |

**模块级测试**:GWT-T7-02~04、T-SEC-01~12(行为层断言:事件流+Provider 计数+文件实况)、INV-04(每 tool.result 有前置 evaluated)、INV-05(拒绝后零副作用)、INV-06(执行 args==日志 args)、test_f026_arg_validation.py、test_f022_tool_calls.py。

## 关联文档

1. PRD-Core.md §2.4(数据流步 8b)/§4.4(原则 4)/§5.2 F022·F026/F017(超时)/F025(取消)/§6.2 R1。
2. DIS-CORE.md §7.3.2(execute 伪代码权威)/§7.4(单调用状态机)/§7.5(错误路径表)/§7.7。
3. DIS-SEAM.md §2.3(统一 Consumer 协议:validate→guard→审批→meter→执行→输出检查→事件)。
4. SECURITY.md §2.3(工具闸口四关表)、§4.4(三种审计证明)、ERR.md §2.9/§5.2(工具错误链:TLB-803→GRD-401→TLB-805→tool.error)。
5. CONSTRAINTS-03-ToolSafety.md(工具安全硬约束);验收:tests/acceptance/test_f022_tool_calls.py、test_f026_arg_validation.py、tests/security/。
