# specs/ — 编码规格索引(AI 编码入口)

> 用途:写代码时的唯一入口。每个 .py 模块一份规格,含函数签名/伪代码/异常表/关联测试。
> 读取顺序:先本索引找模块 → 打开 specs/<模块>.py.md → 照函数清单实现。
> 权威:PRD-Core.md §5(功能)→ 本目录(编码规格);冲突以 PRD 为准。

## 总览

| 指标 | 值 |
|---|---|
| 规格份数 | 32 |
| 总体量 | 592KB |
| 函数定义 | 313 |
| 覆盖阶段 | 0-6 全阶段 |


## 阶段0

| 模块文件 | 体量 | 函数 | 职责 |
|---|---|---|---|
| [`bus.py.md`](bus.py.md) | 23KB | 17 | 一句话:单进程内唯一通信通道——注册表(Registry 索引插件/工具/能力)+ 事件总线(EventBus 三模式分 |
| [`config.py.md`](config.py.md) | 26KB | 17 | 一句话:分层配置引擎——代码默认值(L1)→ config.yaml(L2)→ PH_ 环境变量(L3)→ CLI 覆盖 |
| [`errors.py.md`](errors.py.md) | 13KB | 10 | 一句话:全系统唯一失败语义层——ErrorSpec 注册表 + PyHError 异常树(code/message/re |
| [`events.py.md`](events.py.md) | 16KB | 9 | 一句话:会话事件唯一的 pydantic 模型层——Envelope 信封强校验 + 57 个事件负载词表注册 + 五步 |


## 阶段1

| 模块文件 | 体量 | 函数 | 职责 |
|---|---|---|---|
| [`agent.py.md`](agent.py.md) | 17KB | 11 |  |
| [`agent_loop.py.md`](agent_loop.py.md) | 18KB | 10 |  |
| [`approval.py.md`](approval.py.md) | 14KB | 7 |  |
| [`persistence.py.md`](persistence.py.md) | 19KB | 13 |  |
| [`scope.py.md`](scope.py.md) | 14KB | 9 |  |
| [`session.py.md`](session.py.md) | 16KB | 11 |  |
| [`system_prompt.py.md`](system_prompt.py.md) | 12KB | 6 |  |
| [`tools_executor.py.md`](tools_executor.py.md) | 15KB | 5 |  |
| [`tools_guard.py.md`](tools_guard.py.md) | 16KB | 12 |  |
| [`tools_registry.py.md`](tools_registry.py.md) | 13KB | 7 |  |


## 阶段1-2

| 模块文件 | 体量 | 函数 | 职责 |
|---|---|---|---|
| [`llm.py.md`](llm.py.md) | 19KB | 10 |  |
| [`llm_fallback.py.md`](llm_fallback.py.md) | 14KB | 8 |  |


## 阶段3

| 模块文件 | 体量 | 函数 | 职责 |
|---|---|---|---|
| [`commands.py.md`](commands.py.md) | 17KB | 8 |  |
| [`spill.py.md`](spill.py.md) | 16KB | 7 |  |
| [`tool_fs.py.md`](tool_fs.py.md) | 20KB | 6 |  |
| [`tool_web.py.md`](tool_web.py.md) | 20KB | 7 |  |


## 阶段4

| 模块文件 | 体量 | 函数 | 职责 |
|---|---|---|---|
| [`goal.py.md`](goal.py.md) | 21KB | 11 |  |
| [`jobs.py.md`](jobs.py.md) | 18KB | 9 |  |
| [`plan_mode.py.md`](plan_mode.py.md) | 21KB | 10 |  |
| [`schedule.py.md`](schedule.py.md) | 22KB | 12 |  |
| [`subagent.py.md`](subagent.py.md) | 21KB | 9 |  |
| [`task_queue.py.md`](task_queue.py.md) | 17KB | 9 |  |


## 阶段5

| 模块文件 | 体量 | 函数 | 职责 |
|---|---|---|---|
| [`compaction.py.md`](compaction.py.md) | 20KB | 10 |  |
| [`session_query.py.md`](session_query.py.md) | 18KB | 9 |  |


## 阶段6

| 模块文件 | 体量 | 函数 | 职责 |
|---|---|---|---|
| [`acp.py.md`](acp.py.md) | 16KB | 9 | 一句话:JSON-RPC 2.0 over stdio 自动化桥——逐行读 stdin 解析请求(非法行→-32700) |
| [`cli.py.md`](cli.py.md) | 20KB | 12 | 一句话:PyHarness 第一壳——argparse 解析 14 个叶子子命令(chat/run/plan/sched |
| [`desktop.py.md`](desktop.py.md) | 22KB | 12 | 一句话:双击即用的 Windows 桌面壳——pywebview 主线程开窗(1280×800)加载 FastAPI 同 |
| [`repair.py.md`](repair.py.md) | 20KB | 11 | 一句话:崩溃恢复管线(F060)——启动/打开会话时自动扫描全会话健康(尾部半行/中部坏行/seq 空洞/派生索引对账) |


## 编码顺序建议(按阶段依赖)
```
阶段0: bus → events → errors → config      (地基先立)
阶段1: session → persistence → agent_loop → agent
        → system_prompt → llm → llm_fallback → scope
        → tools_registry → tools_guard → tools_executor → approval
阶段3: tool_fs → spill → tool_web → commands
阶段4: task_queue → plan_mode → goal → schedule → subagent → jobs
阶段5: session_query → compaction
阶段6: cli → repair → acp → desktop             (壳最后)
```

## 关联文档
| 文档 | 关系 |
|------|------|
| PRD-Core.md §5 | 功能规格权威(F 编号) |
| DIS-CORE.md / DIS-SEAM.md | 伪代码级设计 |
| ERR.md | 错误码权威 |
| 本目录各 specs | 每模块编码规格 |