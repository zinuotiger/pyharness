# ADI — LLM 集成对接规范

> 类型: 集成规范 | 权威对齐: PRD-Core.md §5 模型域 + DIS-CORE.md §4(伪代码引用勿重复)
> 覆盖: OpenAI 兼容协议/tool_calls 原始格式/DeepSeek 主模型/qwen-max 降级/流式/超时重试/错误映射/token 计量
# ADI.md — LLM 集成对接规范(协议与解析层)

> 配套:PRD-Core.md(F012/F013/F017/F022/F026-F033、§2.4、§3.3、§3.5)、DIS-CORE.md §4(llm 编排层)、ERR.md(LLM-3xx)、CFG.md(配置键)、ADD.md ADR-007/011。
> **分工**:DIS-CORE §4 已给出编排层伪代码(chat/降级链/退避/流式编排/用量上报/状态机),**本文件只引用、不重复**;本文件覆盖协议与解析层:wire 格式、tool_calls 原始格式拆解与配对、SSE 逐块解析、错误归一矩阵、usage 计量口径、两模型接入参数与配置示例。
> **冲突裁决**:功能语义以 PRD-Core 为准;编排以 DIS-CORE §4 为准;错误码以 ERR.md 为准;配置键以 CFG.md 为准;协议选型以 ADR-007 为准。本文件与其冲突时,以被引章节为准并修订本文件。

# 0 定位与阅读约定

## 0.1 范围与全局不变量

本文件回答对接层问题:①端点与鉴权、②tool_calls 原始格式拆解与多调用处理、③DeepSeek 主模型接入、④qwen-max 备用接入与降级/回切、⑤四种 role 消息构造与派生历史的 wire 转换、⑥SSE 解析聚合、⑦超时分层与幂等重试、⑧API 错误 → LLM-3xx 映射、⑨usage 计量与预算、⑩两模型配置示例。

全局不变量:①**单客户端换三元组**(ADR-007):模型差异=(base_url, api_key, model);降级=换三元组重发同一请求体;②**按码判定不匹配文本**(ADR-011);③**零猜原则**:非法 arguments JSON 回喂 TLB-803,不静默补全;④chunk 只上总线,完成聚合单条 llm.response(F027);错误文案不上行(ERR.md §5.1)。

# 1 OpenAI 兼容协议对接(协议地基)

对应 F012/F030。**为何选兼容协议**:DeepSeek 官方 API 与 dashscope compatible-mode 均提供 OpenAI 兼容端点,单客户端使流式/工具解析/用量只写一份,"加第三家=加配置行,llm 零改动"(ADR-007)。

## 1.1 端点、鉴权与客户端

请求线 `POST {base_url}/chat/completions`:SDK 只拼一次路径;base_url 统一为**裸域或 `/v1` 结尾**(禁写 /chat/completions),入配置去尾部 `/`。

| 项 | 规则 | 边界 |
|---|---|---|
| 鉴权 | `Authorization: Bearer <api_key>`,SDK 自动带 | 禁拼 URL |
| api_key | **只经 F016 单口**:`env:NAME`/`file:PATH` 引用 | 缺失→CRED-701,绝不空串(禁自行 os.environ) |
| 客户端 | AsyncOpenAI(base_url, api_key, timeout, max_retries=0) | **max_retries 必须 0**:SDK 隐式重试不落 llm.retry、不可取消、不参与降级 |
| 实例 | 同 base_url 多模型共用一个;换 base_url 必须换实例 | 降级=换实例重发同一请求体 |

```python
def build_client(triple: AdapterTriple, limits: TimeoutLimits) -> AsyncOpenAI:
    return AsyncOpenAI(base_url=triple.base_url,
        api_key=credential_store.resolve(triple.api_key_ref),   # F016,缺失→CRED-701
        timeout=httpx.Timeout(limits.connect_s, read=limits.first_token_s,
                              write=limits.connect_s, pool=limits.connect_s),
        max_retries=0)   # 重试由编排层显式做(F028);SDK 隐式重试不可审计
```

**边界**:base_url 配错(404)与模型名配错(404/400)都是配置错 → LLM-304 不重试,advice 指向 base_url/模型名(§7)。

## 1.2 请求结构(chat.completions.create)

| 字段 | 取值 | 规则 |
|---|---|---|
| model | 三元组 model | 主 `deepseek-chat` |
| messages | list[dict] | §4 四种 role;必填非空 |
| tools | list \| 省略 | schemas_for(scope)(F008);**无工具时整字段省略**(qwen 对 [] 语义不稳) |
| tool_choice | 默认 auto(省略) | 不强制指定 |
| temperature | 0-1.5,默认 0.7 | 越窗组装期拒(CFG-601) |
| max_tokens | 默认 4096(1-32768) | 单次输出上限 |
| stream | bool | §5 |
| stream_options | {include_usage:true} | 仅流式;末 chunk 带 usage |
| extra_body | dict 默认空 | 厂商扩展透传槽 |

## 1.3 响应结构(非流式)

