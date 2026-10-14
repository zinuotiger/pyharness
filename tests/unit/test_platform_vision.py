"""Real Platform/Adapter path; synthetic transport, never provider validation."""
import asyncio
import base64
import json
import struct
import zlib

import pytest
from pyharness import cli
from pyharness.core import llm
from pyharness.desktop.app import DesktopApp
from pyharness.errors import PyHError
from tests.support import isolated_settings
from tests.unit.test_llm import FakeTransport, _raw, _usage


def png():
    def chunk(kind, data):
        return struct.pack('!I', len(data))+kind+data+struct.pack('!I', zlib.crc32(kind+data))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',1,1,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\x00\x00\x80\xff'))+chunk(b'IEND',b'')


async def upload(p, sid, raw=None, name='sample.png'):
    return await p.upload({'session_id':sid,'filename':name,
        'data_base64':base64.b64encode(png() if raw is None else raw).decode()})


@pytest.mark.parametrize('enabled', [False, True])
async def test_vision_normal_adapter_chain_and_persistent_metadata(tmp_path, monkeypatch, enabled):
    cfg=isolated_settings(tmp_path)
    app=DesktopApp(cli.assemble_ctx(cfg)); service=app.service; p=service.platform
    service.save_model_profile({'id':'vision','model':'synthetic-vision','base_url':'https://synthetic.invalid/v1',
        'api_key':'synthetic-vision-secret','supports_vision':enabled,'fallback_models':[]})
    transport=FakeTransport(result=_raw('A blue pixel',model='synthetic-vision',usage=_usage(12,4)))
    monkeypatch.setattr(llm,'build_client',lambda *args: transport)
    try:
        sid=await service.create_session(); item=await upload(p,sid)
        # Naming through the normal persisted event avoids the optional title call.
        log=await service.require_session(sid)
        await log.append('session.renamed',{'new_title':'Synthetic vision','by':'user'},actor='user',sync=True)
        body={'text':'Describe this synthetic image','session_id':sid,'artifact_ids':[item['artifact_id']],'use_vision':True}
        if not enabled:
            with pytest.raises(PyHError,match='vision_not_supported'):await p.start_run(body)
            assert not transport.requests and not service._queues
            return
        run=await p.start_run(body)
        outcome=await asyncio.wait_for(service._queues[sid].wait_for(run['task_id']),10)
        assert outcome.ok, (outcome.code, service._engines[sid].platform_runtime.model_identity,
                            [(x['id'],x['model'],x['base_url']) for x in service.tenant_state()['profiles']])
        assert len(transport.requests)==1 and transport.pings==0
        req=transport.requests[0]
        content=next(m['content'] for m in req['messages'] if m['role']=='user')
        assert content[0]['type']=='text'
        assert content[1]['image_url']['url']=='data:image/png;base64,'+base64.b64encode(png()).decode()
        assert req['model']=='synthetic-vision' and not req['tools']
        log=await service.require_session(sid)
        public=json.dumps([e.payload for e in log.events_after(0)])
        assert 'synthetic-vision-secret' not in public and 'data:image/png' not in public
        assert any(e.type=='llm.usage' and e.payload['out_tokens']==4 for e in log.events_after(0))
        msgs=(await service.session_messages(sid))['messages']
        assert next(m for m in msgs if m['role']=='user')['attachments'][0]['artifact_id']==item['artifact_id']
    finally:await service.shutdown()


@pytest.mark.parametrize('raw,name',[(b'not an image','x.png'),(png()[:-8],'x.png'),(png(),'x.jpg')])
async def test_preview_rejects_damage_and_mislabelling_without_artifact(tmp_path,raw,name):
    app=DesktopApp(cli.assemble_ctx(isolated_settings(tmp_path)))
    try:
        sid=await app.service.create_session()
        with pytest.raises(PyHError,match='ATT-001'):await upload(app.service.platform,sid,raw,name)
        assert not (await app.service.platform.artifacts())['artifacts']
    finally:await app.service.shutdown()


async def test_vision_rechecks_ownership_hash_and_fallback(tmp_path):
    from pyharness.application.vision import authorized_images,require_connection
    app=DesktopApp(cli.assemble_ctx(isolated_settings(tmp_path)));p=app.service.platform
    try:
        sid=await app.service.create_session();other=await app.service.create_session()
        item=await upload(p,sid)
        with pytest.raises(PyHError,match='artifact_owner_mismatch'):
            await authorized_images(p,other,[item['artifact_id']])
        (p.root/'artifacts'/item['storage_path']).write_bytes(b'changed')
        with pytest.raises(PyHError,match='artifact_integrity_failed'):
            await authorized_images(p,sid,[item['artifact_id']])
        app.service.save_model_profile({'id':'vision','model':'synthetic','base_url':'https://synthetic.invalid',
            'supports_vision':True,'fallback_models':['unknown']})
        with pytest.raises(PyHError,match='vision_fallback_not_supported'):require_connection(p,'vision')
    finally:await app.service.shutdown()


async def test_vision_attaches_to_own_queued_turn_not_later_user_message(tmp_path):
    from types import SimpleNamespace
    from pyharness.application.vision import model_messages
    app=DesktopApp(cli.assemble_ctx(isolated_settings(tmp_path)));p=app.service.platform
    try:
        app.service.save_model_profile({'id':'vision','model':'synthetic','base_url':'https://synthetic.invalid',
            'supports_vision':True,'fallback_models':[]})
        sid=await app.service.create_session();item=await upload(p,sid)
        log=await app.service.require_session(sid)
        await log.append('user.message',{'content':'owned image question','meta':{'platform':{
            'task_id':'owned','use_vision':True,'vision_profile':'vision','vision_model':'synthetic',
            'artifact_ids':[item['artifact_id']]}}},actor='user')
        await log.append('user.message',{'content':'later queued text'},actor='user')
        runtime=SimpleNamespace(platform=p,definition=SimpleNamespace(model_connection='vision'),
            model_identity={'id':'vision','model':'synthetic','base_url':'https://synthetic.invalid','fallback_models':[]})
        result=await model_messages(runtime,log.derive_messages(),SimpleNamespace(session=log,task_id='owned'))
        assert result[0]['content'][0]['text']=='owned image question'
        assert result[1]['content']=='later queued text'
        assert log.derive_messages()[0]['content']=='owned image question'
        with pytest.raises(PyHError,match='vision_message_missing'):
            await model_messages(runtime,log.derive_messages()[1:],SimpleNamespace(session=log,task_id='owned'))
    finally:await app.service.shutdown()
