"""Read-only projection of historical JSONL. No runtime or credential loading."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def replay(source):
    from scripts.ci_verify import install_network_boundary
    install_network_boundary()
    from pyharness.core import llm
    def forbidden(*args, **kwargs):
        raise AssertionError('Replay cannot resolve a credential')
    llm.resolve_secret_ref = forbidden
    from pyharness.application.platform_projection import project
    raw = source.read_bytes()
    rows = [json.loads(line) for line in raw.decode('utf-8').splitlines() if line.strip()]
    events = [SimpleNamespace(**row) for row in rows]
    view = project(rows[0]['session_id'], events)
    requests = [e for e in events if e.type == 'llm.request']
    usages = [e for e in events if e.type == 'llm.usage']
    result = {'source_sha256': hashlib.sha256(raw).hexdigest(),
        'runs': [{k:r[k] for k in ('run_id','status','finished_at','error_code','error_diagnostics',
                                  'usage_status','usage_unknown_requests')} for r in view['runs']],
        'tool_steps': [s for s in view['steps'] if s['name'].startswith('fs.')],
        'requests': len(requests), 'usage_records': len(usages),
        'known_input_tokens': sum(e.payload['in_tokens'] for e in usages),
        'known_output_tokens': sum(e.payload['out_tokens'] for e in usages),
        'missing_usage': 'unknown' if len(usages) < len(requests) else None,
        'approvals': len(view['approvals']),
        'artifacts': sum(e.type == 'platform.artifact' for e in events),
        'real_model_requests': 0, 'credential_resolution': False}
    assert source.read_bytes() == raw
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve() == args.input.resolve() or args.output.exists():
        parser.error('output must be a new file, distinct from source')
    result = replay(args.input)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'statuses':[r['status'] for r in result['runs']],
                      'usage_records':result['usage_records'], 'requests':result['requests']}))