| 字段 | 说明 |
|---|---|
| choices[0].message.content | str\|null;工具轮常为 null,归一 "";**null 且无 tool_calls=协议异常**,不冒充成功 |
| choices[0].message.tool_calls | list\|null;§1.4 拆解对象 |
| choices[0].finish_reason | stop/tool_calls/length;`tool_calls`=还有调用待执行;`length`=被 max_tokens 截断 |
| usage | §8;prompt/completion/total + 厂商缓存扩展 |
| 厂商扩展字段 | reasoning_content 等只进 raw,不进标准字段 |
| choices 空数组 | 内容过滤/异常 → 业务错(LLM-304 语义),不回空文本冒充成功 |

## 1.4 tool_calls 原始格式深度拆解(核心)

### 1.4.1 分层编码与 wire 形态

tool_calls 是 assistant 消息里的**数组**,每元素一次调用声明;协议是两层编码:HTTP 响应整体为 JSON,而 `function.arguments` 是**字符串内嵌的 JSON**——解析层必须先取字符串、再 `json.loads` 一次,才能得到可校验的参数 dict:

```json
{ "choices": [{
    "message": { "role": "assistant", "content": null, "tool_calls": [
      { "index": 0, "id": "call_9f2k1a", "type": "function",
        "function": { "name": "fs.read_file",
          "arguments": "{\"path\": \"C:/Users/LENOVO/Desktop\", \"offset\": 1}" } },
      { "index": 1, "id": "call_7xq3mz", "type": "function",
        "function": { "name": "workspace.list_dir",
          "arguments": "{\"path\": \"C:/Users/LENOVO/Desktop\"}" } } ] },
  "finish_reason": "tool_calls" } ],
  "usage": { "prompt_tokens": 421, "completion_tokens": 96, "total_tokens": 517 } }
```

### 1.4.2 单条 tool_call 字段表

| 字段 | 语义与规则 |
|---|---|
| index | 数组内序号;流式 delta 用其定位(§5.3) |
| id / tool_call_id | 调用唯一 id;回填 tool 消息原样带回(§4.3);缺失=协议异常 |
| type | 只处理 "function";其他类型不进管道,留 raw 审计 |
| function.name | 工具注册名(F008);未注册执行期→TLB-802 回喂 |
| function.arguments | **str,内嵌 JSON 字符串,不是 dict**;必须二次 json.loads |

### 1.4.3 arguments → JSON 解析(零猜原则)

```python
import json

class ToolCallSyntaxError(Exception):
    """arguments 非合法 JSON → 上层映射 TLB-803 回喂,零执行。"""

def parse_tool_calls(message) -> list[ToolCall]:
    """wire 层实现(DIS-CORE §4.3.1 调用的 parse_tool_calls 落地);按序返回。"""
    out = []
    for idx, tc in enumerate(message.tool_calls or []):
        if tc.type not in (None, "function"):
            continue                              # 非 function 不进管道,留 raw
        raw_json = tc.function.arguments or ""
        try:
            args = json.loads(raw_json) if raw_json.strip() else {}
        except json.JSONDecodeError as e:
            raise ToolCallSyntaxError(tc.id, e) from e     # → TLB-803 回喂
        if not isinstance(args, dict):
            raise ToolCallSyntaxError(tc.id, "arguments 非 JSON 对象")
        out.append(ToolCall(id=tc.id, index=idx, name=tc.function.name,
                            raw_args=args, raw_json=raw_json))  # 原文留审计(INV-06)
    return out
```

| 边界 | 判定与处置 |
|---|---|
| arguments 空/纯空白 | 视为 {} 进 F026;合法与否由 schema 定(必填缺失→TLB-803),解析层不猜 |
| JSON 语法错 / 非对象 | ToolCallSyntaxError → TLB-803 回喂明细,零执行;连败 2 次终止轮(F026) |
| finish_reason=length 且 arguments 残缺 | 截断症状(JSON 停在半途)→ 同语法错处置,提示减小单次参数体积 |
| 同响应两条 id 相同 | 协议异常 → 拒本轮(配对必乱),不落脏状态 |
| name 未注册 | 解析通过,执行期失败 → TLB-802 回喂自查(F022) |

### 1.4.4 多 tool_call 并行返回处理

模型一次返回 N 条 → **按数组顺序**(index 升序)进入执行管道(DIS-CORE §7.3.2);默认**串行**,`loop.tool_concurrency` 可配 2-3(F022)。**协议硬约束**:N 条调用必须有 N 条 role=tool 回填,全部回填后才能发下一轮请求——半截回填(缺 1 条)多数端点 422,或下一轮上下文错乱:

```python
def assistant_and_tool_messages(calls, results) -> list[dict]:
    """calls=上轮 N 条 ToolCall;results: call_id→tool.result payload。"""
    msgs = [{
        "role": "assistant", "content": None,
        "tool_calls": [{"id": c.id, "type": "function",
                        "function": {"name": c.name, "arguments": c.raw_json}}  # 原文回传
                       for c in calls]}]
    for c in calls:                              # 顺序=声明顺序
        r = results.get(c.id)                    # 每条必命中,否则 EVT-101 族拒写
        msgs.append({"role": "tool", "tool_call_id": c.id, "content": r.summary})
    return msgs                                  # spill 截断由 F039 先行
```

