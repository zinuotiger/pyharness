"""Offline regression for model failure, terminal fences and safe diagnostics."""
import asyncio
import json
from types import SimpleNamespace as NS

import httpx
import pytest

from pyharness import cli
from pyharness.core.llm import OpenAICompatAdapter, ProviderStatusError, ProviderProtocolError
from pyharness.core.session import SessionLog
from pyharness.desktop.app import DesktopApp
from pyharness.application.platform_projection import project
from pyharness.errors import PyHError
from tests.support import isolated_settings
from tests.unit.test_llm import FakeTransport, _llm_cfg


@pytest.mark.parametrize('failure', ['synthetic',401,403,404,429,500,'connection','timeout','protocol'])
async def test_main_failure_real_service_queue_and_replay(tmp_path, adapter_factory, failure):
    cfg = isolated_settings(tmp_path)
    ad = adapter_factory(cfg)
    entered, release = asyncio.Event(), asyncio.Event()
    async def fail(*args, **kwargs):
        entered.set()
        await release.wait()
        if failure == 'synthetic': raise PyHError('LLM-303')
        error = ProviderStatusError(failure) if isinstance(failure,int) else {'connection':httpx.ConnectError('private'),'timeout':httpx.ReadTimeout('private'),'protocol':ProviderProtocolError('private')}[failure]
        transport=FakeTransport(error=error)
        return await OpenAICompatAdapter('fake',transport=transport).chat([],ctx=kwargs['ctx'])
    code='LLM-302' if failure in (401,403) else 'LLM-304' if failure in (404,'protocol') else 'LLM-303'
    ad.chat = ad.chat_stream = fail
    app = DesktopApp(cli.assemble_ctx(cfg))
    try:
        p = app.service.platform
        ids = await p.start_run({'text': 'synthetic failure'})
        await asyncio.wait_for(entered.wait(), 5)
        release.set()
        result = await asyncio.wait_for(app.service._queues[ids['session_id']].wait_for(ids['task_id']), 5)
        assert not result.ok and result.code == code
        run = await p.run(ids['run_id'])
        assert run['status'] == 'failed' and run['error_code'] == code
        assert run['finished_at']
        log = await app.service.require_session(ids['session_id'])
        events = list(log.events_after(0))
        replay = project(ids['session_id'], events)
        assert replay['runs'][0]['status'] == 'failed'
        assert any(s['status'] == 'failed' for s in replay['steps'])
        assert (await p.artifacts())['artifacts'] == []
        assert not any(e.type == 'task.completed' for e in events)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api), base_url='http://127.0.0.1', headers={'X-PyHarness-Token':app._api_token}) as client:
            assert (await client.get('/api/v1/runs/'+ids['run_id'])).json()['status'] == 'failed'
            assert (await client.get('/api/v1/dashboard')).json()['failed'] == 1
        await app.service.shutdown()
        recovered = DesktopApp(cli.assemble_ctx(cfg))
        try:
            assert (await recovered.service.platform.run(ids['run_id']))['status'] == 'failed'
        finally:
            await recovered.service.shutdown()
    finally:
        release.set()
        await app.service.shutdown()


def event(seq, kind, **payload):
    return NS(seq=seq, type=kind, payload=payload, task_id=None, trace=None,
              ts=f'2026-09-26T08:00:{seq:02d}Z')


@pytest.mark.parametrize('late', ['task.completed', 'task.started', 'queue.resumed', 'segment.end', 'session.finished', 'system.error'])
def test_legacy_failure_is_terminal(late):
    events = [event(1,'task.enqueued',task_id='t'), event(2,'segment.start',task_id='t'),
              event(3,'llm.request',model='fake'), event(4,'system.error',code='LLM-303',hint='error'),
              event(5,late,task_id='t',reason='ok',code='LLM-303')]
    result = project('session-test', events)
    run = result['runs'][0]
    assert run['status'] == 'failed' and run['error_code'] == 'LLM-303'
    assert run['finished_at'] == events[3].ts
    assert result['steps'][0]['status'] == 'failed'
    assert run['error_diagnostics']['http_status'] == 'unknown'


