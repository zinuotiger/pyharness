# PyHarness Engineering RC — remote Windows and POSIX validation

Draft PR: [#1](https://github.com/zinuotiger/pyharness/pull/1), `main` ← `codex/pyharness-engineering-rc-20260924`. Package version remains **0.1.0**. Pushed; unmerged; no tag or GitHub Release.

Base: `41d74db15f47bee5813976bd11435dff1be166ef`. Initial locally accepted RC: `3101ca96ff4a2121fef8cf62ddda90a57eaa2089`. The remote evidence below verifies **`1c063da32f3ca563d6f026aacb81657232a99d36`**. This documentation commit is followed by another complete remote matrix; exact final SHA, local rerun and rebuilt archive hashes belong to the postcommit attestation distributed alongside the final artifacts and recorded in the PR. A report cannot embed the hash of its own future commit/archive.

## Remote result

[Workflow run 36079689277](https://github.com/zinuotiger/pyharness/actions/runs/36079689277) completed successfully. All four jobs check out the candidate head, not a synthetic merge commit.

| Runner OS / Python | collected | passed | failed | skipped | coverage | exit |
|---|---:|---:|---:|---:|---:|---:|
| Linux / 3.11.16 | 2413 | 2393 | 0 | 20 | 83.24% | 0 |
| Linux / 3.13.15 | 2413 | 2393 | 0 | 20 | 83.28% | 0 |
| Windows / 3.13.15 | 2413 | 2374 | 0 | 39 | 83.44% | 0 |

- [ubuntu-24.04 / Python 3.13 / full, permissions, package](https://github.com/zinuotiger/pyharness/actions/runs/36079689277/job/107898671821): success; 2026-09-25T00:54:55Z → 2026-09-25T00:57:28Z.
- [ubuntu-24.04 / Python 3.11 / full, permissions, package](https://github.com/zinuotiger/pyharness/actions/runs/36079689277/job/107898671913): success; 2026-09-25T00:54:55Z → 2026-09-25T00:57:30Z.
- [windows-latest / Python 3.13 / full, permissions, package](https://github.com/zinuotiger/pyharness/actions/runs/36079689277/job/107898671998): success; 2026-09-25T00:54:58Z → 2026-09-25T00:58:34Z.
- [Windows / Python 3.13 / security, acceptance, e2e, invariants, structure](https://github.com/zinuotiger/pyharness/actions/runs/36079689277/job/107898672169): success; 2026-09-25T00:54:55Z → 2026-09-25T00:55:58Z.

Every matrix job passed isolated installation, authorization AST/Git checks, hostile HOME/configuration/plugin/MCP sentinels, full pytest, 75% coverage, high-risk regression, wheel/sdist build, and fresh base wheel installation outside the checkout. Windows additionally passed fresh native/desktop import and entry prechecks (Qt offscreen and loopback HTTP ready/stop/port release). Windows security, acceptance, e2e, invariant and structure gates also passed. JUnit and JSON summaries in workflow artifacts preserve counts and omit raw output, secret bodies and parameter values. Artifacts expire after seven days; the local delivery retains copied evidence.

## R02 real POSIX closure

**R02: confirmed-fixed**, limited to the recorded GitHub-hosted Ubuntu 24.04 environments.

- Ubuntu 24.04 / Python 3.11.16: 20 collected / 20 passed / 0 failed / 0 skipped, exit 0; effective UID 1001; umask 022.
- Ubuntu 24.04 / Python 3.13.15: 20 collected / 20 passed / 0 failed / 0 skipped, exit 0; effective UID 1001; umask 022.

`tests/unit/test_posix_secret_permissions.py` uses real `os.open`, `os.write`, `os.fstat`, permission changes and atomic replace. It observes a zero-byte temporary file already at **0600** before first write, checks actual mode during short writes and after replacement, and checks private residue on injected replace/cleanup failure. Permission failures abort explicitly and preserve the previous committed configuration. Synthetic secrets are absent from captured logs/errors; deliberate fault residue is removed in finally. Traversal, malicious generation names, directory/generation symlinks and replace-over-symlink boundaries are exercised. The dedicated suite rejects root, zero collection and any skipped case. Windows platform skips never count as POSIX validation. These tests do not prove resistance to every hostile concurrent filesystem mutation or correctness on every POSIX system.

## Reused local baseline and this task's scope

F01–F15, R03–R04, G01–G02 remain confirmed-fixed. R01 stays safely-constrained; unsafe PTY paths remain disabled. Existing runtime fixes cover JSONL partial writes, lock/handle ownership, save failures, tenant secrets, cancellation, process/transport cleanup and entry points. This remote task adds CI and narrowly fixes three production modules: `core/spill.py`, `core/llm_fallback.py` and `core/tools_guard.py`. The new tests preserve authorization, side-effect and cleanup assertions.

Initial RC local pytest: 2383 collected / 2364 passed / 0 failed / 19 skipped; coverage 83.42%. High-risk selection 161/161, fault injection 61/61, Hypothesis 6/6, concurrency/ownership 19/19. Selections overlap; six property tests is not a generated-sample count. Initial representative mutation detected 10/10 (9 assertion failures, 1 bounded timeout), not a whole-project score. These are historical baseline statistics, not final-commit rerun claims. The final exact commit receives fresh local full/coverage, selected high-risk, R1 integration, representative mutation and base/native/desktop installation checks; the external final attestation and Draft PR record their outcomes. Original selection IDs remain in JSON.

Fixed evleven R1: 16/16 separate real MCP tests, wheel SHA256 `40d5cbd5ae312b4bb3af133405807fb9764653b95145a80e4633bed17039ea2e`. Runtime, governance, process/transport and persistence are real; model planning is deterministic. R1 is not provisioned in hosted CI; those 16 tests remain explicitly skipped there. Keyword/ID recovery does not establish semantic retrieval or autonomous model planning.

Pre-push Windows rerun after adding 20 POSIX cases: 2403 collected / 2364 passed / 39 skipped / 0 failed; coverage 83.43%. Windows skips include the 20 new POSIX-only cases. Fresh local CI-driver preparation, isolation and base/native/desktop package prechecks also passed; these provisional archives are not the final rebuilt distribution.

## CI failures and bounded corrections

The initial workflow failed because depth-one checkout made the Git whitespace check treat historical content as additions. Fetch depth two supplies the parent; the gate remains. Ubuntu then failed collecting Qt tests because `libEGL.so.1` was missing. Only minimal `libegl1` / `libopengl0` runtime libraries were added; no full desktop/service, deleted test, reduced assertion, unexplained skip, coverage reduction or `continue-on-error` was used. Independent POSIX/high-risk/package stages collect evidence after other failures without making the failed job green.

The third and final remediation round addresses errors exposed after collection succeeded. A spill directory set to 0600 was inaccessible to a non-root POSIX owner; directories now use 0700, while temporary files are exclusively created at 0600 before any write. Collision cleanup never removes another existing file. Python 3.11 uses `Path.open(newline="")` for lossless text reads, since `Path.read_text(newline=...)` is a newer API. Deterministic local Python 3.11 evidence also reproduced health-probe completion racing cancellation; `asyncio.timeout` in the owner task preserves cancellation, avoids an abandoned probe and retains real deadlines. Regression tests cover both successful and failing ping completion and timeout resource release. Windows drive-letter paths, junction commands and Windows Job fault injection are replaced on POSIX with actual outside paths, symlinks and a real post-spawn failure boundary. POSIX credential fixtures are private at creation; the negative loose-permission test remains intact. Windows Python 3.11 also lacked os.path.isjunction and skipped final junction containment; exact mount-point reparse-tag detection now retains the same refusal for junctions and child paths, backed by real Windows junction tests. No supported Python version was removed.

## Reproduction and remaining limits

See [docs/CI.md](../docs/CI.md). `scripts/ci_verify.py` requires a separate output directory. Frozen `uv.lock` dependencies install into fresh environments. HOME/config/cache/temp/coverage/artifacts remain isolated; tests block non-loopback networking and undeclared subprocesses. All data are synthetic. Linux uses real non-root permissions; Windows prechecks cover loopback addresses and resource cleanup. Workflow permission is only `contents: read`, with no `pull_request_target`.

No real conversation model, paid API, model download, complete manual GUI/WebView2, WSL/Docker production deployment or all-platform guarantee is claimed. Client cancellation/timeout cannot establish remote stop or undo side effects. Unknown remote writes must be reconciled without blind retry. Ruff remains unavailable and is not a gate; no formal type-check gate exists. Installation may require package-index access. R01 remains constrained. Final wheel/sdist are rebuilt from the final PR head and delivered with SHA256SUMS; historical archive hashes in JSON are explicitly baseline-only.
