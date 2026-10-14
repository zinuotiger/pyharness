import asyncio
import json

import httpx
import pytest

from pyharness import cli
from pyharness.application.channels import Channels
from pyharness.application.service import ApplicationService
from pyharness.integrations.wechat_ilink import ILinkClient, ILinkError, official_url
from tests.support import isolated_settings


async def test_ilink_wire_contract_and_no_credentials_in_login_status():
    requests = []
    async def handle(request):
        requests.append(request)
        if 'getupdates' in request.url.path:
            assert request.headers['authorization']=='Bearer synthetic'
            assert json.loads(request.content)['get_updates_buf']=='cursor'
            return httpx.Response(200,json={'ret':0,'msgs':[],'get_updates_buf':'next'})
        if 'sendmessage' in request.url.path:
            message = json.loads(request.content)['msg']
            assert message['context_token']=='synthetic-context'
            assert message['client_id']=='unique'
            assert message['to_user_id']=='user'
            return httpx.Response(200,json={'ret':0,'message_id':'9007199254740999'})
        return httpx.Response(200,json={'qrcode':'session','qrcode_img_content':'https://weixin.qq.com/test'})
    client = ILinkClient(token='synthetic',transport=httpx.MockTransport(handle))
    assert (await client.updates('cursor'))['get_updates_buf']=='next'
    assert (await client.send('user','synthetic-context','answer','unique'))['message_id']=='9007199254740999'
    assert requests[0].headers['ilink-app-id']=='bot'
    await client.close()
    with pytest.raises(ILinkError):
        official_url('https://evil.test/')
    with pytest.raises(ILinkError):
        official_url('https://ilinkai.weixin.qq.com@evil.test/')


async def test_wechat_automatic_inbound_queue_dedup_reply_and_shutdown(tmp_path,adapter_factory):
    cfg = isolated_settings(tmp_path)
    adapter_factory(cfg)
    service = ApplicationService(cli.assemble_ctx(cfg))
    try:
        channel = service.channels.wechat
        class FakeClient:
            closed = False
            sent = []
            def __init__(self, **kwargs):
                pass
            async def login_qr(self):
                return {'qrcode':'session','qrcode_img_content':'https://weixin.qq.com/qr-synthetic'}
            async def login_status(self, *args, **kwargs):
                return {'status':'confirmed','bot_token':'synthetic-token','ilink_bot_id':'bot','ilink_user_id':'user'}
            async def send(self,*args):
                self.sent.append(args)
            async def close(self):
                self.closed = True
        channel.client_factory = FakeClient
        login = await channel.begin_login()
        status = await channel.login_status(login['login_id'])
        assert status['bound'] and not status['connected']
        assert 'synthetic-token' not in json.dumps(status)
        config = channel.config()
        config.enabled = True
        channel._save(config)
        message = {'message_type':1,'message_id':'9007199254740999','from_user_id':'user','to_user_id':'bot',
            'context_token':'synthetic-context','item_list':[{'type':1,'text_item':{'text':'hello'}}]}
        await channel._receive(config,message)
        await channel._receive(config,message)
        assert service.channels.db.execute('SELECT COUNT(*) FROM inbox').fetchone()[0]==1
        row = dict(service.channels.db.execute('SELECT * FROM inbox').fetchone())
        assert 'synthetic-context' not in row['context']
        await service.channels._process(row)
        row = dict(service.channels.db.execute('SELECT * FROM inbox').fetchone())
        sid,task=row['run'].split('~')
        assert (await service._queues[sid].wait_for(task)).ok
        await service.channels._process(row)
        row = dict(service.channels.db.execute('SELECT * FROM inbox').fetchone())
        channel.client = FakeClient()
        await service.channels._process(row)
        assert service.channels.db.execute('SELECT state FROM inbox').fetchone()[0]=='delivered'
        assert FakeClient.sent[-1][0:2]==('user','synthetic-context')
        assert FakeClient.sent[-1][3]=='pyharness-'+row['id']
    finally:
        await service.shutdown()


@pytest.mark.parametrize('backpressure',[False,True])
async def test_poll_acknowledges_only_durable_messages_and_stops_on_expiry(tmp_path,backpressure):
    from pyharness.errors import PyHError
    service = ApplicationService(cli.assemble_ctx(isolated_settings(tmp_path)))
    channels = service.channels
    channel = channels.wechat
    closed = []
    class Client:
        def __init__(self,**kwargs): self.calls=0
        async def updates(self,cursor):
            self.calls += 1
            if self.calls>1: raise ILinkError('wechat_session_expired')
            return {'msgs':[{'message_type':1,'message_id':'one','from_user_id':'user','to_user_id':'bot',
                'context_token':'synthetic','item_list':[{'type':1,'text_item':{'text':'task'}}]}],
                'get_updates_buf':'durable-cursor'}
        async def close(self): closed.append(True)
    channel.client_factory=Client
    config=channel.config()
    config.enabled,config.bot_id,config.allowed_users=True,'bot',['user']
    config.secret_ref=service.credentials.set_service_secret('default','wechat:personal',config.base_url,'synthetic')
    channel._save(config)
    if backpressure:
        def reject(*args): raise PyHError('channel_inbox_full')
        channels.enqueue=reject
    try:
        await asyncio.wait_for(channel._poll(),3)
        cursor=channels.db.execute('SELECT value FROM channel_state').fetchone()
        assert bool(cursor) is not backpressure
        if cursor: assert cursor['value']=='durable-cursor'
        assert channel.state==('backpressure' if backpressure else 'rebind_required')
        assert closed and channel.client is None
    finally:
        await service.shutdown()
