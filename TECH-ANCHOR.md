# PyHarness(原 Mini-Harness)— TECH-ANCHOR & Pre-Dispatch Summary

> 项目: PyHarness(Mini-Harness 全功能版)
> 定位: 用 Python 复刻 DeepSeek Harness 全部功能的 Agent 框架——DSH 架构思想(非源码翻译),66 项功能 + 自研轻量插件总线,6 阶段路线
> 类型: 🔵技术引擎型(Agent 框架/引擎)
> 日期: 2026-09-06
> 用户需求原文: 想要 DSH 的架构不要源码;先需求文档→架构→代码;全功能实现(71 项里 5 项 TS 专属不做,其余全做);走完整 full-pipeline 流程(A 档纯核心文档,不要模拟数据附录)
> 项目别名: mini-harness(目录沿用)/ PyHarness(文档正式名)

---

## TECH-ANCHOR (子Agent强制遵守)

### 必用技术栈
| 层 | 技术 | 理由 |
|----|------|------|
| Language | Python 3.11 | 用户主栈 |
| 事件总线/插件 | **自研轻量**事件总线 + 插件注册表 | Cordis 的 Python 等价物,学习目的不引现成框架(预估 500-1000 行) |
| Agent 循环 | 自研(三态机 idle/running) | 不引 LangGraph/LangChain——本项目自己写循环内核 |
| LLM Primary | DeepSeek(OpenAI 兼容格式,openai SDK) | 用户已有 key,单任务 <1 元 |
| LLM Fallback | OpenAI 兼容备用模型(推荐 qwen-max,key 待用户确认) | 用户要求降级链 |
| 参数校验 | pydantic | 用户熟,等价 DSH schema DSL |
| 会话日志 | JSONL append-only + 派生消息历史 | 事件溯源核心,零依赖可回放 |
| 会话查询(第5阶段) | SQLite FTS5 | 旧会话全文搜索 |
| 并发(第4-5阶段) | asyncio(子Agent/jobs/schedule 需要) | 单进程内并发,不跨进程 |
| 配置 | 配置文件(分层:默认→用户覆盖) | 等价 DSH settings |
| 凭据 | 环境变量引用 | 不写死 key |
| 测试 | pytest + 不变量测试 | 用户测试出身 |
| 外壳(第6阶段) | **Windows 桌面程序:pywebview 壳 + FastAPI 同进程本地服务 + 轻量 Web 前端(原生 JS/Vue,非 React 44 包)**,PyInstaller 打包单 .exe 双击运行 | 用户拍板:要可视化桌面 APP 而非纯网页(2026-09-06) |
| 依赖管理 | uv/pip | 零配置 |
| License | MIT | 开源面试作品 |

### 禁止技术
| 技术 | 原因 |
|------|------|
| TypeScript / Node.js / npm / pnpm | 用 Python 复刻架构思想,不是翻译 TS 源码 |
| LangGraph / LangChain / CrewAI / 现成 Agent 框架 | 本项目自研循环与编排,现成框架 = 没有学习价值 |
| 现成插件框架(pluggy 等) | 插件总线必须自研(学习 Cordis 思想) |
| Docker / PostgreSQL / Redis / K8s | 单进程应用,SQLite+JSONL+文件足够 |
| React / 前端 44 包 / typert / Python SDK 自包装 | TS 专属或对 Python 无意义(71 项里的 67-70) |
| 自动生成的模拟数据附录 | 用户历史偏好:纯核心设计文档深度 |
| 跨进程微服务 | 单进程插件架构,不是微服务 |

### 架构原则(全文档一致,防子Agent跑偏)
1. **事件溯源**:会话事件日志(append-only)是唯一真源,消息历史从日志派生,绝不单独存第二份
2. **能力 seam**:可选能力走 Definition/Provider/Consumer 三件套(DSH 核心模式),核心脊柱不带可选能力
3. **guard 单调拒绝**:任何检查可拒绝,无任何机制可放行已被拒的调用——安全只能收紧
4. **LLM 零信任**:参数先验后跑(pydantic)、输出校验、超时强制
5. **核心脊柱 8 模块**:agent-loop / tools / session / llm / system-prompt / scope / agent / session-persistence——每次对话必经,不可换
6. **外围可插拔**:compaction/subagent/sandbox/web/fs/bash/jobs/schedule 等全部可选,挂 ctx.* 上

### 核心技术选型理由
- **事件溯源 + append-only**:可回放、可审计、AI 上下文单一来源——DSH session 思想,面试主战场
- **guard 单调拒绝**:防权限策略被钻空子——DSH tools 思想,面试金句
- **能力 seam 三件套**:契约(Definition)/实现(Provider)/消费(Consumer)分离,换实现不碰调用方——DSH 全局模式
- **自研插件总线**:理解"一切皆插件"的地基——但 Python 版按需简化(事件分发+注册表,不做 fiber/Proxy 魔法)

