"""Feishu signed HTTP events and official message reply API."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import httpx
from pydantic import BaseModel, ConfigDict, Field


class FeishuConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(pattern=r'^[a-z][a-z0-9_]{1,30}$')
    app_id: str = Field(min_length=1,max_length=100)
    tenant_key: str = Field(min_length=1,max_length=100)
    allowed_users: list[str] = Field(min_length=1,max_length=100)
    allowed_chats: list[str] = Field(min_length=1,max_length=100)
    agent_id: str | None = None
    agent_version: int | None = Field(default=None,ge=1)
    allow_approvals: bool = False
    enabled: bool = False
    secret_ref: str = ''


class FeishuRejected(ValueError):
    pass


def verify_event(raw: bytes, headers, config: FeishuConfig, secrets: dict, *, now=None) -> dict:
    if len(raw) > 1024*1024:
        raise FeishuRejected('event_too_large')
    headers = {k.lower():v for k,v in headers.items()}
    key = str(secrets.get('encrypt_key',''))
    if not key:
        raise FeishuRejected('encryption_key_required')
    try:
        body = json.loads(raw)
        if not isinstance(body,dict):
            raise FeishuRejected('invalid_event')
        if 'encrypt' in body:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            from cryptography.hazmat.primitives.padding import PKCS7
            encrypted = base64.b64decode(body['encrypt'], validate=True)
            decryptor = Cipher(algorithms.AES(hashlib.sha256(key.encode()).digest()), modes.CBC(encrypted[:16])).decryptor()
            padded = decryptor.update(encrypted[16:]) + decryptor.finalize()
            unpadder = PKCS7(128).unpadder()
            body = json.loads(unpadder.update(padded)+unpadder.finalize())
        header = body.get('header') or {}
        token = body.get('token') or header.get('token') or ''
        if not secrets.get('verification_token') or not hmac.compare_digest(token, secrets['verification_token']):
            raise FeishuRejected('invalid_verification_token')
        if body.get('type') == 'url_verification':
            return {'challenge':str(body['challenge'])[:1000]}
        timestamp = headers.get('x-lark-request-timestamp','')
        nonce = headers.get('x-lark-request-nonce','')
        try:
            if abs((time.time() if now is None else now) - int(timestamp)) > 300 or not nonce:
                raise ValueError
        except ValueError:
            raise FeishuRejected('stale_event') from None
        expected = hashlib.sha256((timestamp+nonce+key).encode()+raw).hexdigest()
        if not hmac.compare_digest(expected, headers.get('x-lark-signature','')):
            raise FeishuRejected('invalid_signature')
        if header.get('app_id') != config.app_id or header.get('tenant_key') != config.tenant_key:
            raise FeishuRejected('application_identity_mismatch')
        if header.get('event_type') != 'im.message.receive_v1':
            return {'ignored':True}
        sender, msg = body['event']['sender'], body['event']['message']
        user = sender['sender_id']['open_id']
        if sender.get('sender_type') != 'user' or user not in config.allowed_users or msg['chat_id'] not in config.allowed_chats:
            raise FeishuRejected('sender_not_authorized')
        if sender.get('tenant_key') != config.tenant_key:
            raise FeishuRejected('sender_tenant_mismatch')
        if msg.get('message_type') != 'text':
            return {'ignored':True}
        text = json.loads(msg['content'])['text'].strip()
        if not 0 < len(text) <= 32000:
            raise FeishuRejected('invalid_message')
        return {'event_id':header['event_id'], 'message_id':msg['message_id'],
                'user':user, 'chat':msg['chat_id'], 'text':text}
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        if isinstance(exc, FeishuRejected):
            raise
        raise FeishuRejected('invalid_event') from None


class FeishuClient:
    def __init__(self, config: FeishuConfig, secrets: dict, *, transport=None):
        self.config, self.secrets = config, secrets
        self.client = httpx.AsyncClient(base_url='https://open.feishu.cn/open-apis/', timeout=15,
                                         follow_redirects=False, transport=transport)
        self.token, self.expires = '', 0

    async def access_token(self):
        if self.token and time.monotonic() < self.expires:
            return self.token
        response = await self.client.post('auth/v3/tenant_access_token/internal', json={
            'app_id':self.config.app_id, 'app_secret':self.secrets['app_secret']})
        response.raise_for_status()
        body = response.json()
        if body.get('code') != 0 or not body.get('tenant_access_token'):
            raise FeishuRejected('feishu_auth_failed')
        self.token = body['tenant_access_token']
        self.expires = time.monotonic() + max(0, int(body.get('expire',0))-60)
        return self.token

    async def reply(self, message_id: str, text: str, dedup_id: str):
        from urllib.parse import quote
        token = await self.access_token()
        response = await self.client.post('im/v1/messages/'+quote(message_id,safe='')+'/reply',
            headers={'Authorization':'Bearer '+token}, json={'msg_type':'text',
                'content':json.dumps({'text':text[:12000]},ensure_ascii=False), 'uuid':dedup_id})
        if response.status_code == 401:
            self.token = ''
        response.raise_for_status()
        code = response.json().get('code')
        if code in (99991661,99991663):
            self.token = ''
        if code != 0:
            raise FeishuRejected('feishu_reply_failed')

    async def close(self):
        self.token = ''
        await self.client.aclose()
