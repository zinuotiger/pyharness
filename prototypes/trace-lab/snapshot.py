"""Read local logs without opening a runtime; export allowlisted observation facts only."""
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pyharness.application.platform_projection import project

root = Path.home() / '.pyharness'
def error_code(value):
    return value if value is None or re.fullmatch(r'[A-Za-z0-9_.:-]{1,80}', str(value)) else 'unknown_error'
paths = sorted(root.rglob('sessions/*.jsonl')) if root.exists() else []
runs, skipped, event_count = [], 0, 0
skip_reasons = Counter()
for path in paths:
    try:
        rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
        events = [SimpleNamespace(**{k: row.get(k) for k in ('seq', 'ts', 'type', 'session_id', 'task_id', 'payload', 'trace')}) for row in rows]
        sid = rows[0]['session_id'] if rows else path.stem
        # No live owner is attached to this historical snapshot. The production
        # projection marks unclosed tasks interrupted instead of claiming live execution.
        view = project(sid, events, live_tasks=(), trace_limit=None)
        event_count += len(events)
        for run in view['runs']:
            spans = [s for s in view['trace'] if s['run_id'] == run['run_id']]
            runs.append({'run': run, 'spans': spans})
    except Exception as exc:
        skipped += 1
        skip_reasons[type(exc).__name__] += 1

runs.sort(key=lambda row: row['run'].get('started_at') or row['run'].get('queued_at') or '', reverse=True)
counts = Counter(row['run']['status'] for row in runs)
safe_runs = []
for index, row in enumerate(runs[:40]):
    run = row['run']
    ids = {s['span_id']: f'n{i+1}' for i, s in enumerate(row['spans'])}
    safe_runs.append({
        'id': f'history-{index+1}', 'name': f'本机历史任务 {index+1:02d}',
        'status': run['status'], 'started_at': run['started_at'] or run['queued_at'],
        'finished_at': run['finished_at'], 'error_code': error_code(run['error_code']),
        'spans': [{
            'id': ids[s['span_id']], 'parent': ids.get(s['parent_span_id']),
            'missing_parent': bool(s['parent_span_id'] and s['parent_span_id'] not in ids),
            'name': s['type'], 'type': s['type'], 'status': s['status'],
            'started_at': s['started_at'], 'finished_at': s['finished_at'],
            'duration_ms': s['duration_ms'], 'error_code': error_code(s['error_code']),
        } for s in row['spans'][:500]],
        'total_spans': len(row['spans']),
    })
snapshot = {'captured_at': datetime.now(timezone.utc).isoformat(), 'file_count': len(paths),
            'skipped_files': skipped, 'event_count': event_count, 'total_runs': len(runs),
            'counts': dict(counts), 'skip_reasons': dict(skip_reasons), 'runs': safe_runs,
            'note': '只读本机日志快照；任务名称与标识匿名化。未连接运行所有者，未闭合任务按现有投影标为中断。'}
Path(__file__).with_name('history.js').write_text('window.TRACE_HISTORY = ' + json.dumps(snapshot, ensure_ascii=False, indent=2) + ';\n', encoding='utf-8')
print(json.dumps({k: snapshot[k] for k in ('file_count','skipped_files','event_count','total_runs','counts')}, ensure_ascii=False))
