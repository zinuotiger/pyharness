# PyHarness Platform Web V1 verification report

## Original local acceptance (40b80541)

Status: local MVP implemented; independent review passed. Local Docker and real-model acceptance remain environment-blocked; see the remote follow-up for confirmed Ubuntu Docker results. Final commit-bound verification is recorded in the external delivery manifest.

Baseline: ad9a0e8591999b6c81766b05f98ebceca93fd56e. Branch: feat/pyharness-platform-web-v1. Three read-only design agents and two independent read-only reviewers completed. Main and RC worktrees are protected. The original local-only acceptance did not authorize push; the remote follow-up below authorizes the feature branch and a stacked Draft PR only.

The checkpoint is external to the repository and contains original tracked-file hashes, identity, toolchain, route/method inventory and command outputs. Validation uses scripts/ci_verify.py with synthetic configuration, HOME, credentials and sessions. Output directories are supplied externally; the repository contains no test runtime data.

Intermediate full suite: 2477 collected, 2437 passed, 0 failed, 40 skipped, coverage 81.69598556608028%, exit 0. Targeted platform suite after fault tests: 47 passed, 1 skipped (Windows symlink privilege), exit 0. Browser acceptance passed with no uncaught script errors using a fresh headless Edge context and deterministic model transport.

Docker CLI is installed; daemon health returned unavailable. At the original local acceptance, real container limits/network/process checks were environment-blocked. No real-model credentials were read or used, so real-model smoke is environment-blocked. Governed code-change automation uses a deterministic model and fake backend with the real queue, ToolExecutor, approval, files, patch, export and restart replay chain. It does not demonstrate an actual Docker test command.

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

Protected workspace verification is read-only: RC retains the baseline commit with a clean status; main retains its original HEAD and pre-existing engineering changes. No task command writes either workspace. The final external manifest records exact HEAD/status evidence. At the original local checkpoint no push or PR had been performed. The remote follow-up below records the subsequently authorized push and Draft PR. No tag or Release is created.

## Original local precommit verification

2508 collected, 2467 passed, 0 failed, 41 skipped, coverage 82.94563219936354%; exit 0. The final targeted Platform suite has 59 passed and 1 privilege-dependent skip. These results include local Git cloning and default/non-default tenant service-owned HTTP shutdown. Postcommit results and the exact commit SHA are recorded in the external delivery manifest to avoid a self-referential commit hash in this file.

## Remote acceptance follow-up

