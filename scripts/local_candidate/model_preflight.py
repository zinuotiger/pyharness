"""Configuration-only check: never connects to a model or reads credential files."""
import argparse
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('template', type=Path)
    args = p.parse_args()
    data = json.loads(args.template.read_text(encoding='utf-8'))
    mode = data.get('mode')
    if mode == 'deterministic':
        print('A: deterministic command planner; no model inference; use start.ps1')
        return 0
    if mode not in ('local', 'external'):
        raise ValueError('mode must be deterministic/local/external')
    from pyharness.config import LlmCfg
    cfg = LlmCfg.model_validate(data['llm'])
    url = urlsplit(cfg.base_url)
    if url.username or url.password or url.query or url.fragment:
        raise ValueError('Endpoint must not embed credentials, query or fragment')
    if mode == 'local' and url.hostname not in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('Local model endpoint must be loopback')
    if not cfg.api_key.startswith('env:'):
        raise ValueError('Candidate template accepts only explicit env:NAME credentials')
    present = bool(os.environ.get(cfg.api_key[4:]))
    issues = []
    if not data.get('model_source') or 'REPLACE' in str(data['model_source']):
        issues.append('model_source must identify the actual model and its provenance')
    if 'REPLACE' in cfg.model:
        issues.append('llm.model must be the actual served model ID')
    if not present:
        issues.append('explicit credential environment variable is absent (value never printed)')
    print(json.dumps({'mode':mode, 'provider':'existing OpenAICompatAdapter / chat/completions',
        'credential_present':present, 'issues':issues, 'network_requests':0,
        'inference_verified':False, 'external_budget_authorized':False}, indent=2))
    return 2 if issues else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        # Validation errors can include input values; do not echo them.
        print(f'Preflight failed: {type(exc).__name__}; check template fields, no values printed.', file=sys.stderr)
        sys.exit(2)
