# PARAMETER-ANCHOR — 参数锚定(全库数字唯一真源)

> 类型: 参数锚定(第 2a.5 波) | 来源: PRD-Core.md(唯一权威)+ TECH-ANCHOR.md
> 用途: 全库关键数字锁死,子 Agent/编码 Agent 必须使用精确值,禁止"约/30+/50+"式模糊或自创
> 更新规则: 参数变更必须同步 PRD-Core + 本文档 + 受影响文档三处;本文档是交付前审计的核对基准

## 核心数字(锁死)

| 参数 | 锁定值 | 来源 | 禁止写法 |
|------|--------|------|---------|
| 功能项总数 | 66(F001-F066) | PRD §5.0 | "60+"/"约66" |
| 开发阶段 | 6(0-6) | PRD §4.6 | "几个阶段" |
| 核心脊柱模块 | 8(agent-loop/agent/session/llm/system_prompt/scope/tools/persistence) | PRD §2.2 | 漏/多模块 |
| 架构原则 | 6 条 | PRD §4 | "几条原则" |
| 文档总量 | 60 份 md / 1.24MB(2026-09-06) | 实测 | 过期数字 |
| specs 编码规格 | 33 份(32 模块+索引)/ 313 函数 | 实测 | "20+" |

## 配置默认值(锁死)

| 参数 | 锁定值 | 来源 | 说明 |
|------|--------|------|------|
| 任务轮数上限 max_turns | 默认 30 | PRD F007/F032(§5.2) | 超限终态 reason=max_turns;测试用 mock 小值 |
| LLM 三档超时 | 连接 10s / 首 token 60s / 总 180s | PRD F017/F025 | 三层分别设 |
| 审批 TTL | 120s,超时=denied | PRD F015 | 安全默认拒 |
| 任务预算上限 | 默认 ¥1/任务 | PRD F032 | 超限终态 reason=budget |
| 背压阈值 | 默认 1000 在途 | PRD F005 | 可配,丢事件必须可观测 |
| 工具默认超时 | 60s | PRD F017 | 超时 TLB-805 |
| 写文件上限 | 1MB(schema maxLength) | PRD F035 | 超限 TLB-803 零执行 |
| spill 单文件上限 | 10MB | PRD F041 | 超限 PERS-223 |
| 子 Agent 并发 | ≤8 | PRD F049 | 预算 1/4 继承 |
| jobs 并发 | ≤4 | PRD F051 | JOB-001 |
| compaction 触发 | 派生≥75% 窗口 且 新增≥10 轮 | PRD F058 | 保留最近 12 轮 |
| 工具输出截断 | 64KB → spill | PRD F039 | 防上下文爆炸 |
| 会话 FTS 索引 | SQLite FTS5,中文 2-gram | PRD F057 | 只做派生副本 |
| 错误码 | 41 条(ERR.md §2 全量) | ERR.md | 禁表外码 |

## 命名与标识(锁死)

| 项 | 锁定值 | 说明 |
|----|--------|------|
| 包名 | pyharness | import pyharness |
| 桌面入口 | pyharness-desktop | PyInstaller exe |
| CLI 命令 | pyharness <子命令>(14 个) | chat/run/plan/schedule/job/search/session/fork/repair/desktop/acp/config/budget/stats |
| 会话日志目录 | ~/.pyharness/sessions/*.jsonl | DEP §7 |
| 配置文件 | ~/.pyharness/config.yaml | CFG §2 |
| env 前缀 | PH_ | 白名单 CFG §4.3 |
| 桌面形态 | pywebview 壳 + FastAPI 同进程(127.0.0.1 随机端口) | ADR-012(2026-09-06 修订) |

## 关联文档
| 文档 | 关系 |
|------|------|
| PRD-Core.md | 数字唯一权威来源 |
| TECH-ANCHOR.md | 技术栈锚定 |
| ERR.md | 错误码 41 条登记册 |
| SPECS-README | specs 索引 |
