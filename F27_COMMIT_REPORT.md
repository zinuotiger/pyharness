# F27_COMMIT_REPORT

> **性质**：F-27 提交收尾报告（Stage 7）。
> **上游**：`F27_CHANGE_REPORT.md` · `F27_IMPLEMENT_PLAN.md` · `docs/decisions/ADR-021-audit-session-ownership.md`（Accepted）
> **状态**：**已提交（本地）· 未 push**
> **分支**：`main...origin/main [ahead 48]`（提交前 ahead 46 → **+2**）

---

## 1. 提交序列

| # | Commit | Message | 文件 | +/− |
|---|---|---|---|---|
| **C1** | **`7a4050a`** | `docs: adopt ADR-021 on audit session ownership` | 2 | **+337 / −3** |
| **C2** | **`ebbb444`** | `fix: bind guard audit events to the calling session` | 4 | **+295 / −21** |
| | | **合计** | **6** | **+632 / −24** |

```
ebbb444  fix: bind guard audit events to the calling session      ← HEAD
7a4050a  docs: adopt ADR-021 on audit session ownership
d2ccd45  docs: refine S6-2b release presentation                  ← 原 HEAD
```

### C1 明细（`7a4050a`）

| 文件 | +/− | 说明 |
|---|---|---|
| `docs/decisions/ADR-021-audit-session-ownership.md` | **+332**（新建） | ADR 全文；`## Status` = **Accepted**；含 A-1~A-6 裁定记录表与实施约束 6 条 |
| `docs/ADD.md` | **+5 / −3** | 顶部条数 **20→21**；§2 索引追加 ADR-021；§2.1 标题 `013~019`→`013~021` + 路径行 |

### C2 明细（`ebbb444`）

| 文件 | +/− | 说明 |
|---|---|---|
| `pyharness/core/tools_guard.py` | **+50 / −20** | 6 处签名加 keyword-only `session=None`；2 处统一解析；5 处透传；方法级 docstring |
| `pyharness/governance/context.py` | **+9 / −1** | `authorize()` 传 `session=getattr(ctx, "session", None)` |
| `tests/invariants/test_inv_core.py` | **+184** | T-1~T-6 + T-8（7 例）+ 2 助手 |
| `tests/unit/test_tools_guard.py` | **+52** | T-7（2 例） |

---

## 2. 提交前核对件（要求 4）

### 2.1 `git status`

```
 M docs/ADD.md                                  ← F-27 (C1)
 M pyharness/cli.py                             ← F1 既存
 M pyharness/core/approval.py                    ← Stage 1
 M pyharness/core/commands.py                    ← F1 既存
 M pyharness/core/orchestration.py               ← RT-GOV-01
 M pyharness/core/tools_guard.py                 ← F-27 (C2)
 M pyharness/desktop/app.py                      ← Stage 1
 M pyharness/engine.py                           ← Stage 1 (F-01)
 M pyharness/governance/context.py               ← F-27 (C2)
 M tests/invariants/test_inv_core.py             ← F-27 (C2)
 M tests/unit/test_approval.py                   ← Stage 1
 M tests/unit/test_cli.py                        ← F1 既存
 M tests/unit/test_desktop.py                    ← Stage 1
 M tests/unit/test_engine_flow.py                ← Stage 1
 M tests/unit/test_orchestration.py              ← RT-GOV-01
 M tests/unit/test_tools_guard.py                ← F-27 (C2)
?? F1-R1C_FINAL_REVIEW.md · F1_CHECKPOINT.md · F1_RELEASE_NOTES_DRAFT.md
   F1_RELEASE_PREPARATION.md · F27_CHANGE_REPORT.md · F27_IMPLEMENT_PLAN.md
   FUNCTIONAL_RUNTIME_AUDIT_v1.md · RT-FIX-PLAN-v1.md · RT-FIX-STAGE1-REPORT.md
   docs/RUNTIME_GOVERNANCE_METHODOLOGY_v0.1.md · docs/decisions/ · docs/governance.html
```

### 2.2 `git diff --stat`（提交前，全部已跟踪改动）

```
 docs/ADD.md                       |   8 +-      ← F-27
 pyharness/cli.py                  |  77 +++---   ← F1
 pyharness/core/approval.py        |  25 ++-     ← Stage 1
 pyharness/core/commands.py        |  15 +-      ← F1
 pyharness/core/orchestration.py   |  12 +-      ← RT-GOV-01
 pyharness/core/tools_guard.py     |  70 +++---   ← F-27
 pyharness/desktop/app.py          |  14 +-      ← Stage 1
 pyharness/engine.py               |  14 ++      ← Stage 1
 pyharness/governance/context.py   |  10 +-      ← F-27
 tests/invariants/test_inv_core.py | 184 +++++   ← F-27
 tests/unit/test_approval.py       | 108 +++++   ← Stage 1
 tests/unit/test_cli.py            | 359 +++++   ← F1
 tests/unit/test_desktop.py        |  82 +++++   ← Stage 1
 tests/unit/test_engine_flow.py    |  73 +++++   ← Stage 1
 tests/unit/test_orchestration.py  | 280 +++++   ← RT-GOV-01
 tests/unit/test_tools_guard.py    |  52 +++++   ← F-27
 16 files changed, 1288 insertions(+), 95 deletions(-)
```

### 2.3 即将提交文件列表（**显式列出，未用 `git add -A`**）

**C1（2）**：`docs/decisions/ADR-021-audit-session-ownership.md` · `docs/ADD.md`
**C2（4）**：`pyharness/core/tools_guard.py` · `pyharness/governance/context.py` · `tests/invariants/test_inv_core.py` · `tests/unit/test_tools_guard.py`

---

## 3. 合规核验（要求 1 / 2 / 3 / 5 / 6）