Starting Platform commit: `40b80541c41038c161e73b96be6f6ab0e7d9e6fa`. The stacked Draft PR targets `codex/pyharness-engineering-rc-20260924` from `feat/pyharness-platform-web-v1`. [Draft PR #2](https://github.com/zinuotiger/pyharness/pull/2) is open against the RC branch. The first PR workflow completed with a Windows browser startup failure; Ubuntu and real Docker results are confirmed below.

The existing RC Windows/Ubuntu matrix and layered gates are preserved. Added checks cover Platform APIs, system-browser automation at 1440x900 and 1280x900, 204/404/409/422/503 handling, public path/secret scans, and an independent real Docker job. Docker acceptance uses the fixed versioned Python image and a deterministic model with real queue/governance/approval/artifacts. Missing Docker/image, skipped Docker cases or owned container residue fail the job. The runner records actual image ID, daemon version, non-root user, mount/network/resource policy, process cleanup, artifact integrity and restart replay. Failed or incomplete foreground/background validation blocks patch application.

Linux package acceptance validates base installation/import/CLI; HTTP and browser checks use the isolated source environment. Windows additionally checks installed native/desktop entry points, local HTTP and an actual hidden WebView. Artifact uploads are allowlisted with seven-day retention. Exact tested commit SHA is recorded in each CI summary and the PR head; it is not self-embedded in a commit that contains this report.

Real-model status remains `environment-blocked / manual acceptance pending`. The optional explicit one-call smoke and its budget/billing boundary are documented in [remote acceptance guide](../docs/user-guide/platform-remote-acceptance.md). CI uses fake transport only. No model credentials are added to Actions. Host-approved execution is not OS isolation; no Windows Docker Desktop, microVM, hostile multi-tenant formal proof, network allowlist, PTY or power-loss atomicity claim is made.

### First remote run and bounded repair

[PR workflow 36194415836](https://github.com/zinuotiger/pyharness/actions/runs/36194415836) tested 5cf4ced8931c4da94d4f26c54dacb6ad0d1da817. Ubuntu Python 3.11/3.13 and Windows layered gates passed. Windows full pytest and package/HTTP/WebView passed, but installed Edge rejected DevTools startup before page tests. Repair round 1 creates the standard AppData/Local and AppData/Roaming directories beneath the synthetic USERPROFILE. Windows known-folder lookup otherwise fails and Chromium rejects DevTools despite its fresh profile. Existing Chrome is preferred, with Edge retained as a fallback; a real Windows known-folder subprocess regression verifies the isolated path. Local browser acceptance then passed without changing UI assertions or required gates.

The real Docker job passed **13 collected / 13 passed / 0 failed / 0 skipped** with daemon **28.0.4**, image python:3.13.2-slim-bookworm (sha256:126799e6232bdb19aaaa0ef504f10bb25f3ee1cb05ca7fb6fa5be18cb2385b9a). Isolated runtime is **remote-confirmed on Ubuntu GitHub-hosted runner** for that commit. Checks confirm non-root/private mounts/no socket/read-only root, controlled host-network probe blocking, CPU/memory/PID configuration, actual wallclock/output bounds, process/container/workspace cleanup, no host fallback, approved artifact integrity and restart replay. Successful, failing foreground and failing/incomplete background code-change branches passed. Browser health was available with all 12 pages and zero uncaught errors. Final-head revalidation remains required.

### Confirmed matrix and screenshot evidence follow-up

[PR workflow 36196179295](https://github.com/zinuotiger/pyharness/actions/runs/36196179295) passed all five jobs for `507b742a7b7de85bd5ad16fb486a54189d68dc62`: Windows Python 3.13, Ubuntu Python 3.11 and 3.13, real Docker, and the preserved Windows layered security/acceptance/E2E/invariant/structural gates. Windows browser startup, package, HTTP and hidden WebView now pass. No gate was cancelled or marked continue-on-error.

Visual inspection of earlier Linux screenshots exposed missing CJK fonts and a navigation/screenshot race. The follow-up provisions Noto CJK, verifies actual rendered Chinese glyph fonts through the browser protocol, and waits for the target page and completed skeleton removal before capture. Local screenshots and browser contracts pass; no product feature or page is added. These evidence corrections are repair round 2, within the three-round cap. Prior-run screenshots are historical only.

Latest local regression: **2525 collected, 2484 passed, 0 failed, 41 skipped, 83.08484441459592% coverage**. Platform: **76 passed, 1 Windows privilege-dependent skip**. Browser, authorization AST, syntax/path/secret scan, wheel/sdist and base/native/desktop installation with HTTP and hidden WebView pass. Two independent reviewers closed all three confirmed Medium findings (background validation, HTTP status UI coverage and typed sandbox Trace identity); no confirmed Critical/High/Medium remains in this change.

Source reports name the already-tested code commit above. The exact final report/CI commit, its five PR job URLs, fresh local regression and rebuilt package hashes are recorded in the external commit-bound delivery copies of these reports and the PR summary, avoiding a self-referential Git hash. The final candidate must use that exact head; an earlier successful run or package cannot substitute for it. Real models remain **environment-blocked / manual acceptance pending**.

### Third and final remote repair: background completion fence

[PR workflow 36196772651](https://github.com/zinuotiger/pyharness/actions/runs/36196772651), commit `8fa46f2c32535c76a687c43c24800dfc6d742178`, passed Windows and both Ubuntu core jobs, including readable Chinese screenshots. Its real Docker suite failed one background-process case (12 passed, 1 failed, 0 skipped): the test report contained a successful exit code around snapshot pause/unpause. This log establishes a completion-state race; it does not independently prove a Docker CLI defect.

The final repair copies every owned background-process state before the first asynchronous log append or snapshot freeze. Running, cancelled, error, missing and non-integer exit codes become -1 and cannot later be upgraded by snapshot-side completion. Deterministic tests cover two processes, a yielding log append, freeze-time changes and conflicting success codes. The original real Docker nonzero-exit assertion is retained, with stronger patch validation-state and actual exit-code evidence. Independent security review accepts the code fix; final remote closure requires the exact new head's Docker job. This is repair round 3 of 3; further failing remote gates must remain explicit blockers.

Final repair local checks: 2532 collected, 2491 passed, 0 failed, 41 skipped, coverage 83.0863589559923%; Platform 83 passed, 1 skipped. Browser, archive/install/HTTP/WebView, authorization and public scans passed. Real Docker remains a remote gate.
