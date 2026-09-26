# Agent versions

Templates: 通用助手 (execution disabled), 受治理代码变更 Agent (isolated, offline), 知识问答 Agent (evidence required). The JSON draft editor validates name, prompt, connection, tool/MCP/skill/knowledge selections, strict/standard policy, sandbox limits, token/tool/cost budget, maximum rounds, timeout and output schema.

Publishing creates a complete SHA256-verified immutable snapshot. Editing changes the draft only. The versions view shows snapshots, permits copying old versions, compares adjacent versions and deprecates individual versions for new sessions. History remains readable. Runs record actual agent_id and version; existing sessions retain their fixed binding. No hidden rebinding of a cached engine occurs. Deleting a published Agent is refused.

Only explicitly selected tools are allowed. Both fs.* names and file.read/write/list/patch aliases are available. MCP requires explicit host_approved and trusted operator configuration. High-risk approval rules cannot be weakened by arbitrary draft strings; safety gates remain mandatory. System prompts describe public plans/results and do not expose private reasoning. Tool retries are bounded by maximum turns, calls, tokens and timeout; UI task retry allows one retry of a failed/cancelled run.