@pytest.mark.parametrize('status,category,code', [(401,'authentication','LLM-302'), (403,'permission','LLM-302'), (404,'protocol','LLM-304'), (429,'rate_limit','LLM-303'), (500,'provider_server','LLM-303')])
async def test_safe_http_diagnostics(status, category, code):
    from pyharness.core.llm_diagnostics import diagnostics
    exc = ProviderStatusError(status)
    d = diagnostics(exc, code=code, endpoint='https://user:password@example.test/v1?secret=x', model='fake')
    assert d['category'] == category
    assert d['http_status'] == status
    assert d['endpoint'] == 'https://example.test'
    assert d['request_id'] == 'unknown'


@pytest.mark.parametrize('exc,category', [(httpx.ConnectError('private body'),'connection'), (httpx.ReadTimeout('private body'),'timeout'), (ProviderProtocolError('private body'),'invalid_response'), (PyHError('LLM-303'),'unknown')])
def test_missing_metadata_and_no_raw_body(exc, category):
    from pyharness.core.llm_diagnostics import diagnostics
    d = diagnostics(exc, code='LLM-303')
    assert d['category'] == category and d['http_status'] == 'unknown'
    assert 'private body' not in json.dumps(d)


@pytest.mark.parametrize('status', [401,403,404,429,500,200])
async def test_http_transport_adapter_retains_only_safe_metadata(status):
    from pyharness.core.llm import _OpenAICompatHTTPTransport
    from pyharness.core.llm_diagnostics import model_call
    from tests.unit.test_llm import _ctx, _session
    key = 'synthetic-credential-sentinel'
    transport = _OpenAICompatHTTPTransport('https://example.test/v1', key, 1)
    await transport._client.aclose()
    response = httpx.Response(status, headers={'x-request-id':'req-123', 'retry-after':'17',
        'authorization':key}, json={'error':{'code':'provider_busy','message':key + ' /home/private/full.html'}})
    transport._client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response), base_url='https://example.test')
    session = await _session()
    try:
        adapter = OpenAICompatAdapter('fake', transport=transport)
        with model_call('main_agent'), pytest.raises(PyHError) as error:
            await adapter.chat([{'role':'user','content':'private user message'}], ctx=_ctx(session))
        d = error.value.ctx['diagnostics']
        assert d['http_status'] == status
        assert d['provider_code'] == 'provider_busy'
        assert d['request_id'] == 'req-123' and d['retry_after'] == '17'
        assert d['role'] == 'main_agent' and d['request_seq'] == 2
        assert d['attempt'] == 1 and d['retried'] is False
        serialized = json.dumps([e.model_dump() for e in session.events_after(0)])
        for forbidden in [key,'authorization','private user message','/home/private','full.html']:
            assert forbidden not in serialized.lower()
    finally:
        await transport.aclose()


async def test_non_json_and_reflected_credential_headers():
    from pyharness.core.llm import _OpenAICompatHTTPTransport
    from tests.unit.test_llm import _ctx, _session
    key = 'synthetic-opaque-credential'
    t = _OpenAICompatHTTPTransport('https://example.test', key, 1)
    await t._client.aclose()
    t._client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request:
        httpx.Response(200, headers={'x-request-id':key}, text='<html>'+key+'</html>')), base_url='https://example.test')
    try:
        with pytest.raises(PyHError) as err:
            await OpenAICompatAdapter('fake',transport=t).chat([],ctx=_ctx(await _session()))
        d=err.value.ctx['diagnostics']
        assert d['http_status']==200 and d['request_id']=='unknown'
        assert d['category']=='invalid_response'
        assert key not in json.dumps(d) and '<html>' not in json.dumps(d)
    finally:
        await t.aclose()


