# FLC — 全链路优化清单(从已产出文档提取)

> 类型: 优化参考 | 来源: 主 Agent 从 PRD-Core/ADD/DIS/SECURITY/ERR 等文档提取,非凭空生成
> 使用: 代码 6 阶段每阶段门对照本清单查漏;优化点分级 P0(必须)/P1(应该)/P2(有余力)

## 提取方法与覆盖
扫描范围: PRD-Core.md(93 决策点)、ADD.md(64 决策 + 28 风险)、SECURITY.md(29 风险)、DIS-CORE.md、EVENT-SCHEMA.md、ERR.md、CFG.md、DEP 相关。
分类标准: 按 6 阶段归属;每条含【来源文档 → 优化点 → 级别】。

---

## 阶段 0 插件总线(来源: DIS-SEAM/ADD ADR-004)

| # | 优化点 | 级别 | 来源 |
|---|--------|:---:|------|
| FLC-001 | 事件分发三模式(sequential/waterfall/parallel)缺一不可——waterfall 是 guard 链基础 | P0 | DIS-SEAM §4 |
| FLC-002 | 插件装载顺序=依赖拓扑,卸载逆序 detach,禁止回溯 | P0 | DIS-SEAM §4 |
| FLC-003 | 错误隔离: 单插件崩溃不得拖垮总线(捕获+记录+继续) | P0 | DIS-SEAM §2 |
| FLC-004 | ctx 服务惰性加载(_LazyService),41 服务不全 eager | P1 | DIS-SEAM §3 |
| FLC-005 | 插件热插拔仅限开发期;生产装载顺序固定 | P2 | ADD ADR-004 |

## 阶段 1 核心脊柱(来源: PRD-Core/DIS-CORE/CONSTRAINTS)

| # | 优化点 | 级别 | 来源 |
|---|--------|:---:|------|
| FLC-101 | 会话事件日志 append-only 是灵魂:无 delete/update API,先写不变量测试 | P0 | PRD §3 |
| FLC-102 | 消息历史=日志派生(derive reducer 纯函数),禁止第二份历史 | P0 | PRD §4.1 |
| FLC-103 | guard 单调拒绝:只拒绝不放行;deny 不可被后续 allow 覆盖 | P0 | PRD §4.3 |
| FLC-104 | 参数 pydantic 先验后跑,校验失败=函数零调用(副作用断言) | P0 | PRD §4.4 |
| FLC-105 | 强同步三类事件即时 flush,普通事件攒批 | P0 | EVENT-SCHEMA §8 |
| FLC-106 | 原子写(临时文件+rename),断电不损坏已提交事件 | P0 | DIS-CORE §8 |
| FLC-107 | 循环三态机 idle/running + 轮数上限(默认10) | P0 | DIS-CORE §1 |
| FLC-108 | 降级链第 1 阶段就做(主 DeepSeek→备 qwen),不留到以后 | P0 | CONSTRAINTS-02 |
| FLC-109 | 崩溃恢复 repair:截断检测→修复或拒读,不静默 | P1 | DIS-CORE §8 |
| FLC-110 | 工具输出有界:截断/spill,防大输出进上下文 | P1 | CONSTRAINTS-03 T-09 |
| FLC-111 | 错误日志必含错误码,可 grep 追踪 | P1 | CONSTRAINTS-05 |

## 阶段 2 模型加厚(来源: ADI/DIS-CORE §4/CONSTRAINTS-02)

| # | 优化点 | 级别 | 来源 |
|---|--------|:---:|------|
| FLC-201 | tool_calls arguments 容错解析(坏 JSON→可重试错误喂回模型) | P0 | ADI §2 |
| FLC-202 | 超时分层(连接/读分开),无超时=不合格 | P0 | CONSTRAINTS-02 L-04 |
| FLC-203 | 重试有界+指数退避+只对幂等请求 | P0 | ADI §6 |
| FLC-204 | 单任务预算检查(BudgetGuard 挂循环) | P0 | CONSTRAINTS-07 |
| FLC-205 | token 计量(usage 解析+缓存会计) | P1 | ADI §8 |
| FLC-206 | 流式 SSE 逐块解析+BlockAssembler 折叠 | P1 | DIS-CORE §4 |
| FLC-207 | 模型分级路由(简单任务 chat,复杂推理强模型) | P2 | CONSTRAINTS-07 §8 |
| FLC-208 | 降级回切策略(备用成功后下请求回主) | P1 | CONSTRAINTS-02 §7 |

