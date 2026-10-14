"""Bounded generated invariants; all files belong to the external pytest sandbox."""
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from hypothesis import example, given, settings, strategies as st
from pyharness import persistence as p
from pyharness.bus import EventBus
from pyharness.desktop.sessions import DesktopSessionManager
from pyharness.core.tenant_settings import TenantSettingsStore, normalize_tenant_id
from pyharness.errors import PyHError
from tests.unit.test_remediation_storage import SID, event, FaultFile
from tests.unit.test_remediation_credentials import body

bounded = settings(max_examples=15, deadline=None, database=None)
text = st.text(st.characters(blacklist_categories=('Cs',)), min_size=1,max_size=24).filter(lambda x: bool(x.strip()))

@bounded
@given(st.lists(st.tuples(text,text),min_size=1,max_size=6))
async def test_property_restart_preserves_independent_messages(tmp_path_factory, pairs):
    directory=tmp_path_factory.mktemp('unicode recovery 中文')
    first=DesktopSessionManager(dir=directory,bus=EventBus())
    sid=await first.create(); log=await first.open_session(sid)
    expected=[]
    try:
        for user,assistant in pairs:
            await log.append('user.message',{'content':user},actor='user',sync=True)
            await log.append('llm.response',{'model':'synthetic','finish_reason':'stop','content':assistant},actor='llm',sync=True)
            expected.extend([{'role':'user','content':user},{'role':'assistant','content':assistant}])
    finally: await first.shutdown_all()
    second=DesktopSessionManager(dir=directory,bus=EventBus())
    try:
        restored=await second.open_session(sid)
        assert restored.derive_messages()==expected
        events=list(restored.events_after(0))
        assert [e.seq for e in events]==list(range(1,len(events)+1))
    finally: await second.shutdown_all()

@bounded
@given(st.integers(3,8),st.integers(0,30),st.sampled_from(['write','partial','flush-after']))
async def test_property_partial_retry_exact_suffix(tmp_path_factory,count,index,mode):
    directory=tmp_path_factory.mktemp('partial')
    store=p.open_store(SID,dir=directory);real=store._fh
    store._fh=FaultFile(real,index%count,mode)
    try:
        for seq in range(1,count+1):await store.append(event(seq))
        with pytest.raises(PyHError):await store.flush()
        await store.flush();before=store.path.read_bytes();await store.flush()
        assert store.path.read_bytes()==before
        rows=[]
        for line in before.splitlines():
            try:rows.append(json.loads(line))
            except ValueError:pass
        assert [r['seq'] for r in rows]==list(range(1,count+1))
    finally:store._fh=real;store.close()

@bounded
@given(st.integers(1,4),st.lists(st.integers(0,8),min_size=1,max_size=15))
def test_property_close_owner_once(tmp_path_factory,count,sequence):
    directory=tmp_path_factory.mktemp('owners')
    baseline={k:v[1] for k,v in p._LOCKS.items()}
    stores=[p.open_store(SID,dir=directory) for _ in range(count)]
    key=str(stores[0]._lock_path);live=set(range(count))
    try:
        for index in sequence:
            index%=count;stores[index].close();live.discard(index)
            assert (p._LOCKS.get(key,[None,0])[1])==len(live)
            assert all(not stores[i]._fh.closed for i in live)
    finally:
        for store in stores:store.close()
    assert {k:v[1] for k,v in p._LOCKS.items()}==baseline

@bounded
@given(st.lists(st.sampled_from(['keep','direct','ref','clear']),min_size=1,max_size=10),text)
def test_property_secret_four_states(tmp_path_factory, operations, label):
    directory=tmp_path_factory.mktemp('secret states')
    store=TenantSettingsStore(directory);expected=None
    os.environ['SYNTHETIC_PROPERTY']='REF-synthetic'
    try:
        for index,operation in enumerate(operations):
            extra={}
            if operation=='direct':expected='value-'+str(index);extra={'api_key':expected}
            elif operation=='ref':expected='REF-synthetic';extra={'api_key_ref':'env:SYNTHETIC_PROPERTY'}
            elif operation=='clear':expected=None;extra={'clear_api_key':True}
            store.upsert_profile('default',body(name=label,**extra))
            if expected is None:
                with pytest.raises(PyHError):store.resolve_ref('tenant:default:test')
            else:assert store.resolve_ref('tenant:default:test')==expected
    finally:os.environ.pop('SYNTHETIC_PROPERTY',None)

@bounded
@given(st.text(max_size=40))
def test_property_tenant_id_cannot_escape(tmp_path_factory,value):
    store=TenantSettingsStore(tmp_path_factory.mktemp('ids'))
    try:tenant=normalize_tenant_id(value)
    except PyHError:return
    assert store.tenant_dir(tenant).resolve().is_relative_to(store.root.resolve())

PROBE="""import sys,json
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from pyharness.persistence import open_store
from pyharness.errors import PyHError
try:s=open_store(sys.argv[3],dir=Path(sys.argv[2]))
except PyHError as e:
 print(json.dumps({'code':e.code,'op':e.ctx.get('op')}));raise SystemExit(23)
else:s.close();print(json.dumps({'opened':True}))
"""

