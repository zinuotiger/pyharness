import asyncio
import hashlib
import json
import time

import httpx
import pytest

from pyharness import cli
from pyharness.application.channels import Channels
from pyharness.desktop.app import DesktopApp
from pyharness.integrations.feishu import FeishuConfig, FeishuClient, FeishuRejected, verify_event
from tests.support import isolated_settings

SECRETS = {'app_secret':'synthetic','encrypt_key':'synthetic-key','verification_token':'synthetic-token'}
CONFIG = {'name':'test','app_id':'cli_test','tenant_key':'tenant','allowed_users':['ou_test'],
          'allowed_chats':['oc_test'],'enabled':True}


def event(text='hello', message='om_test', user='ou_test'):
    return {'schema':'2.0','header':{'event_id':'ev_test','event_type':'im.message.receive_v1',
        'app_id':'cli_test','tenant_key':'tenant','token':SECRETS['verification_token']},
        'event':{'sender':{'sender_type':'user','sender_id':{'open_id':user},'tenant_key':'tenant'},
        'message':{'message_id':message,'message_type':'text','chat_id':'oc_test','content':json.dumps({'text':text})}}}


def signed(body, timestamp=None):
    raw = json.dumps(body).encode()
    timestamp = str(int(time.time()) if timestamp is None else timestamp)
    headers = {'X-Lark-Request-Timestamp':timestamp,'X-Lark-Request-Nonce':'nonce'}
    headers['X-Lark-Signature'] = hashlib.sha256((timestamp+'nonce'+SECRETS['encrypt_key']).encode()+raw).hexdigest()
    return raw, headers


def test_signed_encrypted_event_identity_and_replay():
    import base64
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.padding import PKCS7
    raw, headers = signed(event())
    config = FeishuConfig(**CONFIG)
    assert verify_event(raw,headers,config,SECRETS)['text']=='hello'
    with pytest.raises(FeishuRejected):
        verify_event(raw+b' ',headers,config,SECRETS)
    with pytest.raises(FeishuRejected):
        verify_event(*signed(event(),timestamp=1),config,SECRETS)
    with pytest.raises(FeishuRejected):
        verify_event(*signed(event(user='intruder')),config,SECRETS)
    iv = b'0'*16
    pad = PKCS7(128).padder()
    padded = pad.update(raw)+pad.finalize()
    encryptor = Cipher(algorithms.AES(hashlib.sha256(SECRETS['encrypt_key'].encode()).digest()),modes.CBC(iv)).encryptor()
    encrypted = base64.b64encode(iv+encryptor.update(padded)+encryptor.finalize()).decode()
    assert verify_event(*signed({'encrypt':encrypted}),config,SECRETS)['message_id']=='om_test'
    challenge = {'type':'url_verification','token':SECRETS['verification_token'],'challenge':'synthetic'}
    assert verify_event(json.dumps(challenge).encode(),{},config,SECRETS)=={'challenge':'synthetic'}
    with pytest.raises(FeishuRejected):
        verify_event(b'[]',{},config,SECRETS)


async def test_official_reply_api_with_http_protocol_simulation():
    sent = []
    async def handle(request):
        sent.append(request)
        if '/auth/' in request.url.path:
            return httpx.Response(200,json={'code':0,'tenant_access_token':'synthetic','expire':7200})
        assert request.headers['authorization']=='Bearer synthetic'
        assert json.loads(request.content)['uuid']=='dedup'
        return httpx.Response(200,json={'code':0,'data':{'message_id':'out'}})
    client = FeishuClient(FeishuConfig(**CONFIG),SECRETS,transport=httpx.MockTransport(handle))
    await client.reply('om_test','result','dedup')
    await client.reply('om_test','result','dedup')
    assert len(sent)==3
    assert '/im/v1/messages/om_test/reply'==sent[-1].url.path.removeprefix('/open-apis')
    await client.close()


async def test_ingress_real_queue_dedup_restart_and_permission(tmp_path,adapter_factory):
    cfg = isolated_settings(tmp_path)
    adapter_factory(cfg)
    app = DesktopApp(cli.assemble_ctx(cfg))
    try:
        channels = app.service.channels
        channels.save({**CONFIG,'secrets':SECRETS})
        channels.start = lambda: None  # Drive delivery explicitly to avoid timer races.
        raw, headers = signed(event())
        # Authenticate entirely by signed platform envelope; never by operator token.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api),base_url='http://127.0.0.1') as client:
            assert (await client.post('/hooks/feishu/test',content=raw,headers=headers)).status_code==200
            assert (await client.post('/hooks/feishu/test',content=raw,headers=headers)).status_code==200
            assert (await client.post('/api/v1/runs',json={'text':'unauthorized'})).status_code==401
            assert (await client.post('/hooks/feishu/test',content=raw+b' ',headers=headers)).status_code==403
        if channels.worker:
            channels.worker.cancel()
            await asyncio.gather(channels.worker,return_exceptions=True)
        row = dict(channels.db.execute('SELECT * FROM inbox').fetchone())
        await channels._process(row)
        row = dict(channels.db.execute('SELECT * FROM inbox').fetchone())
        assert row['state']=='accepted'
        sid,task=row['run'].split('~')
        assert (await app.service._queues[sid].wait_for(task)).ok
        await channels._process(row)
        assert channels.db.execute('SELECT state FROM inbox').fetchone()[0]=='reply_pending'
        accepted = await app.service.platform.start_run({'text':'hello','session_id':sid,'request_id':'channel:'+row['id']})
        assert accepted['run_id']==row['run']
        assert len([e for e in (await app.service.require_session(sid)).events_after(0) if e.type=='task.enqueued'])==1
        await channels.close()
        app.service._channels = Channels(app.service)
        await app.service.channels.receive('test',raw,headers)
        assert app.service.channels.db.execute('SELECT COUNT(*) FROM inbox').fetchone()[0]==1
    finally:
        await app.service.shutdown()
