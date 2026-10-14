"""Durable channel inbox/delivery state; tasks execute only in the existing queue."""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
import uuid
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from pyharness.application.service import ApplicationService

from pyharness.application.accounts import Accounts, current_actor
from pyharness.core.sandbox import fail
from pyharness.integrations.feishu import FeishuClient, FeishuConfig, verify_event

ENDPOINT = 'https://open.feishu.cn/open-apis/'


class Channels:
    def __init__(self, service: ApplicationService):
        self.service = service
        root = service.tenant_root()/'channels'
        root.mkdir(parents=True,exist_ok=True)
        self.db = sqlite3.connect(root/'inbox.db')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS sessions (owner TEXT PRIMARY KEY, sid TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS inbox (id TEXT PRIMARY KEY, connection TEXT NOT NULL,
                owner TEXT NOT NULL, message TEXT NOT NULL, text TEXT NOT NULL,
                sid TEXT, run TEXT, state TEXT NOT NULL DEFAULT 'pending',
                reply TEXT, attempts INTEGER NOT NULL DEFAULT 0, next_try REAL NOT NULL DEFAULT 0,
                error TEXT, created REAL NOT NULL);
        ''')
        if 'context' not in {r[1] for r in self.db.execute('PRAGMA table_info(inbox)')}:
            self.db.execute("ALTER TABLE inbox ADD COLUMN context TEXT NOT NULL DEFAULT '{}'")
            self.db.commit()
        self.clients = {}
        self.worker = None
        self.closed = False
        from pyharness.application.wechat_channel import WechatChannel
        self.wechat = WechatChannel(self)

    def _local_only(self):
        if self.service.tenant_id != 'default' or current_actor.get() or Accounts.manages_tenant(self.service.storage_root, self.service.tenant_id):
            fail('channel_operator_required')

    def configs(self):
        return self.service.platform.store.read()['preferences'].get('channels',{})

    def save(self, body):
        self._local_only()
        body = dict(body)
        secrets = body.pop('secrets', None)
        config = FeishuConfig.model_validate(body)
        old = self.configs().get(config.name, {})
        config.secret_ref = old.get('secret_ref','')
        if secrets is not None:
            if set(secrets) != {'app_secret','encrypt_key','verification_token'} or not all(isinstance(v,str) and v for v in secrets.values()):
                fail('channel_secrets_invalid')
            config.secret_ref = self.service.credentials.set_service_secret(self.service.tenant_id,
                'feishu:'+config.name, ENDPOINT, json.dumps(secrets,ensure_ascii=False))
        if config.enabled and not config.secret_ref:
            fail('channel_credentials_missing')
        if bool(config.agent_id) != bool(config.agent_version):
            fail('channel_agent_version_required')
        if config.agent_id:
            definition = self.service.platform.new_binding_version(config.agent_id,config.agent_version)['snapshot']
            from pyharness.application.platform_models import AgentDefinition
            self.service.platform.validate_execution_policy(AgentDefinition.model_validate(definition))
        with self.service.platform.store.transaction() as doc:
            doc['preferences'].setdefault('channels',{})[config.name] = config.model_dump()
        return self.status()

    def status(self):
        configs = self.configs()
        return {'feishu':[{'name':name,'enabled':c['enabled'], 'app_id':c['app_id'],
            'has_credentials':bool(c['secret_ref']), 'status':'configured' if c['enabled'] else 'disabled',
            'network_tested':False, 'callback_path':'/hooks/feishu/'+name} for name,c in configs.items()],
            'delivery':[dict(row) for row in self.db.execute('SELECT state,COUNT(*) AS count FROM inbox GROUP BY state')],
            'wechat':self.wechat.status()}

    def secrets(self, config):
        return json.loads(self.service.credentials.resolve_service_secret(config.secret_ref,
            self.service.tenant_id, ENDPOINT))

    def deliveries(self, *, after=0, limit=100):
        self._local_only()
        if type(limit) is not int or not 1<=limit<=100 or type(after) is not int or after<0:
            fail('channel_page_invalid')
        rows = [dict(r) for r in self.db.execute('SELECT rowid AS cursor,id,connection,sid,run,state,attempts,error,created FROM inbox WHERE rowid>? ORDER BY rowid LIMIT ?', (after,limit+1))]
        return {'deliveries':rows[:limit],'cursor':rows[limit-1]['cursor'] if len(rows)>limit else None}

    def retry_delivery(self, ident):
        self._local_only()
        row = self.db.execute('SELECT * FROM inbox WHERE id=?',(ident,)).fetchone()
        if not row or row['state']!='feedback_failed':
            fail('channel_delivery_not_retryable')
        state = 'reply_pending' if row['reply'] else 'accepted' if row['run'] else 'pending'
        with self.db:
            self.db.execute('UPDATE inbox SET state=?,attempts=0,next_try=0,error=NULL WHERE id=?',(state,ident))
        self.start()
        return {'ok':True,'state':state}

    async def receive(self, name, raw, headers):
        self.service._ensure_open()
        config = FeishuConfig.model_validate(self.configs().get(name, {}))
        if not config.enabled:
            fail('channel_disabled')
        event = verify_event(raw, headers, config, self.secrets(config))
        if 'challenge' in event or event.get('ignored'):
            return event
        owner = hashlib.sha256((name+'\0'+event['user']+'\0'+event['chat']).encode()).hexdigest()
        ident = hashlib.sha256((name+'\0'+event['message_id']).encode()).hexdigest()
        # Idempotency is message-based: different event IDs for the same message
        # cannot create another task. Limits bound disk growth until operator cleanup.
        self.enqueue(ident,name,owner,event['message_id'],event['text'])
        self.start()
        return {'ok':True}

    def enqueue(self, ident, name, owner, message, text, context='{}'):
        if self.db.execute('SELECT COUNT(*) FROM inbox').fetchone()[0] >= 10000 and not self.db.execute('SELECT 1 FROM inbox WHERE id=?',(ident,)).fetchone():
            fail('channel_inbox_full')
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO inbox(id,connection,owner,message,text,created,context) VALUES(?,?,?,?,?,?,?)',
                            (ident,name,owner,message,text,time.time(),context))

    def start(self):
        self.wechat.start()
        if not self.closed and (self.worker is None or self.worker.done()):
            self.worker = asyncio.create_task(self._work())

    async def _work(self):
        while not self.closed:
            rows = list(self.db.execute("SELECT * FROM inbox WHERE state IN ('pending','accepted','reply_pending') AND next_try<=? ORDER BY created LIMIT 20",(time.time(),)))
            for row in rows:
                try:
                    await self._process(dict(row))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    tries = row['attempts']+1
                    with self.db:
                        self.db.execute('UPDATE inbox SET attempts=?,next_try=?,error=?,state=? WHERE id=?',
                            (tries,time.time()+min(300,2**tries),type(exc).__name__,
                             'feedback_failed' if tries>=6 else row['state'],row['id']))
            await asyncio.sleep(1)

    async def _process(self, row):
        config = self.wechat.config() if row['connection'].startswith('wechat:') else FeishuConfig.model_validate(self.configs().get(row['connection'],{}))
        if not config.enabled:
            return
        token = current_actor.set(None)  # Identity is the explicit local operator's channel allowlist.
        try:
            if row['state'] == 'pending':
                session = self.db.execute('SELECT sid FROM sessions WHERE owner=?',(row['owner'],)).fetchone()
                if not session:
                    sid = await self.service.create_session()
                    with self.db:
                        self.db.execute('INSERT INTO sessions(owner,sid) VALUES(?,?)',(row['owner'],sid))
                else:
                    sid = session['sid']
                if row['text'].startswith('/'):
                    response = await self._command(row,sid,config)
                    with self.db:
                        self.db.execute("UPDATE inbox SET sid=?,state='reply_pending',reply=? WHERE id=?",(sid,response,row['id']))
                else:
                    result = await self.service.platform.start_run({'text':row['text'],'session_id':sid,
                        'agent_id':config.agent_id,'agent_version':config.agent_version,'request_id':'channel:'+row['id']})
                    with self.db:
                        self.db.execute("UPDATE inbox SET sid=?,run=?,state='accepted',attempts=0 WHERE id=?",(sid,result['run_id'],row['id']))
            elif row['state'] == 'accepted':
                run = await self.service.platform.run(row['run'])
                if run['status'] not in {'completed','failed','cancelled'}:
                    return
                log = await self.service.require_session(row['sid'])
                # Exact task ownership, not the last message from a multi-turn session.
                from pyharness.application.platform_projection import task_events
                messages = [e.payload['content'] for e in task_events(log.events_after(0),run['task_id']) if e.type=='agent.message']
                from pyharness.application.platform_projection import summary
                text = '任务 '+row['run']+'：'+run['status']+'\n'+summary('\n'.join(messages) or run.get('error_code') or '',10000)
                with self.db:
                    self.db.execute("UPDATE inbox SET state='reply_pending',reply=?,attempts=0 WHERE id=?",(text,row['id']))
            else:
                if row['connection'].startswith('wechat:'):
                    await self.wechat.reply(row)
                else:
                    client = self.clients.get(config.name)
                    if client is None:
                        client = self.clients[config.name] = FeishuClient(config,self.secrets(config))
                    await client.reply(row['message'],row['reply'],str(uuid.UUID(row['id'][:32])))
                with self.db:
                    self.db.execute("UPDATE inbox SET state='delivered',error=NULL WHERE id=?",(row['id'],))
        finally:
            current_actor.reset(token)

    async def _command(self, row, sid, config):
        parts = row['text'].split()
        command = parts[0]
        own = self.db.execute('SELECT run FROM inbox WHERE owner=? AND run IS NOT NULL ORDER BY created DESC LIMIT 1',(row['owner'],)).fetchone()
        if command in {'/status','/cancel'}:
            ident = parts[1] if len(parts)>1 else own['run'] if own else None
            if not ident or not self.db.execute('SELECT 1 FROM inbox WHERE owner=? AND run=?',(row['owner'],ident)).fetchone():
                return '没有属于当前用户及聊天的任务。'
            result = await (self.service.platform.cancel(ident) if command=='/cancel' else self.service.platform.run(ident))
            return json.dumps({k:result[k] for k in ('run_id','status','ok','error_code') if k in result},ensure_ascii=False)
        if command in {'/approve','/reject'} and config.allow_approvals and len(parts)==2:
            if not parts[1].isdigit():
                return '审批编号无效。'
            pending = await self.service.pending_approvals()
            if not any(a['session_id']==sid and a['approval_id']==int(parts[1]) for a in pending['pending']):
                return '当前用户会话没有此待审批请求。'
            await self.service.decide_approval(int(parts[1]),'approve' if command=='/approve' else 'deny',sid=sid)
            return '审批结果已记录。'
        if command=='/approvals':
            pending = await self.service.pending_approvals()
            return json.dumps([a for a in pending['pending'] if a['session_id']==sid],ensure_ascii=False)[:10000]
        return '命令：/status [任务编号]、/cancel [任务编号]、/approvals。人工审批需操作者显式启用后使用 /approve 或 /reject。'

    async def close(self):
        if self.closed:
            return
        self.closed = True
        await self.wechat.close()
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker,return_exceptions=True)
        await asyncio.gather(*(client.close() for client in self.clients.values()))
        self.clients.clear()
        self.db.close()