@pytest.mark.parametrize('disconnect', [True,False])
async def test_stream_midway_failure_preserves_received_headers(disconnect):
    from pyharness.core.llm import _OpenAICompatHTTPTransport
    from tests.unit.test_llm import _ctx, _session
    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"partial"},"finish_reason":null}]}\n\n'
            if disconnect:
                raise httpx.ReadError('sensitive proxy body')
    t = _OpenAICompatHTTPTransport('https://example.test','synthetic-only',1)
    await t._client.aclose()
    t._client=httpx.AsyncClient(transport=httpx.MockTransport(lambda req:
        httpx.Response(200,headers={'x-request-id':'stream-123'},stream=BrokenStream())),base_url='https://example.test')
    try:
        s=await _session()
        with pytest.raises(PyHError) as err:
            await OpenAICompatAdapter('fake',transport=t).chat_stream([],ctx=_ctx(s))
        d=err.value.ctx['diagnostics']
        assert d['http_status']==200 and d['request_id']=='stream-123'
        assert not any(e.type in ('llm.response','llm.usage') for e in s.events_after(0))
        assert 'sensitive proxy body' not in json.dumps(d)
    finally:
        await t.aclose()


def test_provider_code_without_status():
    from pyharness.core.llm_diagnostics import diagnostics
    err=ProviderProtocolError()
    err.diagnostics={'provider_code':'quota_exhausted'}
    d=diagnostics(err,code='LLM-304')
    assert d['provider_code']=='quota_exhausted' and d['http_status']=='unknown'


@pytest.mark.parametrize('main_fails,title_fails', [(False,True),(True,False),(False,False)])
async def test_explicit_title_role_never_controls_main(tmp_path,adapter_factory,main_fails,title_fails):
    from pyharness.core.llm_diagnostics import call_role
    cfg=isolated_settings(tmp_path)
    ad=adapter_factory(cfg)
    original=ad.chat
    roles=[]
    async def chat(*args,**kw):
        role=call_role.get();roles.append(role)
        if (role=='main_agent' and main_fails) or (role=='title_generation' and title_fails):
            raise PyHError('LLM-303')
        return await original(*args,**kw)
    ad.chat=ad.chat_stream=chat
    app=DesktopApp(cli.assemble_ctx(cfg))
    try:
        p=app.service.platform
        ids=await p.start_run({'text':'synthetic title distinction'})
        result=await asyncio.wait_for(app.service._queues[ids['session_id']].wait_for(ids['task_id']),5)
        assert result.ok is (not main_fails)
        run=await p.run(ids['run_id'])
        assert run['status']==('failed' if main_fails else 'completed')
        if main_fails: assert roles==['main_agent']
        if title_fails:
            assert 'title_generation' in roles
            assert any(t['status']=='warning' for t in run['trace'])
    finally:
        await app.service.shutdown()


async def test_concurrent_terminal_event_single_winner():
    from pyharness.core.task_queue import TaskQueue
    from tests.unit.test_task_queue import _new_session
    s=await _new_session();q=TaskQueue(s)
    entered,release=asyncio.Event(),asyncio.Event()
    append=s.append
    async def blocked(kind,payload,**kw):
        if kind=='task.failed':
            entered.set();await release.wait()
        return await append(kind,payload,**kw)
    s.append=blocked
    failed=asyncio.create_task(q._terminal_event('task.failed',{'task_id':'t','reason':'error','error':'LLM-303'}))
    await asyncio.wait_for(entered.wait(),5)
    completed=asyncio.create_task(q._terminal_event('task.completed',{'task_id':'t','reason':'ok'}))
    release.set()
    await asyncio.wait_for(asyncio.gather(failed,completed),5)
    assert [e.type for e in s.events_after(0) if e.type.startswith('task.')]==['task.failed']
    await q._terminal_event('task.failed',{'task_id':'t','reason':'error','error':'LLM-303'})
    assert len(q._terminals)==1


@pytest.mark.parametrize('first', ['failed','cancelled','completed'])
def test_all_terminal_states_are_irreversible(first):
    seq=[event(1,'task.enqueued',task_id='t'),event(2,'segment.start',task_id='t'),event(3,'agent.message',content='done')]
    seq.append(event(4,'task.completed' if first=='completed' else 'task.failed',task_id='t',reason='cancelled' if first=='cancelled' else 'error',error='LLM-303'))
    seq.extend([event(5,'task.completed',task_id='t',reason='ok'),event(6,'task.failed',task_id='t',reason='error',error='CYC-999')])
    r=project('session-test',seq)['runs'][0]
    assert r['status']==first and r['finished_at']==seq[3].ts