| # | 要求 | 核验方式 | 结果 |
|---|---|---|---|
| **1** | 仅提交 F-27 本轮变更 | 提交后 `git status --porcelain -- <F-27 六文件>` = **空**；既存 11 文件仍为 `M` | **✓** |
| **2** | 严格 C1/C2 两次提交 | `git log --oneline d2ccd45..HEAD` = **恰 2 条**，顺序为 docs → fix | **✓** |
| **3** | 禁止 `git add -A` | 全程仅用 `git add <显式路径>`；命令留痕可见 | **✓** |
| **4** | 提交前输出 status / diff --stat / 文件列表 | 见 §2 | **✓** |
| **5** | 不含 F1 / RT-GOV-01 / Stage 1 其它改动 | 逐文件扫描 F-27 标记：**11 个被排除文件全部 F-27 标记 = 0**；且它们提交后仍为 `M`（未被卷入） | **✓** |
| **6** | commit message 无 AI 署名 | `git log -2 --format=%B \| grep -ci "claude\|anthropic\|co-authored\|generated with"` = **0** | **✓** |

### 3.1 未被卷入的既存改动（提交后仍在工作树）

| 归属 | 文件 |
|---|---|
| **F1 既存** | `pyharness/cli.py` · `pyharness/core/commands.py` · `tests/unit/test_cli.py` |
| **RT-GOV-01** | `pyharness/core/orchestration.py` · `tests/unit/test_orchestration.py` |
| **Stage 1（F-19/F-28/F-01）** | `pyharness/core/approval.py` · `pyharness/desktop/app.py` · `pyharness/engine.py` · `tests/unit/test_approval.py` · `tests/unit/test_desktop.py` · `tests/unit/test_engine_flow.py` |

---

## 4. 独立验证：`HEAD + C1 + C2` 是否自洽可绿（提交前执行）

> **动机**：本轮全部测试是在**含 F1 / RT-GOV-01 / Stage 1 的工作树**上跑的；而 C1+C2 提交的是**子集**。必须证明**被提交的状态本身**独立可绿，否则等于提交了一个未经验证的状态。

**方法**：`git worktree add --detach tmp/f27_verify HEAD` → 仅拷入 C1+C2 的 **6 个文件** → 在该隔离工作树内跑全量。

| 项 | 结果 |
|---|---|
| worktree 内工作树状态 | **恰 6 项**（5 M + 1 新目录），**无** F1/RT-GOV-01/Stage 1 文件 |
| **全量回归（HEAD+C1+C2）** | **1713 collected / 0 failures / 0 errors / 2 skipped = 1711 passed** |
| 覆盖率 | 78%（缺 Stage 1 的用例，故低于主树的 79%） |
| 算术自洽 | HEAD 独有 **1704** + F-27 的 **9** = **1713** ✓（与早前 HEAD+F1 实测 1714 互洽） |

**结论：C1+C2 在 `d2ccd45` 之上独立自洽、零失败。** 验证后 worktree 已移除（`git worktree list` 仅剩主仓库）。

> 附注：worktree 目录一度因**当前 shell 的 CWD 位于其内**而删除失败（Windows 目录占用），切回主仓库后已清除；主工作树未受影响（`git status` 仍 28 项）。

---

## 5. 主工作树当前全量状态（提交后）

| 项 | 值 |
|---|---|
| `HEAD` | **`ebbb444`** |
| 分支 | `main...origin/main **[ahead 48]**`，**未 push** |
| F-27 六文件 | **全部已入库**（`git status -- <六文件>` 为空） |
| 其余未提交 | 11 个已跟踪文件（F1 / RT-GOV-01 / Stage 1）+ 12 个未跟踪（阶段报告 + `docs/decisions/` 已随 C1 入库，其余为报告类） |
| 全量回归（主树，含全部未提交改动） | **1741 collected / 1739 passed / 0 failed / 2 skipped**（`F27_CHANGE_REPORT.md` §5） |

> 提交不改变工作树字节 ⇒ 主树的全量结果与提交前**同值**，未重复跑。

---

## 6. 回滚

| 级 | 手段 |
|---|---|
| **精确回滚 F-27** | **逆序** `git revert ebbb444 7a4050a`（保留历史） |
| **丢弃两提交（保留工作树）** | `git reset --soft d2ccd45`（改动回到暂存区）／`--mixed`（回到工作树） |
| ⚠️ **禁止** | `git reset --hard d2ccd45` —— 会**一并丢弃**工作树中 F1 / RT-GOV-01 / Stage 1 的未提交改动 |
| 文件级快照 | `tmp/f27_bak/`（改动前，6 文件）· `tmp/f27_post/`（改动后，2 源文件）· `tmp/f27_apply.py`（可复现重放） |

---

## 7. 遗留

| # | 项 | 归属 |
|---|---|---|
| 1 | **`git push`** —— 公开仓库，需人工决定披露时机 | 待裁定 |
| 2 | **F1 既存改动**（3 文件）尚未提交 | 待裁定（F1 Closure 未结） |
| 3 | **RT-GOV-01**（2 文件）尚未提交 | 待裁定 |
| 4 | **Stage 1**（6 文件）尚未提交 | 待裁定 |
| 5 | **R-a**：INV registry 行号证据锚漂移 → **符号锚迁移** | 另立任务 |
| 6 | **F-28 第二级**（复合键） | 建议与 ADR-021 的"跨会话归属"合并设计 |
| 7 | **ADR storage convention ADR**（A-6 记录待办） | 另立 ADR |

> ⚠️ **提交健康提示**：工作树现有**四批互不相关的未提交改动**（F1 / RT-GOV-01 / Stage 1 / 阶段报告类未跟踪文件）。建议尽快分批结清，避免后续 commit 误卷。
