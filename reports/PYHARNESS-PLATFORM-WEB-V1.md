# PyHarness Platform Web V1 verification report

Status: local MVP implemented; independent review passed. Real Docker and real-model acceptance remain environment-blocked. Final commit-bound verification is recorded in the external delivery manifest.

Baseline: ad9a0e8591999b6c81766b05f98ebceca93fd56e. Branch: feat/pyharness-platform-web-v1. Three read-only design agents and two independent read-only reviewers completed. Main and RC worktrees are protected; no push/tag/remote release is authorized.

The checkpoint is external to the repository and contains original tracked-file hashes, identity, toolchain, route/method inventory and command outputs. Validation uses scripts/ci_verify.py with synthetic configuration, HOME, credentials and sessions. Output directories are supplied externally; the repository contains no test runtime data.

Intermediate full suite: 2477 collected, 2437 passed, 0 failed, 40 skipped, coverage 81.69598556608028%, exit 0. Targeted platform suite after fault tests: 47 passed, 1 skipped (Windows symlink privilege), exit 0. Browser acceptance passed with no uncaught script errors using a fresh headless Edge context and deterministic model transport.

Docker CLI is installed; daemon health returned unavailable. Real container limits/network/process security tests are environment-blocked. No real-model credentials were read or used, so real-model smoke is environment-blocked. Governed code-change automation uses a deterministic model and fake backend with the real queue, ToolExecutor, approval, files, patch, export and restart replay chain. It does not demonstrate an actual Docker test command.

Known MVP boundaries are documented in architecture/platform-web-v1 and security/sandbox-boundary: no interactive PTY, no network allowlist, unknown provider capabilities/context length, classic skill/plugin lifecycle UI, escaped basic Markdown preview, local-only bounded Git clone, label-verified orphan recovery, no power-loss atomicity across multiple patch files. No SaaS/microVM/security guarantees are implied.

External release evidence includes package hashes, installation/base/native/desktop entry checks, browser screenshots, reviewer dispositions, full-suite counters and local commit metadata. Public reports intentionally contain no machine-specific absolute paths or secrets.

## Independent review dispositions

Security reviewer: three High findings (host filesystem TOCTOU from container mutation, failed create/failed cleanup ownership loss, special-file event-loop blocking), and three Medium findings (orphan recovery, host append special files, Windows long argv). All closed. Isolated I/O now uses a container stdin broker; snapshot capture freezes the container; regular-file reads validate the opened descriptor; cleanup retains ownership; recovered containers require tenant labels; 100 KB writes never enter argv.

Platform reviewer: original High terminal-state regression and five Medium findings (deprecated binding bypass, wrong test Agent, refresh draft loss, attachment cross-session state, global run order). Follow-up checks found in-flight POST/upload session races and version evolution of existing sessions. The final admission follow-up fixed the dynamically selected tenant owner and added a real ASGI shutdown barrier. All closed and final independent read-only signoff received. Browser Event barriers reproduce draft, send and upload races; old sessions retain v1 after v2 publication. Action buttons inside the composer explicitly use type=button.

Both reviewers inspected final code and evidence; they did not rerun the entire suite independently. The root implementation agent runs all final regression/install checks. No reviewer modified files.

## Validation and reproduction

Run outside-output validation with:

```powershell
py -3.13 -B scripts/ci_verify.py prepare --output-dir $CHECKPOINT/validation
py -3.13 -B scripts/ci_verify.py test --output-dir $CHECKPOINT/validation --suite full
py -3.13 -B scripts/ci_verify.py static --output-dir $CHECKPOINT/validation
py -3.13 -B scripts/ci_verify.py package --output-dir $CHECKPOINT/validation
py -3.13 -B scripts/platform_browser_smoke.py --output-dir $CHECKPOINT/browser
```

All successful verification commands exit 0. Earlier red runs are retained in the checkpoint: initial full regression exposed six contract/inventory issues, which were fixed rather than removed. Browser harness locator/environment issues and an offline-loopback test assumption were corrected; the final browser test explicitly disconnects while offline and confirms EventSource reconnect/catch-up.

Latest reviewed full suite before the final two regression additions: 2505 collected, 2464 passed, 0 failed, 41 skipped, coverage 82.73774828498408%. The Windows symlink test accounts for one additional privilege-dependent skip; real Docker resource enforcement tests are reported as blocked rather than fake passes. A real synthetic local Git repository validates committed snapshot cloning and cleanup. The service admission regression verifies shutdown cancels an in-flight platform request.

Browser: 12 pages and 14 checks, no uncaught JavaScript errors, screenshots including a mobile view. API failure injection intentionally logs one handled error. WebView: actual hidden window loaded the dashboard, exit 0. Wheel/sdist resource checks include platform.html and index.html. Base/native/desktop installation, isolated import, CLI help, native offscreen entry, local HTTP server and WebView checks passed. Reinstallation forces the current wheel even when the version remains 0.1.0.

## Remaining boundaries

The platform uses the existing single local tenant/session ownership model. Published Agent versions are immutable, while explicitly versioned knowledge sources remain independently replaceable. Approval UI offers one-shot grant/reject; modifying a proposed operation means rejecting and resubmitting, and persistent trust is not silently enabled. There is no interactive PTY. Model capability/context fields are unknown without provider evidence. These are explicit MVP limitations, not claims of a complete SaaS or perfect isolation.

Protected workspace verification is read-only: RC retains the baseline commit with a clean status; main retains its original HEAD and pre-existing engineering changes. No task command writes either workspace. The final external manifest records exact HEAD/status evidence. No push, PR, tag or remote release was performed.

## Final precommit verification

2508 collected, 2467 passed, 0 failed, 41 skipped, coverage 82.94563219936354%; exit 0. The final targeted Platform suite has 59 passed and 1 privilege-dependent skip. These results include local Git cloning and default/non-default tenant service-owned HTTP shutdown. Postcommit results and the exact commit SHA are recorded in the external delivery manifest to avoid a self-referential commit hash in this file.
