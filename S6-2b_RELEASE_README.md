# S6-2b Release Package — 不变量测试面（INV-01 ~ INV-05）

> **这是什么**：PyHarness「治理型 Agent 运行时」的 **S6-2b 阶段**（不变量测试面）交付物导览。
> **状态**：**已冻结**（Freeze 锚点 `a0c0917`）· HEAD `4ea6220` · 本包**只读展示**，不含任何代码改动。
> **唯一入口**：本文件 —— **5 分钟**读完即可判断"做了什么 / 值不值得深挖 / 怎么复跑"。

---

## 1. 一句话

**把 5 条架构不变量（INV-01~05）转成 31 条可执行断言，用"必然失败"口径逐条证伪，以 14 次 mutation 反证测试自身的鉴别力 —— 并在此过程中真的抓出并修复了一条 P0 生产违约。**

---

## 2. 四个核心数字

| 数字 | 含义 |
|---|---|
| **23 函数 / 31 用例** | 编号化不变量测试（[tests/invariants/test_inv_core.py](tests/invariants/test_inv_core.py)，1073 行） |
| **14 次** | 已执行的 mutation 反证次数（全部 RED 验证后**立即还原，无残留**） |
| **1702 passed / 0 failed** | 全量回归（1704 collected / 2 skipped）—— 本阶段每步均 **0 failed** |
| **+15 行** | 本阶段**生产代码净增**（仅 KF-A 修复的 2 个文件）；同期测试代码 +1073 行 |

---

## 3. 三个可深挖工程点

### ① 真的抓到了一条 P0 违约（不是纸面制度）

`auto_title` 越过 Agent Loop 直调 `llm.chat`，经 `engine.py:805-806` **生产接线** —— **违反 INV-02**。根因：规格指定的 `mini` 出口**在实现中缺失**，实现者退回到对话出口。

**完整链**：[S6-2_COVERAGE_MATRIX.md](S6-2_COVERAGE_MATRIX.md) §10（裁决）→ [S6-2a-P0-F_PLAN.md](S6-2a-P0-F_PLAN.md)（修复设计）→ [S6-2a-P0-F_CHANGE_REPORT.md](S6-2a-P0-F_CHANGE_REPORT.md)（实施，**+15 行**）→ [S6-2a-P0-F_FINAL_REVIEW.md](S6-2a-P0-F_FINAL_REVIEW.md)（独立复核）

### ② "类别式边界"而非"例外名单"

修复方案被刻意设计为**新增出口类别**（`chat`/`chat_stream` = 对话出口，唯一合法调用方 = agent-loop；`mini`/`summarize`/`json_chat` = 系统工具出口），**而不是给某个调用方开例外** ⇒ 可**静态校验**，且**后续新增系统工具无需改不变量**。

**证据**：[tests/invariants/test_inv_core.py](tests/invariants/test_inv_core.py) 的 `test_inv02_*`（3 条静态 + 2 条行为）

### ③ 判据自检 —— "测试的测试"

每个静态/字节级判据都配一条"**该判据真能识别违约**"的自检用例（在合成输入上验证**假阴/假阳**）⇒ 排除"判据写错但测试全绿"这类最隐蔽的假绿。

**证据**：同文件的 `test_inv01_append_only_predicate_detects_rewrite` · `test_inv01_write_scan_detects_rogue_writer`

---

## 4. 一条诚实记录

**本阶段公开登记并更正了自己的 3 处错误**（**留痕 + 加 Revision，而非静默改动**）：
① INV-05 的 G-3 误判（审计只查了 executor 侧）· ② INV-04 T-2 初版假阴性（executor 用**裸可调用**而非 `.handle(`）· ③ 总结的用例合计数（38 → 31）。

**证据**：[S6-2b-4_COVERAGE_AUDIT.md](S6-2b-4_COVERAGE_AUDIT.md) 文首 `Revision / Correction` · [S6-2b_RETROSPECTIVE.md](S6-2b_RETROSPECTIVE.md) §2.4

---

## 5. 复跑（一条命令）

```bash
.venv/Scripts/python.exe -m pytest tests/invariants -q --no-cov -p no:cacheprovider
```

**预期**：`48 passed`（本阶段 31 条 + 既有 17 条）。
**取权威计数**：加 `--collect-only -q` ⇒ `test_inv_core.py: 31`。

---

## 6. 三层阅读路径

| 层 | 时长 | 路径 |
|---|---|---|
| **L1** | 5 分钟 | **本文件** |
| **L2** | 20 分钟 | 本文件 → [S6-2b_FINAL_SUMMARY.md](S6-2b_FINAL_SUMMARY.md)（**结论**）→ [S6-2b_RETROSPECTIVE.md](S6-2b_RETROSPECTIVE.md)（**判断力**） |
| **L3** | 深挖 | [S6-2b_RELEASE_INDEX.md](S6-2b_RELEASE_INDEX.md) —— 按"面试追问"直达具体文档 |

---

## 7. 与普通 Agent Demo 的区别

普通 Agent Demo 交付的是"能跑通的对话 + 工具调用";这里交付的是**可被反证推翻的治理结论**。

1. **Event Log 是唯一事实源,不是运行结果的存档。** 会话 JSONL 只追加,消息历史 / 统计 / 索引全部由日志派生、可整体丢弃重建(INV-01、INV-03)。日志不是"事后打印",而是系统里唯一被信任的那份状态。
2. **拒绝 / Approval / Tool Execution 之间是有明确治理边界的。** 每次执行前必有携带同一 `call_id` 的 guard 求值事实;guard 决策恰三值且不存在 bypass 入口;任一次拒绝之后 Provider 调用计数必为 0(INV-04、INV-05)。"拦住了"和"没执行"都是**可证**的,而不是靠日志自述。
3. **治理规则靠可执行 Invariant + Mutation 反证,而不是功能测试。** 每条不变量配一条 1 行级专属 mutation,注入违约后测试**必须变红**;静态判据还要自证"真能识别违约"。功能测试回答"功能对不对",这套东西回答"结构上有没有可能绕过"。

---

## 8. 边界

- **本包只读**：不含代码改动；**唯一生产代码改动**是 `50c19ca`（KF-A 修复，+15 行）。
- **权威定义**以 [docs/INVARIANT_REGISTRY.md](docs/INVARIANT_REGISTRY.md) 为**唯一真源**。
- **仍开放事项**（KF-B / R-2 / O-1 / FC-8 等）**不在此重述** —— 见 [S6-2b_FINAL_SUMMARY.md](S6-2b_FINAL_SUMMARY.md) §7（**单一来源**）。
