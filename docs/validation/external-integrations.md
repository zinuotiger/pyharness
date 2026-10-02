# External integration evidence boundaries

## Model and Docker

The bounded real-provider acceptance used the normal adapter, transport, usage,
governance and TaskQueue. The first request used container-root file spelling and
was cancelled after an approval request, without approval or execution. The path
contract was clarified: invalid paths are rejected before approval, never silently
rewritten. One pure-format correction round is allowed; traversal is terminal.

A new Run made four requests and five model-originated tool calls plus three
operator Artifact actions. Eight approvals covered reads, calculator-only write,
`python -m pytest -q`, Patch application and two exports. The target changed only
after separate approval. Session, Run, steps, approval history and Artifacts
survived restart. Including the first failed request: five requests, 11,401 input
tokens and 1,010 output tokens; no Transport retry or fallback. This evidence
covers a synthetic calculator, not arbitrary projects or production security.

Local Docker Linux Engine 29.6.2 used a fixed Python 3.13.2 / pytest 9.1.1 image.
Runtime retains `--pull=never`, non-root, dropped capabilities, read-only root,
private workspace, network none, no socket mount and bounded CPU/memory/PID/time/
output. Controlled sentinel, symlink, loopback network and process cleanup checks
passed. This is local Docker Desktop evidence, separate from historical Ubuntu CI.

## RAG and MCP

Remote RAG uses an explicit endpoint plus `/query`; it never guesses URLs.
Canonical `citations/answer_allowed` and typed `status=ok/data/match_status`
retrieval envelopes are supported. Native `source/text` must bind exactly to the
authorized current source ID, text and computed line. Remote answer prose and
similarity score do not authorize an answer. Scheduled and child Agents use the
same execution-time no-evidence boundary. Connection probing is separate from an
empty-authority query.

Fixed rag-engine 0.2.0 was actually tested with isolated offline NGram/extractive
ingest, source/quote/line query, deletion, restart and service-unavailable startup.
No external repository source was changed. This does not validate BGE or broad
semantic answer quality. Remote ingest remains an explicit operator action.

Fixed evleven 1.1.0 wheel, manifest and installed source hashes were verified;
16 actual MCP integration tests exercised approval denial, timeout/cancellation,
lost-acknowledgement reconciliation and fresh-process persistence. Planning was
deterministic; no paid model was used for MCP testing. Arbitrary MCP and plugins
are trusted host capabilities, not Docker isolation. Platform plugin loading
stays disabled; team KV writes require the caller's current owner role.

## Manual boundaries

Real vision lacks declared provider capability. Hidden WebView2 and Qt offscreen
automation do not replace physical IME/DPI/accessibility tests. Local accounts are
not a hardened public SaaS. PTY, network allowlists and power-loss-atomic multi-file
Patch application remain unsupported. This task performs no remote CI, push or release.
