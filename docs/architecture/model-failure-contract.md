# Model failure and Run finalization

`LLM-303` is a PyHarness error code registered as a retryable upstream error in
`errors.py`. `raise_code` constructs `LLMError`. The adapter maps HTTP 429/5xx,
httpx transport errors (including read/connect timeouts), and selected optional
OpenAI SDK exception classes to this compatibility code. It is not a provider
error code and cannot identify an HTTP status on its own.

The September 26 historical failure contained `system.error / LLM-303` at seq
47 followed by `task.completed / ok` at seq 48. Previously, AgentLoop returned
`RunResult(reason="error")`; TaskQueue discarded that result and interpreted
normal coroutine return as success. The platform projection ignored
`system.error` and accepted the queue completion. Platform artifact finalization
also ran without checking the main result.

The previous HTTP transport retained status only in `ProviderStatusError`, never
extracted response request IDs, Retry-After or provider codes, and the adapter
replaced exceptions with only model/retryable context. The historical event does
not identify which transport exception occurred. Its HTTP status, provider code,
request ID and Retry-After remain **unknown**. No new request is needed or allowed
to diagnose that historical record.

## Terminal contract

- A queue runner must explicitly return `RunResult(reason="complete")` or an
  explicit `TaskResult(ok=True)`. Returning None or arbitrary text is failure.
- AgentLoop retains the stable failure code and safe diagnostics in its result,
  writes a fatal main-execution event, and stops draining on error.
- Platform output/patch/test-report finalization runs only after explicit main
  success. Cleanup failure cannot replace an already failed main result.
- Queue terminal event writes are serialized and deduplicated; result settlement
  is idempotent. Cancellation and failure are not success. Task IDs are recovered
  from history after restart; explicit reuse is rejected before enqueue awaits.
- The persisted-event projection is authoritative for API, Trace, Dashboard,
  usage, task center and inspector. `completed`, `failed` and `cancelled` are
  absorbing states. Success requires both an Agent message and task completion;
  the Agent message alone is not sufficient.
- An unhandled main `system.error` immediately fails the Run and its model step.
  Late completion, close, approval or shutdown events cannot reverse it.
- Old fatal model events without a role are interpreted only within their owned
  task/segment. Old title generation did not emit system.error. Unknown foreign
  task IDs are never attributed to the currently active task.

## Diagnostics and call roles

Coroutine-local context explicitly labels `main_agent`, `title_generation`,
`probe` or `other` and the attempt number. It does not infer role from request
position. The adapter records each attempt's error; only an unhandled main error
is terminal. Auxiliary title failure records a warning and leaves main success
intact. Retry/fallback propagation preserves the final attempt's cause metadata.

The allowlist contains public code, category, actual HTTP status, provider code,
request ID, Retry-After, endpoint scheme/hostname, model, role, request event seq,
attempt, retried, safe-to-retry and a fixed short category summary. Missing values
are `unknown`. Header values and error codes are bounded and grammar checked;
credentials reflected by the provider are rejected using the transport's already
supplied credential, without resolving credentials for diagnostics. Response
messages, exception text, HTML, full proxy bodies, request headers and user
messages are never serialized as diagnostics. Streaming HTTP error bodies are
inspected only within a 16 KiB bound for the error code. Public projection and SSE
revalidate diagnostic fields. Per-call observations preserve already received
headers through stream timeouts and post-response validation without shared
mutable transport state.

Request counts and returned usage counts are separate. Missing usage is unknown;
known token totals sum only observed usage. A failed Run never contributes to the
success count or manufactures approvals, tests, patches or artifacts.

## Offline evidence

`tests/unit/test_model_failure_contract.py` uses fake transports, controlled
exceptions and asyncio Events. It covers HTTP classification, missing metadata,
credential reflection, non-JSON and interrupted streaming, real service/queue/API
failure, restart, terminal races, role isolation and historical replay. Existing
queue test runners now declare success explicitly; their original FIFO,
cancellation, approval and failure assertions remain intact.

`tests/fixtures/model_failure_legacy.json` is a minimal projection fixture derived
from the historical JSONL. It preserves sequence, timestamps, task ownership,
three successful read steps and three usage facts, without request/response
content. `scripts/replay_model_failure.py` reads the original JSONL without
opening a runtime, forbids credential resolution and installs a network boundary.
It writes only to a new output file. Expected replay: first Run completed, second
Run failed/LLM-303, 4 requests, 3 usage records, 4336 input + 545 output tokens,
fourth usage unknown, zero approvals or artifacts.

The browser smoke uses a deterministic model error through the real queue and
checks red failure styles and LLM-303 in the inspector, task center and Dashboard,
including refresh. CI runs the model-failure suite in the Platform gate as well
as full pytest; no real model is used.
