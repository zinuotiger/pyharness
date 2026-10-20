"""Server-owned fixed skill views and explicit isolated wiring rehearsals."""
from __future__ import annotations
import asyncio
import copy
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pyharness.application.mcp_workbench import _key
from pyharness.application.skill_rehearsal import exercise
from pyharness.core.sandbox import fail


class SkillWorkbench:
    def __init__(self, platform):
        self.platform = platform

    def capture(self, name, *, authorize):
        authorize()
        if type(name) is not str or len(name) > 64:
            fail('invalid_skill_name')
        mgr = self.platform.service.skills_mgr().frozen_copy([name])
        snapshot = mgr.snapshot(name)
        if len(json.dumps(snapshot, ensure_ascii=False).encode('utf-8')) > 65536:
            fail('skill_snapshot_limit')
        ident = 'ss-' + uuid.uuid4().hex
        row = {'snapshot_id': ident, 'captured_at': datetime.now(timezone.utc).isoformat(),
               'snapshot': snapshot}
        authorize()
        with self.platform.store.transaction() as data:
            rows = data['preferences'].setdefault('skill_snapshots', {})
            if len(rows) >= 64:
                fail('skill_snapshot_history_limit')
            rows[ident] = row
        return copy.deepcopy(row)

    def snapshot(self, ident, *, authorize):
        authorize()
        row = self.platform.store.read()['preferences'].get('skill_snapshots', {}).get(_key(ident))
        if not row:
            fail('skill_snapshot_not_found')
        return row

    def result(self, key, *, authorize):
        authorize()
        row = self.platform.store.read()['preferences'].get('skill_rehearsals', {}).get(_key(key))
        if not row:
            fail('skill_rehearsal_not_found')
        if row['status'] == 'requested':
            row['status'] = 'unconfirmed'
        return row

    async def rehearse(self, ident, body, *, authorize):
        authorize()
        if type(body) is not dict or set(body) != {'command_key', 'confirm_controlled_rehearsal', 'loaded_sha256'}:
            fail('invalid_skill_rehearsal')
        if body['confirm_controlled_rehearsal'] is not True:
            fail('skill_rehearsal_confirmation_required')
        key = _key(body['command_key'])
        snapshot = self.snapshot(ident, authorize=authorize)['snapshot']
        if body['loaded_sha256'] != snapshot['loaded_sha256']:
            fail('skill_snapshot_integrity')
        previous = self.platform.store.read()['preferences'].get('skill_rehearsals', {}).get(key)
        if previous:
            if previous['snapshot_id'] != ident or previous['loaded_sha256'] != snapshot['loaded_sha256']:
                fail('skill_rehearsal_key_conflict')
            return self.result(key, authorize=authorize)
        lock = self.platform.locks.setdefault('skill-rehearsal', asyncio.Lock())
        if lock.locked():
            fail('skill_rehearsal_busy')
        async with lock:
            authorize()
            with self.platform.store.transaction() as data:
                rows = data['preferences'].setdefault('skill_rehearsals', {})
                if key in rows:
                    fail('skill_rehearsal_key_conflict')
                if len(rows) >= 64:
                    fail('skill_rehearsal_history_limit')
                rows[key] = {'command_key': key, 'snapshot_id': ident,
                             'loaded_sha256': snapshot['loaded_sha256'], 'status': 'requested'}
            directory = self.platform.root/'diagnostics'/'skills'/hashlib.sha256(key.encode()).hexdigest()
            result = {'status': 'unconfirmed'}
            action = 'skill-rehearsal:' + key
            self.platform.actions[action] = asyncio.current_task()
            try:
                async with asyncio.timeout(20):
                    result = await exercise(snapshot, directory, self.platform.service.tenant_id)
            except asyncio.CancelledError:
                result = {'status': 'unconfirmed', 'error_code': 'skill_rehearsal_cancelled'}
                raise
            except Exception:
                result = {'status': 'failed', 'error_code': 'skill_rehearsal_failed'}
            finally:
                result['finished_at'] = datetime.now(timezone.utc).isoformat()
                try:
                    with self.platform.store.transaction() as data:
                        data['preferences']['skill_rehearsals'][key].update(result)
                finally:
                    self.platform.actions.pop(action, None)
            authorize()
            return self.result(key, authorize=authorize)
