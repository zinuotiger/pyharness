# CODE-MATRIX — PyHarness 实现状态追踪(面试展示用)

> **初版快照**:2026-09-07 | code-pipeline 全流程走完 | 32 模块全部实现
> **总览已按当前 RC 更新**:`e561712` @ 2026-09-17 —— 下表**新增「当前」列并保留「原值」列**;其后的阶段章节(阶段 0~6)仍是 2026-09-07 原始记录,**未改动**。
> 当前能力与已知限制以 [LIMITATIONS.md](LIMITATIONS.md) 为准。

## 总览

| 指标 | 当前(RC `e561712` @ 2026-09-17) | 原值(2026-09-07) |
|------|------|------|
| 模块 | `pyharness/` **85 个 .py 文件 / 33,586 行** | 32/32 实现(40 个 .py 文件 / 21,528 行) |
| 测试规模 | `tests/` **72 个 .py 文件 / 30,989 行** | unit 33 文件 + invariants |
| 测试 | **1751 collected / 1749 passed / 0 failed / 2 skipped** | **1298 passed** / 2 skipped |
| 覆盖率 | **79%** | 88% |
| 演示 | demo_phase0~6(脚本在位;本次未重测) | demo_phase0~6 全部跑通 |
| git | 72 次提交(至 `e561712`) | 6 次提交(92a328f → eaeb2b6) |

> 「当前」列数据均为实测:`git rev-list --count HEAD` / `git ls-files … | xargs wc -l` / 全量 `pytest`(默认口径须 `--ignore` 两个缺 PySide6 的用例文件,见 [LIMITATIONS.md](LIMITATIONS.md) L-6)。

## 当前 RC 修复状态(至 `e561712`)

| commit | 范围 | 修复项 |
|--------|------|--------|
| `4aefc96` | RT-GOV-01 | governance 注入 orchestration runtime ctx |
| `e4de9a8` | F1 | CLI render envelope 解包 / approval 身份与事件路由 / exit 生命周期闸 / scan 逐文件容错 |
| `e561712` | Stage 1 | F-01 `policy.updated` 运行时发射 / F-19 session id 正则兼容 / F-28 审批跨会话隔离(一级) |

> 未修复项与已知限制**以 [LIMITATIONS.md](LIMITATIONS.md) 为准**。

## 阶段 0(地基)✅ | 阶段 1(核心脊柱 12 模块)✅
详见 git 92a328f / ec061d4(129 / 612 测试)

## 阶段 3(工具域 4 模块)✅ d93554b
spill(F039)/ tool_fs(fs.*)/ tool_web(web.*)/ commands(斜杠总闸)

## 阶段 4(编排 6 模块)✅ df3c553
task_queue(F043)/ goal(F047)/ plan_mode(F045/46)/ schedule(F048)/ subagent(F049)/ jobs(F051)
> 修实现 bug:plan revise 旧 intent 残留、cron 尾随空格漏检

## 阶段 5(检索压缩 2 模块)✅ 6f516b1
session_query(F057 FTS5)/ compaction(F058)
> 修实现 bug:rebuild 幂等闸失效、announce 非幂等

## 阶段 6(外壳 4 模块)✅ eaeb2b6
cli(14 子命令)/ acp(JSON-RPC)/ desktop(pywebview+FastAPI)/ repair(F060)
> 修实现 bug:PyHError 漏 import、对账口径不一致(永不收敛)、desktop 3 处

## 关键质量证据
- INV-01~09 不变量测试全绿(invariants/ 12 项)
- 安全核心:tools_guard 80 测试(INV-03 单调拒绝/INV-04 零副作用)
- 每模块错误码锚定 ERR.md(41 码零表外)
- 事件词表 64 类型(57 核心 + plan/schedule 扩展)
- **后续演进(2026-09-14)**:事件词表已增至 **74 型**(强同步 **12**)。本文件为 2026-09-07 历史快照,原数字**保留不改**;现行值见 `docs/baseline/BASELINE_REPORT.md`。
