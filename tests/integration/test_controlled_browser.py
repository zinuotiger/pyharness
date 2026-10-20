"""Actual temporary Chrome DOM/stdio/Job checks. No paid model/site/login."""
import asyncio
import json
import os
from pathlib import Path
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
import subprocess
from pyharness.core.browser_mcp import BrowserCarrier
from pyharness.core.mcp import McpClient, StdioTransport

pytestmark=[pytest.mark.controlled_process,pytest.mark.skipif(os.name!='nt',reason='Windows Job browser carrier only')]
from tests.browser_dependencies import browser_dependencies
NODE,PW,CHROME=browser_dependencies() if os.name=='nt' else (None,None,None)


@pytest.fixture
def local_page():
    events=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            if self.path=='/slow':
                events.append('GET /slow')
                time.sleep(8)
            if self.path.startswith('/submit'):
                events.append(self.path)
                body=b'<body>ACCEPTED exact-target</body>'
            elif self.path=='/changing':
                body=b'<body><button id="send">Send</button><script>setTimeout(()=>document.querySelector("button").textContent="Changed",700)</script></body>'
            elif self.path in ('/unicode','/surrogate'):
                label='X'+'\U0001f600'*40 if self.path=='/unicode' else 'X\ud800Y'
                body=('<body><button onclick="location.href=\'/submit?target=correct\'">placeholder</button>'+
                    '<script>document.querySelector("button").textContent='+json.dumps(label)+';</script></body>').encode('utf-8')
            elif self.path=='/many':
                body=('<body>'+''.join('<button style="display:block;height:40px" onclick="location.href=\'/submit?target=many-'+str(i)+'\'">Many '+str(i)+'</button>' for i in range(1005))+'</body>').encode('utf-8')
            elif self.path.startswith('/network'):
                from urllib.parse import urlsplit,parse_qs
                target=parse_qs(urlsplit(self.path).query)['target'][0]
                body=('<body>Network boundary<script>fetch('+json.dumps(target)+').catch(()=>{});'+
                    'new WebSocket("ws://127.0.0.1:"+location.port+"/socket");</script></body>').encode()
            elif self.path=='/socket':
                events.append('WEBSOCKET /socket');body=b'not permitted'
            else:
                body=b'<body><h1>Local browser acceptance</h1><button onclick="location.href=\'/submit?target=correct\'">Send exact</button><button onclick="location.href=\'/submit?target=wrong\'">Wrong</button><input name="note"><p>Untrusted page text</p></body>'
            try:
                self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers();self.wfile.write(body)
            except (BrokenPipeError,ConnectionResetError):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    origin='http://127.0.0.1:'+str(server.server_port)
    yield origin,events
    server.shutdown();server.server_close();thread.join(3)


def carrier(origin,timeout=10):
    return BrowserCarrier(node=str(NODE),playwright=str(PW),executable=str(CHROME),origins=[origin],timeout=timeout)


def action(observation,index=0,**extra):
    row=observation['targets'][index]
    return dict(session=observation['session'],generation=observation['generation'],
        target=row['target'],label=row['label'],action='click')|extra


def identities(pid):
    rows=process_rows();parents={pid};selected=[]
    while True:
        added=[row for row in rows if row['ParentProcessId'] in parents and row['ProcessId'] not in parents]
        if not added:return selected
        parents.update(row['ProcessId'] for row in added)
        selected.extend((row['ProcessId'],row['CreationDate']) for row in added
            if row['Name'].lower() in {'python.exe','node.exe','chrome.exe','chrome_crashpad_handler.exe','msedge.exe','msedge_crashpad_handler.exe'})


def process_rows():
    command='Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,CreationDate,Name | ConvertTo-Json -Compress'
    return json.loads(subprocess.check_output(['powershell.exe','-NoProfile','-NonInteractive','-Command',command],
        creationflags=0x08000000,timeout=20).decode('utf-8'))


def assert_gone(rows):
    for _ in range(5):
        current={(row['ProcessId'],row['CreationDate']) for row in process_rows()}
        alive=[pid for pid,birth in rows if (pid,birth) in current]
        if not alive:return
        time.sleep(.2)
    assert not alive,alive


