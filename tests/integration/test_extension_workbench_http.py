"""Operator-only HTTP edges, fixed snapshots, no accidental formal sessions."""
import json
import asyncio
import httpx
import pytest
from pyharness import cli
from pyharness.desktop.app import DesktopApp
from tests.support import isolated_settings


async def test_operator_snapshot_rehearsal_cold_duplicate_and_team_denials(tmp_path):
    cfg=isolated_settings(tmp_path)
    path=tmp_path/'skills'/'sample'/'SKILL.md';path.parent.mkdir(parents=True)
    path.write_text('---\nname: sample\ndescription: controlled sample\n---\nFixed skill content.',encoding='utf-8')
    app=DesktopApp(cli.assemble_ctx(cfg))
    headers={'X-PyHarness-Token':app._api_token}
    base='/api/v1/extension-workbench'
    saved_key='http-lab-1'
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api),base_url='http://127.0.0.1',headers=headers) as client:
            response=await client.post(base+'/skill-snapshots',json={'name':'sample'})
            assert response.status_code==200,response.text
            captured=response.json();ident=captured['snapshot_id']
            assert not app.service._engines and not app.service._queues
            path.write_text('CHANGED-FORMAL-FILE',encoding='utf-8')
            body={'command_key':saved_key,'confirm_controlled_rehearsal':True,'loaded_sha256':captured['snapshot']['loaded_sha256']}
            invalid=await client.post(base+f'/skill-snapshots/{ident}/rehearsals',json={**body,'confirm_controlled_rehearsal':1})
            assert invalid.status_code==422
            response=await client.post(base+f'/skill-snapshots/{ident}/rehearsals',json=body)
            assert response.status_code==200,response.text
            result=response.json();assert result['status']=='completed'
            assert result['next_context_contains_loaded_view'] and result['forbidden_provider_execution_authorized'] is False
            assert result['forbidden_provider_calls_measured'] is False
            assert path.read_text()=='CHANGED-FORMAL-FILE'
            assert not app.service._engines and not app.service._queues
            assert (await client.post(base+f'/skill-snapshots/{ident}/rehearsals',json=body)).json()==result
            assert (await client.post('/api/account/users',json={'username':'team-owner','password':'synthetic-owner-password','tenant':'team-owned'})).status_code==201
            assert (await client.post('/api/account/login',json={'username':'team-owner','password':'synthetic-owner-password'})).status_code==200
            for method,url,request_body in [('GET',base+'/skill-snapshots/'+ident,None),
                ('GET',base+'/skill-rehearsals/'+saved_key,None),('GET',base+'/mcp/managed/echo',None),
                ('POST',base+'/skill-snapshots',{'name':'sample'})]:
                denied=await client.request(method,url,json=request_body)
                assert denied.status_code==403,(url,denied.text)
    finally:
        for service in app._service_registry.all():await service.shutdown()
    restored=DesktopApp(cli.assemble_ctx(cfg))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=restored.api),base_url='http://127.0.0.1',
            headers={'X-PyHarness-Token':restored._api_token}) as client:
            assert (await client.get(base+'/skill-rehearsals/'+saved_key)).json()==result
            assert (await client.post(base+f'/skill-snapshots/{ident}/rehearsals',json=body)).json()==result
            assert not restored.service._engines and not restored.service._queues
    finally:await restored.service.shutdown()


@pytest.mark.controlled_process
async def test_operator_cookie_revoked_during_real_probe_returns_no_observation(tmp_path):
    from tests.integration.test_mcp_workbench import workbench,started
    from pyharness.application.accounts import Accounts
    fixture_root=tmp_path/'fixture';fixture_root.mkdir()
    fixture,trace=workbench(fixture_root,'timeout')
    cfg=isolated_settings(tmp_path/'application')
    app=DesktopApp(cli.assemble_ctx(cfg));accounts=Accounts(tmp_path/'application')
    accounts.provision_operator('local-admin','synthetic-local-admin-password')
    token=accounts.login('local-admin','synthetic-local-admin-password')
    connection=fixture.platform.store.read()['connections']['mcp:echo']['config']
    app.service.platform.save_connection({'type':'mcp','config':connection})
    base='/api/v1/extension-workbench'
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api),base_url='http://127.0.0.1',
            cookies={'pyharness_account':token}) as client:
            preview=await client.get(base+'/mcp/managed/echo')
            assert preview.status_code==200,preview.text
            task=asyncio.create_task(client.post(base+'/mcp/managed/echo/discoveries',json={
                'command_key':'revoked-cookie-1','confirm_host_process':True,
                'config_sha256':preview.json()['config_sha256']}))
            await started(trace);accounts.logout(token)
            response=await task
            assert response.status_code==403,response.text
            assert 'fixture' not in response.text and 'tools' not in response.text
            row=app.service.platform.store.read()['preferences']['mcp_diagnostics']['revoked-cookie-1']
            assert row['carrier_closed'] is True and row['status']=='failed'
            assert not app.service.platform.actions
    finally:await app.service.shutdown()
