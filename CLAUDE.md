# CLAUDE.md — AI 协作入口

本文件告诉 Claude Code **从哪读、按什么流程做、何时算完成**。它不复制协议内容，只做导航与纪律声明。

## 1. 项目入口（读取优先级）

做任何受约束的任务前，按顺序读取：

1. `.ai-coding/PROTOCOL.md` —— AI Coding 执行协议（必读）
2. 当前 `TASK`（见 `.ai-coding/TASK-TEMPLATE.md`）
3. 与任务相关的 `docs/specs/*.md`
4. 相关 `docs/CONSTRAINTS-*.md`（硬约束）
5. `docs/ADD.md`（架构/决策）
6. `docs/KEY-FINDINGS.md`（经验/坑位）

> 其他按需：`docs/MAP.md`（架构总览）、`docs/EVENT-SCHEMA.md`（事件）、`docs/SECURITY.md`、`docs/ERR.md`、`docs/CFG.md`、`TECH-ANCHOR.md`、`CODE-MATRIX.md`。

## 2. 默认开发流程

任何受 Protocol 约束的任务，固定走：

`LOAD → UNDERSTAND → IMPACT → PLAN → IMPLEMENT → VERIFY → REPORT`

**未通过 GATE-01（READY）不得开始大规模修改。** 详细 Gate 定义见 `.ai-coding/PROTOCOL.md` §7。

## 3. 文档职责

- `docs/` = 项目**规格、架构、知识与约束**（PRD / ADD / CONSTRAINTS / SPECS / KEY-FINDINGS / CHANGELOG）。
- `.ai-coding/` = **AI Coding 执行协议**（PROTOCOL / TASK-TEMPLATE / evidence）。
- 两者**不重复**：协议只说"怎么执行"，不承载项目知识。

## 4. 开发要求

开始编码前必须完成：

- **任务理解**（目标 + 验收口径）
- **架构影响分析**（模块/接口/状态/数据流/并发面）
- **边界分析**（不做什么）
- **约束分析**（引用 `docs/CONSTRAINTS-*`，冲突以更严者为准）
- **契约核对**（行为变化前核对既有测试/规格/ADR 是否已锁定该行为；无变更依据不得实施）— 见 `.ai-coding/PROTOCOL.md` §7 GATE-01
- **测试计划**（含触发的 CONDITIONAL Required Test；回归深度按影响面分档，见 §5 CORE-02）

标出需人确认的决策点；不明确即提问，不猜测实现。

## 5. 完成要求

**没有实际验证证据，不得声称完成。** 必须区分并如实标注：

- `IMPLEMENTED` —— 代码已写
- `TESTED` —— 相关测试已跑
- `VERIFIED` —— 验证路径已执行且通过
- `DONE` —— 通过 GATE-04（含收敛与证据）

收尾按 `.ai-coding/PROTOCOL.md` §11 报告格式输出。