async def test_empty_runner_result_is_not_success():
    from pyharness.core.task_queue import TaskQueue
    from tests.unit.test_task_queue import _new_session
    s=await _new_session()
    async def runner(task): return None
    q=TaskQueue(s,runner=runner)
    ident=await q.submit('empty')
    assert not (await asyncio.wait_for(q.wait_for(ident),5)).ok
    await q.shutdown()


async def test_historical_failure_replay_usage_no_network_or_credentials(monkeypatch):
    from pathlib import Path
    import socket
    from pyharness.core import llm
    from pyharness.application.platform_usage import usage
    def forbidden(*args,**kwargs):
        raise AssertionError('Replay may not resolve credentials or use network')
    monkeypatch.setattr(llm,'resolve_secret_ref',forbidden)
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    rows=json.loads((Path(__file__).parents[1]/'fixtures/model_failure_legacy.json').read_text(encoding='utf-8'))
    events=[NS(**{'task_id':None,'trace':None,**r}) for r in rows]
    sid='s-5e537525e6a3'
    view=project(sid,events)
    assert [r['status'] for r in view['runs']]==['completed','failed']
    failed=view['runs'][1]
    assert failed['error_code']=='LLM-303'
    assert failed['error_diagnostics']['request_seq']==46
    for key in ('http_status','provider_code','request_id','retry_after'):
        assert failed['error_diagnostics'][key]=='unknown'
    assert failed['usage_status']=='unknown' and failed['usage_unknown_requests']==1
    assert failed['current_step']==0 and failed['total_steps']==1
    reads=[s for s in view['steps'] if s['name'].startswith('fs.')]
    assert len(reads)==3 and all(s['status']=='completed' for s in reads)
    assert view['approvals']==[]
    assert not any(e.type=='platform.artifact' for e in events)
    async def session(sid):return NS(events_after=lambda seq:events)
    async def artifacts():return {'artifacts':[]}
    counts=await usage(NS(service=NS(require_session=session),artifacts=artifacts),
                       {**view,'sessions':[{'sid':sid}]},{})
    assert counts['tokens']==4881
    assert counts['input_tokens']==4336 and counts['output_tokens']==545
    assert counts['model_requests']==4 and counts['usage_records']==3
    assert counts['usage_unknown_requests']==1 and counts['usage_status']=='unknown'
    assert counts['success_rate']==.5 and counts['error_rate']==.5


async def test_sse_close_does_not_mutate_failure_and_redacts():
    from pyharness.desktop.projection import EventStreamHub, _serialize_event
    events=[event(1,'task.enqueued',task_id='t'),event(2,'segment.start',task_id='t'),
            event(3,'system.error',code='LLM-303',hint='Bearer synthetic-secret',ctx={'secret':'synthetic-secret'})]
    encoded=_serialize_event('system.error',{'type':'system.error','payload':events[-1].payload})
    assert 'synthetic-secret' not in encoded and 'Bearer' not in encoded
    hub=EventStreamHub()
    before=project('session-test',events)
    hub.close_all()
    assert project('session-test',events)==before
    assert before['runs'][0]['status']=='failed'


async def test_call_roles_are_coroutine_local():
    from pyharness.core.llm_diagnostics import model_call,call_role
    entered,release=asyncio.Event(),asyncio.Event()
    async def auxiliary():
        with model_call('title_generation'):
            entered.set();await release.wait()
            assert call_role.get()=='title_generation'
    with model_call('main_agent'):
        child=asyncio.create_task(auxiliary())
        await asyncio.wait_for(entered.wait(),5)
        assert call_role.get()=='main_agent'
        release.set();await asyncio.wait_for(child,5)
    assert call_role.get()=='other'


def test_foreign_task_error_does_not_fail_active_run():
    events=[event(1,'task.enqueued',task_id='t'),event(2,'segment.start',task_id='t'),
            event(3,'system.error',task_id='foreign',code='LLM-303',hint='error'),
            event(4,'agent.message',content='done'),event(5,'task.completed',task_id='t',reason='ok')]
    assert project('session-test',events)['runs'][0]['status']=='completed'


