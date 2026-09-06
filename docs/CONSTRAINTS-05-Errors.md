# CONSTRAINTS-05 — 错误处理约束

> 类型: 硬约束 | 权威: ERR.md + PRD-Core.md §6
> 适用: 全部模块,错误处理是工程级分水岭

## 1. 错误码体系
| # | 约束 | 说明 |
|---|------|------|
| E-01 | 全量错误码化 | 所有可预期失败必须走 ERR.md 目录中的错误码,禁止裸字符串错误 |
| E-02 | 域前缀分区 | EVT-1xx 事件/BUS-2xx 总线/LLM-3xx 模型/PERS-2xx 持久化/GRD-4xx guard/APR-5xx 审批/CFG-6xx 配置/CRED-7xx 凭据/TLB-8xx 工具/CON-9xx 通用 |
| E-03 | 分层传播 | 底层异常→包装(带码+上下文)→日志→模型可见→用户可见;每层转换有规则(见 ERR.md §4) |

## 2. 处理纪律
| # | 约束 | 说明 |
|---|------|------|
| E-04 | 每个 try 有 except | 禁止裸 except: pass 静默吞错;至少记日志 |
| E-05 | 每个 if 有 else | 禁止空 else;else 走默认分支或显式报错 |
| E-06 | 可重试 vs 致命分类 | 错误带 retryable 标记;可重试才进重试链,致命立即终止 |
| E-07 | 降级路径明确 | 主路径失败→降级(备用模型/缓存/默认值)→全挂才报错 |

## 3. 可观测性
| # | 约束 | 说明 |
|---|------|------|
| E-08 | 错误必进日志 | 所有 caught 异常至少 log 一行(错误码+上下文+堆栈) |
| E-09 | 日志脱敏 | key/token/PII 脱敏后才写日志 |
| E-10 | 用户消息友好 | 模型/用户看到的错误要可行动("重试/检查配置/联系"),非技术内部细节 |

## 4. 如果违反会发生什么
- 违反 E-01(裸字符串): 错误无法 grep、无法分类、无法自动化处理(重试/降级无从判断)
- 违反 E-04(静默吞): 最危险——系统悄悄做错事,用户和日志都不知道,问题潜伏到不可收拾
- 违反 E-07(无降级): 一个组件挂了全系统崩,没有"部分可用"的缓冲

## 5. 验收
- [ ] 代码 grep 无 `except: pass` / `except Exception: pass`
- [ ] 每个错误码在 ERR.md 有登记(反向扫描)
- [ ] 错误日志含错误码,可 grep 追踪
- [ ] 模型可见错误不含堆栈/内部路径

## 6. 深度场景:静默吞错
**根因**: 开发者怕报错打断流程,把不确定的调用全包 try/except 忽略。
**连锁后果**: 持久化失败被吞 → 用户以为会话存了,重启全丢;工具执行失败被吞 → AI 以为工具成功了,基于错误结果继续推理,产出垃圾答案;审计时无从查起(没日志)。**静默吞错 = 把确定性故障变成随机幽灵**。
**正确做法**: except 里至少 log;不确定如何处理就重新抛出或转致命错误;能降级就显式降级并记录降级事件。

## 关联文档
| 文档 | 章节 | 关系 |
|------|------|------|
| ERR.md | 全篇 | 错误码权威目录 |
| LOG.md(第2.9波) | 格式规范 | 日志格式 |


## 7. 错误传播链详解(每层转换规则)
```
底层异常(requests.Timeout / json.JSONDecodeError / FileNotFoundError)
   │ ① 捕获点: 模块边界统一捕获
   ▼
PyHarnessError(code=LLM-301, message=中文可行动描述, retryable=true,
               cause=原始异常, context={module, seq, call_id})
   │ ② 日志层: 记 ERROR [LLM-301] 上下文(脱敏后)
   ▼
模型可见层(喂回给模型的消息): "工具 get_weather 调用失败: 参数格式错误(LLM-301)。
   请修正参数后重试。" —— 不给堆栈,给修正指引
   │ ③ 若连续失败超过阈值
   ▼
用户可见层: "模型服务暂时不可用(LLM-399),请稍后重试或检查 API 配置。"
```

### 层间转换铁律
| 层 | 内容 | 禁止 |
|----|------|------|
| 底层→包装 | 保留 cause 链,映射到错误码 | 吞掉原始异常 |
| 包装→日志 | 错误码+模块+上下文+堆栈 | 记录密钥/完整 prompt |
| 日志→模型 | 结构化错误摘要+修正指引 | 堆栈/内部路径/文件绝对路径 |
| 模型→用户 | 最终状态+下一步动作 | 错误码细节(用户不需要) |

## 8. 高频错误码排查伪代码(ERR.md 配套速查)
```python
def diagnose(err_code: str):
    if err_code == "TLB-802":   # 参数校验失败
        # 1) 看日志里模型原始 arguments
        # 2) 对照工具 schema 找差异字段
        # 3) 是模型持续给错?→ 工具 description 不够明确, 改进描述
    elif err_code == "LLM-301":  # 响应解析失败
        # 1) 原始响应是否被截断(长输出)?
        # 2) 是否 model 不支持 tool_calls?→ 换模型
    elif err_code == "GRD-401":  # 危险操作被拒
        # 1) 正常现象: 模型试图做危险操作被拦(面试演示点)
        # 2) 若误伤: 调整危险分级表, 降级为 ask
```


## 9. 错误处理代码模板(实现参照)
```python
from pyharness.errors import PyHarnessError, ErrorCode

def safe_tool_call(name: str, args: dict) -> dict:
    try:
        return executor.execute(name, args)
    except PyHarnessError as e:
        # 已知错误: 记录 + 转模型可见
        logger.error(f"[{e.code}] {e.message}", extra={"module": "executor"})
        return {"ok": False, "error": e.to_model_message()}
    except Exception as e:  # 未知错误: 包装成致命, 不静默
        logger.exception("未知工具错误")
        raise PyHarnessError(ErrorCode.TLB_899, "工具执行未知错误", cause=e) from e
```
要点: 已知错误转模型可修正消息;未知错误包装上升;绝不裸 except 吞掉。
