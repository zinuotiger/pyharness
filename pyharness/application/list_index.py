"""Disposable tenant-local list projections. SessionLog remains the fact source.

Only changed sessions are replayed. Pages are selected in SQLite before JSON
decoding; every request supplies a fresh authorized session set. Cursors are
keyset positions, so deleting their anchor does not skip the following row.
"""
from __future__ import annotations

import base64
import asyncio
import hashlib
import json
import sqlite3
from contextlib import contextmanager

from pyharness.core.sandbox import fail


class ListIndex:
    def __init__(self, path):
        self.path = path
        self.scope = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:24]
        self.metrics = {}
        self._refresh_lock = asyncio.Lock()

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path)
        try:
            db.execute('CREATE TABLE IF NOT EXISTS sources (sid TEXT PRIMARY KEY, stamp TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS rows (kind TEXT, id TEXT, sid TEXT, position TEXT, payload TEXT, PRIMARY KEY(kind,id))')
            db.execute('CREATE INDEX IF NOT EXISTS list_order ON rows(kind,position,id)')
            db.execute('CREATE INDEX IF NOT EXISTS list_source ON rows(sid)')
            yield db
            db.commit()
        finally:
            db.close()

    async def refresh(self, service):
        # A single service owns this derived index. Never let a second request
        # synchronously wait for SQLite while its writer awaits session repair.
        async with self._refresh_lock:
            return await self._refresh(service)

    async def _refresh(self, service):
        sessions = (await service.list_sessions())['sessions']
        authorized = {r['sid'] for r in sessions}
        self.metrics = dict(scanned_sessions=0, projection_count=0,
                            cache_hit=0, cache_miss=0, index_hit=0, returned_rows=0,
                            checked_sessions=len(sessions))
        from pyharness.application.platform_projection import project
        from pyharness.application.platform_models import ArtifactRecord
        # Load source facts before opening any SQLite write transaction. Failed
        # or cancelled reads leave the previous index intact and release the gate.
        with self.connect() as db:
            prior_stamps = dict(db.execute('SELECT sid,stamp FROM sources'))
        changed = []
        for row in sessions:
            sid = row['sid']
            loaded = service._logs.get(sid)
            seq = loaded.stats()['seq'] if loaded else row.get('seq')
            queue = service._queues.get(sid)
            live = []
            if queue:
                status = queue.status()
                live = [status.running, *status.waiting]
            stamp = json.dumps([row.get('updated'),row.get('size_bytes'),seq,repr(live)])
            if prior_stamps.get(sid) == stamp:
                self.metrics['cache_hit'] += 1
                continue
            self.metrics['cache_miss'] += 1
            self.metrics['scanned_sessions'] += 1
            log = await service.require_session(sid)
            events = list(log.events_after(0))
            stamp = json.dumps([row.get('updated'),row.get('size_bytes'),log.stats()['seq'],repr(live)])
            changed.append((sid,stamp,events,live))
        if changed:
            authorized.intersection_update(r['sid'] for r in (await service.list_sessions())['sessions'])
        with self.connect() as db:
            for sid, in db.execute('SELECT sid FROM sources').fetchall():
                if sid not in authorized:
                    db.execute('DELETE FROM sources WHERE sid=?', (sid,))
                    db.execute('DELETE FROM rows WHERE sid=?', (sid,))
            for sid,stamp,events,live in changed:
                if sid not in authorized:
                    continue
                view = project(sid, events, live, trace_limit=0)
                self.metrics['projection_count'] += 1
                db.execute('DELETE FROM rows WHERE sid=?', (sid,))
                steps = {}
                for step in view['steps']:
                    steps.setdefault(step['run_id'], []).append(step)
                for run in view['runs']:
                    payload = {**run, '_steps':steps.get(run['run_id'], [])}
                    position = run['queued_at'] or run['started_at'] or ''
                    db.execute('INSERT INTO rows VALUES (?,?,?,?,?)', ('runs',run['run_id'],sid,position,json.dumps(payload)))
                artifacts = {}
                for event in events:
                    if event.type == 'platform.artifact':
                        record = ArtifactRecord.model_validate(event.payload['record'])
                        if event.payload['action'] == 'deleted':
                            artifacts.pop(record.artifact_id, None)
                        else:
                            artifacts[record.artifact_id] = record.model_dump()
                for ident, record in artifacts.items():
                    db.execute('INSERT INTO rows VALUES (?,?,?,?,?)', ('artifacts',ident,sid,ident,json.dumps(record)))
                db.execute('INSERT OR REPLACE INTO sources VALUES (?,?)', (sid,stamp))
        return authorized

    def page(self, kind, authorized, *, cursor=None, limit=None):
        if limit is not None and (type(limit) is not int or not 1 <= limit <= 500):
            fail('invalid_page_limit')
        after = None
        if cursor is not None:
            try:
                if not isinstance(cursor,str) or len(cursor)>4096:
                    raise ValueError()
                scope, cursor_kind, sid, position, ident = json.loads(base64.urlsafe_b64decode(cursor.encode()))
                if scope != self.scope or cursor_kind != kind or sid not in authorized or not all(isinstance(v,str) for v in (position,ident)):
                    raise ValueError()
                after = position, ident
            except (ValueError, TypeError, UnicodeError):
                fail('invalid_page_cursor')
        descending = kind == 'runs'
        with self.connect() as db:
            db.execute('CREATE TEMP TABLE authorized (sid TEXT PRIMARY KEY)')
            db.executemany('INSERT INTO authorized VALUES (?)', ((sid,) for sid in authorized))
            predicate = 'kind=? AND sid IN (SELECT sid FROM authorized)'
            total = db.execute('SELECT count(*) FROM rows WHERE '+predicate,(kind,)).fetchone()[0]
            args = [kind]
            if after:
                predicate += ' AND (position,id) '+('<' if descending else '>')+' (?,?)'
                args.extend(after)
            order = 'DESC' if descending else 'ASC'
            sql = 'SELECT id,sid,position,payload FROM rows WHERE '+predicate+' ORDER BY position '+order+',id '+order
            if limit is not None:
                sql += ' LIMIT ?'
                args.append(limit+1)
            records = db.execute(sql,args).fetchall()
        more = limit is not None and len(records)>limit
        records = records[:limit] if limit is not None else records
        next_cursor = None
        if records and more:
            ident,sid,position,_ = records[-1]
            next_cursor = base64.urlsafe_b64encode(json.dumps([self.scope,kind,sid,position,ident]).encode()).decode()
        self.metrics.update(index_hit=1,returned_rows=len(records))
        return [json.loads(r[3]) for r in records], dict(total=total,truncated=more,cursor=next_cursor)