**边界**:call_id 无对应 tool.result → 轮状态不完整,拒绝以残缺上下文发下一轮(与 EVT-101"seq 不连续拒写"同原则);每条 result 独立落 tool.result 并带 trace.parent_seq 关联父 llm.response(F022),回放可按 call_id 复原。content 与 tool_calls 并存时(模型给说明文本又调工具):两路都保留、互不覆盖(§1.4.5 内容并入历史文本,调用照常执行)。

**关联**:DIS-CORE §4.3.1(编排)、§7.3.2(执行管道);ERR.md TLB-802/803;PRD §3.3 C 组(llm.response 事件不含 tool_calls,重建见 §4.2)。

# 2 DeepSeek 主模型接入

对应 F012;主适配器三元组与降级链第一个元素。

## 2.1 接入三元组

| 元 | 值(默认) | 说明 |
|---|---|---|
| base_url | `https://api.deepseek.com`(CFG 默认;`/v1` 为兼容别名) | 拼 `/chat/completions`(§1.1) |
| api_key | `env:DEEPSEEK_API_KEY`(F016 单口) | 缺失→CRED-701;主/备 key 独立(ERR.md §4-⑤) |
| model | `deepseek-chat`(DeepSeek-V3 系);`deepseek-reasoner` 可选 | 换模型=F030 注册,不改 llm 模块 |

响应为标准 OpenAI 形态,无字段级补丁;特有扩展一律进 `raw` 透传槽。

## 2.2 工具调用与输出特性

| 特性 | 状态 | 影响 |
|---|---|---|
| function calling | 原生支持 | F022 全链可用;arguments 按 §1.4 解析 |
| 多 tool_call 并行 | 支持一次多条 | 数组序=执行序(§1.4.4) |
| JSON 输出约束 | 无 `response_format` 式 schema 强制 | 结构性输出靠 F026 强校验兜底 |
| finish_reason=length | 截断 | §1.4.3 截断检测入口 |
| reasoning_content(reasoner) | 扩展字段 | 只进 raw,不进标准字段(透传槽) |

**边界**:工具轮 content 多为 null(归一 "",§1.3);无参工具可能给 `arguments: ""`,按 §1.4.3 视为 {} 进 F026,成败由 schema 定。

## 2.3 用量字段与上下文硬盘缓存

DeepSeek 兼容 OpenAI usage 三件套,另有缓存扩展字段(§8.3 计量口径):

| 字段 | 语义 |
|---|---|
| prompt_tokens | 输入总额(含命中+未命中) |
| completion_tokens / total_tokens | 输出额 / 合计 |
| prompt_cache_hit_tokens | 命中上下文硬盘缓存的部分(自动,无请求侧动作) |
| prompt_cache_miss_tokens | 未命中部分;hit+miss≈prompt_tokens |

**边界**:命中 token 按折扣价计费(官方约未命中的 1/10,以官方价目为准;折扣系数=适配器常量集中维护);命中率受派生历史截窗影响——前缀稳定才命中,compaction/截窗掉命中(§8.3)。

---

# 3 qwen-max 备用模型接入

对应 F013/ADR-007;降级链第二元素(CFG `llm.fallback_models: [qwen-max]`)。

## 3.1 接入三元组(同客户端换三元组)

| 元 | 值(适配器内置默认) | 说明 |
|---|---|---|
| base_url | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 百炼 compatible-mode;**必须 `/v1` 结尾**(与 DeepSeek 裸域不同,恰是配置级切换的价值) |
| api_key | `env:QWEN_API_KEY`(独立 key) | 与主 key 分离;经 F016 解析 |
| model | `qwen-max` | 更贵:单价表 CFG §3.1(qwen-max 4.0/12.0 vs deepseek-chat 2.0/8.0 元/百万) |

**边界**:备用适配器三元组默认值在 F030 注册表实例化时注入;`llm.*` 键权威在 CFG.md(仅主模型),备用模型若需覆盖 base_url/key 经注册表配置扩展点提供——**改配置不改代码**(ADR-007 验收口径)。**401 时备用模型用自己的 key 重发同一请求体,绝不继承主模型 key**(ERR.md §4-⑤)。

## 3.2 兼容差异点(解析层打补丁清单)

| 差异 | 表现 | 处置 |
|---|---|---|
| 协议形态 | 与 OpenAI 一致(消息/tools/SSE/usage) | 无需专用分支;补丁只发生在解析层(ADR-007 代价) |
| tools 传空 | 部分版本对 `tools: []` 语义不稳 | **无工具时整个字段省略**(§1.2 边界,两模型统一规则) |
| usage | 无 DeepSeek 式缓存字段 | 按全价估算(§8.3);缺失字段以 0 处理,不做厂商猜 |
| 工具轮 content | 可能给占位文本(如"好的,我来处理") | 视为 content 入历史;不阻断 tool_calls 执行(§1.4.5) |

