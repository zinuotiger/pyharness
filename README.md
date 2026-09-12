# PyHarness — DeepSeek Harness 的 Python 全功能复刻

> 一句话: 用 Python 复刻 DSH 全部架构思想(非源码翻译)的 Agent 框架——事件溯源会话 + 工具管道 + 自研插件总线,71 项清单按代码/入口落地,6 阶段开发,Windows 桌面程序形态;关键主链均有可重复真链探针。
> 状态: 核心主链与用户入口接地完成 — 1,400 passed / 2 skipped,事件词表 73 型(append-only 唯一真源)。CLI chat/run/plan/search/session/fork/schedule/job、ACP、jobs/schedule/subagent 编排、Web 与 PySide6 两套桌面壳均已接真实引擎。Web 和原生壳现在共享 `ApplicationService` 能力契约,两端均提供会话、消息编辑/重发/反馈、附件、权限档位、Jobs、定时任务、子 Agent、技能 Registry、插件、Workflow、审批/反问和审计;真实 LLM、审批执行、MCP stdio、Bing RSS 搜索、流式 chunk 探针均 PASS。MCP/Web 仍按外部配置与网络可用性启用。


## 2026-09-11 修复完成

> 本轮针对审计发现的“模块存在但入口未接线”问题做了收口,并补了可重复的真实探针:

| 修复 | 结果 |
|---|---|
| CLI chat/run 接真实 AgentLoop | `pyharness run "..."` / `chat` 不再 CYC-999 |
| plan/schedule/job/search/session/fork | 六类单发命令均有真实 handler |
| jobs/subagent/schedule/plan 编排 | engine spine 装配,jobs/subagent 有真实 runner 与持久化子会话 |
| ACP | `initialize → chat` 惰性接真实引擎、队列与 LLM |
| MCP | `scripts/probe_mcp_stdio.py`:真实 stdio `initialize → tools/list → tools/call` PASS |
| Web 搜索 | 默认 Bing RSS 无 key 后端;`scripts/probe_web_search.py` PASS |
| 流式 | 修复降级链吞掉 `chat_stream` 的缺陷;`scripts/probe_streaming_real.py` 收到真实 chunk |
| 持久化 | `llm.chunk` 等瞬时事件不再误进 JSONL 持久化订阅 |
| 桌面 UI | 新增 Jobs / 定时任务 / 子 Agent 三个管理面板,后端 REST 路由与测试同步接入 |
| 服务层 | 新增 `ApplicationService`,会话/引擎/队列/审批/反问/消息/Jobs/定时/子 Agent/技能/插件/权限预设/只读投影已从 FastAPI 壳抽离;桌面壳委托服务层 |

## 2026-09-12 双壳能力对齐

> 共享层新增 `pyharness/application/models.py`,并把双壳能力清单固化为 `SHELL_CAPABILITIES`;测试会同时检查服务层、Web 路由、Qt Controller 和两端 UI 入口。

| 功能 | Web 壳 | PySide6 原生壳 | 共享服务 |
|---|---|---|---|
| 会话 / 聊天 / 流式 | ✅ | ✅ | `ApplicationService` |
| 编辑 / 重发 / 点赞点踩标记 | ✅ | ✅ | ✅ |
| 附件 / 权限档位 | ✅ | ✅ `QFileDialog` + `QComboBox` | ✅ |
| Jobs / 定时 / 子 Agent | ✅ | ✅ | ✅ |
| Skill 本地管理与 Registry | ✅ 搜索/版本/安装/回滚/卸载 | ✅ 同能力 | `SkillInstaller` |
| 插件 / Workflow / 审计 | ✅ | ✅ | ✅ |
| 审批 / 反问 | Web 弹窗 | Qt 原生弹窗 | ✅ |
| 租户 / 模型 API Key | 设置页 | 设置页 | 租户密钥库 |

能力契约可运行时读取:Web `GET /api/capabilities`,原生 `NativeController.capabilities()`。

### 租户隔离与模型设置

