"""Personal WeChat iLink HTTP adapter, verified against Tencent's 2.4.10 source.

Reference commit 874637a0dbc24050f72d1385add4fc6170a2d726, Tencent/openclaw-weixin.
This is an independent Python implementation of the public protocol. It never
reads another client's login files, obtains a password, or exports chat history.
"""
from __future__ import annotations

import base64
import secrets
from urllib.parse import urlsplit
import httpx
from pydantic import BaseModel, ConfigDict, Field

BASE = 'https://ilinkai.weixin.qq.com/'
VERSION = '2.4.10'


class ILinkError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def official_url(url: str) -> str:
    parsed = urlsplit(url)
    host = parsed.hostname or ''
    if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None,443)
        or parsed.query or parsed.fragment or parsed.path not in ('','/')
        or not (host == 'ilinkai.weixin.qq.com' or host.endswith('.ilinkai.weixin.qq.com'))):
        raise ILinkError('wechat_untrusted_endpoint')
    return url.rstrip('/')+'/'


class WechatConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(default='personal',pattern=r'^[a-z][a-z0-9_]{1,30}$')
    enabled: bool = False
    agent_id: str | None = None
    agent_version: int | None = Field(default=None,ge=1)
    allow_approvals: bool = False
    allowed_users: list[str] = Field(default_factory=list,max_length=100)
    base_url: str = BASE
    bot_id: str = ''
    secret_ref: str = ''


class ILinkClient:
    def __init__(self, *, base_url=BASE, token='', transport=None):
        self.base_url = official_url(base_url)
        self.token = token
        self.http = httpx.AsyncClient(timeout=40, follow_redirects=False, transport=transport)

    def headers(self):
        return {'Content-Type':'application/json','AuthorizationType':'ilink_bot_token',
            'Authorization':'Bearer '+self.token if self.token else '',
            'X-WECHAT-UIN':base64.b64encode(str(secrets.randbits(32)).encode()).decode(),
            'iLink-App-Id':'bot','iLink-App-ClientVersion':str((2<<16)|(4<<8)|10)}

    async def post(self, path, body, *, timeout=15):
        response = await self.http.post(self.base_url+path, headers=self.headers(),
            json={**body,'base_info':{'channel_version':VERSION,'bot_agent':'PyHarness/0.1.0'}},timeout=timeout)
        response.raise_for_status()
        value = response.json()
        code = value.get('errcode') or value.get('ret') or 0
        if code:
            raise ILinkError('wechat_session_expired' if code == -14 else 'wechat_api_'+str(code))
        return value

    async def login_qr(self):
        return await self.post('ilink/bot/get_bot_qrcode?bot_type=3', {'local_token_list':[]})

    async def login_status(self, qrcode, *, verification_code=None):
        response = await self.http.get(self.base_url+'ilink/bot/get_qrcode_status',
            headers={k:v for k,v in self.headers().items() if k.startswith('iLink-')},
            params={'qrcode':qrcode,**({'verify_code':verification_code} if verification_code else {})},timeout=38)
        response.raise_for_status()
        return response.json()

    async def updates(self, cursor):
        return await self.post('ilink/bot/getupdates',{'get_updates_buf':cursor},timeout=38)

    async def send(self, user, context_token, text, ident):
        if not context_token:
            raise ILinkError('wechat_reply_context_missing')
        return await self.post('ilink/bot/sendmessage',{'msg':{'from_user_id':'',
            'to_user_id':user,'client_id':ident,'message_type':2,'message_state':2,
            'context_token':context_token,'item_list':[{'type':1,'text_item':{'text':text[:10000]}}]}})

    async def close(self):
        self.token = ''
        await self.http.aclose()