def test_title_success_after_main_failure_cannot_complete_run():
    events=[event(1,'task.enqueued',task_id='t'),event(2,'segment.start',task_id='t'),
            event(3,'system.error',code='LLM-303',hint='error',ctx={'role':'main_agent','fatal':True}),
            event(4,'llm.request',model='fake',role='title_generation'),
            event(5,'llm.response',model='fake',content='title'),event(6,'task.completed',task_id='t',reason='ok')]
    assert project('session-test',events)['runs'][0]['status']=='failed'


async def test_cleanup_failure_does_not_replace_model_failure(tmp_path,adapter_factory,monkeypatch):
    from pyharness.application.platform_runtime import PlatformRuntime
    cfg=isolated_settings(tmp_path);ad=adapter_factory(cfg)
    async def model(*args,**kwargs):raise PyHError('LLM-303')
    async def cleanup(*args,**kwargs):raise RuntimeError('cleanup failed')
    ad.chat=ad.chat_stream=model
    monkeypatch.setattr(PlatformRuntime,'release',cleanup)
    app=DesktopApp(cli.assemble_ctx(cfg))
    try:
        ids=await app.service.platform.start_run({'text':'synthetic cleanup race'})
        q=app.service._queues[ids['session_id']]
        result=await asyncio.wait_for(q.wait_for(ids['task_id']),5)
        assert not result.ok and result.code=='LLM-303'
        await q.shutdown()
        assert (await q.wait_for(ids['task_id'])).code=='LLM-303'
        assert (await app.service.platform.run(ids['run_id']))['error_code']=='LLM-303'
    finally:
        await app.service.shutdown()


async def test_retry_attempt_metadata_survives_fallback_exhaustion():
    from pyharness.core.llm_fallback import FallbackChain
    from pyharness.core.llm_diagnostics import model_call
    from tests.unit.test_llm import _ctx,_session
    from pyharness.config import load_settings
    cfg=load_settings();cfg.llm.retry.attempts=2;cfg.llm.retry.base_delay_s=0
    cfg.llm.degrade.enabled=False
    from pyharness.core.llm import UsageCounters
    s=await _session();ctx=_ctx(s,counters=UsageCounters());ctx.config=cfg
    ad=OpenAICompatAdapter(cfg.llm.model,transport=FakeTransport(error=ProviderStatusError(429)))
    chain=FallbackChain(adapters={cfg.llm.model:ad},config=cfg,chain=[cfg.llm.model])
    with model_call('main_agent'),pytest.raises(PyHError) as err:
        await chain.chat_with_fallback([],ctx=ctx)
    assert err.value.code=='LLM-310'
    d=err.value.ctx['diagnostics']
    assert d['http_status']==429 and d['attempt']==2 and d['retried'] is True
    assert d['safe_to_retry'] is True
    errors=[e for e in s.events_after(0) if e.type=='llm.error']
    assert [e.payload['diagnostics']['attempt'] for e in errors]==[1,2]


