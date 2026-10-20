"""Actual ASGI/account boundary. Configuration preview must be zero process."""
import os
import asyncio
import json
import pytest
from tests.integration.test_configuration_history_http import environment
from tests.integration.test_controlled_browser import NODE,PW,CHROME

pytestmark=pytest.mark.skipif(os.name!='nt',reason='Windows setup only')
PATH='/api/v1/browser-workbench/configuration'


async def test_configuration_is_disabled_no_process_no_store_write(environment):
    env=environment;store=env.app.service.platform.store;before=store.read()
    response=await env.client.post(PATH,json=dict(node=str(NODE),playwright=str(PW),executable=str(CHROME),origins=['http://127.0.0.1:8080']))
    assert response.status_code==200,response.text
    assert response.headers['cache-control']=='no-store'
    value=response.json()
    assert value['status']=='configuration_only' and value['process_started'] is False
    assert value['connection']['config']['enabled'] is False
    assert set(value['connection']['config']['allowed_tools'])=={'open','observe','act','close'}
    assert store.read()==before and not env.app.service._engines and not env.app.service._queues


@pytest.mark.parametrize('origin',['file:///C:/private','https://user:secret@example.test','http://127.0.0.1/path'])
async def test_invalid_origin_returns_no_local_path_or_input(environment,origin):
    env=environment
    response=await env.client.post(PATH,json=dict(node=str(NODE),playwright=str(PW),executable=str(CHROME),origins=[origin]))
    assert response.status_code==422,response.text
    assert response.headers['cache-control']=='no-store' and set(response.json())=={'code'}
    assert origin not in response.text and str(NODE) not in response.text


async def test_missing_auth_and_oversize_refused(environment):
    env=environment
    response=await env.client.post(PATH,headers={'X-PyHarness-Token':'wrong'},json={})
    assert response.status_code in (401,403)
    response=await env.client.post(PATH,content='x'*9000)
    assert response.status_code==422
    response=await env.client.post(PATH,content='['*2000+'0'+']'*2000)
    assert response.status_code==422 and response.headers['cache-control']=='no-store'


@pytest.mark.parametrize('role',['owner','member','viewer'])
async def test_real_team_account_cannot_use_operator_header_for_browser_setup(environment,role):
    env=environment
    env.accounts.provision('browser-owner','synthetic-browser-owner-password','browser-team')
    env.accounts.provision('browser-user','synthetic-browser-user-password','browser-home')
    env.accounts.membership('browser-team','browser-user',role)
    response=await env.client.post('/api/account/login',json=dict(username='browser-user',
        password='synthetic-browser-user-password',tenant='browser-team'))
    assert response.status_code==200,response.text
    response=await env.client.post(PATH,json={},headers={'X-PyHarness-Tenant':'default'})
    assert response.status_code==403,response.text


async def test_cookie_revocation_during_body_read_refuses_metadata_and_no_header_fallback(environment):
    env=environment;env.accounts.provision_operator('browser-operator','synthetic-browser-operator-password')
    assert (await env.client.post('/api/account/login',json=dict(username='browser-operator',
        password='synthetic-browser-operator-password'))).status_code==200
    body=json.dumps(dict(node=str(NODE),playwright=str(PW),executable=str(CHROME),origins=['http://127.0.0.1:1'])).encode()
    entered=asyncio.Event();release=asyncio.Event()
    async def chunks():
        yield body[:10];entered.set();await release.wait();yield body[10:]
    task=asyncio.create_task(env.client.post(PATH,content=chunks()))
    try:
        await asyncio.wait_for(entered.wait(),10)
        env.accounts.logout(env.client.cookies.get('pyharness_account'))
        release.set();response=await asyncio.wait_for(task,10)
        assert response.status_code==403 and str(NODE) not in response.text
        assert not env.app.service._engines and not env.app.service._queues
    finally:
        release.set()
        if not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