def test_exact_target_readback_consumption_origin_refusal_and_cleanup(local_page):
    origin,events=local_page;c=carrier(origin);children=[]
    try:
        denied=c.call('open',{'url':'file:///C:/private.txt'})
        assert denied['error']=='browser_origin_denied' and not events
        observed=c.call('open',{'url':origin})
        assert observed['untrusted'] and observed['targets'][0]['label']=='Send exact',observed
        children=identities(c.owner.proc.pid);assert len(children)>=3
        forged=action(observed);forged['label']='Wrong approval label'
        assert c.call('act',forged)['error']=='browser_target_stale' and not events
        result=c.call('act',action(observed))
        assert result['acted']=='click' and 'ACCEPTED exact-target' in result['readback']['text']
        assert events==['/submit?target=correct']
        assert c.call('act',action(observed))['error']=='browser_target_stale'
        assert c.call('close',{})=={'closed':True}
        with pytest.raises(ValueError,match='closed'):c.call('observe',{})
    finally:c.close()
    assert_gone(children)


def test_two_real_browsers_never_accept_other_session_target(local_page):
    origin,events=local_page;a=carrier(origin);b=carrier(origin)
    try:
        left=a.call('open',{'url':origin});right=b.call('open',{'url':origin})
        assert left['session']!=right['session']
        assert b.call('act',action(left))['error']=='browser_target_stale' and not events
        result=b.call('act',action(right,2,action='fill',text='local input'))
        assert result['acted']=='fill' and not events
    finally:a.close();b.close()


def test_dom_change_invalidates_observation_without_action(local_page):
    origin,events=local_page;c=carrier(origin)
    try:
        old=c.call('open',{'url':origin+'/changing'});time.sleep(.9)
        assert c.call('act',action(old))['error']=='browser_target_stale' and not events
        assert c.call('observe',{})['targets'][0]['label']=='Changed'
    finally:c.close()


def test_real_browser_network_requests_cannot_escape_origin_or_open_websocket(local_page):
    origin,events=local_page;seen=[]
    class Forbidden(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            seen.append(self.path);self.send_response(200);self.end_headers()
    other=ThreadingHTTPServer(('127.0.0.1',0),Forbidden);other.daemon_threads=True
    thread=threading.Thread(target=other.serve_forever,daemon=True);thread.start()
    from urllib.parse import quote
    c=carrier(origin)
    try:
        forbidden='http://127.0.0.1:'+str(other.server_port)+'/private'
        result=c.call('open',{'url':origin+'/network?target='+quote(forbidden,safe='')})
        assert result['untrusted'],result
        time.sleep(.15)
        assert seen==[] and events==[]
    finally:
        c.close();other.shutdown();other.server_close();thread.join(3)


def test_timeout_retires_carrier_and_owned_tree(local_page):
    origin,_=local_page;c=carrier(origin,1)
    try:
        result=c.call('open',{'url':origin+'/slow'})
        assert result['fatal'] and result['error']=='browser_carrier_unconfirmed'
        assert c.closed and c.owner.poll() is not None
        with pytest.raises(ValueError,match='closed'):c.call('observe',{})
    finally:c.close()


async def test_real_stdio_discovery_open_and_eof_cleanup(local_page):
    origin,events=local_page
    script=Path(__file__).parents[2]/'pyharness/core/browser_mcp.py'
    tr=StdioTransport([*worker_python(),str(script),'--node',str(NODE),
        '--playwright',str(PW),'--executable',str(CHROME),'--origin',origin],timeout_s=15)
    client=McpClient('controlled_browser',tr);children=[]
    try:
        assert (await client.connect())['tools']==4
        assert not identities(tr._proc.pid),'discovery unexpectedly spawned Node/Chrome'
        result=await client.call_tool('open',{'url':origin})
        assert not result['isError'] and json.loads(result['content'][0]['text'])['targets']
        children=identities(tr._proc.pid);assert children and not events
    finally:await client.close()
    assert_gone(children)


async def test_owned_mcp_parent_hard_exit_still_kills_its_browser_job(local_page):
    origin,_=local_page;script=Path(__file__).parents[2]/'pyharness/core/browser_mcp.py'
    tr=StdioTransport([*worker_python(),str(script),'--node',str(NODE),
        '--playwright',str(PW),'--executable',str(CHROME),'--origin',origin],timeout_s=15)
    client=McpClient('controlled_browser',tr);children=[]
    try:
        await client.connect();await client.call_tool('open',{'url':origin})
        children=identities(tr._proc.pid);assert len(children)>=3
        tr._proc.kill();await tr._proc.wait()
        assert_gone(children)
    finally:await client.close()


@pytest.mark.parametrize('cancel',[False,True])
async def test_actual_transport_timeout_or_cancel_discards_carrier_without_restart(local_page,cancel):
    from pyharness.application.browser_setup import browser_configuration
    from pyharness.errors import PyHError
    origin,events=local_page
    config=browser_configuration(dict(node=str(NODE),playwright=str(PW),executable=str(CHROME),origins=[origin]))['connection']['config']
    tr=StdioTransport(config['command'],timeout_s=4);client=McpClient('owned_browser',tr)
    children=[]
    try:
        await client.connect()
        pending=asyncio.create_task(client.call_tool('open',{'url':origin+'/slow'}))
        async with asyncio.timeout(6):
            while not events and not pending.done():await asyncio.sleep(.01)
        assert events==['GET /slow'],'request never reached the actual local browser page'
        children=identities(tr._proc.pid);assert len(children)>=3
        if cancel:
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):await pending
        else:
            with pytest.raises(PyHError):await pending
        assert tr._closed
        with pytest.raises(PyHError):await client.call_tool('observe',{})
        assert events==['GET /slow'] # No second launch/replay or form submission.
    finally:await client.close()
    assert_gone(children)