@pytest.mark.parametrize('provider_code,expected', [('quota_exhausted','quota_exhausted'),('synthetic-opaque-credential','unknown')])
async def test_in_band_stream_error_code_and_credential_reflection(provider_code,expected):
    from pyharness.core.llm import _OpenAICompatHTTPTransport
    from tests.unit.test_llm import _ctx,_session
    class InBand(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield ('data: '+json.dumps({'error':{'code':provider_code,'message':'private message'}})+'\n\n').encode()
    t=_OpenAICompatHTTPTransport('https://example.test','synthetic-opaque-credential',1)
    await t._client.aclose()
    t._client=httpx.AsyncClient(transport=httpx.MockTransport(lambda req:
        httpx.Response(200,headers={'x-request-id':'in-band-123'},stream=InBand())),base_url='https://example.test')
    try:
        with pytest.raises(PyHError) as err:
            await OpenAICompatAdapter('fake',transport=t).chat_stream([],ctx=_ctx(await _session()))
        d=err.value.ctx['diagnostics']
        assert d['provider_code']==expected
        assert d['http_status']==200 and d['request_id']=='in-band-123'
        assert 'private message' not in json.dumps(d)
        assert 'synthetic-opaque-credential' not in json.dumps(d)
    finally:
        await t.aclose()


async def test_stream_total_gate_preserves_headers_at_barrier(monkeypatch):
    from pyharness.core.llm import _OpenAICompatHTTPTransport
    from tests.unit.test_llm import _ctx,_session
    entered=asyncio.Event()
    class Held(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"partial"},"finish_reason":null}]}\n\n'
            entered.set()
            await asyncio.Event().wait()
    async def controlled_gate(coro,timeout):
        child=asyncio.create_task(coro)
        await entered.wait()
        child.cancel()
        with pytest.raises(asyncio.CancelledError):await child
        raise TimeoutError()
    t=_OpenAICompatHTTPTransport('https://example.test','synthetic-only',1)
    await t._client.aclose()
    t._client=httpx.AsyncClient(transport=httpx.MockTransport(lambda req:
        httpx.Response(200,headers={'x-request-id':'total-gate-123'},stream=Held())),base_url='https://example.test')
    try:
        monkeypatch.setattr(asyncio,'wait_for',controlled_gate)
        with pytest.raises(PyHError) as err:
            await OpenAICompatAdapter('fake',transport=t).chat_stream([],ctx=_ctx(await _session()))
        d=err.value.ctx['diagnostics']
        assert err.value.code=='LLM-301' and d['category']=='timeout'
        assert d['http_status']==200 and d['request_id']=='total-gate-123'
    finally:
        await t.aclose()


@pytest.mark.parametrize('first_ok', [True,False])
async def test_rebuilt_queue_never_reuses_historical_run_id(first_ok):
    from pyharness.core.task_queue import TaskQueue,TaskResult
    from tests.unit.test_task_queue import _new_session
    s=await _new_session()
    async def runner(task):
        ok=first_ok if task.id=='t-1' else not first_ok
        if ok:
            await s.append('agent.message',{'content':'explicit success'},actor='agent',task_id=task.id)
            return TaskResult(ok=True)
        await s.append('system.error',{'code':'LLM-303','hint':'synthetic failure',
            'ctx':{'role':'main_agent','fatal':True}},actor='system',task_id=task.id)
        return TaskResult(ok=False,code='LLM-303')
    q=TaskQueue(s,runner=runner)
    first=await q.submit('before restart')
    assert (await asyncio.wait_for(q.wait_for(first),5)).ok is first_ok
    await q.shutdown()

    q=TaskQueue(s,runner=runner)
    second=await q.submit('after restart')
    assert first=='t-1' and second=='t-2'
    assert (await asyncio.wait_for(q.wait_for(second),5)).ok is not first_ok
    with pytest.raises(PyHError,match='EVT-100'):
        await q.submit('explicit duplicate',task_id=first)
    states=[r['status'] for r in project(s.sid,list(s.events_after(0)))['runs']]
    assert states==(['completed','failed'] if first_ok else ['failed','completed'])
    await q.shutdown()


async def test_empty_sse_preserves_observed_response_metadata():
    from pyharness.core.llm import _OpenAICompatHTTPTransport
    from tests.unit.test_llm import _ctx,_session
    t=_OpenAICompatHTTPTransport('https://example.test','synthetic-only',1)
    await t._client.aclose()
    t._client=httpx.AsyncClient(transport=httpx.MockTransport(lambda req:
        httpx.Response(200,headers={'x-request-id':'empty-123'},content=b'data: [DONE]\n\n')),base_url='https://example.test')
    try:
        with pytest.raises(PyHError) as err:
            await OpenAICompatAdapter('fake',transport=t).chat_stream([],ctx=_ctx(await _session()))
        d=err.value.ctx['diagnostics']
        assert err.value.code=='LLM-304'
        assert d['http_status']==200 and d['request_id']=='empty-123'
    finally:
        await t.aclose()
