# Sandbox security boundary

Docker is a container boundary sharing the kernel. It is not a microVM or a guarantee against kernel/daemon vulnerabilities. The daemon and configured image must be trusted. The configured image must already exist; the app never pulls it. network=none is the only supported isolated network policy; allowlists are not supported and validation rejects them. Non-root, capabilities, readonly root, mounts and resource limits are enforced by Docker flags, subject to daemon support.

Current delivery environment: Docker CLI exists but daemon health is unavailable. Actual container execution, public network denial, PID/memory enforcement and container child-process cleanup were environment-blocked. Fake-backend tests verify routing, flag construction, ownership, cancellation and fail-closed; they are not evidence that real isolation ran. Windows symlink creation may require privileges; the test records an explicit skip when unavailable.

HostApproved is not secure OS isolation. Arbitrary code can access resources available to the operating-system account despite a private cwd and sanitized environment. Never select it for untrusted code expecting container guarantees. Trusted stdio MCP also runs on the host; platform isolated/disabled agents cannot bind it.

Generated artifacts have bounded metadata and SHA256. Secret-pattern redaction is defense in depth, not a universal detector for arbitrary confidential content. Do not supply sensitive input that should not reach the chosen model or RAG service. The RAG endpoint receives only explicitly selected documents, with no inherited HTTP environment credentials. No private model reasoning is exposed as platform Trace.

Patch operations validate owner, baseline hashes, manifest hash and relative paths; only listed files change. Rollback handles ordinary write/audit failure. Power loss, concurrent external filesystem manipulation or rollback disk failure cannot be made atomic across multiple files; reported recovery failures need operator intervention. No SaaS isolation, cloud workers, enterprise SSO or hostile multi-user filesystem guarantees are claimed.