## 3.3 降级触发条件(编排层 DIS-CORE §4.4 状态机的协议侧判据)

| 触发 | 错误码 | 处置(编排层) | 是否立即降级 |
|---|---|---|---|
| 认证失败(401/403) | LLM-302 | 不重试直接降级 | 是(F013) |
| 连续 N 次限流(429)达阈值 | LLM-303 | 退避后仍 429,达 `rate_limit_consecutive`(默认 2) | 是 |
| 网络不可达/断网/TLS | LLM-303 | 退避耗尽(4 次)后 | 是 |
| 超时(总闸) | LLM-301 | 退避耗尽后 | 是(DIS-CORE 4.3.3:301 进重试循环) |
| 业务性失败(400/404/422…) | LLM-304 | 不重试不降级,上抛回喂 | **否**——降级只救"上游坏",不救"我们错"(请求体/配置错降级也白降) |

## 3.4 切换粒度与留痕

- **请求级降级,而非请求内即时切换**:当前请求先在本适配器内退避重试(F028),耗尽才尝试下一适配器;降级结果(`chain.idx` 前移)对**后续请求**生效(DIS-CORE §4.3.2)。
- 每次实际降级落 `llm.request` 事件,payload 带 `model=qwen-max, degraded_from=deepseek-chat`(PRD §3.3 C 组);单会话降级次数 >5 告警(F013)。
- 降级不降安全:备用模型响应同过 F026 强校验 + guard 链 + 预算(F032)——DIS-CORE §4.6 边界 2。

## 3.5 回切策略(F033 探针)

| 项 | 规则 |
|---|---|
| 机制 | 周期(60s,CFG `llm.probe.interval_s`)对每个适配器 `ping()`(轻量延迟探测) |
| 降级判定 | 连续 3 败 → down(防瞬时抖动);down 者被 `pick()` 跳过 |
| 回切判定 | 连续 2 次健康 → healthy → 降级链自动回到主模型 |
| 探针副作用 | 不计 F029 用量;探针失败不落 llm.retry |

**边界**:回切不是立即发生(防乒乓);探针只判"网络/认证可达",不替代真实请求的错误映射——探针 healthy 但请求仍 401 时,按 §3.3 走正常降级路径。

# 4 请求组装与消息格式

## 4.1 四种 role 消息构造表

| role | 来源/构造 | 必填字段 | 规则与边界 |
|---|---|---|---|
| system | F010 模板 + 护栏段 F024(在 system-prompt 模块拼,llm 只透传) | content | **0..1 条置顶**;多来源合并为单条(见 DIS-CORE §5) |
| user | 派生历史 user.message;新输入经 agent.submit | content | 纯文本;附件/图片由外围能力(F061)转文本/路径引用并入 content,消息体不携多模态原始块 |
| assistant | 派生历史 llm.response | content 或 tool_calls | 纯文本轮 content 必填;工具轮 content 可 null + tool_calls(§1.4.4);可并存 |
| tool | 派生历史 tool.result 回填 | tool_call_id + content | 必须对应前一条 assistant 的某 tool_call id;content=result summary(spill 截断先行 F039);缺 id 多数端点直接 422 |

**边界**:`derive_history()`(PRD §3.5)是派生历史的唯一权威实现,但其输出是**给 UI/上下文窗口的简化视图**——assistant 工具轮缺 tool_calls 重建、tool 消息缺 tool_call_id。发往 wire 前必须做"wire 富化"(§4.2);富化是派生历史的投影、可整体重建,不构成第二份历史(原则 1)。

## 4.2 日志事件 → wire 消息映射(富化规则)

llm 模块按 `session.derive_history()` 的结果做 wire 富化,补回 wire 必需的关联字段。重建所需素材均已在日志中(事件即事实):

| 日志事件 | wire 消息 | 富化动作 |
|---|---|---|
| user.message | user | 直出 content |
| llm.response(content 非空) | assistant | 直出 content |
| llm.response(content 空) + 后续 tool.call 序列 | assistant(tool_calls) | 从该轮 tool.call 重建:name/raw_args 原文 JSON/id=call_id;多条按日志序 |
| tool.call 之后的 tool.result | tool | content=result summary;**tool_call_id = 对应 tool.call 的 call_id**(call_id join,F022 保证一一对应) |
| guard.rejected / approval 拒绝 | 不进上下文 | 只留审计流(PRD §3.5);被拒无 tool.result,也无 tool 消息 |
| context.compacted | 摘要文本 | system-prompt 模块组装期注入,llm 不感知(DIS-CORE §5) |
| user.message_edited | 覆盖对应 user 消息 | F063,按目标 seq 替换 |

**重建伪代码**(组装期,每轮一次;在 derive_history 结果上补关联):

```python
def enrich(derived, calls_by_round, results_by_call):
    """derived=PRD §3.5 派生结果;用日志素材补 tool_calls/tool_call_id。"""
    wire = []
    for m in derived:
        if m["role"] == "tool":                 # 派生视图缺 id → 按序补
            wire.append({"role": "tool", "tool_call_id": results_by_call[m["name"]],
                         "content": m["content"]})
        else:
            wire.append(m)
    return wire
```

