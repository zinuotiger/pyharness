"""New diagnostics exercise the original client and a real owned stdio process."""
from __future__ import annotations
import json
import asyncio
import sys
from types import SimpleNamespace
import pytest
from pyharness.application.platform_store import PlatformStore
from pyharness.application.mcp_workbench import McpWorkbench
from pyharness.errors import PyHError


def workbench(tmp_path, mode='ok'):
    trace = tmp_path / 'methods.jsonl'
    script = tmp_path / 'server.py'
    script.write_text('''import json,sys,time
from pathlib import Path
trace=Path(sys.argv[1]);mode=sys.argv[2]
for line in sys.stdin:
 r=json.loads(line)
 with trace.open('a') as f:f.write(json.dumps(r['method'])+'\\n')
 if mode=='timeout':time.sleep(30)
 if 'id' not in r:continue
 if r['method']=='initialize':
  result={'protocolVersion':'wrong' if mode=='wrong' else '2024-11-05','serverInfo':{'name':'fixture','version':'2.1'}}
  if mode=='legacy':result.pop('protocolVersion')
 else:
  result={'tools':[{'name':'echo','description':'Echo text','inputSchema':{'type':'object','properties':{'text':{'type':'string'}},'required':['text']}}]}
  if mode=='paged':result['nextCursor']='another'
  if mode=='bad-schema':result['tools'][0]['inputSchema']={'allOf':[{'type':'object'}]}
 print(json.dumps({'jsonrpc':'2.0','id':r['id'],'result':result}),flush=True)
''',encoding='utf-8')
    store = PlatformStore(tmp_path/'platform')
    cfg = {'name':'echo','command':[sys.executable,str(script),str(trace),mode],
           'enabled':True,'timeout_s':1,'allowed_tools':None}
    with store.transaction() as data:
        data['connections']['mcp:echo']={'type':'mcp','name':'echo','config':cfg}
    service=SimpleNamespace(tenant_id='default',effective_settings=lambda:SimpleNamespace(
        plugins=SimpleNamespace(mcp_servers=[])))
    return McpWorkbench(SimpleNamespace(store=store,service=service,locks={},actions={})),trace


def auth():return None


def test_preview_does_not_start_process_and_binds_source(tmp_path):
    bench,trace=workbench(tmp_path)
    view=bench.preview('managed','echo',authorize=auth)
    assert view['status']=='configuration_only' and not trace.exists()
    assert 'command' not in json.dumps(view) and view['config_sha256']
    with pytest.raises(PyHError):bench.preview('runtime','echo',authorize=auth)


@pytest.mark.asyncio
@pytest.mark.controlled_process
async def test_real_discovery_closed_and_argument_preview_zero_calls(tmp_path):
    bench,trace=workbench(tmp_path)
    view=bench.preview('managed','echo',authorize=auth)
    result=await bench.discover('managed','echo',{'command_key':'probe-1',
        'confirm_host_process':True,'config_sha256':view['config_sha256']},authorize=auth)
    assert result['status']=='completed' and result['carrier_closed'] is True
    assert result['protocol_status']=='verified' and result['server']['version']=='2.1'
    assert result['tools'][0]['name']=='mcp.echo.echo'
    before=trace.read_bytes()
    args=bench.arguments('probe-1','mcp.echo.echo',{'text':'private-value'},authorize=auth)
    assert args['valid'] is True and args['execution_authorized'] is False
    assert 'private-value' not in json.dumps(args)
    with pytest.raises(PyHError):bench.arguments('probe-1','mcp.echo.echo',{'text':12},authorize=auth)
    assert trace.read_bytes()==before
    repeated=await bench.discover('managed','echo',{'command_key':'probe-1',
        'confirm_host_process':True,'config_sha256':view['config_sha256']},authorize=auth)
    assert repeated==result and trace.read_bytes()==before
    assert [json.loads(x) for x in before.decode().splitlines()]==[
        'initialize','notifications/initialized','tools/list']


@pytest.mark.parametrize('mode',['wrong','timeout','paged','bad-schema'])
@pytest.mark.asyncio
@pytest.mark.controlled_process
async def test_bad_protocol_timeout_or_partial_list_never_ready(tmp_path,mode):
    bench,trace=workbench(tmp_path,mode)
    view=bench.preview('managed','echo',authorize=auth)
    result=await bench.discover('managed','echo',{'command_key':'probe-2',
        'confirm_host_process':True,'config_sha256':view['config_sha256']},authorize=auth)
    assert result['status']=='failed' and result['carrier_closed'] is True
    assert 'tools/call' not in trace.read_text()
    with pytest.raises(PyHError):bench.arguments('probe-2','mcp.echo.echo',{'text':'x'},authorize=auth)


