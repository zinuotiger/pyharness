"""Controlled synthetic native processes prove bounded output/timeout/cancel cleanup."""
import asyncio
import sys
import pytest
from pyharness.errors import PyHError
from pyharness.application import workspace_git


@pytest.mark.controlled_process
@pytest.mark.parametrize('operation',['output-limit','timeout','cancel'])
async def test_git_reader_is_reaped_and_never_reused(tmp_path, monkeypatch, operation):
    original=asyncio.create_subprocess_exec
    processes=[];ready=asyncio.Event()
    async def spawn(*argv,**options):
        code=('import sys,time;sys.stdout.buffer.write(b"x"*(8*1024*1024));sys.stdout.flush();time.sleep(30)'
              if operation=='output-limit' else 'import time;time.sleep(30)')
        proc=await original(sys.executable,'-B','-c',code,**options)
        processes.append(proc);ready.set();return proc
    monkeypatch.setattr(workspace_git.asyncio,'create_subprocess_exec',spawn)
    try:
        if operation=='output-limit':
            with pytest.raises(PyHError,match='EVT-100'):
                await asyncio.wait_for(workspace_git._git(tmp_path,tmp_path,tmp_path,['synthetic'],128,timeout=10),2)
        elif operation=='timeout':
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(workspace_git._git(tmp_path,tmp_path,tmp_path,['synthetic'],128,timeout=.1),2)
        else:
            task=asyncio.create_task(workspace_git._git(tmp_path,tmp_path,tmp_path,['synthetic'],128,timeout=10))
            await asyncio.wait_for(ready.wait(),2);task.cancel()
            with pytest.raises(asyncio.CancelledError): await asyncio.wait_for(task,2)
        assert len(processes)==1 and processes[0].returncode is not None
    finally:
        for proc in processes:
            if proc.returncode is None: proc.kill()
            await asyncio.wait_for(proc.communicate(),2)