**边界**:日志中间态(repair 截断 F060 后某轮只有 assistant 无 tool.result)→ 轮不完整,富化失败按 EVT-101 同族原则**拒绝发出残缺上下文**;声明 3 条调用只落 1 条 result 同样不发(§1.4.4 半截回填禁止)。

## 4.3 消息序列合法性约束(wire 硬规则)

| 规则 | 违反后果 |
|---|---|
| messages 非空,首条为 system(若有) | 空列表组装期拒;端点 400 |
| tool 消息紧跟其 assistant(tool_calls);N 条全回填后才允许下一条非 tool 消息 | 422;语义错乱 |
| 相邻同 role 文本消息合并为一条(DeepSeek 对连续同 role 敏感) | 400;合并防呆 |
| tool_call_id 不悬空/不重复 | 400/422;解析层已拒(§1.4.3) |
| 无 tools 时 tools/tool_choice 整字段省略 | `[]` 在 qwen 语义不稳(§3.2) |
| 内容截窗由 system-prompt 上游解决(F010);本层不截内容 | max_tokens 截断(text)非错误:记 finish_reason=length,agent-loop 决定续否 |

---

# 5 流式与非流式

对应 F027;编排骨架见 DIS-CORE §4.3.4(本层补 SSE wire 解析与增量 tool_calls 合并——编排层未展开的部分)。

## 5.1 取舍表

| 维度 | 非流式 | 流式 |
|---|---|---|
| 首字节延迟 | 全量完成才返回(最坏≈180s) | 逐 token,首 chunk 通常 <2s |
| usage 可得性 | 响应自带 | 末 chunk 才有(include_usage) |
| 工具轮 | 一次解析 | 增量 delta 需合并(§5.3) |
| 断流 | 无中间态 | 已渲染不可撤销;重试=整请求重发,UI 标"续写" |
| 降级兼容 | 直接 | 兼容:断流退避后整请求换适配器重发 |
| 默认选择 | 后台任务/离线管道(F051) | 交互会话(打字机,`--stream` 里程碑) |

## 5.2 SSE wire 格式与逐块解析

响应体为 `text/event-stream`:每事件两行(`data: {json}` + 空行),流结束发 `data: [DONE]`。chunk JSON 与 §1.3 同构,但 `message` 换成 `delta`(增量):

```text
data: {"choices":[{"delta":{"content":"你好"},"index":0}]}
data: {"choices":[{"delta":{"content":""},"finish_reason":"stop"},"index":0}]}
data: {"choices":[],"usage":{"prompt_tokens":9,"completion_tokens":3,"total_tokens":12}}
data: [DONE]
```

| chunk 元素 | 语义 |
|---|---|
| 首个 delta | 常只有 role,无 content(空 delta 合法,跳过) |
| delta.content | 增量文本,逐块追加;可能为 null |
| delta.tool_calls | 增量工具调用(§5.3),finish_reason 前逐片到达 |
| choices[0].finish_reason | 最后文本块携带;此后仍可能有 usage 块 |
| choices=[]+usage | 仅 include_usage=true;流的最后一个数据块,其后 [DONE] |
| [DONE] | 正常终止;未收到即断流 → F028 重试 |

## 5.3 增量聚合(含 tool_calls delta 合并)

流式 tool_calls 的关键差异:非流式是**完整数组**,流式是**按 index 分片到达**——id/name 只在该 index 的首块,`function.arguments` 是跨块追加的字符串片。逐块丢弃会导致 arguments 残缺(半截 JSON,§1.4.3 截断症状):

```python
async def accumulate_stream(stream, ctx):
    """编排层 chat_stream(DIS-CORE §4.3.4)的聚合器;返回与 chat 同型的完整结果。"""
    parts, slots, finish, usage = [], {}, None, None
    async for chunk in stream:
        if not chunk.choices:                  # include_usage 末块
            usage = chunk.usage
            continue
        d = chunk.choices[0].delta
        if d.content:
            parts.append(d.content)
        for dt in (d.tool_calls or []):        # 按 index 定位增量槽
            slot = slots.setdefault(dt.index, {"id": "", "name": "", "arguments": ""})
            if dt.id: slot["id"] = dt.id                       # 仅首块
            if dt.function and dt.function.name: slot["name"] = dt.function.name
            if dt.function and dt.function.arguments:          # 跨块追加,绝不覆盖
                slot["arguments"] += dt.function.arguments
        if chunk.choices[0].finish_reason:
            finish = chunk.choices[0].finish_reason
    calls = [ToolCall(id=s["id"], index=i, name=s["name"], raw_json=s["arguments"])
             for i, s in sorted(slots.items())]
    if calls:                                  # 增量拼完仍可能不完整 JSON
        parse_tool_calls({"tool_calls": calls})  # §1.4.3 统一校验,失败→TLB-803
    return "".join(parts), calls, finish, usage
```

**聚合一致性钉死**:逐 chunk 拼接 == 整段文本;`llm.chunk` 只上总线(不进日志),完成时聚合单条 `llm.response`(F027/DIS-CORE §4.3.4),一致性由 INV 比对断言。