---

## 功能范围:66 项全实现(6 阶段)

| 阶段 | 功能域 | 对应编号(71项表) |
|:---:|------|------|
| 0 | 插件总线 + 事件分发 + 注册表(自研 Cordis 等价物) | 50,51,52(简化) |
| 1 | 核心脊柱:循环/工具管道/事件日志/提示词/持久化/降级链/guard/审批/超时/不变量测试 | 1,2,3,4,6,16,17,36,37,41,60,61,62,63,64 |
| 2 | 模型加厚:流式/重试/token计量/多适配器/运行时自检 | 18,19,20,22,42 |
| 3 | 能力层:文件读写/列目录/Web搜索/Web抓取/spill/todo/斜杠命令/标题 | 24,25,26,27,7,32,33,65 |
| 4 | 编排:任务队列/plan/goal/schedule/子Agent/workflow/jobs | 43,44,45,46,47,48,49 |
| 5 | 系统:subprocess/终端PTY/sandbox(Windows等价)/workspace/storage域/会话查询/compaction/fork | 5,8,9,10,14,53,58,59 |
| 6 | 会话周边+外壳:崩溃恢复/附件/反馈/编辑/重发/CLI完整/**Windows桌面程序(pywebview+FastAPI)**/ACP等价 | 11,12,13,15,54,55,56,57 |
| — | 不做(TS 专属/无意义) | 67,68,69,70(自研替代),71(用pytest) |

**文档描述目标 = 最终态(66 项全系统);代码实现按 6 阶段推进,每阶段可运行可演示。**

---

## 差异化矩阵

| 维度 | DSH(原版) | PyHarness(本项目) |
|------|-----------|---------------------|
| 语言/规模 | TS,250 npm 包,~19,300 行核心 | Python,预估 8,000-12,000 行(不含测试) |
| 插件系统 | Cordis 全量(fiber/Proxy/5种分发) | 自研轻量:事件分发+注册表(按需简化) |
| 会话 | 事件溯源+surface+compaction | 事件溯源核心+compaction(第5阶段简化版) |
| 并发 | Node 单线程事件循环 | asyncio |
| 前端 | React 44 包 | **pywebview 桌面窗口 + FastAPI 本地服务 + 轻量 Web 前端** |
| 受众 | 生产级框架 | 学习+面试作品+开源 |

| 维度 | LangGraph | PyHarness |
|------|-----------|-----------|
| 定位 | 编排框架(别人造) | 自写 Agent 全栈(循环/工具/日志/编排) |
| 面试叙事 | "我用过 LangGraph" | "我把 DSH 用 Python 全功能复刻了" |

---

## 分派计划 (v2.21: 一Agent一文件,每批≤2,timeout 1200s,model deepseek-chat)

### 第 2a 波 — Agent A (串行): PRD-Core
- 文件: PRD-Core.md(目标 100KB+,技术引擎型,66 功能全覆盖)
- 结构: 引擎定位(vs DSH/LangGraph 差异化)→ 架构正确性基准(6 大架构原则→不变量清单)→ 系统架构(脊柱+能力seam+插件总线)→ 6 阶段功能规格(每功能:功能/输入输出/边界/验收伪代码)→ 安全模型 → 事件协议(51类→Python 版事件词汇)→ v1.0 边界
- 体量说明: 66 项功能 × 每项验收伪代码,100KB 是自然体量;子Agent 写 100KB 有超时风险 → 若超时,主Agent 按 PRD-Core 扩容 playbook 接管补齐

### 第 2b 波并行(每批≤2,六批)
**Batch 1**:
| Agent | 文件 | 目标 |
|:---:|------|:---:|
| B1 | ADD.md(架构决策记录 ≥8 条 ADR:事件溯源/guard单调/能力seam/插件总线简化/自研vs现成框架/单进程/降级链/6阶段切分) | ~40KB |
| B2 | MAP.md(全架构 ASCII 图:地基+脊柱+能力层+外壳;6阶段↔模块↔文档映射) | ~20KB |

**Batch 2**:
| Agent | 文件 | 目标 |
|:---:|------|:---:|
| C1 | DIS-CORE.md(脊柱 8 模块伪代码级:agent-loop/session/tools/llm/system-prompt 每函数≥15行伪代码+状态机+错误路径) | ~60KB |
| C2 | DIS-SEAM.md(能力 seam 模式+插件总线:Definition/Provider/Consumer 三件套,事件分发,注册表) | ~50KB |

