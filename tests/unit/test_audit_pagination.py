"""Actual HTTP adapters over durable event projections and tenant authorization."""
import base64
import asyncio
import httpx
import pytest
from pyharness import cli
from pyharness.desktop.app import DesktopApp
from pyharness.application.accounts import Accounts
from tests.support import isolated_settings

async def test_run_artifact_pages_restart_delete_and_bounded_cache(tmp_path):
    cfg=isolated_settings(tmp_path)
    app=DesktopApp(cli.assemble_ctx(cfg))
    expected_runs=set(); expected_artifacts=set(); sessions=[]
    try:
        for group in range(3):
            sid=await app.service.create_session(); sessions.append(sid)
            log=await app.service.require_session(sid)
            for i in range(13):
                task=f'synthetic-{group}-{i}'
                await log.append('task.enqueued',dict(task_id=task,pos=1),actor='system')
                await log.append('task.started',dict(task_id=task),actor='system')
                await log.append('task.completed',dict(task_id=task,reason='ok'),actor='system')
                expected_runs.add(sid+'~'+task)
            for i in range(6):
                record=await app.service.platform.upload(dict(session_id=sid,filename=f'fixture-{group}-{i}.txt',data_base64=base64.b64encode(b'synthetic').decode()))
                expected_artifacts.add(record['artifact_id'])
        async def check_pages():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api),base_url='http://127.0.0.1',headers={'X-PyHarness-Token':app._api_token}) as client:
                for kind,key,expected in [('runs','run_id',expected_runs),('artifacts','artifact_id',expected_artifacts)]:
                    seen=[]; cursor=None
                    while True:
                        params=dict(limit=7)
                        if cursor: params['cursor']=cursor
                        response=await client.get('/api/v1/'+kind,params=params)
                        assert response.status_code==200,response.text
                        data=response.json()
                        assert data['total']==len(expected) and len(data[kind])<=7
                        seen.extend(r[key] for r in data[kind])
                        if kind=='runs': assert all(s['run_id'] in {r[key] for r in data[kind]} for s in data['steps'])
                        cursor=data['cursor']
                        if not data['truncated']: break
                        assert cursor
                    assert len(seen)==len(set(seen)) and set(seen)==expected
                    assert (await client.get('/api/v1/'+kind,params=dict(limit=0))).status_code==422
                    assert (await client.get('/api/v1/'+kind,params=dict(cursor='foreign-cursor',limit=7))).status_code==422
        await check_pages()
        await app.service.platform.runs(limit=7)
        assert app.service.platform.list_index.metrics['projection_count']==0
        await app.service.shutdown()
        app=DesktopApp(cli.assemble_ctx(cfg))
        await check_pages()
        ident=next(iter(expected_artifacts))
        await app.service.platform.delete_artifact(ident); expected_artifacts.remove(ident)
        await check_pages()
        sid=sessions[0]
        await app.service.delete_session(sid)
        expected_runs={r for r in expected_runs if not r.startswith(sid+'~')}
        remaining=(await app.service.platform.artifacts())['artifacts']
        expected_artifacts={a['artifact_id'] for a in remaining}
        await check_pages()
        assert sid not in app.service.platform.projection_cache.rows
        assert sid not in app.service.platform.artifact_cache.rows
        # The separate detail cache remains bounded; list indexing is durable.
        for _ in range(35): await app.service.create_session()
        await app.service.platform.runs(limit=7); await app.service.platform.artifacts(limit=7)
        assert len(app.service.platform.projection_cache.rows)<=32
        assert len(app.service.platform.artifact_cache.rows)<=32
    finally:
        await app.service.shutdown()


