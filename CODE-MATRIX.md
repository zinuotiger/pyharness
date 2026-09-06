# CODE-MATRIX — PyHarness 实现状态追踪(面试展示用)

> 更新规则:每阶段门通过后由主 Agent 更新本表;状态:✅完成 / ⏳进行中 / 🔴未开始

## 阶段 0(地基:bus/events/errors/config)✅ 2026-09-06

| 模块 | spec | 状态 | 单测 | 覆盖 | 备注 |
|------|------|:---:|-----:|-----:|------|
| errors.py | errors.py.md | ✅ | 5 | 92% | 41 码锚定 ERR.md |
| events/ | events.py.md | ✅ | 27 | 97-99% | Envelope 五要素 + 57 词表 |
| config.py | config.py.md | ✅ | 41 | 95% | 四层合并 + PH_ 白名单 |
| bus/ | bus.py.md | ✅ | 49 | 86% | 三模式 + 五态生命周期 |

**阶段 0 门**:129 passed / 覆盖率 91% / demo_phase0 双插件互发+热卸载 ✅
**git**: 92a328f

## 阶段 1(核心脊柱:12 模块)⏳ 11/12 完成

| 模块 | spec | 状态 | 单测 | 备注 |
|------|------|:---:|-----:|------|
| session.py | session.py.md | ✅ | 29 | core/session.py,事件溯源核心 |
| persistence.py | persistence.py.md | ✅ | 27 | JSONL 原子写 + repair |
| agent_loop.py | agent_loop.py.md | ✅ | 23 | 三态机 + 三闸(主 Agent 修 5 bug) |
| agent.py | agent.py.md | ✅ | 30 | Ctx + Agent 生命周期 |
| llm.py | llm.py.md | ✅ | 79 | 三档超时 + 五码归一 |
| llm_fallback.py | llm_fallback.py.md | ✅ | 20 | 降级链 + 探针(主 Agent 修 4 测试 bug) |
| system_prompt.py | system_prompt.py.md | ✅ | 31 | 护栏恒末 F024 |
| scope.py | scope.py.md | ✅ | 27 | 单调收紧 + 预算状态机 |
| tools_registry.py | tools_registry.py.md | ✅ | 81 | schema 编译器 + TLB 码 |
| tools_guard.py | tools_guard.py.md | ✅ | 80 | g1-g7 链 + INV-03/04(发现 2 死代码缺陷待决策) |
| approval.py | approval.py.md | ✅ | ~15 | 743 行,TTL 120s + 防轰炸合并(120 次压力复跑稳定) |
| tools_executor.py | tools_executor.py.md | 🔴 | - | 依赖 guard/approval 齐后派 |

## 阶段 3(工具域:4 模块)⏳ 3.5/4

| 模块 | spec | 状态 | 单测 | 备注 |
|------|------|:---:|-----:|------|
| spill.py | spill.py.md | ✅ | ~63 | F039 输出溢出,原子落盘 + 600 权限 |
| tool_fs.py | tool_fs.py.md | ✅ | 44 | fs.* 四工具,83% 覆盖 |
| tool_web.py | tool_web.py.md | ⏳ | - | 实现完成,测试补写中 |
| commands.py | commands.py.md | ✅ | 27 | 斜杠命令总闸,100% 覆盖 |
