# Approvals

审批中心 projects pending, approved, rejected and expired decisions from the original session events. Inspect tool, bounded argument summary, risk, session and TTL before choosing 批准一次 or 拒绝. Pending decisions without an owning live provider become expired after restart. Approval IDs include session identity. Repeating the same terminal decision is idempotent; conflicting or expired decisions return a conflict.

The provider writes the grant/denial before resolving its waiting future, then the existing queue resumes. Platform high-risk file writes, patches, command execution and artifact application/export use ToolExecutor governance. Buttons cannot bypass it. The V1 UI deliberately exposes one-shot grants only; it does not silently enable persistent trust or edit arguments under an old approval digest. To change an operation, reject it and submit a corrected task.