## 5.4 流式中断与重试边界

| 情形 | 处置 |
|---|---|
| 流中 HTTP 断连/超时 | 已发 chunk 不可撤销;整请求重发(非断点续传),UI 标"续写";按 F028 退避 |
| 未收到 [DONE] | 同断流;空响应不上抛,按重试处理 |
| 用户取消 | F025:即停,不重试不降级;半截文本不当 llm.response 落日志 |
| usage 未达 | 该请求用量未知,不计 F029(§8.5);留 system.cancelled 痕迹 |
| 工具轮走流式 | delta.tool_calls 合并后与 §1.4.4 同路径,调用方无感知 |

# 6 超时与重试

对应 F017/F028;编排层退避循环见 DIS-CORE §4.3.3(引用,不重复),本层给超时接线与幂等边界。

## 6.1 三档超时语义与接线层

| 档 | 值(CFG llm.timeout.*) | 作用对象 | 接线 | 超时表现 → 码 |
|---|---|---|---|---|
| connect | 10s | TCP/TLS/连接池等待 | httpx.Timeout(connect/pool/write)(§1.1) | APITimeoutError → LLM-303 |
| first_token | 60s | 首字节(含排队/首 token 生成) | httpx.Timeout(read) | APITimeoutError → LLM-303 |
| total | 180s | 单请求总闸 | 编排层 wait_for(..., total) | asyncio.TimeoutError → LLM-301(DIS-CORE §4.3.1) |

**分层规则**:connect/first 档在 SDK 内(httpx),total 档在编排层,两者独立触发;最终都进 DIS-CORE §4.3.3 重试循环。配置校验强制 `total_s > first_token_s > connect_s`(CFG §5,违者 CFG-601)。

| 边界 | 规则 |
|---|---|
| 取消 vs 超时 | 用户取消=asyncio.CancelledError(F025),不重试不降级;超时走码路径;只取消当前任务不杀进程(F017) |
| 工具默认 60s | loop.step_timeout_s 墙钟,与本表无关(F017/F052) |

## 6.2 重试参数与幂等性

参数表(CFG `llm.retry.*`,编排实现 DIS-CORE §4.3.3):attempts=4、base_delay_s=1.0(×2 递增:1/2/4/8s)、jitter=±30%、只重试可重试码。

**幂等判定——为什么补全类请求可以重发**(编排循环调用 DIS-CORE §4.3.3):

```python
def retryable(code: str, attempt: int) -> bool:
    if code in ("LLM-301", "LLM-303"):      # 超时/429/5xx/断网:请求未达或未生效
        return attempt < 4                   # 上限 4,耗尽交降级链(F013)
    return False                             # LLM-302/304 永不重试
```

| 幂等维度 | 判定与处置 |
|---|---|
| 状态副作用 | chat.completions 是**无状态补全**,不写服务端状态 → 重发安全(与下单类接口本质不同) |
| 计费副作用 | 每次重发独立计费——**钱不幂等** → 重试上限 4+降级封顶,杜绝无限重发;usage 只计成功响应(§8.5) |
| 迟到成功 | 超时后服务端可能已完成 → 协议无幂等键标准;接受重复消耗,靠 usage/llm.retry 留痕 |
| 用户取消 | 意图已变 → 不进入重试(F025) |
| 工具调用请求 | 请求本身仍无副作用(副作用在工具端,F022 单次化)→ 只重试"请求未达/未返回",不重放执行 |

## 6.3 可重试矩阵(HTTP 视角)

| HTTP/故障 | SDK 异常 | 码 | 处置 |
|---|---|---|---|
| 401/403 | AuthenticationError/PermissionDeniedError | LLM-302 | 不重试,直接降级 |
| 429 | RateLimitError | LLM-303 | 退避≤4;连续达阈值后降级 |
| 408/409/5xx | APIStatusError/InternalServerError | LLM-303 | 退避≤4,耗尽降级 |
| 断网/DNS/TLS | APIConnectionError | LLM-303 | 同上 |
| SDK/httpx 超时 | APITimeoutError | LLM-303 | 同上 |
| 编排总闸超时 | asyncio.TimeoutError | LLM-301 | 退避≤4,耗尽降级 |
| 400/404/422/其他 4xx | BadRequest/NotFound/Unprocessable/APIStatusError | LLM-304 | 不重试不降级,上抛回喂 |
| 非 API 异常 | 任意 | — | 内部缺陷 → CYC-999;禁出口吞掉伪装 LLM 码 |

# 7 错误映射(API 错误 → LLM-3xx)

对应 F012 错误归一;权威码语义见 ERR.md §2.4/§3(不重复),本层给**完整异常类 × 状态码 → 内部码**的归一矩阵与实现。

## 7.1 归一原则

1. 只按异常类别/HTTP 状态判定(ADR-011),**绝不匹配厂商错误文本**;message 仅进本地日志。
2. ctx 只放脱敏现场(model/attempt);原文不上行(ERR.md §5.1);归一在适配器唯一出口内完成,无裸异常外泄(GWT-L4-01)。

