# S6-2b_RELEASE_INDEX.md — 资产索引（映射与导航）

> **本文件只做映射与导航，不重述任何文档内容。**
> **阶段**：S6-2b（INV-01 ~ INV-05）｜ **Freeze 锚点** `a0c0917` ｜ **HEAD** `4ea6220`
> **权威定义**：[docs/INVARIANT_REGISTRY.md](docs/INVARIANT_REGISTRY.md)（**唯一真源**）

---

## 1. 阅读顺序

### 1.1 主读序（推荐，单向递进）

| 步 | 文件 | 作用 |
|---:|---|---|
| 1 | [S6-2b_RELEASE_README.md](S6-2b_RELEASE_README.md) | 5 分钟入口 |
| 2 | [S6-2b_FINAL_SUMMARY.md](S6-2b_FINAL_SUMMARY.md) | 阶段终态与结论 |
| 3 | [S6-2b_RETROSPECTIVE.md](S6-2b_RETROSPECTIVE.md) | 方法 / 价值 / 风险 / 亮点 |
| 4 | [docs/INVARIANT_REGISTRY.md](docs/INVARIANT_REGISTRY.md) | Canonical 定义（唯一真源） |

### 1.2 前置依赖（**不属于本阶段**，但必须先读）

| 文件 | 说明 |
|---|---|
| [S6-1_INV_CLASSIFICATION.md](S6-1_INV_CLASSIFICATION.md) | INV 编号的**4 个语义族**清理与最终裁决（**S6-1**，早于本阶段；§12 = 裁决区） |
| [docs/INVARIANT_REGISTRY.md](docs/INVARIANT_REGISTRY.md) | 由 S6-1 建立（commit `4f05c95`），本阶段的**语义唯一来源** |

> ⚠️ 这两份**不在** S6-2b 的 13 个 commit 内 —— 阅读时勿误并入本阶段产出。

---

## 2. 文档职责映射（含 INV 与 commit）

| 层 | 文件 | 职责（回答什么问题） | 对应 INV | 引入 commit |
|---|---|---|---|---|
| **入口** | `S6-2b_RELEASE_README.md` | 这阶段做了什么？值得看吗？怎么复跑？ | 01~05 | 本包新增 |
| **索引** | `S6-2b_RELEASE_INDEX.md` | 各文件是什么？按什么顺序读？ | 01~05 | 本包新增 |
| **结论** | `S6-2b_FINAL_SUMMARY.md` | 终态：状态矩阵 / KF / 测试资产 / 冻结面 / **残余（§7 = 单一来源）** | 01~05 | `4a41068` · `a0c0917` |
| **价值** | `S6-2b_RETROSPECTIVE.md` | 设计意图 / 方法论 / 过度工程风险 / 作品集亮点 | 01~05 | `4ea6220` |
| **裁决** | `S6-1_INV_CLASSIFICATION.md` | 编号为什么改？语义族与最终裁决 | 01~09 编号 | S6-1 |
| **权威** | `docs/INVARIANT_REGISTRY.md` | Canonical 定义（**唯一真源**）+ Legacy Mapping | 01~09 | `4f05c95` · `5cd66c9` |
| **覆盖矩阵** | `S6-2_COVERAGE_MATRIX.md` | 九条 INV 的缺口盘点 + P0 裁决（§10）与修复设计（§11） | 01~09 | `142765e` |
| **P0 专线** | `S6-2a-P0_CHANGE_REPORT.md` | KF-A 发现与冻结 | 02 | `142765e` |
| | `S6-2a-P0-F_PLAN.md` | KF-A 修复设计（Option A 比较与推荐） | 02 | `50c19ca` |
| | `S6-2a-P0-F_CHANGE_REPORT.md` | KF-A 实施（+15 行）+ 反证 | 02 | `50c19ca` |
| | `S6-2a-P0-F_FINAL_REVIEW.md` | KF-A 独立复核 | 02 | `c5fa851` |
| **审计层** | `S6-2b-2_COVERAGE_AUDIT.md` | INV-03 逐子性质审计 + 实现审计 | 03 | `c44c9ce` |
| | `S6-2b-3_COVERAGE_AUDIT.md` | INV-04 逐子性质审计 + F-1 裁定 | 04 | `1a36eef` |
| | `S6-2b-4_COVERAGE_AUDIT.md` | INV-05 逐子性质审计 + F-2 裁定 + R-1 更正 | 05 | `4f74f93` |
| **实施层** | `S6-2b-1_CHANGE_REPORT.md` | INV-01 实施与 mutation | 01 | `9349596` |
| | `S6-2b-2_CHANGE_REPORT.md` | INV-03 实施与 mutation | 03 | `c44c9ce` |
| | `S6-2b-3_CHANGE_REPORT.md` | INV-04 实施与 mutation | 04 | `1a36eef` |
| | `S6-2b-4_CHANGE_REPORT.md` | INV-05 实施与 mutation + **O-1 Technical Debt（§9）** | 05 | `4f74f93` |
| **审查层** | `S6-2b-1_FINAL_REVIEW.md` | INV-01 独立复核 + 残余 | 01 | `9349596` |
| | `S6-2b-2_FINAL_REVIEW.md` | INV-03 独立复核 + 残余 | 03 | `c44c9ce` |
| | `S6-2b-3_FINAL_REVIEW.md` | INV-04 独立复核 + 残余 | 04 | `1a36eef` |
| | `S6-2b-4_FINAL_REVIEW.md` | INV-05 独立复核 + 残余 | 05 | `f0047cb` |
| **中点** | `S6-2b_MIDPOINT_REVIEW.md` | INV-04 前的阶段性冻结检查 | 01~04 | `b65d102` |
| **登记** | `docs/KEY-FINDINGS.md`（**附录 A**） | KF-A（`CLOSED`）· KF-B（`OPEN`） | 02 / 控制面 | `c5fa851` |
| **代码** | `tests/invariants/test_inv_core.py` | 断言本身（23 函数 / 31 用例） | 01~05 | 四段扩展 |
| **契约** | `docs/specs/llm.py.md`（`mini` 小节） | 新出口的接口契约 | 02 | `50c19ca` |