@pytest.mark.parametrize('count',[41,105])
async def test_all_authorized_sources_keyset_mutation_cost_and_rebuild(tmp_path,count):
    cfg=isolated_settings(tmp_path)
    app=DesktopApp(cli.assemble_ctx(cfg))
    p=app.service.platform
    sessions=[]
    async def collect(kind):
        seen=[];cursor=None
        while True:
            data=await getattr(p,kind)(limit=9,cursor=cursor)
            seen.extend(data[kind]);cursor=data['cursor']
            if not data['truncated']:break
        key='run_id' if kind=='runs' else 'artifact_id'
        assert len(seen)==len({r[key] for r in seen})
        return seen
    try:
        for i in range(count):
            sid=await app.service.create_session();sessions.append(sid)
            log=await app.service.require_session(sid)
            await log.append('task.enqueued',dict(task_id=f'load-{i}',pos=1),actor='system')
            await log.append('task.completed',dict(task_id=f'load-{i}',reason='ok'),actor='system')
            await p.upload(dict(session_id=sid,filename=f'fixture-{i}.txt',data_base64='eA=='))
        assert len(await collect('runs'))==count
        artifacts=await collect('artifacts')
        assert len(artifacts)==count
        for kind in ('runs','artifacts'):
            await getattr(p,kind)(limit=9)
            metrics=p.list_index.metrics
            assert metrics['scanned_sessions']==metrics['projection_count']==metrics['cache_miss']==0
            assert metrics['cache_hit']==count and metrics['index_hit']==1 and metrics['returned_rows']==9
        assert app.service.surface_mgr().list_scanned_sessions==0

        # Keyset position survives removal of its anchor, including page boundaries.
        for offset in (0, count//2, count-9):
            current=await collect('artifacts')
            page=await p.artifacts(limit=offset+1)
            cursor=page['cursor']
            if not cursor:continue
            anchor=page['artifacts'][-1]['artifact_id']
            await p.delete_artifact(anchor)
            tail=(await p.artifacts(cursor=cursor))['artifacts']
            assert [r['artifact_id'] for r in tail]==[r['artifact_id'] for r in current[offset+1:]]
            assert anchor not in {r['artifact_id'] for r in await collect('artifacts')}

        sid=sessions[-1];log=await app.service.require_session(sid)
        await log.append('task.enqueued',dict(task_id='new',pos=1),actor='system')
        await log.append('task.completed',dict(task_id='new',reason='ok'),actor='system')
        await log.append('user.message',dict(content='new source event'),actor='user')
        await p.upload(dict(session_id=sid,filename='new.txt',data_base64='eA=='))
        assert len(await collect('runs'))==count+1
        await p.runs(limit=9)
        assert p.list_index.metrics['projection_count']==0
        for position in (0,count//2,count-1):
            await app.service.delete_session(sessions[position])
        runs=await collect('runs');artifacts=await collect('artifacts')
        assert len(runs)==count-3  # final session owned two runs
        for row in runs+artifacts:
            assert row['session_id'] not in {sessions[i] for i in (0,count//2,count-1)}
        await app.service.shutdown()
        app=DesktopApp(cli.assemble_ctx(cfg));p=app.service.platform
        assert await collect('runs')==runs
        assert await collect('artifacts')==artifacts
        # Disposable DB removal causes a rebuild from unchanged SessionLog facts.
        p.list_index.path.unlink()
        assert await collect('runs')==runs
        assert await collect('artifacts')==artifacts
        await p.runs(limit=9)
        assert p.list_index.metrics['projection_count']==0
    finally:
        await app.service.shutdown()

async def test_history_and_page_scope_after_membership_revocation(tmp_path):
    app=DesktopApp(cli.assemble_ctx(isolated_settings(tmp_path)))
    accounts=Accounts(tmp_path)
    accounts.provision('alice','synthetic-password-alice','history-team')
    accounts.provision('bob','synthetic-password-bob','history-team')
    accounts.provision('eve','synthetic-password-eve','foreign-team')
    try:
        service=app._service_registry.get('history-team')
        sid=await service.create_session()
        log=await service.require_session(sid)
        await log.append('user.message',dict(content='private elderberry source'),actor='user',sync=True)
        for i in range(3):
            await log.append('task.enqueued',dict(task_id=f'role-{i}',pos=1),actor='system')
            await service.platform.upload(dict(session_id=sid,filename=f'role-{i}.txt',data_base64='eA=='))
        transport=httpx.ASGITransport(app=app.api)
        async with httpx.AsyncClient(transport=transport,base_url='http://127.0.0.1') as alice, httpx.AsyncClient(transport=transport,base_url='http://127.0.0.1') as eve:
            for client,user in [(alice,'alice'),(eve,'eve')]:
                assert (await client.post('/api/account/login',json=dict(username=user,password='synthetic-password-'+user))).status_code==200
            params=dict(q='elderberry',session_id=sid)
            assert (await alice.get('/api/v1/history/search',params=params)).json()['hits']
            denied=await eve.get('/api/v1/history/search',params=params)
            assert denied.status_code==404 and 'private elderberry' not in denied.text
            absent=await eve.get('/api/v1/history/search',params=dict(q='elderberry',session_id='s-000000000000'))
            assert absent.status_code==denied.status_code
            assert not (await eve.get('/api/v1/history/search',params=dict(q='elderberry'))).json()['hits']
            cursors={kind:(await alice.get('/api/v1/'+kind,params=dict(limit=1))).json()['cursor'] for kind in ('runs','artifacts')}
            for role in ('owner','member','viewer'):
                accounts.membership('history-team','alice',role)
                for kind,cursor in cursors.items():
                    response=await alice.get('/api/v1/'+kind,params=dict(limit=1,cursor=cursor))
                    assert response.status_code==200 and response.json()['total']==3
                    foreign=await eve.get('/api/v1/'+kind,params=dict(limit=1,cursor=cursor))
                    assert foreign.status_code==422 and sid not in foreign.text
                if role=='viewer':
                    assert (await alice.post('/api/v1/artifacts',json=dict(session_id=sid,filename='forbidden.txt',data_base64='eA=='))).status_code==403
            accounts.membership('history-team','alice','remove')
            for path in ['/api/v1/history/search?q=elderberry&session_id='+sid,'/api/v1/runs?limit=7','/api/v1/artifacts?limit=7']:
                response=await alice.get(path)
                assert response.status_code==401 and 'private elderberry' not in response.text
            for kind,cursor in cursors.items():
                assert (await alice.get('/api/v1/'+kind,params=dict(limit=1,cursor=cursor))).status_code==401
    finally:
        for service in app._service_registry.all(): await service.shutdown()


async def test_parallel_index_rebuild_failure_and_cancellation_release_gate(tmp_path,monkeypatch):
    app=DesktopApp(cli.assemble_ctx(isolated_settings(tmp_path)))
    p=app.service.platform
    try:
        sid=await app.service.create_session();log=await app.service.require_session(sid)
        await log.append('task.enqueued',dict(task_id='concurrent',pos=1),actor='system')
        await p.upload(dict(session_id=sid,filename='concurrent.txt',data_base64='eA=='))
        original=app.service.require_session
        async def scheduled(sid):
            await asyncio.sleep(0)
            return await original(sid)
        monkeypatch.setattr(app.service,'require_session',scheduled)
        async def both():
            rows=await asyncio.wait_for(asyncio.gather(p.runs(limit=1),p.artifacts(limit=1)),5)
            assert [r['total'] for r in rows]==[1,1]
        await both();await both()
        for mode in ('failure','cancel'):
            p.list_index.path.unlink()
            entered=asyncio.Event();release=asyncio.Event()
            async def fault(sid):
                entered.set()
                if mode=='failure':raise RuntimeError('synthetic read fault')
                await release.wait()
                return await original(sid)
            monkeypatch.setattr(app.service,'require_session',fault)
            request=asyncio.create_task(p.runs(limit=1))
            await asyncio.wait_for(entered.wait(),5)
            if mode=='cancel':request.cancel()
            with pytest.raises(RuntimeError if mode=='failure' else asyncio.CancelledError):await request
            monkeypatch.setattr(app.service,'require_session',scheduled)
            release.set();await both()
        p.list_index.path.unlink();await both()
        await app.service.delete_session(sid)
        assert (await p.runs(limit=1))['total']==0
        assert (await p.artifacts(limit=1))['total']==0
    finally:
        await app.service.shutdown()
