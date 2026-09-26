"""Real Docker only. All files, processes and sentinels are test-created.

Opt in with --real-docker. Missing daemon/image is a failure, never a skip.
The controlled image is pulled by CI, never by application/runtime code.
"""
import asyncio
import hashlib
import http.server
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace

import httpx
import pytest
from pyharness import cli
from pyharness.application.platform_models import SandboxProfile
from pyharness.application.platform_service import TEMPLATES
from pyharness.core.sandbox import SandboxManager, HostApprovedSandboxBackend
from pyharness.desktop.app import DesktopApp
from pyharness.errors import PyHError
from tests.support import isolated_settings

IMAGE='python:3.13.2-slim-bookworm'
pytestmark=pytest.mark.controlled_process

def record(request, **values):
    config=request.config
    if not hasattr(config,'docker_evidence'): config.docker_evidence=[]
    config.docker_evidence.append({'test':request.node.name,**values})

def profile(**kw):
    return SandboxProfile(mode='isolated',image=IMAGE,timeout=10,**kw)

async def inspect(backend,ident):
    result=await backend._command(['inspect',ident])
    assert result['exit_code']==0,result['output']
    return json.loads(result['output'])[0]

@pytest.fixture
async def manager(tmp_path,monkeypatch):
    assert os.name=='posix','Real Docker acceptance is for the Ubuntu runner'
    calls=[]
    async def forbidden(*args,**kwargs):
        calls.append(True)
        raise AssertionError('isolated must never execute on the host')
    for method in ('create','execute'):
        monkeypatch.setattr(HostApprovedSandboxBackend,method,forbidden)
    m=SandboxManager(tmp_path/'sandboxes')
    backend=m.backends['isolated']
    assert (await backend.health())['status']=='available','Docker daemon required'
    image=await backend._command(['image','inspect',IMAGE])
    assert image['exit_code']==0,'CI must provision the fixed image before testing'
    try: yield m
    finally:
        await m.close()
        left=await backend._command(['ps','-aq','--filter','label=pyharness.owner='+backend.owner_label])
        assert left['exit_code']==0 and not left['output'].strip(),'owned container residue'
        assert not calls
        assert not m.root.exists() or not list(m.root.iterdir()),'workspace residue'

async def test_real_identity_mounts_and_resource_policy(manager,request):
    p=profile(cpus=.5,memory_mb=128,pids_limit=32)
    r=await manager.create('identity',p)
    b=manager.backends['isolated'];info=await inspect(b,r.sandbox_id)
    host=info['HostConfig']
    assert host['Privileged'] is False and host['ReadonlyRootfs'] is True
    assert host['NetworkMode']=='none'
    assert host['NanoCpus']==500_000_000
    assert host['Memory']==128*1048576 and host['MemorySwap']==host['Memory']
    assert host['PidsLimit']==32
    assert 'ALL' in host['CapDrop'] and 'no-new-privileges' in host['SecurityOpt']
    assert [(x['Destination'],x['Type']) for x in info['Mounts']]==[('/workspace','bind')]
    result=await manager.execute(r.sandbox_id,"import os,pathlib,json; print(json.dumps({'uid':os.getuid(),'socket':pathlib.Path('/var/run/docker.sock').exists(),'env':dict(os.environ)}))",python=True)
    data=json.loads(result['output'])
    assert data['uid']!=0 and not data['socket']
    assert info['Config']['User'].split(':')[0]==str(data['uid'])
    write=await manager.execute(r.sandbox_id,"from pathlib import Path; Path('/workspace/writable').write_text('yes'); Path('/tmp/writable').write_text('yes');\ntry: Path('/forbidden').write_text('no')\nexcept OSError: print('root-readonly')\nelse: raise AssertionError('root writable')",python=True)
    assert write['exit_code']==0 and 'root-readonly' in write['output']
    version=await b._command(['version','--format','{{.Server.Version}}'])
    record(request,daemon_version=version['output'].strip(),image=IMAGE,image_id=info['Image'],non_root=True,
           privileged=False,root_readonly=True,network='none',cpus=.5,memory_mb=128,pids=32,mounts=['/workspace'],socket=False)

async def test_real_private_workspaces_and_synthetic_secrets(manager,tmp_path,monkeypatch,request):
    secret='synthetic-secret-'+os.urandom(8).hex()
    sentinel=tmp_path/'outside-sentinel';sentinel.write_text(secret)
    home_secret=Path.home()/'synthetic-secret';home_secret.write_text(secret)
    monkeypatch.setenv('PYHARNESS_NONALLOWLIST_SECRET',secret)
    source=tmp_path/'source';source.mkdir();(source/'input.txt').write_text('input');(source/'.env').write_text(secret)
    a=await manager.create('a',profile(),source);b=await manager.create('b',profile(),source)
    assert a.sandbox_id!=b.sandbox_id and manager.workspace(a.sandbox_id)!=manager.workspace(b.sandbox_id)
    await manager.file_operation(a.sandbox_id,'fs.write_file',{'path':'only-a','content':'a'})
    probe="import os,json; from pathlib import Path; print(json.dumps({'outside':Path(%r).exists(),'home':Path(%r).exists(),'other':Path(%r).exists(),'env':os.getenv('PYHARNESS_NONALLOWLIST_SECRET'),'dotenv':Path('/workspace/.env').exists(),'otherfile':Path('/workspace/only-a').exists()}))" % (str(sentinel),str(home_secret),str(manager.workspace(a.sandbox_id)))
    result=await manager.execute(b.sandbox_id,probe,python=True)
    assert result['exit_code']==0 and not any(json.loads(result['output']).values())
    assert secret not in result['output']
    assert (source/'input.txt').read_text()=='input' and not (source/'only-a').exists()
    record(request,private_containers=True,private_workspaces=True,host_sentinel_hidden=True,home_secret_hidden=True,environment_filtered=True)