---

## 3. commit 序列（`4f05c95` S6-1 Freeze → HEAD，13 个）

| # | commit | 主题 | 文件 | 变更 | 生产代码 |
|---:|---|---|---:|---|---|
| 1 | `142765e` | docs: freeze S6-2 P0 remediation design | 3 | +1195 / −0 | ❌ |
| 2 | **`50c19ca`** | **fix: restore F042 mini LLM endpoint** | 6 | +686 / −3 | ✅ **唯一生产代码改动** |
| 3 | `c5fa851` | docs: freeze INV-02 remediation final review | 2 | +248 / −2 | ❌ |
| 4 | `9349596` | test: establish INV-01 invariant coverage | 3 | +603 / −0 | ❌ |
| 5 | `c44c9ce` | test: establish INV-03 invariant coverage | 4 | +792 / −0 | ❌ |
| 6 | `1a36eef` | test: establish INV-04 invariant coverage | 4 | +1034 / −7 | ❌ |
| 7 | `b65d102` | docs: add S6-2b midpoint review record | 1 | +183 / −0 | ❌ |
| 8 | `4a41068` | docs: add S6-2b final summary | 1 | +207 / −0 | ❌ |
| 9 | `5cd66c9` | docs: update INV-05 canonical definition for rejection sources | 1 | +2 / −1 | ❌（**Registry 文档**） |
| 10 | `4f74f93` | test: establish INV-05 invariant coverage | 3 | +670 / −0 | ❌ |
| 11 | `f0047cb` | docs: archive S6-2b-4 final review | 1 | +201 / −0 | ❌ |
| 12 | **`a0c0917`** | docs: correct S6-2b final summary test counts… | 1 | +132 / −93 | ❌ ← **Freeze 锚点** |
| 13 | `4ea6220` | docs: archive S6-2b retrospective | 1 | +156 / −0 | ❌ ← HEAD |

**阶段累计**：24 文件、**+5851 / −4** = 测试 **+1073** · 生产 **+15** · 文档 **+4763**。

---

## 4. 面试追问导航

| 追问 | 直达 |
|---|---|
| "你怎么知道不变量真没被破坏？" | [S6-2b_RETROSPECTIVE.md](S6-2b_RETROSPECTIVE.md) §2.2（**四重证据法**）→ 各 `*_FINAL_REVIEW.md` |
| "发现 bug 后怎么保证没改错？" | [S6-2a-P0-F_CHANGE_REPORT.md](S6-2a-P0-F_CHANGE_REPORT.md) §8（反证）→ [S6-2a-P0-F_FINAL_REVIEW.md](S6-2a-P0-F_FINAL_REVIEW.md) §10（残余） |
| "凭什么判 covered 而不是 partial？" | 各 `S6-2b-{2,3,4}_COVERAGE_AUDIT.md` 的"**破坏时是否必红**"列 → 对应 `_FINAL_REVIEW.md` |
| "测试有没有鉴别力？" | 各 `*_CHANGE_REPORT.md` 的 **mutation 表**（14 次，含"拒绝执行不自然的 mutation"的理由） |
| "这套东西对工程有什么价值？" | [S6-2b_RETROSPECTIVE.md](S6-2b_RETROSPECTIVE.md) §2.4（价值）· §4（对 runtime 演进的影响 A-1~A-6） |
| "你自己的判断有没有出错？" | [S6-2b-4_COVERAGE_AUDIT.md](S6-2b-4_COVERAGE_AUDIT.md) 文首 `Revision / Correction` · [S6-2b_RETROSPECTIVE.md](S6-2b_RETROSPECTIVE.md) §2.4（3 处错误） |
| "编号为什么这么定？" | [S6-1_INV_CLASSIFICATION.md](S6-1_INV_CLASSIFICATION.md) §12 · [docs/INVARIANT_REGISTRY.md](docs/INVARIANT_REGISTRY.md) §0（P-1~P-5） |
| "还有哪些没解决？" | [S6-2b_FINAL_SUMMARY.md](S6-2b_FINAL_SUMMARY.md) §7（**单一来源**） |
| "过度工程了吗？" | [S6-2b_RETROSPECTIVE.md](S6-2b_RETROSPECTIVE.md) §3.2（**OE-1~OE-5**，含 4.4:1 文档/代码比自评与改进建议） |

---

## 5. 边界

- 本索引**只做映射**，不重述内容；任何数字的**单一来源**见 §2 "职责"列所指文件。
- **残余与开放事项**以 [S6-2b_FINAL_SUMMARY.md](S6-2b_FINAL_SUMMARY.md) §7 为**唯一来源**（本文件不复制）。
- **Freeze 定义**：除 **bug 修复 / 文档勘误 / 经授权变更** 外，不修改本阶段的**治理结论与证据链**。
