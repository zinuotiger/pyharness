# PyHarness Engineering Release Candidate

Package version remains 0.1.0. The engineering candidate is pushed to its
independent branch with Draft PR #1; it is not merged, tagged or released.

F01–F15, R03–R04 and G01–G02 retain the verified remediation baseline.
R01 remains safely-constrained: unsafe PTY entry points remain disabled.
R02 is confirmed-fixed for the recorded real, non-root GitHub-hosted Ubuntu
24.04 Python 3.11/3.13 runs with umask 022 and actual filesystem permissions.
This does not establish every POSIX platform or a multi-user production guarantee.

See [the RC report](reports/PYHARNESS-ENGINEERING-RC-20260924.md), its JSON,
and [CI reproduction](docs/CI.md). This documentation commit must also pass
the unchanged remote matrix; the final exact SHA and rebuilt wheel/sdist hashes
are recorded in the external postcommit attestation and Draft PR.

Runtime, governance, storage and the dedicated external evleven R1 MCP boundary
are real. Models in acceptance tests are deterministic substitutes. No real
model, paid API or complete manual GUI/WebView2 validation is claimed.
Cancellation does not roll back side effects; remote timeout does not prove
remote work stopped. Unknown writes must not be blindly retried.
Installation may need a package source; this is not an offline portable package.
Raw logs, environments and synthetic test data are excluded from Git.