## 7.2 归一实现(覆盖 DIS-CORE 未展开的细分异常)

```python
from openai import (AuthenticationError, PermissionDeniedError, RateLimitError,
                    APIConnectionError, APITimeoutError, InternalServerError,
                    BadRequestError, NotFoundError, UnprocessableEntityError,
                    APIStatusError, APIError)

def normalize_exc(e) -> str:              # 编排层 except 分支只做 raise_code(code)
    if isinstance(e, asyncio.TimeoutError): return "LLM-301"      # 编排总闸
    if isinstance(e, (AuthenticationError, PermissionDeniedError)):
        return "LLM-302"                                           # 401/403
    if isinstance(e, (RateLimitError, APIConnectionError,
                      APITimeoutError, InternalServerError)):
        return "LLM-303"                                           # 429/断网/超时/5xx
    if isinstance(e, (BadRequestError, NotFoundError,
                      UnprocessableEntityError, APIStatusError, APIError)):
        return "LLM-304"                                           # 4xx 业务/未知
    raise e                                  # 非 API 异常不上 LLM 码
```

| 情形 | 码 | 处置 |
|---|---|---|
| 401/403 | LLM-302 | 不重试直接降级;查 CRED-701/备用 key(ERR.md §4-⑤) |
| 429/5xx/断网/SDK 超时 | LLM-303 | 退避≤4 → 耗尽降级;可取消 |
| 编排总闸超时 | LLM-301 | 同上进重试循环 |
| 400/404/422/其他 | LLM-304 | 不重试不降级上抛;agent-loop 收 reason=error |
| 404 细分 | LLM-304+advice | base_url 拼错/模型名未注册(F030);422:请求体不合 schema(tools 定义/消息配对,§4.3)——均不重试,重试也白费 |
| 非 API 异常 | 不上码 | 内部缺陷(CYC-999);禁止伪装 LLM 码 |

**边界**:归一后仅 5 码(301/302/303/304/310)对外,编排/降级/终止只看码(ERR.md §5.2);同码不同 HTTP 状态靠 ctx(http_status)区分供排查,不改变决策。

# 8 token 计量

对应 F029/F032;用量上报函数 report_usage 见 DIS-CORE §4.3.4(引用),本层给 usage 字段语义与预算检查口径。

## 8.1 usage 字段语义表

| 字段 | DeepSeek | qwen-max | 计量用途 |
|---|---|---|---|
| prompt_tokens | 输入总额(含缓存命中+未命中) | 输入总额 | in_tokens(F029) |
| completion_tokens / total_tokens | 输出额/合计 | 同 | out_tokens(预算硬闸主指标,F032)/展示 |
| prompt_cache_hit_tokens | **有**(硬盘缓存,自动) | 无 | 缓存会计(§8.3),缺失按 0 |
| prompt_cache_miss_tokens | 有(hit+miss≈prompt_tokens) | 无 | 同上,缺失按 0 |

## 8.2 计量链路与审计

每成功请求:raw.usage → report_usage(DIS-CORE §4.3.4)→ 落 `llm.usage` 事件(model/in_tokens/out_tokens/cost_est)+计数器;计数器可从日志重建(事件即事实)。成本估算(只进事件/报表,不打预算硬闸——N14):

```python
def estimate_cost(model, usage, prices, hit_discount: float = 1.0) -> float:
    """元/百万单价表来自 CFG llm.usage.unit_price;仅报表/告警用。"""
    p = prices[model]                                    # {in_per_million, out_per_million}
    hit = getattr(usage, "prompt_cache_hit_tokens", 0) or 0
    miss = getattr(usage, "prompt_cache_miss_tokens", 0) or 0
    billed_in = miss + hit * hit_discount                # 命中按折扣价(§8.3)
    return billed_in / 1e6 * p["in_per_million"] + usage.completion_tokens / 1e6 * p["out_per_million"]
```

## 8.3 缓存会计

| 项 | 规则 |
|---|---|
| 命中判定 | DeepSeek 自动上下文缓存:prompt 前缀稳定即命中,无请求侧开关 |
| 折扣 | 命中 token 按折扣价计费(官方约为未命中 1/10,以官方价目为准);系数=适配器常量集中维护;CFG 单价表无折扣字段时按**全价估算(保守)** |
| 对计量影响 | hit 只影响 cost_est;**token 计数照算**——预算硬闸用 token 数(输出为主),不因命中打折 |
| 前缀敏感性 | 系统提示词/历史前缀稳定才命中;compaction/截窗掉命中;重试/降级重发同一请求体则前缀不变,命中友好 |

## 8.4 单任务预算检查口径

| 闸(CFG budget.task.*) | 默认 | 判定时机与状态 |
|---|---|---|
| max_in_tokens | 2,000,000 | agent-loop 每轮前;超出→exhausted 终态 |
| max_out_tokens | 50,000 | 每轮前累计 out:80% warn / 100% paused(暂停发审批)/ 超=终态 reason=budget(F032) |
| max_cost_yuan | 1.0 | 同上(估算值);后台 job 超预算直接杀死 |
| 单请求 max_tokens | 4096 | 请求参数——与任务累计预算是**两把尺**:单次输出帽+任务累计帽 |

