# Platform remote acceptance

The Platform pull request targets `codex/pyharness-engineering-rc-20260924`, not main. Keep both pull requests Draft and unmerged. CI checks out the PR head SHA; a successful push run does not replace the PR run. The existing Engineering RC matrix and layered gates remain required.

The matrix runs Windows Python 3.13 and Ubuntu 24.04 Python 3.11/3.13, with full coverage (75% minimum), Platform API/approval/artifact tests, existing system-browser automation, packaging and authorization checks. Windows additionally validates native and desktop installs and an actual hidden WebView. No browser bundle download is needed.

The separate Ubuntu Docker job provisions only `python:3.13.2-slim-bookworm`, records its resolved image identity and daemon version, and invokes `scripts/ci_verify.py test --suite docker --output-dir <external-directory>`. Real Docker acceptance is explicitly collected with `--real-docker`; missing Docker/image and any skip fail this suite. It uses synthetic input, a local controlled network probe, bounded output/timeouts and a deterministic model. No paid model credentials are needed. Runtime image pulling and host fallback remain forbidden.

Container flags and real checks cover a private bind-mounted workspace, non-root identity, dropped capabilities, no-new-privileges, read-only root, writable workspace/tmp, no network, no Docker socket, CPU/memory/PID settings, cancellation and cleanup. This confirms the tested Ubuntu runner/image combination only. It is not a Windows Docker Desktop, microVM, hostile multi-tenant or zero-risk security guarantee. `host_approved` remains host execution without OS isolation.

## Optional local real-model smoke

`python -B scripts/platform_real_model_smoke.py` refuses by default. Only an explicit `--execute`, endpoint, model, named credential environment variable and positive model pricing enable a call. Set the named variable locally without printing it; never put it in CI or a command-line literal. For example:

```text
python -B scripts/platform_real_model_smoke.py --execute --base-url https://provider.example/v1 --model selected-model --key-env MY_EXPLICIT_MODEL_KEY --input-price 1 --output-price 2 --max-cost 0.02 --max-tokens 128 --timeout 15
```

This is one non-streaming call with no tools and execution disabled. It reads only the bundled code-change sample and prints structured status/usage, never model text, headers or secrets. There are no retries and no writes to a user project. Maximum output is 256 tokens, timeout 30 seconds and accepted estimated cost ceiling USD 0.05. The estimate conservatively bounds input by UTF-8 bytes plus overhead and requires operator-supplied per-million-token prices; provider billing cannot be guaranteed by client code. Full real-model governed code-change acceptance remains a separate manual step with a safely configured isolated sandbox.

CI tests argument handling, refusal without credentials, fake transport, budgets, timeout and redaction. A fake transport pass is never reported as real-model success. Workflow artifacts contain only allowlisted summaries, sanitized JUnit, screenshots and distributions; raw HOME, configs, sessions and credential files are excluded.