@pytest.mark.controlled_process
async def test_windows_real_lock_survives_duplicate_close(tmp_path):
    directory=tmp_path/'Windows 中文 空格';directory.mkdir()
    def probe():
        return subprocess.run([sys.executable,'-I','-B','-X','utf8','-c',PROBE,str(Path(p.__file__).resolve().parents[1]),str(directory),SID],cwd=directory,capture_output=True,text=True,encoding='utf-8',timeout=10)
    a=p.open_store(SID,dir=directory);b=p.open_store(SID,dir=directory)
    try:
        a.close();a.close()
        blocked=probe();assert blocked.returncode==23,blocked.stderr
        assert json.loads(blocked.stdout)=={'code':'PERS-202','op':'lock'}
        await b.append(event(1),sync=True)
        assert [e.seq for e in b.replay()]==[1]
        b.close();opened=probe();assert opened.returncode==0,opened.stderr
        assert json.loads(opened.stdout)=={'opened':True}
    finally:a.close();b.close()

@pytest.mark.parametrize('stage',['created-before','created-after','replay','subscribe'])
@pytest.mark.parametrize('cleanup_error',[False,True])
async def test_half_initialization_preserves_other_owner(tmp_path,monkeypatch,stage,cleanup_error):
    from pyharness.core.session import SessionLog
    bus=EventBus();keeper=DesktopSessionManager(dir=tmp_path,bus=bus)
    candidate=DesktopSessionManager(dir=tmp_path,bus=bus)
    sid=await keeper.create();kept=await keeper.open_session(sid)
    baseline={k:v[1] for k,v in p._LOCKS.items()}
    subs=lambda:{id(s) for values in bus._by_type.values() for s in values}
    before=subs();opened=[];failure=RuntimeError('initialization fault')
    try:
        with monkeypatch.context() as patch:
            original=candidate._open_store
            async def capture(sid):
                store=await original(sid);opened.append(store)
                if stage=='replay':
                    replay=store.replay
                    def fail():
                        yield next(iter(replay()))
                        raise failure
                    patch.setattr(store,'replay',fail)
                if cleanup_error:
                    close=store.close
                    def failclose():close();raise OSError('secondary cleanup')
                    patch.setattr(store,'close',failclose)
                return store
            patch.setattr(candidate,'_open_store',capture)
            append=SessionLog.append
            async def failappend(log,type_,payload,**kw):
                target=type_=='session.created' and opened and log.sid==opened[-1].session_id
                if target and stage=='created-before':raise failure
                result=await append(log,type_,payload,**kw)
                if target and stage=='created-after':raise failure
                return result
            patch.setattr(SessionLog,'append',failappend)
            if stage=='subscribe':
                subscribe=bus.subscribe;calls=[]
                def failsub(*a,**kw):
                    result=subscribe(*a,**kw);calls.append(True)
                    if len(calls)==2:raise failure
                    return result
                patch.setattr(bus,'subscribe',failsub)
            with pytest.raises(RuntimeError) as raised:
                async with asyncio.timeout(5):
                    if stage=='replay':await candidate.open_session(sid)
                    else:await candidate.create()
            assert raised.value is failure
            assert not candidate._logs and not candidate._stores and not candidate._owners
            assert {k:v[1] for k,v in p._LOCKS.items()}==baseline
            assert subs()==before
            assert opened[-1]._fh.closed and opened[-1]._lock_path is None
            if cleanup_error:assert failure.__notes__
        await kept.append('user.message',{'content':'keeper remains writable'},actor='user',sync=True)
        assert list(keeper._stores[sid].replay())[-1].payload['content']=='keeper remains writable'
    finally:await candidate.shutdown_all();await keeper.shutdown_all()


@pytest.mark.parametrize('value',['../outside','a/b','a\\b','.', '..','名字','x'*65,'C:drive'])
def test_tenant_identifier_grammar_is_rejected_before_path_use(value):
    with pytest.raises(PyHError):normalize_tenant_id(value)

@bounded
@given(text)
@example('0\x85')
@example('\U00010000')
def test_property_unicode_settings_serialization(tmp_path_factory,value):
    import yaml
    from pyharness.config import Settings
    from tests.support import isolated_settings
    directory=tmp_path_factory.mktemp('unicode config')
    cfg=isolated_settings(directory)
    cfg.llm.model=value
    path=directory/'配置 空格.yaml'
    # YAML escapes preserve both line separators and non-BMP scalar values;
    # JSON surrogate-pair escapes do not round-trip through this YAML reader.
    path.write_text(yaml.safe_dump(cfg.model_dump(mode='json'),allow_unicode=False),encoding='utf-8',newline='\r\n')
    assert Settings.model_validate_json(cfg.model_dump_json()).model_dump()==cfg.model_dump()
    restored=Settings.model_validate(yaml.safe_load(path.read_text(encoding='utf-8')))
    assert restored.model_dump()==cfg.model_dump()
