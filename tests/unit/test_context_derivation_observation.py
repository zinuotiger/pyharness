"""Observe the actual derivation once; counters cannot become shared history."""
import asyncio,copy
from types import SimpleNamespace
import pytest
from pyharness.core.session import SessionLog
from pyharness.core.system_prompt import SystemPromptAssembler,GUARD_HEADER
from tests.unit.test_system_prompt import mk_ctx,mk_scope


async def test_history_window_counts_match_the_actual_pure_reducer_and_preserve_events():
    from pyharness.core.context_derivation import derive_observed_messages
    log=SessionLog('s-public-window')
    await log.append('session.created',dict(title='Window test',model='synthetic'),actor='system')
    await log.append('user.message',dict(content='older '*1000),actor='user')
    await log.append('user.message',dict(content='Newest public input'),actor='user')
    before=[e.model_dump(mode='json') for e in log.events_after(0)]
    expected=log.derive_messages(10);actual,window=derive_observed_messages(log,10)
    assert actual==expected and actual==[dict(role='user',content='Newest public input')]
    assert window==dict(available=True,source_seq=3,input_messages=2,kept_messages=1,dropped_messages=1,window_tokens=10)
    assert [e.model_dump(mode='json') for e in log.events_after(0)]==before
    full,uncut=derive_observed_messages(log,None)
    assert len(full)==2 and uncut['dropped_messages']==0


def test_actual_system_assembly_is_called_once_and_its_real_head_truncation_is_reported(monkeypatch):
    from pyharness.core.context_derivation import assemble_observed
    assembler=SystemPromptAssembler();ctx=mk_ctx(scope=mk_scope(window_tokens=1500))
    history=[dict(role='user',content='public '*300) for _ in range(20)];before=copy.deepcopy(history)
    original=assembler.assemble;calls=[]
    def once(*args,**kwargs):
        value=original(*args,**kwargs);calls.append(value);return value
    monkeypatch.setattr(assembler,'assemble',once)
    actual,window=assemble_observed(assembler,history,ctx)
    assert calls==[actual] and history==before and GUARD_HEADER in actual[0]['content']
    assert window['available'] and window['input_messages']==20
    assert window['kept_messages']==len(actual)-1 and window['dropped_messages']==20-window['kept_messages']>0
    assert actual[1:]==history[-window['kept_messages']:]


def test_unrecognized_assembler_cannot_lend_false_window_statistics():
    from pyharness.core.context_derivation import assemble_observed
    class Other:
        def assemble(self,history,*,ctx):return [dict(role='user',content='Different custom assembly')]
    actual,window=assemble_observed(Other(),[dict(role='user',content='Original')],None)
    assert actual[0]['content']=='Different custom assembly'
    assert window==dict(available=False)


async def test_request_window_scope_is_coroutine_and_session_bound_and_resets_after_use():
    from pyharness.core.context_derivation import request_context_scope,request_context_metadata
    ready=asyncio.Event();seen=[]
    async def one(ident):
        ctx=SimpleNamespace(session=SimpleNamespace(sid=ident));messages=[dict(role='user',content=ident)]
        counts=dict(available=True,source_seq=7,input_messages=4,kept_messages=2,dropped_messages=2,window_tokens=10)
        assert request_context_metadata(ctx,messages) is None
        with request_context_scope(ctx,messages,counts,dict(available=False)):
            seen.append(ident)
            if len(seen)==2:ready.set()
            await ready.wait()
            own=request_context_metadata(ctx,messages)
            assert own['history_window']==counts
            assert request_context_metadata(SimpleNamespace(session=SimpleNamespace(sid='another-session')),messages) is None
            assert request_context_metadata(SimpleNamespace(session=SimpleNamespace(sid=ident,tenant_id='other-tenant')),messages) is None
            assert request_context_metadata(ctx,[dict(role='user',content='Different actual request')]) is None
            own['history_window']['dropped_messages']=999
            assert request_context_metadata(ctx,messages)['history_window']['dropped_messages']==2
        assert request_context_metadata(ctx,messages) is None
    await asyncio.gather(one('s-A'),one('s-B'))


def test_request_window_scope_restores_outer_state_after_exception():
    from pyharness.core.context_derivation import request_context_scope,request_context_metadata
    ctx=SimpleNamespace(session=SimpleNamespace(sid='s-outer'));messages=[dict(role='user',content='Public')]
    outer=dict(available=True,source_seq=1,input_messages=1,kept_messages=1,dropped_messages=0,window_tokens=None)
    with request_context_scope(ctx,messages,outer,dict(available=False)):
        with pytest.raises(RuntimeError):
            with request_context_scope(ctx,messages,dict(available=False),dict(available=False)):
                raise RuntimeError('Controlled request failure')
        assert request_context_metadata(ctx,messages)['history_window']==outer
    assert request_context_metadata(ctx,messages) is None
