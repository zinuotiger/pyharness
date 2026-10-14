"""Personal WeChat account lifecycle around the Tencent iLink protocol."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import time
import uuid
import httpx
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from pyharness.application.channels import Channels

from pyharness.integrations.wechat_ilink import BASE, ILinkClient, ILinkError, WechatConfig, official_url
from pyharness.core.sandbox import fail


class WechatChannel:
    def __init__(self, channels: Channels):
        self.channels, self.service = channels, channels.service
        self.client = None
        self.poller = None
        self.login = None
        self.state = 'not_bound'
        self.error = None
        self.last_received = self.last_sent = None
        self.lock = asyncio.Lock()
        self.login_client = None
        self.client_factory = ILinkClient
        channels.db.execute('CREATE TABLE IF NOT EXISTS channel_state(key TEXT PRIMARY KEY,value TEXT NOT NULL)')

    def config(self):
        return WechatConfig.model_validate(self.service.platform.store.read()['preferences'].get('wechat',{}))

    def _save(self, config):
        with self.service.platform.store.transaction() as doc:
            doc['preferences']['wechat'] = config.model_dump()

    async def configure(self, body):
        self.channels._local_only()
        old = self.config()
        config = WechatConfig.model_validate({**old.model_dump(),**body})
        if any(k in body for k in ('secret_ref','base_url','bot_id')):
            fail('wechat_server_owned_configuration')
        if config.name != old.name and old.secret_ref:
            fail('wechat_bound_name_fixed')
        if bool(config.agent_id) != bool(config.agent_version):
            fail('channel_agent_version_required')
        if config.agent_id:
            self.service.platform.new_binding_version(config.agent_id,config.agent_version)
        await self.stop()
        self._save(config)
        self.start()
        return self.status()

    def status(self):
        config = self.config()
        return {'kind':'personal','provider':'tencent_ilink','name':config.name,
            'enabled':config.enabled,'bound':bool(config.secret_ref),'connected':self.state=='receiving',
            'status':self.state,'error':self.error,'last_received':self.last_received,'last_sent':self.last_sent,
            'account_action_required':not bool(config.secret_ref),
            'detail':'微信 ClawBot / iLink 自动收发；需在微信启用插件并扫码确认。当前仅处理已授权私聊文本。'}

    async def begin_login(self):
        self.channels._local_only()
        async with self.lock:
            if self.login_client:
                await self.login_client.close()
            self.login_client = self.client_factory()
            value = await self.login_client.login_qr()
            content = str(value['qrcode_img_content'])
            if len(content)>10000 or not value.get('qrcode'):
                raise ILinkError('wechat_invalid_qrcode')
            import qrcode
            buffer = io.BytesIO()
            qrcode.make(content).save(buffer, format='PNG')
            self.login = {'id':uuid.uuid4().hex,'qrcode':value['qrcode'],'created':time.time()}
            self.state = 'waiting_scan'
            return {'login_id':self.login['id'],'image':'data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode(),
                    'status':self.state,'expires_in':300}

    async def login_status(self, ident, verification_code=None):
        self.channels._local_only()
        async with self.lock:
            if not self.login or self.login['id'] != ident or time.time()-self.login['created']>300:
                self.state = 'login_expired'
                if self.login_client:
                    await self.login_client.close()
                    self.login_client = None
                self.login = None
                return {'status':self.state}
            value = await self.login_client.login_status(self.login['qrcode'], verification_code=verification_code)
            status = value.get('status','wait')
            if status=='scaned_but_redirect':
                # Validate before forwarding any QR session identifier; never trust an arbitrary URL.
                self.login_client.base_url = official_url('https://'+value['redirect_host'])
                self.state = 'waiting_confirmation'
            elif status=='confirmed':
                config = self.config()
                if not all(value.get(k) for k in ('bot_token','ilink_bot_id','ilink_user_id')):
                    raise ILinkError('wechat_invalid_login')
                config.base_url = official_url(value.get('baseurl') or BASE)
                same_bot = config.bot_id == value['ilink_bot_id']
                config.bot_id = value['ilink_bot_id']
                config.allowed_users = sorted(set([*(config.allowed_users if same_bot else []),value['ilink_user_id']]))
                config.secret_ref = self.service.credentials.set_service_secret(self.service.tenant_id,
                    'wechat:'+config.name, config.base_url, value['bot_token'])
                # Binding is not permission to receive/execute; explicit enable remains separate.
                self._save(config)
                self.login = None
                await self.login_client.close()
                self.login_client = None
                self.state = 'bound'
                self.start()
            elif status=='binded_redirect':
                self.state = 'bound' if self.config().secret_ref else 'rebind_required'
            else:
                self.state = {'wait':'waiting_scan','scaned':'waiting_confirmation','expired':'login_expired',
                              'need_verifycode':'verification_code_required','verify_code_blocked':'verification_blocked'}.get(status,'login_unavailable')
            return self.status()

    def start(self):
        config = self.config()
        if config.enabled and config.secret_ref and not self.channels.closed and (self.poller is None or self.poller.done()):
            self.poller = asyncio.create_task(self._poll())
            self.poller.add_done_callback(self._poll_done)

    def _poll_done(self, task):
        if task.cancelled():
            return
        error = task.exception()
        if error:
            self.state = 'connection_failed'
            self.error = str(getattr(error,'code',type(error).__name__))

    async def _poll(self):
        config = self.config()
        token = self.service.credentials.resolve_service_secret(config.secret_ref,self.service.tenant_id,config.base_url)
        self.client = self.client_factory(base_url=config.base_url,token=token)
        delay = 1
        try:
            while True:
                try:
                    cursor = self.channels.db.execute('SELECT value FROM channel_state WHERE key=?',('wechat:'+config.name,)).fetchone()
                    result = await self.client.updates(cursor['value'] if cursor else '')
                    # Persist every accepted message before acknowledging the opaque cursor.
                    for message in result.get('msgs') or []:
                        await self._receive(config,message)
                    with self.channels.db:
                        self.channels.db.execute('INSERT OR REPLACE INTO channel_state(key,value) VALUES(?,?)',
                            ('wechat:'+config.name,str(result.get('get_updates_buf') or (cursor['value'] if cursor else ''))))
                    self.state, self.error, delay = 'receiving', None, 1
                    if not result.get('msgs'):
                        await asyncio.sleep(0.25)
                except ILinkError as exc:
                    self.error = exc.code
                    if exc.code=='wechat_session_expired':
                        self.state = 'rebind_required'
                        return
                    self.state = 'reconnecting'
                    await asyncio.sleep(delay)
                    delay = min(delay*2,30)
                except (httpx.HTTPError, ValueError, KeyError) as exc:
                    self.state, self.error = 'reconnecting', type(exc).__name__
                    await asyncio.sleep(delay)
                    delay = min(delay*2,30)
                except Exception as exc:
                    self.state = 'backpressure'
                    self.error = str(getattr(exc,'code',type(exc).__name__))
                    return  # Keep the cursor unacknowledged; require operator recovery.
        finally:
            await self.client.close()
            self.client = None

    async def _receive(self, config, message):
        if message.get('message_type') != 1 or message.get('group_id'):
            return
        user = message.get('from_user_id')
        if user not in config.allowed_users or message.get('to_user_id') != config.bot_id:
            return
        text = '\n'.join(str(item.get('text_item',{}).get('text','')) for item in message.get('item_list',[]) if item.get('type')==1).strip()
        if not text or len(text)>32000 or not message.get('context_token') or not message.get('message_id'):
            return
        name = 'wechat:'+config.name
        ident = hashlib.sha256((name+'\0'+str(message['message_id'])).encode()).hexdigest()
        owner = hashlib.sha256((name+'\0'+user).encode()).hexdigest()
        context = self.service.credentials.set_service_secret(self.service.tenant_id,
            'wechat_context:'+ident,config.base_url,message['context_token'])
        self.channels.enqueue(ident,name,owner,str(message['message_id']),text,
                              json.dumps({'user':user,'context_ref':context}))
        self.last_received = time.time()

    async def reply(self, row):
        if not self.client:
            raise ILinkError('wechat_not_receiving')
        config = self.config()
        context = json.loads(row['context'])
        if context['user'] not in config.allowed_users:
            raise ILinkError('wechat_recipient_not_authorized')
        value = self.service.credentials.resolve_service_secret(context['context_ref'],self.service.tenant_id,config.base_url)
        await self.client.send(context['user'],value,row['reply'],'pyharness-'+row['id'])
        self.last_sent = time.time()

    async def stop(self):
        if self.poller:
            self.poller.cancel()
            await asyncio.gather(self.poller,return_exceptions=True)
            self.poller = None
        if self.client:
            await self.client.close()
            self.client = None
        self.state = 'bound' if self.config().secret_ref else 'not_bound'

    async def close(self):
        await self.stop()
        if self.login_client:
            await self.login_client.close()
            self.login_client = None
        self.login = None