@pytest.mark.parametrize('path',['../outside','/etc/passwd','link/secret'])
async def test_real_broker_rejects_escape(manager,path,request):
    r=await manager.create('paths',profile())
    result=await manager.execute(r.sandbox_id,"from pathlib import Path; Path('/tmp/secret').write_text('synthetic'); Path('/workspace/link').symlink_to('/tmp',target_is_directory=True)",python=True)
    assert result['exit_code']==0
    with pytest.raises(PyHError): await manager.file_operation(r.sandbox_id,'fs.read_file',{'path':path})
    record(request,path_boundary='rejected')

async def test_real_network_denies_controlled_host_probe(manager,request):
    class Probe(http.server.BaseHTTPRequestHandler):
        def do_GET(self):self.send_response(200);self.end_headers();self.wfile.write(b'controlled')
        def log_message(self,*args):pass
    server=http.server.ThreadingHTTPServer(('0.0.0.0',0),Probe)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    try:
        port=server.server_port
        async with httpx.AsyncClient(trust_env=False) as client:
            assert (await client.get(f'http://127.0.0.1:{port}')).text=='controlled'
        backend=manager.backends['isolated']
        bridge=await backend._command(['network','inspect','bridge','--format','{{(index .IPAM.Config 0).Gateway}}'])
        assert bridge['exit_code']==0
        gateway=bridge['output'].strip()
        r=await manager.create('network',profile())
        code="import socket; s=socket.socket(); s.settimeout(2);\ntry: s.connect((%r,%d))\nexcept OSError: print('isolated')\nelse: raise AssertionError('network escaped')\nfinally: s.close()"%(gateway,port)
        result=await manager.execute(r.sandbox_id,code,python=True)
        assert result['exit_code']==0 and result['output'].strip()=='isolated'
        record(request,host_probe_reachable=True,container_probe_blocked=True,public_internet_used=False)
    finally:
        server.shutdown();server.server_close();worker.join(timeout=3)
        assert not worker.is_alive()

async def test_real_wallclock_timeout_and_cleanup(manager,request):
    p=profile();p.timeout=1
    r=await manager.create('timeout',p)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(manager.execute(r.sandbox_id,'import time; time.sleep(60)',python=True),10)
    result=await manager.backends['isolated']._command(['inspect',r.sandbox_id])
    assert result['exit_code']!=0
    await manager.destroy(r.sandbox_id)
    assert r.status=='destroyed' and not (manager.root/r.sandbox_id).exists()
    record(request,wallclock_timeout=True,container_destroyed=True,workspace_cleaned=True)

async def test_real_output_is_bounded(manager,request):
    r=await manager.create('output',profile(output_limit=1024))
    result=await manager.execute(r.sandbox_id,"print('x'*20000)",python=True)
    assert result['exit_code']==0 and result['truncated'] and len(result['output'].encode())<=1024
    record(request,output_limit_bytes=1024,truncated=True)

async def test_real_cancel_reaps_parent_children_and_container(manager,request):
    r=await manager.create('cancel',profile())
    command="python -c \"import subprocess,sys,time; from pathlib import Path; subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); Path('ready').write_text('ready'); time.sleep(60)\""
    process=await manager.start_process(r.sandbox_id,command)
    async with asyncio.timeout(8):
        while not (manager.workspace(r.sandbox_id)/'ready').exists(): await asyncio.sleep(.05)
    top=await manager.backends['isolated']._command(['top',r.sandbox_id,'-eo','pid'])
    assert top['exit_code']==0
    pids=[int(x.strip()) for x in top['output'].splitlines()[1:] if x.strip().isdigit()]
    assert len(pids)>=3
    def identity(pid):
        try:return Path(f'/proc/{pid}/stat').read_text().split(') ',1)[1].split()[19]
        except FileNotFoundError:return None
    before={pid:identity(pid) for pid in pids}
    await asyncio.wait_for(manager.kill_process(r.sandbox_id,process['pid']),15)
    assert all(identity(pid)!=start for pid,start in before.items() if start is not None)
    assert r.status=='destroyed' and not (manager.root/r.sandbox_id).exists()
    record(request,parent_and_children_reaped=True,container_destroyed=True)

