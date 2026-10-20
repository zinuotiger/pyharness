"""Real owned engine cancellation, no formal service/session replacement."""
import asyncio
from pathlib import Path
import pytest
from pyharness import cli
from pyharness.desktop.app import DesktopApp
from pyharness.application.skill_workbench import SkillWorkbench
from pyharness.application.skill_rehearsal import _ControlledSkillPlanner
from pyharness.engine_types import EngineSpine
from tests.support import isolated_settings


@pytest.mark.parametrize('repeated_cancel',[False,True])
async def test_cancelled_rehearsal_awaits_owned_engine_cleanup_and_duplicate_does_not_restart(tmp_path,monkeypatch,repeated_cancel):
    path=tmp_path/'skills'/'sample'/'SKILL.md';path.parent.mkdir(parents=True)
    path.write_text('---\nname: sample\ndescription: sample\n---\noriginal',encoding='utf-8')
    app=DesktopApp(cli.assemble_ctx(isolated_settings(tmp_path)))
    entered=asyncio.Event();hold=asyncio.Event();closing=asyncio.Event();release_close=asyncio.Event();cleaned=[];calls=[]
    if not repeated_cancel:release_close.set()
    original_chat=_ControlledSkillPlanner._respond;original_close=EngineSpine.close
    async def gated_chat(self,messages,tools=None,*,ctx):
        calls.append(ctx.session.sid);entered.set();await hold.wait()
        return await original_chat(self,messages,tools,ctx=ctx)
    async def observed_close(self):
        closing.set();await release_close.wait()
        await original_close(self)
        cleaned.append(self)
    monkeypatch.setattr(_ControlledSkillPlanner,'_respond',gated_chat)
    monkeypatch.setattr(EngineSpine,'close',observed_close)
    authorize=lambda:None
    try:
        bench=SkillWorkbench(app.service.platform)
        saved=bench.capture('sample',authorize=authorize)
        body={'command_key':'cancel-real-1','confirm_controlled_rehearsal':True,
            'loaded_sha256':saved['snapshot']['loaded_sha256']}
        task=asyncio.create_task(bench.rehearse(saved['snapshot_id'],body,authorize=authorize))
        await asyncio.wait_for(entered.wait(),10);task.cancel()
        if repeated_cancel:
            await asyncio.wait_for(closing.wait(),5)
            task.cancel();await asyncio.sleep(.02)
            assert not task.done() and not cleaned
            release_close.set()
        with pytest.raises(asyncio.CancelledError):await task
        assert len(cleaned)==1
        assert cleaned[0].task_queue._closing is True
        assert cleaned[0]._flush_task is None and not cleaned[0].bus_owners
        assert not app.service.platform.actions
        row=bench.result('cancel-real-1',authorize=authorize)
        assert row['status']=='unconfirmed'
        assert await bench.rehearse(saved['snapshot_id'],body,authorize=authorize)==row
        assert len(calls)==1 and len(cleaned)==1
        assert path.read_text().endswith('original')
    finally:
        hold.set();release_close.set();await app.service.shutdown()
