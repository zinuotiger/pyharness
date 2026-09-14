# TASK-TEMPLATE

> 新任务入口模板。受 AI Coding Protocol v0.1（`.ai-coding/PROTOCOL.md`）约束。
> 复制本模板填写后开始；未通过 GATE-01（READY）不得开始大规模修改。

---

## Task ID
（唯一标识，如 `TASK-2026-09-13-01`）

## Objective
（一句话说明要达成什么；可用"动词 + 对象 + 目的"描述）

## Expected Result
（可验证的终态：行为/接口/数据/输出应是什么样）

## Source Specifications
（权威来源，按优先级；示例：`docs/specs/<module>.md`、`docs/CONSTRAINTS-*`、`docs/ADD.md`、`docs/EVENT-SCHEMA.md`、相关测试）

## Affected Components
（预计波及的模块/文件/接口；不确知则写"待 IMPACT 阶段确认"）

## Architecture Impact
（是否改变模块边界、依赖方向、状态机、数据流、并发面；无则写"无"）

## Boundaries
（**不**做的事 / 不触碰的范围；防范围蔓延）

## Constraints
（硬约束：来自 `docs/CONSTRAINTS-*` 或平台/安全/性能要求；冲突时以更严者为准）

## Failure Cases
（预期失败路径及期望行为：拒绝、回滚、降级、不变量保持等）

## Acceptance Criteria
（逐条可判定的验收项；建议编号 AC-1、AC-2…）

## Test Requirements
（必要测试：单元/集成/对抗/端到端；标注触发的 CONDITIONAL 规则——CND-01~07）

## Evidence Requirements
（收尾须提交的证据：测试输出、程序行为、截图/控制台、对账结果等，对应 §10）

---

### 执行记录（实现过程中填写）

- **Triggered Conditional Rules**：（CND-01~07 中命中的）
- **Lifecycle State**：PLANNED / READY / ANALYZING / PLANNING / IMPLEMENTING / TESTING / VERIFYING / CONVERGING / VERIFIED / DONE / PARTIAL / BLOCKED
- **Gate Status**：GATE-01 / GATE-02 / GATE-03 / GATE-04 是否通过
- **Deviations**：（与计划的偏离及原因）
- **Known Limitations**：（已知限制）
- **Remaining Work**：（未完成项 / 后续任务）

---

### 收尾报告（按 PROTOCOL §11）

`STATUS` · `SUMMARY` · `FILES CHANGED` · `ARCHITECTURE IMPACT` · `BOUNDARIES` · `CONSTRAINTS` · `FAILURES` · `TESTS` · `TEST RESULTS` · `EVIDENCE` · `CONVERGENCE` · `KNOWN LIMITATIONS` · `REMAINING WORK`