async def test_real_create_failure_never_falls_back(manager,request):
    p=profile();p.image='pyharness-acceptance-missing-image:never-pull'
    with pytest.raises(PyHError):await manager.create('failure',p)
    assert not manager.records and not list(manager.root.iterdir())
    record(request,create_failed_closed=True,host_fallback=False)

@pytest.mark.parametrize('test_exit,background',[(0,False),(1,False),(1,True)])
async def test_real_governed_code_change_and_restart(manager,tmp_path,adapter_factory,test_exit,background,request):
    cfg=isolated_settings(tmp_path/'app')
    fixed='def add(a, b):\n    return a + b\n'
    test_call={'id':'test','name':'proc.start','args':{'command':'python -c "raise SystemExit(1)"'}} if background else {'id':'test','name':'exec.python_run','args':{'code':f'from add import add; assert add(2,3)==5; raise SystemExit({test_exit})'}}
    adapter_factory(cfg,[{'id':'write','name':'fs.write_file','args':{'path':'add.py','content':fixed}},test_call])
    app=DesktopApp(cli.assemble_ctx(cfg));p=app.service.platform;p.sandboxes=manager
    approvals=asyncio.Queue()
    async def observe(kind,event):approvals.put_nowait((event.session_id,event.seq))
    app.ctx.bus.subscribe('approval.requested',observe,owner='docker-acceptance')
    sid=await app.service.create_session();root=Path(app.service._session_workspace(sid));root.mkdir(parents=True,exist_ok=True)
    original=(Path(__file__).resolve().parents[2]/'examples/platform-code-change/add.py').read_text()
    (root/'add.py').write_text(original)
    draft=p.save_agent({**TEMPLATES[1],'sandbox_profile':profile().model_dump(),'allowed_tools':[*TEMPLATES[1]['allowed_tools'],'proc.start']});p.publish_agent(draft['agent_id'])
    async def approve():
        approved_sid,aid=await asyncio.wait_for(approvals.get(),20)
        assert approved_sid==sid
        await app.service.decide_approval(aid,'approve',sid=sid)
    try:
        run=await p.start_run({'session_id':sid,'agent_id':draft['agent_id'],'agent_version':1,'text':'Fix the sample'})
        assert (root/'add.py').read_text()==original
        await approve();await approve()
        await asyncio.wait_for(app.service._queues[sid].wait_for(run['task_id']),30)
        assert (root/'add.py').read_text()==original
        assert all(r.status=='destroyed' for r in manager.records.values())
        artifacts=(await p.artifacts())['artifacts']
        patch=next(a for a in artifacts if a['filename']=='changes.patch')
        test_report=next(a for a in artifacts if a['filename']=='test-report.json')
        _,test_bytes=await p.artifact_content(test_report['artifact_id'],preview=True)
        command_report=json.loads(test_bytes)
        assert (command_report[0]['exit_code']==test_exit if not background else command_report[0]['exit_code']!=0),command_report
        assert patch['validation_status']==('failed' if test_exit else 'passed')
        _,raw=await p.artifact_content(patch['artifact_id'],preview=True)
        assert hashlib.sha256(raw).hexdigest()==patch['sha256']
        with pytest.raises(PyHError):await p.artifact_content(patch['artifact_id'])
        if test_exit:
            with pytest.raises(PyHError,match='patch_validation_failed'):
                await p.request_artifact_action(patch['artifact_id'],'apply')
            assert (root/'add.py').read_text()==original
        else:
            op=await p.request_artifact_action(patch['artifact_id'],'apply')
            assert (root/'add.py').read_text()==original
            await approve();assert (await asyncio.wait_for(p.actions[op['operation_id']],10))['ok']
            assert (root/'add.py').read_text()==fixed
            op=await p.request_artifact_action(patch['artifact_id'],'export')
            await approve();assert (await asyncio.wait_for(p.actions[op['operation_id']],10))['ok']
            assert (await p.artifact_content(patch['artifact_id']))[1]==raw
        view=await p.run(run['run_id']);public=json.dumps(view['trace'])
        assert {r.sandbox_id for r in manager.records.values()} <= {s['sandbox_id'] for s in view['trace'] if s.get('sandbox_id')}
        for term in ('sandbox_id','cpus','memory_mb','pids_limit','exit_code','destroyed','command_summary'):
            assert term in public
        assert 'chain_of_thought' not in public and 'reasoning_content' not in public
    finally:await app.service.shutdown()
    reopened=DesktopApp(cli.assemble_ctx(cfg))
    try:
        restored=await reopened.service.platform.run(run['run_id'])
        assert restored['status']==view['status'] and restored['trace']
        assert (await reopened.service.platform.artifact_content(patch['artifact_id'],preview=True))[1]==raw
        assert len((await reopened.service.platform.approvals())['approvals'])==(4 if test_exit==0 else 2)
    finally:await reopened.service.shutdown()
    record(request,real_container=True,real_queue_governance_approval=True,model='deterministic',test_exit=test_exit,
           background=background,validation_status=patch['validation_status'],recorded_exit_codes=[c['exit_code'] for c in command_report],
           unapproved_source_unchanged=True,failed_test_blocks_patch=bool(test_exit),artifact_sha_verified=True,restart_replay=True,host_fallback=False)
