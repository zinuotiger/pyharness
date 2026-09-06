# CONSTRAINTS-02 — LLM API 约束

> 类型: 硬约束 | 权威: PRD-Core.md §5 模型域 + ADI.md
> 适用: llm 模块全部实现

## 1. 模型接入
| # | 约束 | 说明 |
|---|------|------|
| L-01 | 主模型 DeepSeek | OpenAI 兼容格式,openai SDK,base_url=https://api.deepseek.com |
| L-02 | 备用模型 qwen-max | 同一客户端换 base_url+model 实现降级,禁止第二套客户端逻辑 |
| L-03 | API key 走环境变量 | 禁止硬编码;命名 DEEPSEEK_API_KEY / QWEN_API_KEY |

## 2. 调用纪律
| # | 约束 | 说明 |
|---|------|------|
| L-04 | 超时强制 | 连接超时与读超时分开设;LLM 调用无超时 = 不合格 |
| L-05 | 重试有界 | 指数退避 + 次数上限;只对幂等请求重试 |
| L-06 | 降级触发明确 | 触发: 超时/限流(429)/5xx;切换粒度=请求级;回切策略文档化 |
| L-07 | 参数先验后跑 | 模型输出参数必须 pydantic 校验后才执行工具 |
| L-08 | 工具调用解析健壮 | arguments 是字符串必须 JSON 解析;解析失败返回可重试错误给模型 |

## 3. 成本与计量
| # | 约束 | 说明 |
|---|------|------|
| L-09 | 单任务预算检查 | 超预算中止循环,返回用户"成本超限" |
| L-10 | 最大轮数上限 | 循环轮数 ≤ 配置值(默认 10),防死循环烧钱 |

## 4. 如果违反会发生什么
- 违反 L-04(无超时): 模型 API 卡住 → 线程/协程耗尽 → 整个 agent 假死
- 违反 L-07(不校验直接跑): 模型传 `delete_file(path="*")` 直接执行 → 数据丢失
- 违反 L-09(无预算): 死循环一次烧掉几百元 → 成本事故

## 5. 验收
- [ ] 所有 LLM 调用路径有超时参数
- [ ] 降级链测试: mock 主模型 5xx → 自动切备用成功
- [ ] tool_calls arguments 非法 JSON 时返回结构化错误(LLM-3xx)而非崩溃
- [ ] 单任务 token 预算检查在循环内生效

## 关联文档
| 文档 | 章节 | 关系 |
|------|------|------|
| ADI.md | §2/§3 | 主备模型接入细节 |
| ERR.md | LLM-3xx | 模型域错误码 |
| PRD-Core.md | §5 模型域 | 功能规格 |


---

## 6. 深度场景推演:如果违反 LLM 约束会怎样

### 场景 6.1:降级链没做,主模型 DeepSeek 故障(违反 L-06)
**根因**: "备用模型以后再说",先上线主模型。
**连锁后果**: ① DeepSeek 限流或宕机 → 整个 agent 不可用,用户以为产品死了;② 没有降级 = 没有 SLA,演示现场 API 抖动直接翻车(面试演示最怕这个);③ 事后补降级要改 llm 模块核心路径,不如一开始就留 seam。
**正确做法**: 第 1 阶段就实现降级链(成本 ~50 行),主备两个 base_url 走同一客户端;演示前先测一次主模型限流时的自动切换。

### 场景 6.2:tool_calls 的 arguments 不做容错解析(违反 L-08)
**根因**: "模型一般不会给坏 JSON"。
**连锁后果**: ① 模型偶尔输出截断的 arguments(长参数被 token 限制切断) → json.loads 抛异常 → 整个循环崩溃;② 崩溃发生在工具执行后 → 事件日志记了"已调用"但实际没执行,回放出现幽灵调用;③ 用户看到的是莫名其妙的报错。
**正确做法**: arguments 解析包 try/except,失败返回结构化错误(LLM-3xx, 可重试),把错误消息喂回模型让它修正——模型自纠错能力是 Agent 可靠性的关键一环。

### 场景 6.3:没有预算检查,死循环烧钱(违反 L-09)
**根因**: 开发时用免费额度没在意。
**连锁后果**: ① 某次 prompt 诱导模型无限循环调工具 → 每轮几千 token → 半小时烧掉几十上百元;② 用户(你自己)发现时已经超支;③ 面试被问"成本怎么控制"只能支吾。
**正确做法**: 循环每步检查累计 token/费用,超预算立刻中止并告知用户;单任务预算默认 ¥1,配置可调。

## 7. 降级链测试矩阵(必跑)
| 场景 | mock 行为 | 预期 |
|------|----------|------|
| 主模型 5xx | deepseek 返回 500 | 自动切 qwen,任务继续 |
| 主模型超时 | 读超时 > 阈值 | 切 qwen 重试一次 |
| 主模型限流 | 429 响应 | 指数退避后切 qwen |
| 双模型都挂 | 均 5xx | 返回 LLM-399 致命错误,任务终止不静默 |
| 恢复回切 | qwen 成功后主模型恢复 | 下个请求回主模型 |

## 8. 配置联动
本文件约束的阈值(超时/重试次数/预算)全部走 CFG.md 配置项,禁止硬编码在 llm 模块内。配置键: llm.timeout.connect / llm.timeout.read / llm.retry.max_attempts / llm.budget.per_task / llm.max_turns。


## 9. 模型输出校验链(零信任落地清单)
| 层 | 校验点 | 失败处理 | 错误码 |
|----|--------|---------|--------|
| 结构层 | 响应 JSON 格式 | 重试一次,仍败报错 | LLM-301 |
| 工具层 | tool_call 存在性 | 返回"无此工具"给模型 | TLB-801 |
| 参数层 | pydantic 校验 | 拦下,错误喂回模型修正 | TLB-802 |
| 危险层 | guard 单调检查 | deny/ask | GRD-4xx |
| 结果层 | 返回 schema 校验 | 标记异常 | TLB-803 |
| 成本层 | 累计 token 预算 | 中止循环 | LLM-305 |

## 10. 主备模型配置模板(与 CFG.md 联动)
```yaml
llm:
  primary:
    provider: deepseek
    base_url: https://api.deepseek.com
    model: deepseek-chat
    api_key_env: DEEPSEEK_API_KEY
  fallback:
    provider: qwen
    base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
    model: qwen-max
    api_key_env: QWEN_API_KEY
  timeout:
    connect: 10        # 秒
    read: 60           # 秒
  retry:
    max_attempts: 3
    backoff_base: 2    # 指数退避基数(秒)
  budget:
    per_task_cny: 1.0
    max_turns: 10
  fallback_trigger: [timeout, rate_limit_429, server_5xx]
```
