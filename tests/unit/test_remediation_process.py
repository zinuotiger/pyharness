import asyncio
import ctypes
import json
import os
import sys
from pathlib import Path

import pytest

from pyharness.core import proc, tool_exec


def test_process_cleanup_failure_remains_retryable():
    from types import SimpleNamespace
    from pyharness.core.process_owner import ProcessOwner
    import threading
    calls = []
    class Job:
        def close(self):
            calls.append('job')
            if len(calls) == 1: raise OSError('synthetic close failure')
    owner = ProcessOwner.__new__(ProcessOwner)
    owner._mutex = threading.RLock()
    owner._closed = False
    owner._reaped = False
    owner._tree_stopped = False
    owner._assigned = True
    owner.job = Job()
    owner.proc = SimpleNamespace(wait=lambda **kw: calls.append('wait'), poll=lambda:None)
    with pytest.raises(OSError): owner.terminate()
    owner.terminate()
    assert calls.count('job') == 2
    assert 'wait' in calls


def test_close_all_processes_even_if_first_cleanup_fails(monkeypatch):
    from types import SimpleNamespace
    calls = []
    first = SimpleNamespace(token='a', timer=None, reader=None, watcher=None)
    second = SimpleNamespace(token='b', timer=None, reader=None, watcher=None)
    proc._table()['cleanup-fault'] = {'a': first, 'b': second}
    def kill(sess):
        calls.append(sess.token)
        if sess is first: raise OSError('synthetic failure')
    monkeypatch.setattr(proc, '_kill_tree', kill)
    try:
        with pytest.raises(BaseException): proc.close_session('cleanup-fault')
        assert calls == ['a', 'b']
        assert proc._table()['cleanup-fault'] == {'a': first}
    finally: proc._table().pop('cleanup-fault', None)


def test_R01_pty_without_tree_ownership_fails_before_spawn(monkeypatch, tmp_path):
    from pyharness.core import pty
    from pyharness.errors import PyHError
    calls = []
    monkeypatch.setattr(pty._WinPtySession, 'start', lambda self: calls.append('spawn'))
    monkeypatch.setattr(pty._PosixPty, 'start', lambda self: calls.append('spawn'))
    with pytest.raises(PyHError) as error: pty.open_pty('synthetic', cwd=tmp_path)
    assert error.value.code == 'TLB-807'
    assert calls == []


def alive(pid):
    if os.name == 'nt':
        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle: return False
        code = ctypes.c_ulong()
        kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
        kernel.CloseHandle(ctypes.c_void_p(handle))
        return code.value == 259
    try: os.kill(pid, 0)
    except ProcessLookupError: return False
    return True


@pytest.mark.controlled_process
async def test_F06_cancel_stops_test_owned_parent_and_child(tmp_path):
    child = "import os,time; from pathlib import Path; Path('child.pid').write_text(str(os.getpid())); time.sleep(30)"
    code = f"import os,subprocess,sys,time; from pathlib import Path; Path('parent.pid').write_text(str(os.getpid())); subprocess.Popen([sys.executable,'-B','-c',{child!r}]); time.sleep(30)"
    task = asyncio.create_task(proc.wait_result_async(tmp_path, code, timeout_s=25, python=True))
    pids = []
    try:
        async with asyncio.timeout(5):
            while not (tmp_path/'child.pid').exists(): await asyncio.sleep(.01)
        pids = [int((tmp_path/name).read_text()) for name in ('parent.pid','child.pid')]
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        async with asyncio.timeout(3):
            while any(alive(pid) for pid in pids): await asyncio.sleep(.02)
        assert all(not alive(pid) for pid in pids)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        # RED cleanup is restricted to the two PIDs recorded by our synthetic code.
        for pid in pids:
            if alive(pid):
                if os.name == 'nt':
                    k = ctypes.windll.kernel32
                    k.OpenProcess.restype = ctypes.c_void_p
                    h = k.OpenProcess(1, False, pid)
                    if h:
                        k.TerminateProcess(ctypes.c_void_p(h), 1)
                        k.CloseHandle(ctypes.c_void_p(h))
                else: os.kill(pid, 9)


def test_R01_closed_process_does_not_kill_recycled_pid(monkeypatch):
    from types import SimpleNamespace
    calls = []
    sess = SimpleNamespace(pid=12345, timer=None, proc=SimpleNamespace(poll=lambda:0, kill=lambda:calls.append('kill')))
    monkeypatch.setattr(proc, '_kill_pid_tree', lambda pid:calls.append(pid))
    proc._kill_tree(sess)
    assert calls == []


@pytest.mark.controlled_process
async def test_F09_nonzero_exec_is_not_tool_success(tmp_path):
    from tests.unit.test_tools_executor import AsyncSess, FakeScope, make_ctx, make_registry, mk_defn
    from tests.unit.test_exec import _Config
    from pyharness.core.tools_guard import GuardChain, ToolCall
    from pyharness.core.tools_executor import ToolExecutor
    reg = make_registry(mk_defn(name='exec.python_run', schema={'type':'object','properties':{'code':{'type':'string'}},'required':['code']}),
                        providers={'exec.python_run': tool_exec.exec_python})
    sess = AsyncSess()
    ctx = make_ctx(sess, FakeScope(str(tmp_path)), chain=GuardChain(session=sess, validator=reg.validate_args))
    ctx.config = _Config()
    result = await ToolExecutor(reg).execute(ToolCall(name='exec.python_run', raw_args={'code':"import sys; print('out'); print('err',file=sys.stderr); sys.exit(3)"},call_id='exit-3'), ctx)
    assert result.ok is False
    assert '3' in result.summary
    assert 'out' in result.summary and 'err' in result.summary
    assert sess.of('tool.result')[-1]['payload']['ok'] is False