**流式计量时机**:usage 只在流末块到达(include_usage,§5.2)——流式任务聚合完成才入账;中途取消则 usage 未知,不计 F029,留 system.cancelled 痕迹(§8.5)。

## 8.5 无 usage 场景兜底

| 场景 | 计量 |
|---|---|
| 超时/断流/取消 | usage 未知→不计 F029,不伪造 0 入账(留错误/取消痕迹) |
| 退避中间失败 | 各次失败无 usage;只计**成功那次**的响应 |
| 降级后成功 | 只记实际服务模型的 usage(事件 model=实际适配器) |
| 探针 ping | 不计 F029(F033 边界) |

# 9 配置示例(两模型完整接入片段)

权威键表见 CFG.md §3.1,完整模板见 CFG.md §8——此处只列两模型接入相关片段并标注对接层注意事项,两文件不一致时以 CFG.md 为准。

## 9.1 主模型 deepseek-chat(完整片段)

```yaml
llm:
  model: deepseek-chat              # 主模型(F012);401 自动降级
  base_url: https://api.deepseek.com   # 兼容端点;禁写 /chat/completions(§1.1)
  api_key: env:DEEPSEEK_API_KEY     # F016 秘密引用;缺失使用时→CRED-701
  temperature: 0.7                  # 0-1.5
  max_tokens: 4096                  # 单次输出上限;与任务累计预算两把尺(§8.4)
  timeout: {connect_s: 10, first_token_s: 60, total_s: 180}   # 三档(§6.1)
  retry: {attempts: 4, base_delay_s: 1.0, jitter: 0.3}        # 幂等重试(§6.2)
  degrade: {enabled: true, rate_limit_consecutive: 2, max_per_session: 5}
  probe: {interval_s: 60}           # 探针回切(F033/§3.5)
  usage:
    unit_price:                     # 元/百万 token;只影响估算,硬闸看 token(F032)
      deepseek-chat: {in_per_million: 2.0, out_per_million: 8.0}
      qwen-max:    {in_per_million: 4.0, out_per_million: 12.0}
```

## 9.2 备用模型 qwen-max(三元组 + 降级链)

`llm.fallback_models: [qwen-max]` 声明降级链(F013)。备用适配器三元组默认值在 F030 注册表实例化时注入(§3.1):base_url=`https://dashscope.aliyuncs.com/compatible-mode/v1`、api_key=`env:QWEN_API_KEY`、model=`qwen-max`——**与主模型同客户端逻辑,换三元组即切换(ADR-007);新增第三家模型=注册+配置,llm 模块零改动**。接入自检清单:

| 检查 | 失败症状 → 码 |
|---|---|
| base_url 可达且路径形态正确(裸域 vs /v1,§1.1) | 404 → LLM-304(advice:查 base_url) |
| env 变量已设且经引用(禁明文,F016) | 使用期 CRED-701;启动 tip:config show 只显引用 |
| 模型名在 F030 注册表 | 404/400 → LLM-304(advice:查模型名) |
| 时间档序 total>first>connect | CFG-601 启动中止 |
| 备用 key 独立(不暴露主通道) | 401 → LLM-302(查 CRED-701) |

## 9.3 env 汇总(秘密来源,B 机制,CFG.md §4.1)

| env | 用途 | 引用点 |
|---|---|---|
| `DEEPSEEK_API_KEY` | 主模型凭据 | `llm.api_key: env:DEEPSEEK_API_KEY` |
| `QWEN_API_KEY` | 备用模型凭据 | 备用适配器三元组(§3.1);主/备独立 |
| `PH_LLM_MODEL` / `PH_LLM_BASE_URL` / `PH_LLM_FALLBACK_MODELS` / `PH_LLM_TIMEOUT_*` / `PH_LLM_TEMPERATURE` / `PH_LLM_MAX_TOKENS` | 配置值覆盖(L3/L4) | CFG.md §4.3 映射表 |

**边界**:秘密值绝不以字面量入配置(CFG-601 reason=secret_literal 拒载);`config show`/错误回显只显示 `env:NAME` 引用;全出口脱敏(INV-09)。

---

**关联文档**:DIS-CORE.md §4(编排:chat/降级链/退避/流式/用量/状态机)、§7.3.2(执行管道);PRD-Core.md §5.2 F012/F013/F017/F022、§5.3 F027-F030/F032/F033;ERR.md §2.4 LLM-3xx、§5.2 模型失败链;CFG.md §3.1/§4/§8;ADD.md ADR-007/ADR-011;EVENT-SCHEMA.md(llm.* 事件字段权威,payload 要点见 PRD §3.3 C 组)。


---

## 10. 关联文档
| 文档 | 关系 |
|------|------|
| PRD-Core.md §5 | 模型功能规格权威 |
| DIS-CORE.md §4 | llm 模块伪代码 |
| ERR.md LLM-3xx | 模型域错误码 |
| CFG.md §3 | 配置项定义 |
