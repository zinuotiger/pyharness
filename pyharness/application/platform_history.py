"""PlatformHistory operations, separated from facade assembly."""
from __future__ import annotations
import asyncio
import json
from pyharness.application.platform_models import AgentDefinition
from pyharness.application.platform_projection import project, summary
from pyharness.core.sandbox import fail

class PlatformHistory:
    """PlatformHistory responsibility; state is owned by the composing facade."""

    async def all_views(self, *, trace_run_id=None, trace_limit=500):
        result = {'runs':[], 'trace':[], 'steps':[], 'approvals':[]}
        sessions = (await self.service.list_sessions())['sessions']
        self.projection_cache.retain(r['sid'] for r in sessions)
        for row in sessions:
            sid = row['sid']
            log = await self.service.require_session(sid)
            queue = self.service._queues.get(sid)
            live = []
            if queue is not None:
                status = queue.status()
                live = [status.running, *status.waiting]
            stamp = (log.stats()['seq'], repr(live), trace_run_id, trace_limit)
            view = self.projection_cache.read(sid, stamp, lambda: project(
                sid, log.events_after(0), live, trace_run_id=trace_run_id, trace_limit=trace_limit))
            for key in result:
                result[key].extend(view[key])
        return {**result, 'sessions':sessions}

    async def runs(self, *, cursor=None, limit=None):
        authorized = await self.list_index.refresh(self.service)
        selected,meta = self.list_index.page('runs',authorized,cursor=cursor,limit=limit)
        steps = [step for row in selected for step in row.pop('_steps',[])]
        return {'runs':selected, 'steps':steps, **meta}

    async def run(self, ident, *, cursor=None, limit=500):
        if type(limit) is not int or not 1 <= limit <= 500:
            fail('invalid_trace_limit')
        # Resolve only the requested session. Never scan other sessions to read one run.
        if '~' not in ident:
            fail('run_not_found')
        sid, task_id = ident.split('~',1)
        from pyharness.application.session_store import validate_session_id
        validate_session_id(sid)
        if sid not in {row['sid'] for row in (await self.service.list_sessions())['sessions']}:
            fail('run_not_found')
        log = await self.service.require_session(sid)
        queue = self.service._queues.get(sid)
        live = [queue.status().running,*queue.status().waiting] if queue else []
        view = project(sid,log.events_after(0),live,trace_run_id=ident,trace_limit=None)
        item = next((r for r in view['runs'] if r['run_id'] == ident), None)
        if item is None:
            fail('run_not_found')
        trace = [s for s in view['trace'] if s['run_id'] == ident]
        start = 0
        if cursor is not None:
            index = next((i for i, span in enumerate(trace) if span['span_id'] == cursor), None)
            if index is None:
                fail('invalid_trace_cursor')
            start = index + 1
        page = trace[start:start+limit]
        more = start + len(page) < len(trace)
        inputs = [e for e in log.events_after(0) if e.type=='user.message' and
            (e.payload.get('meta') or {}).get('platform',{}).get('task_id')==task_id]
        from pyharness.application.platform_projection import task_events
        owned = list(task_events(log.events_after(0),task_id))
        answers = [e.payload['content'] for e in owned if e.type=='agent.message']
        artifacts = [e.payload['record'] for e in owned if e.type=='platform.artifact']
        return {**item, 'input_summary':summary(inputs[0].payload.get('content',''),2000) if inputs else '',
                'result_summary':summary('\n\n'.join(answers),8000),
                'artifacts':[{k:a.get(k) for k in ('artifact_id','filename','validation_status','size')} for a in artifacts[-50:]],
                'steps':[s for s in view['steps'] if s['run_id'] == ident],
                'trace':page, 'trace_total':len(trace), 'trace_truncated':more,
                'trace_cursor':page[-1]['span_id'] if more else None}

    @staticmethod
    def binding(log):
        for e in log.events_after(0):
            if e.type == 'user.message':
                meta = e.payload.get('meta') or {}
                if isinstance(meta.get('platform'), dict):
                    return meta['platform']
        return None

    async def sessions(self):
        result = await self.service.list_sessions()
        prefs = self.store.read()['session_preferences']
        result['sessions'] = [{**r, **prefs.get(r['sid'], {})} for r in result['sessions']]
        return result

    async def history_search(self, query, sid=None):
        from dataclasses import asdict
        from pyharness.core.session_query import SessionQueryIndex
        if not isinstance(query,str) or not 2 <= len(query.strip()) <= 200:
            fail('invalid_search_query')
        # Authorization defines lifetime; the global window defines visibility.
        # A targeted query may update an authorized source outside that window.
        if sid:
            await self.service.require_session(sid)
        sessions = (await self.service.list_sessions())['sessions']
        authorized_ids = {row['sid'] for row in sessions}
        visible_ids = {sid} if sid else {row['sid'] for row in sessions[:100]}
        sources = {row['sid']: await self.service.require_session(row['sid']) for row in sessions[:100]}
        if sid and sid not in sources:
            sources[sid] = await self.service.require_session(sid)
        async with self.locks.setdefault('history-index',asyncio.Lock()):
            if self.history_index is None:
                index = SessionQueryIndex(self.root/'history-search.db',sources={},result_limit=50,query_timeout_s=3)
                await index.enter()
                self.history_index = index
            index = self.history_index
            scanned = await index.reconcile_sources(sources, authorized_ids=authorized_ids)
            result = await index.query(query,limit=50,session_ids=visible_ids)
            from pyharness.config import redact
            safe=json.loads(redact(json.dumps(asdict(result),ensure_ascii=False)))
            return {**safe,'session_limit':100,'truncated':not sid and len(sessions)>100,'indexed_events':scanned}

    async def edit_message(self, sid, seq, body):
        text=body.get('text')
        if set(body)!={'text'} or not isinstance(text,str) or not 1<=len(text.strip())<=32000:
            fail('invalid_message')
        async with self.locks.setdefault(sid,asyncio.Lock()):
            await self.service.require_session(sid)
            queue=self.service._queues.get(sid)
            if queue is not None and (queue.status().depth or queue.status().running):fail('session_busy')
            return await self.service.edit_message(sid,seq,text,resend=False)

    async def feedback(self,sid,seq,body):
        if set(body)-{'kind','note'} or not isinstance(body.get('note',''),str) or len(body.get('note',''))>500:
            fail('invalid_feedback')
        return await self.service.feedback(sid,seq,body.get('kind'),body.get('note',''))

    async def activity(self,sid):
        log=await self.service.require_session(sid)
        from pyharness.config import redact
        rows=[{'seq':e.seq,'type':e.type,'payload':json.loads(redact(json.dumps(e.payload,ensure_ascii=False)))} for e in log.events_after(0)
              if e.type.startswith(('goal.','todo.','plan.','context.','compaction.'))]
        return {'events':rows[-200:],'truncated':len(rows)>200,
                'manual_compaction':'Not exposed: automatic compaction may call the configured model.',
                'plan_actions':'Use the existing CLI plan interface; this view is read-only.'}

    async def session_metadata(self, sid, body):
        await self.service.require_session(sid)
        if set(body) - {'archived','pinned','tags','title'}:
            fail('invalid_session_metadata')
        if any(key in body and type(body[key]) is not bool for key in ('archived', 'pinned')):
            fail('invalid_session_metadata')
        if 'tags' in body and (not isinstance(body['tags'],list) or len(body['tags'])>10 or any(not isinstance(t,str) or len(t)>40 for t in body['tags'])):
            fail('invalid_session_metadata')
        if 'title' in body and (not isinstance(body['title'],str) or len(body['title'])>120):
            fail('invalid_session_metadata')
        with self.store.transaction() as data:
            data['session_preferences'].setdefault(sid,{}).update(body)
        return {'ok':True}

    async def schedule_session(self, body):
        sid = body.get('session_id') or await self.service.create_session()
        async with self.locks.setdefault(sid, asyncio.Lock()):
            log = await self.service.require_session(sid)
            binding = self.binding(log)
            if not binding:
                if sid in self.service._engines:
                    fail('new_session_required')
                definition = (self.new_binding_version(body['agent_id'],int(body.get('agent_version',0)))['snapshot']
                              if body.get('agent_id') else AgentDefinition(name='计划助手').model_dump())
                self.validate_execution_policy(AgentDefinition.model_validate(definition))
                await log.append('user.message', {'content':'计划任务运行策略已绑定',
                    'meta':{'platform':{'definition':definition,'agent_id':definition['agent_id'],
                                       'agent_version':definition['current_version']}}},actor='user',sync=True)
        return sid

