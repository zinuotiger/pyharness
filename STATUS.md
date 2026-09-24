# PyHarness Engineering Release Candidate

This candidate retains package version 0.1.0 and uses its Git branch/commit to
identify the engineering release. It has not been pushed or published.

F01–F15, R03–R04 and G01–G02 retain the verified remediation baseline.
R01 uses safe constraints: unsafe PTY entry points remain disabled.
R02 remains environment-blocked: controlled syscall tests are available, but
actual Linux/WSL POSIX permission validation requires a separate environment.

Candidate results are recorded in
[the public RC report](reports/PYHARNESS-ENGINEERING-RC-20260924.md).
Raw logs, virtual environments, user data and historical local reports are
excluded from the Git candidate. The reviewed runtime and installation checks passed before commit.
The committed report records that source state; the external distribution
attestation identifies the exact commit and its postcommit rerun results.
Two independent reviewers verified candidate runtime and delivery changes.

Models in acceptance tests are deterministic substitutes. The runtime,
governance, persistence and dedicated external evleven R1 MCP boundary are real.
No real model, paid API, complete manual GUI/WebView2 or remote CI validation is
claimed. Cancellation does not roll back side effects; remote MCP timeout does
not prove the remote operation stopped. Installation may require dependency
sources or an existing cache; this is not an offline portable distribution.
