# Platform Web V1

## Scope and decision
The local Web platform and desktop shell share ApplicationService. PlatformService owns configuration and projections; it does not introduce a second AgentLoop, TaskQueue, ApprovalProvider, Session or execution journal. Existing desktop management remains at `/classic`; `/` serves the Chinese platform. The PySide6 shell is unchanged.

The explicit Platform V1 request authorizes an optional operator-managed Docker backend despite earlier phase constraints against Docker. This is a scoped architecture decision: no Docker daemon startup, image pull, cloud worker, Node runtime, Redis, Kubernetes, billing or organization subsystem is introduced. Vanilla JavaScript needs no frontend build step.

## Data and execution
Agent drafts and immutable hashed snapshots live in tenant-scoped atomic configuration. Additive configuration keys migrate on read; unknown format versions fail closed. User input stores the exact version binding in the existing user.message metadata. Runs, steps, approvals, artifacts, knowledge queries and sandbox facts project existing SessionLog events. Platform resource records use three registered event types and explicit sync writes. Old logs remain readable. Archived sessions are a reversible presentation preference; no history is moved or deleted.

The path is model -> argument validation -> GovernanceContext -> ApprovalProvider -> ToolExecutor -> PlatformRuntime -> selected backend -> result validation -> original events. Scope is narrowed to the published allowlist. User-initiated patch/export tools are hidden from model schemas and require a trusted operation binding. Artifact approval uses the existing provider and session queue, not a new approval engine.

Run statuses: queued, running, waiting_approval, paused, retrying, completed, failed, cancelled. Step statuses: pending, running, waiting_approval, completed, failed, cancelled, skipped. Missing live handles after restart project as runtime_interrupted; execution is never silently replayed. Unavailable latency/cost/capability values remain unknown. Trace is bounded to 500 public spans per session and excludes raw model requests, responses and private reasoning. Historical queries currently scan tenant logs; this is a local MVP, not a large-data index.

## API groups
Authenticated `/api/v1`: dashboard; sessions and metadata; runs/detail/cancel/retry/trace; approvals/approve/reject; agents/edit/publish/copy/versions/diff/deprecate-version; artifacts/upload/detail/preview/download/apply/export/delete; operations; sandboxes/detail/destroy; knowledge/query/replace/retry/provider; connections/test/start/stop/enable/disable/delete; schedules/add/edit/pause/resume/remove/run_now; usage filters; system/health. Existing model/skill/plugin/SSE APIs are reused. Domain failures map to 404/409/422/503. No API returns secrets. Repeated matching approval decisions and terminal cancellations are idempotent.

## Concurrency
Per-session creation locks fix the version before engine construction. Existing queue owns execution serialization. Sandbox owns processes, and cancellation gathers owned children. Patch baseline validation precedes writes and failures restore previous bytes; external writers and storage failure can prevent rollback, which is reported as a recovery failure rather than success. Configuration uses a file lock and atomic replacement. No lock claims distributed coordination.

## Deliberate MVP limits
No embedded interactive PTY: use governed exec/proc tools from chat. Network allowlists are rejected; isolated networking is none. stdio MCP and plugins are trusted host capabilities, not container isolation. Platform Agent plugins remain disabled. Model feature support and context length are unknown until supplied by the provider; GET /models tests connectivity, not tool/image fidelity. Skill/plugin lifecycle remains available through the shared classic manager. Remote RAG uses bounded query-time document transfer, not an asynchronous remote ingestion worker. No default semantic/BGE reliability claim. Archive/tags are presentation metadata. There is no machine-crash transaction across multiple patch files and the session log.
