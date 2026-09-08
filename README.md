# PyHarness — DeepSeek Harness 的 Python 全功能复刻

> 一句话: 用 Python 复刻 DSH 全部架构思想(非源码翻译)的 Agent 框架——事件溯源会话 + 工具管道 + 自研插件总线,66 项功能,6 阶段开发,Windows 桌面程序形态。
> 状态: 全部完成 — 32 模块 / 40 文件 / 21,500+ 行,1298 测试全绿 / 88% 覆盖率,桌面真链运行(DeepSeek 直连),PyInstaller 单 exe,轨迹时间线回放可演示。

## AI 编码地图(从哪开始读)

| 目的 | 读什么 |
|------|--------|
| 先懂全局 | MAP.md(架构总览图)→ PRD-Core.md §1-2 |
| 了解设计决策 | ADD.md(12 条 ADR,每条含"违反后果") |
| 写核心代码 | DIS-CORE.md(脊柱 8 模块伪代码)+ specs/(编码规格) |
| 写能力代码 | DIS-SEAM.md(seam 三件套+插件总线)+ specs/ |
| 事件格式 | EVENT-SCHEMA.md(信封/词表/JSONL) |
| 安全实现 | SECURITY.md(威胁模型/guard/审批)+ CONSTRAINTS-03 |
| 错误码 | ERR.md(全量目录+排查) |
| 配置 | CFG.md(全量配置项) |
| 跑起来 | DEP.md(6 阶段运行指南+面试演示脚本) |
| 运维 | OPS.md(故障排查)/ SOP.md(标准操作流程) |
| 优化查漏 | FLC.md(全链路优化清单) |
| 经验沉淀 | KEY-FINDINGS.md(坑位 14 条+面试讲法) |
| 质量记录 | IMPACT-MATRIX.md(矛盾扫描与修复) |
| 硬约束 | CONSTRAINTS-01~08(违反=不合格) |

## 文档导航(2026-09-06 全量刷新)

| 分组 | 文档 | 大小 |
|------|------|------|
| 需求 | PRD-Core.md | 123KB |
| 架构 | MAP.md / ADD.md | 24KB / 44KB |
| 设计 | DIS-CORE.md / DIS-SEAM.md | 72KB / 56KB |
| 协议 | EVENT-SCHEMA.md | 42KB |
| 安全 | SECURITY.md | 37KB |
| 集成 | ADI.md(LLM 对接) | 34KB |
| 工程 | ERR.md / CFG.md / DEP.md | 32KB / 28KB / 33KB |
| 运维 | OPS.md / SOP.md | 28KB / 22KB |
| 约束 ×8 | CONSTRAINTS-01~08 | 40KB |
| 优化 | FLC.md | 6KB |
| 沉淀 | KEY-FINDINGS.md / IMPACT-MATRIX.md | 17KB / 4KB |
| 编码规格 | specs/(33 份,313 函数)→ [索引](specs/README.md) | 596KB |
| 文档站 | docs_html/(56 页可视化) | 双击 index.html |

## 技术栈
Python 3.11 · pydantic · JSONL 事件溯源 · DeepSeek + qwen-max 降级 · pytest · asyncio · SQLite FTS(查询) · **pywebview 桌面壳 + FastAPI(外壳)**

## 核心架构(一句话版)
单进程插件架构: 自研插件总线(地基)→ 8 模块核心脊柱(agent-loop/session/tools/llm/system-prompt/scope/agent/persistence,不可换)→ 40+ 能力挂 ctx.*(可插拔)→ CLI/**Windows 桌面程序(pywebview)**/ACP 外壳。会话日志 append-only 是唯一真源,消息历史从日志派生。guard 单调拒绝,LLM 零信任。

## 开发路线(6 阶段,每阶段可运行)
| 阶段 | 内容 | 里程碑演示 |
|:---:|------|-----------|
| 0 | 插件总线+事件分发 | demo_bus 跑通 |
| 1 | 核心脊柱(循环/工具/日志/持久化/降级/guard) | 查天气+危险拦截 |
| 2 | 流式/重试/token 计量 | 流式输出 |
| 3 | 文件/Web 工具/spill/todo | 整理文件夹 |
| 4 | 任务队列/plan/子 Agent/jobs | 多任务指派 |
| 5 | 会话查询/compaction/fork/sandbox | 长会话压缩 |
| 6 | CLI 完整/**桌面程序(pywebview:对话+轨迹回放+审批)** | 双击 exe 桌面对话 |

## 面试叙事(30 秒版)
"我完整分析了 DeepSeek Harness(9,044 文件),然后用 Python 全功能复刻了它的架构——事件溯源会话日志、guard 单调拒绝的工具管道、能力 seam 三件套、自研插件总线,66 项功能分 6 阶段推进,产出 60 份文档 1.24MB(含 32 份编码规格/313 函数),每阶段可运行可演示,最终形态是双击即用的 Windows 桌面程序,带 Agent 干活轨迹回放。代码和全套规格文档都在 GitHub。"

## 关联
- 需求文档.md / 架构设计.md(早期草案,PRD-Core.md 为准)
- TECH-ANCHOR.md(根目录,技术锚定 + 变更记录)
- pyharness/(代码,按 specs/ 顺序实现中)