def worker_python():
    # Invoke the real interpreter directly so the retained subprocess handle is
    # the server, not Windows' venv redirector. Add only this trusted venv's libs.
    library=str(Path(sys.prefix)/'Lib/site-packages')
    bootstrap='import runpy,sys;sys.path.insert(0,'+repr(library)+');p=sys.argv.pop(1);runpy.run_path(p,run_name="__main__")'
    return [sys._base_executable,'-I','-B','-X','utf8','-c',bootstrap]



@pytest.mark.parametrize('route,expected',[('/unicode','X'+'\U0001f600'*40),('/surrogate','X\ufffdY')])
async def test_actual_unicode_label_remains_valid_utf8_and_exact_action(local_page,route,expected):
    from pyharness.application.browser_setup import browser_configuration
    from pyharness.core.tools_registry import ToolRegistry
    origin,events=local_page
    config=browser_configuration(dict(node=str(NODE),playwright=str(PW),executable=str(CHROME),origins=[origin]))['connection']['config']
    client=McpClient('unicode_browser',StdioTransport(config['command'],timeout_s=20))
    try:
        await client.connect()
        value=await client.call_tool('open',{'url':origin+route})
        assert not value['isError'],value
        observed=json.loads(value['content'][0]['text'])
        assert observed['targets'][0]['label']==expected
        value['content'][0]['text'].encode('utf-8')
        reg=ToolRegistry();client.register_into(reg)
        args=reg.validate_args('mcp.unicode_browser.act',action(observed))
        actual=await client.call_tool('act',args)
        assert not actual['isError'] and events==['/submit?target=correct']
        assert 'ACCEPTED exact-target' in json.loads(actual['content'][0]['text'])['readback']['text']
        assert json.loads((await client.call_tool('close',{}))['content'][0]['text'])=={'closed':True}
    finally:await client.close()


def test_bounded_large_dom_can_scroll_to_its_exact_issued_target(local_page):
    origin,events=local_page;c=carrier(origin)
    try:
        first=c.call('open',{'url':origin+'/many'})
        assert first['capped'] and first['total']==1000 and len(first['targets'])==4
        observed=c.call('observe',{'offset':996})
        assert observed['capped'] and observed['targets'][0]['label']=='Many 996'
        assert c.call('act',action(first))['error']=='browser_target_stale' and not events
        result=c.call('act',action(observed))
        assert result['acted']=='click' and events==['/submit?target=many-996']
    finally:c.close()