@pytest.mark.asyncio
async def test_stale_confirmation_real_bool_authority_and_cold_unknown(tmp_path):
    bench,trace=workbench(tmp_path)
    view=bench.preview('managed','echo',authorize=auth)
    body={'command_key':'probe-3','confirm_host_process':1,'config_sha256':view['config_sha256']}
    with pytest.raises(PyHError):await bench.discover('managed','echo',body,authorize=auth)
    body['confirm_host_process']=True
    with bench.platform.store.transaction() as data:
        data['connections']['mcp:echo']['config']['command'].append('changed')
    with pytest.raises(PyHError):await bench.discover('managed','echo',body,authorize=auth)
    assert not trace.exists()
    with bench.platform.store.transaction() as data:
        data['preferences']['mcp_diagnostics']={'probe-cold':{
            'command_key':'probe-cold','source':'managed','name':'echo',
            'config_sha256':view['config_sha256'],'status':'requested'}}
    assert bench.result('probe-cold',authorize=auth)['status']=='unconfirmed'
    def denied():raise PermissionError('revoked')
    with pytest.raises(PermissionError):bench.preview('managed','echo',authorize=denied)


@pytest.mark.asyncio
@pytest.mark.controlled_process
async def test_legacy_negotiation_honest_and_runtime_source_distinct(tmp_path):
    from pyharness.config import McpServerCfg
    bench,trace=workbench(tmp_path,'legacy')
    cfg=bench.platform.store.read()['connections']['mcp:echo']['config']
    bench.platform.service.effective_settings=lambda:SimpleNamespace(plugins=SimpleNamespace(
        mcp_servers=[McpServerCfg.model_validate(cfg)]))
    managed=bench.preview('managed','echo',authorize=auth)
    runtime=bench.preview('runtime','echo',authorize=auth)
    assert managed['config_sha256']!=runtime['config_sha256']
    row=await bench.discover('managed','echo',{'command_key':'legacy-1',
        'confirm_host_process':True,'config_sha256':managed['config_sha256']},authorize=auth)
    assert row['status']=='completed' and row['protocol_status']=='legacy_unverified'
    assert row['protocol_version'] is None and row['carrier_closed'] is True


async def started(trace):
    async with asyncio.timeout(5):
        while not trace.exists():await asyncio.sleep(.01)


@pytest.mark.asyncio
@pytest.mark.controlled_process
async def test_current_configuration_change_and_authority_revocation_after_await(tmp_path):
    bench,trace=workbench(tmp_path,'timeout')
    permitted=True
    def current():
        if not permitted:raise PermissionError('revoked')
    view=bench.preview('managed','echo',authorize=current)
    body={'command_key':'revoked-1','confirm_host_process':True,'config_sha256':view['config_sha256']}
    task=asyncio.create_task(bench.discover('managed','echo',body,authorize=current))
    await started(trace)
    permitted=False
    with pytest.raises(PermissionError):await task
    row=bench.result('revoked-1',authorize=auth)
    assert row['status']=='failed' and row['carrier_closed'] is True
    assert not bench.platform.actions
    with bench.platform.store.transaction() as data:
        data['connections']['mcp:echo']['config']['timeout_s']=2
    assert bench.result('revoked-1',authorize=auth)['stale'] is True
    before=trace.read_bytes()
    assert (await bench.discover('managed','echo',body,authorize=auth))['stale'] is True
    assert trace.read_bytes()==before


@pytest.mark.asyncio
@pytest.mark.controlled_process
async def test_cancelled_discovery_never_automatically_replays(tmp_path):
    bench,trace=workbench(tmp_path,'timeout')
    view=bench.preview('managed','echo',authorize=auth)
    body={'command_key':'cancel-1','confirm_host_process':True,'config_sha256':view['config_sha256']}
    task=asyncio.create_task(bench.discover('managed','echo',body,authorize=auth))
    await started(trace);task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    row=bench.result('cancel-1',authorize=auth)
    assert row['status']=='unconfirmed' and row['carrier_closed'] is True
    before=trace.read_bytes()
    assert (await bench.discover('managed','echo',body,authorize=auth))==row
    assert trace.read_bytes()==before and not bench.platform.actions
