# Approval and team memory policy

Team memory uses the caller's current server-side membership. Members and viewers
cannot write through an owner-published Agent. Owner writes remain allowed; every
queued Run binds its own submitter instead of inheriting the queue pump's context.
Children inherit the running parent's identity. Missing identity in a managed team
fails closed; a published Agent never grants an owner's role. Unmanaged local
operator workspaces retain their existing behavior.

Approval rules are tighten-only. `file_write` and `command` retain the historical
mandatory approval categories. `approve:<tool>` requires approval;
`deny:<tool>` adds an irreversible denial within the published policy. Empty rules
do not remove mandatory high-risk approvals. Critical tools remain rejected.
Unknown rules, expressions, wildcards and weakening actions are rejected at save.

Supported exact tool targets: `fs.read_file`, `fs.list_dir`, `fs.write_file`,
`file.read`, `file.list`, `file.write`, `fs.patch`, `file.patch`, `exec.shell_run`,
`exec.python_run`, `proc.start`, `proc.wait`, `proc.kill`, `storage.kv`,
`subagent.spawn`, `workspace.apply_patch`, `workspace.export_artifact`.

Knowledge-question Agents use `require_evidence=true`. Missing sources or no
reliable citations stop before model submission. Ordinary chat remains unaffected.
Remote timeouts and unavailable services are distinct controlled errors. Evidence
gating is not a proof that every answer with citations is factually correct.

Unsupported legacy configuration overrides are rejected rather than silently
ignored. Compatibility values are step timeout 60, tool concurrency 1, thread
pool size 4, host process memory limit 0, credential cache TTL 300, lazy context
true, and pre-activation list `[storage.spill, credentials, storage.kv]`.
The timeout/pool/cache/pre-activation defaults are deprecated placeholders, not
working controls. Tool-level timeout and Docker memory limits remain separate.
