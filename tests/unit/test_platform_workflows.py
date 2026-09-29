"""Background workflows use the existing queue and persist only SessionLog facts."""
import asyncio
import pytest
from pyharness import cli
from pyharness.desktop.app import DesktopApp
from pyharness.errors import PyHError
from tests.support import isolated_settings


async def test_background_workflow_idempotency_and_reopen(tmp_path,adapter_factory):
    cfg=isolated_settings(tmp_path)
    adapter_factory(cfg)
    app=DesktopApp(cli.assemble_ctx(cfg))
    try:
        platform=app.service.platform
        body={'steps':['first synthetic step','second synthetic step'],'idempotency_key':'synthetic-one'}
        first=await platform.workflows.start(body)
        second=await platform.workflows.start(body)
        assert second['workflow_id']==first['workflow_id']
        await asyncio.wait_for(platform.workflows.wait(first['workflow_id']),10)
        row=(await platform.workflows.list())['workflows'][0]
        assert row['status']=='completed' and row['completed_steps']==2
        assert len((await platform.runs())['runs'])==2
        with pytest.raises(PyHError,match='workflow_idempotency_conflict'):
            await platform.workflows.start({**body,'steps':['different']})
    finally:
        await app.service.shutdown()
    reopened=DesktopApp(cli.assemble_ctx(cfg))
    try:
        assert (await reopened.service.platform.workflows.list())['workflows'][0]['status']=='completed'
        assert not reopened.service._engines
        same=await reopened.service.platform.workflows.start(body)
        assert same['workflow_id']==first['workflow_id']
        assert len((await reopened.service.platform.runs())['runs'])==2
        assert not reopened.service._engines,'Idempotent resubmission after restart must not execute'
    finally:
        await reopened.service.shutdown()


async def test_http_client_close_does_not_cancel_owned_workflow(tmp_path,adapter_factory):
    import httpx
    cfg=isolated_settings(tmp_path)
    adapter=adapter_factory(cfg)
    original=adapter.chat
    entered,release=asyncio.Event(),asyncio.Event()
    calls=[]
    async def blocked(*args,**kwargs):
        from pyharness.core.llm_diagnostics import call_role
        role=call_role.get()
        calls.append(role)
        if role=='main_agent':
            entered.set()
            await release.wait()
        return await original(*args,**kwargs)
    adapter.chat=adapter.chat_stream=blocked
    app=DesktopApp(cli.assemble_ctx(cfg))
    body={'steps':['one synthetic side effect'],'idempotency_key':'http-close'}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api),base_url='http://127.0.0.1',
                headers={'X-PyHarness-Token':app._api_token}) as client:
            response=await client.post('/api/v1/workflows',json=body)
            assert response.status_code==200
            workflow=response.json()
            await asyncio.wait_for(entered.wait(),5)
        # The request/client no longer owns the work; only the service does.
        assert not app.service.platform.workflows.tasks[workflow['workflow_id']].done()
        release.set()
        await asyncio.wait_for(app.service.platform.workflows.wait(workflow['workflow_id']),10)
        assert calls.count('main_agent')==1
        runs=(await app.service.platform.runs())['runs']
        assert len(runs)==1 and runs[0]['status']=='completed'
        assert (await app.service.platform.workflows.list())['workflows'][0]['status']=='completed'
    finally:
        release.set()
        await app.service.shutdown()
    reopened=DesktopApp(cli.assemble_ctx(cfg))
    try:
        before=list(calls)
        assert (await reopened.service.platform.workflows.start(body))['workflow_id']==workflow['workflow_id']
        assert calls==before and not reopened.service._engines
    finally:
        await reopened.service.shutdown()


async def test_failed_workflow_restart_and_duplicate_never_replays_effect(tmp_path,adapter_factory):
    cfg=isolated_settings(tmp_path);adapter=adapter_factory(cfg)
    calls=[]
    async def fail(*args,**kwargs):
        calls.append(True)
        raise PyHError('LLM-303')
    adapter.chat=adapter.chat_stream=fail
    body={'steps':['fail once','must not execute'],'idempotency_key':'failed-once'}
    app=DesktopApp(cli.assemble_ctx(cfg))
    try:
        workflow=await app.service.platform.workflows.start(body)
        await asyncio.wait_for(app.service.platform.workflows.wait(workflow['workflow_id']),10)
        assert (await app.service.platform.workflows.list())['workflows'][0]['status']=='failed'
        assert len(calls)==1
    finally:
        await app.service.shutdown()
    restored=DesktopApp(cli.assemble_ctx(cfg))
    try:
        same=await restored.service.platform.workflows.start(body)
        assert same['workflow_id']==workflow['workflow_id'] and same['status']=='failed'
        assert len(calls)==1 and not restored.service._engines
    finally:
        await restored.service.shutdown()


@pytest.mark.parametrize('wait_until_started',[False,True])
async def test_workflow_cancel_stops_queued_work_and_restarts_cancelled(tmp_path,adapter_factory,wait_until_started):
    cfg=isolated_settings(tmp_path)
    adapter=adapter_factory(cfg)
    entered=asyncio.Event()
    async def blocked(*args,**kwargs):
        entered.set()
        await asyncio.Event().wait()
    adapter.chat=adapter.chat_stream=blocked
    app=DesktopApp(cli.assemble_ctx(cfg))
    try:
        workflow=await app.service.platform.workflows.start({'steps':['blocked','never'],'idempotency_key':'cancel'})
        if wait_until_started:
            await asyncio.wait_for(entered.wait(),5)
        # Keep cancellation in this task: wait_for creates a separate task on
        # Python 3.11 and can start the supposedly not-yet-started workflow.
        async with asyncio.timeout(5):
            await app.service.platform.workflows.cancel(workflow['workflow_id'])
        row=(await app.service.platform.workflows.list())['workflows'][0]
        assert row['status']=='cancelled'
        runs=(await app.service.platform.runs())['runs']
        if wait_until_started:
            assert len(runs)==1 and runs[0]['status']=='cancelled'
        else:
            assert not runs
    finally:
        await app.service.shutdown()
    reopened=DesktopApp(cli.assemble_ctx(cfg))
    try:
        assert (await reopened.service.platform.workflows.list())['workflows'][0]['status']=='cancelled'
    finally:
        await reopened.service.shutdown()