@pytest.mark.controlled_process
async def test_F06_timeout_has_no_surviving_owned_process(tmp_path):
    code="import os,time;from pathlib import Path;Path('timeout.pid').write_text(str(os.getpid()));time.sleep(30)"
    from pyharness.core.process_owner import ProcessOwner
    owner=ProcessOwner([sys.executable,'-I','-B','-c',code],cwd=tmp_path,env=dict(os.environ))
    try:
        async with asyncio.timeout(8):
            while not (tmp_path/'timeout.pid').exists():await asyncio.sleep(.01)
        result=await asyncio.wait_for(asyncio.to_thread(owner.collect,.05),8)
        assert result['timeout'] is True
        pid=int((tmp_path/'timeout.pid').read_text())
        assert not alive(pid)
    finally:owner.terminate()



def test_R01_posix_group_is_killed_before_leader_reaped(monkeypatch):
    import threading
    from types import SimpleNamespace
    from pyharness.core import process_owner as module
    calls=[]
    owner=module.ProcessOwner.__new__(module.ProcessOwner)
    owner._mutex=threading.RLock();owner._closed=False;owner._reaped=False;owner._tree_stopped=False
    owner._assigned=True;owner.job=None
    owner.proc=SimpleNamespace(pid=424242,wait=lambda **kw:calls.append('wait'))
    monkeypatch.setattr(module.os,'killpg',lambda pid,sig:calls.append(('killpg',pid)),raising=False)
    monkeypatch.setattr(module.signal,'SIGKILL',9,raising=False)
    owner.terminate();owner.terminate()
    assert calls==[('killpg',424242),'wait']


@pytest.mark.controlled_process
@pytest.mark.parametrize('failure',['assign' if os.name == 'nt' else 'close-stdin','second-reader'])
def test_process_partial_initialization_cleans_every_owned_resource(tmp_path,monkeypatch,failure):
    import threading
    from pyharness.core import process_owner as module
    made=[];original=module.subprocess.Popen
    def capture(*a,**kw):
        child=original(*a,**kw);made.append(child)
        if failure == 'close-stdin':
            # POSIX has no Windows Job assignment. Fail the real post-spawn
            # initialization boundary instead, then allow rollback to close it.
            stream = child.stdin
            class FailFirstClose:
                def __init__(self): self.calls = 0
                def __getattr__(self, name): return getattr(stream, name)
                def close(self):
                    self.calls += 1
                    if self.calls == 1: raise OSError('stdin close fault')
                    return stream.close()
            child.stdin = FailFirstClose()
        return child
    monkeypatch.setattr(module.subprocess,'Popen',capture)
    if failure=='assign':
        monkeypatch.setattr(module.WindowsJob,'assign',lambda *a:(_ for _ in ()).throw(OSError('assignment fault')))
    elif failure == 'second-reader':
        start=threading.Thread.start;calls=[]
        def failstart(thread):
            calls.append(thread)
            if len(calls)==2:raise OSError('second reader fault')
            return start(thread)
        monkeypatch.setattr(threading.Thread,'start',failstart)
    owner=None
    try:
        expected = {'assign': 'assignment fault', 'close-stdin': 'stdin close fault',
                    'second-reader': 'second reader fault'}[failure]
        with pytest.raises(OSError, match=expected):
            owner=module.ProcessOwner([sys.executable,'-I','-B','-c','import time;time.sleep(30)'],cwd=tmp_path,env=dict(os.environ))
            owner.collect(3)
        assert made and all(child.poll() is not None for child in made)
        assert all(stream is None or stream.closed for child in made for stream in (child.stdin,child.stdout,child.stderr))
    finally:
        if owner is not None:owner.terminate()



def test_process_wait_failure_keeps_reap_retryable():
    import threading
    from types import SimpleNamespace
    from pyharness.core.process_owner import ProcessOwner
    calls=[]
    def wait(**kw):
        calls.append('wait')
        if calls.count('wait')==1:raise OSError('wait fault')
    owner=ProcessOwner.__new__(ProcessOwner)
    owner._mutex=threading.RLock();owner._closed=False;owner._reaped=False;owner._tree_stopped=False;owner._assigned=True
    owner.job=SimpleNamespace(close=lambda:calls.append('stop'))
    owner.proc=SimpleNamespace(wait=wait)
    with pytest.raises(OSError,match='wait fault'):owner.terminate()
    assert not owner._closed
    owner.terminate();assert owner._closed
    assert calls==['stop','wait','wait']

@pytest.mark.controlled_process
@pytest.mark.parametrize('cancel_first',[False,True])
async def test_exec_natural_exit_cancel_race_reaps_owned_process(tmp_path,cancel_first):
    code="import os,time;from pathlib import Path;Path('ready.pid').write_text(str(os.getpid()));\nwhile not Path('release').exists():time.sleep(.005)\nprint('natural exit')"
    task=asyncio.create_task(proc.wait_result_async(tmp_path,code,timeout_s=8,python=True))
    try:
        async with asyncio.timeout(5):
            while not (tmp_path/'ready.pid').exists():await asyncio.sleep(.005)
        pid=int((tmp_path/'ready.pid').read_text())
        if cancel_first:task.cancel();(tmp_path/'release').write_text('go')
        else:(tmp_path/'release').write_text('go');task.cancel()
        async with asyncio.timeout(5):
            result=await asyncio.gather(task,return_exceptions=True)
        assert isinstance(result[0],asyncio.CancelledError) or result[0]['exit_code']==0
        assert not alive(pid)
    finally:
        (tmp_path/'release').write_text('go');task.cancel();await asyncio.gather(task,return_exceptions=True)