## 阶段 3 工具能力(来源: SECURITY/CONSTRAINTS-03/DIS-SEAM)

| # | 优化点 | 级别 | 来源 |
|---|--------|:---:|------|
| FLC-301 | 四关管道强制:契约→guard→执行→结果检查,禁止跳过 | P0 | CONSTRAINTS-03 T-01 |
| FLC-302 | 危险工具五级表(L0-L4),L3 审批,L4 硬拒 | P0 | CONSTRAINTS-03 §7 |
| FLC-303 | 输出脱敏(key/token 掩码)后才进日志与上下文 | P0 | CONSTRAINTS-03 T-10 |
| FLC-304 | 工具描述防注入:描述与执行同谓词 | P1 | CONSTRAINTS-03 T-07 |
| FLC-305 | spill 溢出落盘给指针,不整文件进上下文 | P1 | CONSTRAINTS-03 T-09 |
| FLC-306 | 审批 60s 超时=拒绝 + 会话级信任名单 | P1 | SECURITY §5 |

## 阶段 4 编排(来源: PRD §5 阶段4/ADD)

| # | 优化点 | 级别 | 来源 |
|---|--------|:---:|------|
| FLC-401 | 任务队列顺序执行,每任务独立日志段可单独回放 | P0 | PRD F043 |
| FLC-402 | 子 Agent 生命周期:enter→announce→detach,child-first 清理 | P1 | PRD F044 |
| FLC-403 | 子 Agent 上下文继承策略明确(继承父 or 隔离) | P1 | PRD F044 |
| FLC-404 | plan mode 先方案后执行(软指导,不硬限制) | P1 | PRD F049 |
| FLC-405 | goal 事件溯源(目标状态可回放) | P2 | PRD F048 |
| FLC-406 | jobs 后台任务进度可查+取消 | P2 | PRD F046 |

## 阶段 5 系统能力(来源: PRD §5 阶段5/EVENT-SCHEMA)

| # | 优化点 | 级别 | 来源 |
|---|--------|:---:|------|
| FLC-501 | compaction=append compacted 事件+遮蔽,物理日志仍只追加 | P0 | EVENT-SCHEMA |
| FLC-502 | SQLite FTS 只做查询索引副本,不动真源 JSONL | P0 | CONSTRAINTS-01 H-09 |
| FLC-503 | sandbox Windows 等价:命令白名单+文件影响域(非完整 ACL) | P1 | SECURITY §7 |
| FLC-504 | fork=复制事件流到新会话,边界校验 | P2 | PRD F063 |
| FLC-505 | 会话查询结果关联原事件 seq,可跳回上下文 | P2 | PRD F058 |

## 阶段 6 外壳 + 横切(来源: DEP/CFG/ERR)

| # | 优化点 | 级别 | 来源 |
|---|--------|:---:|------|
| FLC-601 | CLI /status(任务/轮数/成本)可查 | P1 | CONSTRAINTS-08 |
| FLC-602 | /cost 命令输出成本报告模板 | P1 | CONSTRAINTS-07 §9 |
| FLC-603 | 配置加载校验(类型/范围/未知键),失败指到配置行 | P0 | CFG §5 |
| FLC-604 | 凭据环境变量引用+轮换即时生效 | P0 | CFG §4 |
| FLC-605 | 配置热更新分级(哪些免重启) | P2 | CFG §6 |

---

## 使用方式
1. 每阶段编码前: 对照本阶段 P0 项逐条确认已进 specs/ 函数规格
2. 阶段门: P0 项全部实现+测试 = 阶段过;P1/P2 记录 backlog
3. 面试弹药: 挑 3 条讲深度(FLC-101 事件溯源/FLC-103 guard 单调/FLC-204 预算)——"我按全链路清单逐条落地,这是我从 DSH 分析里提取的优化清单"

## 关联文档
| 文档 | 关系 |
|------|------|
| PRD-Core.md | 优化点权威来源 |
| specs/(第2.11波) | 优化点落地为函数规格 |
| IMPACT-MATRIX.md(第3波) | P0 实现追踪 |