**Batch 3**:
| Agent | 文件 | 目标 |
|:---:|------|:---:|
| D1 | EVENT-SCHEMA.md(事件协议:Python 版事件词汇表/JSON 结构/字段表/校验/版本化) | ~35KB |
| D2 | SECURITY.md(安全模型:guard 单调/审批流/凭据/沙箱策略/威胁分析) | ~30KB |

**Batch 4**:
| Agent | 文件 | 目标 |
|:---:|------|:---:|
| E1 | ERR.md(错误码全量目录:llm/tool/param/session/plugin 五域+诊断流程) | ~30KB |
| E2 | CFG.md(配置全量:模型/温度/轮数/危险工具表/预算/权限预设) | ~20KB |

**Batch 5**:
| Agent | 文件 | 目标 |
|:---:|------|:---:|
| F1 | ADI.md(集成:LLM API 协议/tool_calls 原始格式拆解/降级链/备用模型接入) | ~30KB |
| F2 | DEP.md(部署运行:安装/首次运行/CLI 命令/6阶段里程碑运行指南/故障恢复) | ~30KB |

### 第 2.8 波 — 约束 + FLC + README (主Agent)
- CONSTRAINTS 系列 8 份(硬约束/LLM/安全/会话日志/编排/测试/成本/生产故障),每份 ≥5KB
- FLC 系列:从已产出文档 grep 提取(不凭空生成)
- README.md:AI 编码地图+导航表

### 第 2.9~2.11 波
- 模块化拆分(巨型单体→独立工程文档)
- specs/ 编码规格 ≥20 份(按 6 阶段模块:plugin_bus/agent_loop/tools_registry/tools_guard/session/session_persistence/llm_client/llm_fallback/llm_stream/prompt_assembler/tool_fs/tool_web/subagent/jobs/schedule/goal/workflow/sandbox/session_query/cli/web...),每份 ≥5KB,合计 ≥150 函数定义

### 第 3 波后处理
- 4 子Agent: OPS/SOP/KEY-FINDINGS/IMPACT-MATRIX(≥30 行实际 grep 扫描)
- 3.3 自动修复 → 3.5 README 刷新 → 3.7 代码骨架验证(可选)→ 3.8 文档站

---

## 预估产出

| 会话 | 产出 | 说明 |
|:---:|:---:|:---:|
| 会话 1 | 15-18 份 / 700KB-1MB | PRD-Core + ADD/MAP + DIS×2 + EVENT/ERR/CFG/ADI/DEP 首批 |
| 会话 2 | +20-30 份 | 约束系列 + FLC + specs/ 批量 + 拆分 |
| 会话 3 | +10-15 份 | 后处理 + README + 文档站 + 骨架验证 |
| 总计 | 45-55 份 / 1.2-1.8MB | 纯核心文档(技术引擎型自然体量,无模拟数据) |

---

## 关键风险
| 风险 | 缓解 |
|------|------|
| 子 Agent 超时(100KB PRD / 60KB DIS) | 一Agent一文件 + 1200s + 超时降级主Agent按扩容playbook补齐 |
| 子 Agent 跑偏(写成 TS 翻译/引入现成框架/加禁止技术) | 本锚禁止表+6架构原则内联每个 goal |
| 66 项规格浮于表面(体量大深度浅) | 架构门禁:每功能必须含验收伪代码+边界表;深度密度抽查 |
| PRD 100KB 单 Agent 写不完 | Agent A 若超时→主Agent 用 PRD-Core 扩容 playbook 接管(144KB 已有先例) |
| 文档描述"实现"但代码阶段没跟上 | 第 3.7 波骨架验证:specs 签名→import 验证;代码按 6 阶段独立推进 |
| 用户学习目标被文档淹没 | specs/ 每函数伪代码含设计决策;代码阶段用户逐行审查 |

---

## 第 1 会话目标
产出文档体系地基:PRD-Core(66 功能全规格)+ ADD/MAP + DIS-CORE/DIS-SEAM + EVENT-SCHEMA + ERR/CFG + ADI/DEP,共 15-18 份 / ≥700KB。用户确认后第 2 会话扩容(约束/FLC/specs),第 3 会话收尾(后处理/README/文档站/骨架验证)。


---

## 变更记录
| 日期 | 变更 | 影响 |
|------|------|------|
| 2026-09-06 | 形态升级:第6阶段外壳从"浏览器 Web 页面"改为 **Windows 桌面程序(pywebview 壳 + FastAPI 同进程 + 轻量前端)**,PyInstaller 单 exe 双击运行 | F065 重写;ADR-012 修订;MAP/DEP/README/CONSTRAINTS-01 同步;新增 Agent 轨迹时间线回放面板(面试主演示) |
