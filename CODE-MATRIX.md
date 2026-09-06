# CODE-MATRIX — PyHarness 实现状态追踪(面试展示用)

> 终版:2026-09-07 | code-pipeline 全流程走完 | 32 模块全部实现

## 总览

| 指标 | 值 |
|------|-----|
| 模块 | 32/32 实现(40 个 .py 文件,21,528 行) |
| 测试 | **1298 passed** / 2 skipped(unit 33 文件 + invariants) |
| 覆盖率 | 88% |
| 演示 | demo_phase0~6 全部跑通 |
| git | 6 次提交(92a328f → eaeb2b6) |

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