- Web 顶栏 `⚙ 设置`、原生壳 `设置` 页都可以创建/切换租户并配置任意 OpenAI 兼容模型。
- 每个租户拥有独立会话目录、技能目录、插件根目录和模型档案；同名模型使用独立适配器，禁止复用其他租户的 Key。
- API Key 是只写字段：Windows 使用当前用户 DPAPI 加密，其他平台回退为 0600 文件；查询接口只返回 `has_api_key`，永不回显明文。
- Web 通过 `X-PyHarness-Tenant` 请求头隔离会话，SSE 通过 `tenant` 查询参数绑定租户。
- Web 左侧会话列表支持右键删除：右键会话后在鼠标位置出现“删除会话”，二次确认后清理主 JSONL、轮转段和备份文件；运行中或队列有待处理任务的会话拒绝删除。
- 模型配置修改后建议重启已有桌面进程；新启动的引擎按当前租户活动模型装配。
- 当前隔离范围是应用级逻辑租户；插件/MCP 的进程级配置仍由部署者管理，不应把它当作恶意本地用户的强安全边界。

## 2026-09-08 增量(砍掉项复活 + UI 波)

> 承接 9-07 基线,全量补齐 DSH 功能清单的"砍掉"项并接线,每项带单测锁证(全量回归 87% 绿):
>
> | 族 | 复活/新增项 | 落点 |
> |----|------------|------|
> | 工具 | **bash/代码执行/子进程工具**(exec.shell_run·python_run·proc.start/status/kill,审批门控 + cwd/env/超时/配额约束;**非 OS 级沙箱**) | core/proc.py + tool_exec.py |
> | 工具 | **MCP 客户端**(2024-11-05 协议子集,Inline/Stdio,工具桥 mcp.<name>.<tool>) | core/mcp.py |
> | 工具 | **消息编辑/重发**(经公开 append 追加 message_edited,保留 append-only 卖点) | core/message_edit.py |
> | 工具 | **workflow 顺序编排**(workflow.step 事件) | core/workflow.py |
> | 编排 | **LLM 自动标题 F042**(run 收尾命名→session.renamed,幂等) | core/auto_title.py + engine 钩子 |
> | 编排 | **agent 反问 #38**(user.question/answer 事件 + AskProvider + 桌面弹窗) | core/user_ask.py + tool_ask.py |
> | 插件 | **插件装载器 + HMR**(单文件插件 MANIFEST→register_tools,装载前清 pyc) | core/plugin_loader.py |
> | 系统 | **本地遥测/审计导出** | core/telemetry.py |
> | 计量 | 预算硬闸 F032(budget.paused)+ scope.updated 落盘 | llm.py + events |
> | 桌面 | 编辑/重发/反馈/反问/标题/附件/webhook API + 前端操作条与反问弹窗(node --check 通过) | pyharness/desktop/ + ui/index.html |
> | 真链 | scripts/probe_real_chain.py(真实 LLM 端到端;需 DEEPSEEK_API_KEY 或时代中转) | scripts/ |


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

安装桌面运行依赖:`uv sync --all-extras`,或用 `pip install ".[desktop]"`。

## PySide6 原生桌面(MVP)

```powershell
pip install -e ".[native]"
pyharness-native
```

原生版直接运行 `QApplication + qasync + ApplicationService`,不启动 FastAPI、uvicorn、SSE 或 WebView2。当前包含会话、聊天、流式草稿、消息编辑/重发/反馈、附件、权限档位、轨迹、Jobs、定时任务、子 Agent、技能 Registry、插件、Workflow 和审计面板。技能页支持 Registry 搜索、SHA-256 校验、quarantine 安装、卸载和版本回滚。Web 桌面仍可用:`pyharness-desktop`;两套壳共享同一业务服务和能力契约。

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
"我完整分析了 DeepSeek Harness(9,044 文件),然后用 Python 全功能复刻了它的架构——事件溯源会话日志、guard 单调拒绝的工具管道、能力 seam 三件套、自研插件总线,71 项功能分 6 阶段推进,产出 60 份文档 1.24MB(含 32 份编码规格/313 函数),每阶段可运行可演示,最终形态是双击即用的 Windows 桌面程序,带 Agent 干活轨迹回放。代码和全套规格文档都在 GitHub。"

## 关联
- 需求文档.md / 架构设计.md(早期草案,PRD-Core.md 为准)
- TECH-ANCHOR.md(根目录,技术锚定 + 变更记录)
- pyharness/(代码,按 specs/ 顺序实现中)
