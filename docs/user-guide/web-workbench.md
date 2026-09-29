# Web workbench

Start the existing `pyharness-desktop` entry and use its authenticated local browser/WebView. `/` opens the light Chinese workbench; `/classic` retains the original runtime manager. Empty installations display zero counts and explicit missing model/sandbox states.

Navigation: 工作台、新建会话、任务中心、审批中心、Agent、知识库、工具与连接、沙盒环境、计划任务、文件与产物、用量与监控、系统设置. Ctrl+K focuses global navigation search. Dates accept ISO or epoch seconds and show 时间未知 for invalid input. API errors surface with retry and toast; HTTP 204 is handled without parsing JSON. SSE reconnect reloads persisted state; no second realtime transport is introduced.

Publish an Agent, create a new session, select the version, attach optional inputs and send a task. A session fixes its first version; use a new session to change execution policy. Inputs are staged under inputs/ in the session-owned workspace, copied into the private sandbox and remain separate from an arbitrary host repository. The inspector links steps, approvals, public trace and files. Archive/restore and tags are reversible session metadata.

Schedules create a dedicated version-bound session; interval values are seconds. Enable/disable, edit, run now and delete use the existing Schedule engine. They run only while the application is open; event history persists. Usage filters accept from/to timezone-aware ISO, session_id, agent_id, run_id, model and tool. Unknown costs/latencies remain unknown.

For the code-change exercise, upload the two files in examples/platform-code-change to a new session; choose the published code template. Ask it to fix inputs/add.py and run `python inputs/check.py`. Review file/command approvals, inspect the generated patch and test report, then request apply/export and approve separately. Real execution requires a safely configured model and available Docker image. Automated acceptance uses deterministic model transport and a fake backend; neither is a real model/container claim.
