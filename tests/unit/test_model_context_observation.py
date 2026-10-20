"""Actual core dispatch wiring, apart from later product persistence/UI proof."""
import json
from types import SimpleNamespace
import httpx
import pytest
from pyharness.core.llm import LLMClient,OpenAICompatAdapter,LLMResponse,TimeoutLimits,UsageCounters
from pyharness.core.llm_diagnostics import call_attempt,call_role,model_call
from pyharness.core.llm_fallback import FallbackChain
from pyharness.core.llm_transport import _OpenAICompatHTTPTransport
from pyharness.errors import PyHError
from tests.unit.test_llm import _ctx,_session,_raw as _response,_chunk,FakeTransport
from tests.unit.test_llm_fallback import _cfg,FakeAdapter,_ctx as fallback_ctx


class Observer:
    observes_public_context=True
    def __init__(self,*,withdraw=False,fail=False):
        self.views=[];self.order=[];self.allowed=True;self.withdraw=withdraw;self.fail=fail
        self.definition=SimpleNamespace(budget=SimpleNamespace(input_tokens=100000,output_tokens=100000,cost=100000))
    async def observe_model_request(self,request,*,ctx,layer,request_seq=None,public_view=None):
        assert ctx.platform_runtime is self
        if not self.allowed:raise PyHError('GRD-403')
        if self.fail:raise RuntimeError('Controlled public observation failure')
        self.order.append('observe')
        self.views.append(dict(layer=layer,request_seq=request_seq,public_view=public_view,model=request['model'],
            role=call_role.get(),attempt=call_attempt.get()))
        if self.withdraw:self.allowed=False
    async def before_model_attempt(self,model,method,ctx):
        self.order.append('authorize')
        if not self.allowed:raise PyHError('GRD-403')


class Direct:
    def __init__(self,observer):self.calls=0;self.observer=observer
    async def chat(self,messages,tools=None,*,ctx):
        self.calls+=1;self.observer.order.append('adapter')
        return LLMResponse(content='Public response',model='synthetic',finish_reason='stop')


async def test_custom_adapter_is_observed_before_the_final_actual_model_gate():
    observer=Observer();adapter=Direct(observer);client=LLMClient(model='synthetic',registry={'synthetic':adapter})
    ctx=SimpleNamespace(platform_runtime=observer)
    with model_call('main_agent'):await client.chat([dict(role='user',content='Actual public input')],ctx=ctx)
    assert observer.order==['observe','authorize','adapter']
    assert len(observer.views)==1 and observer.views[0]['layer']=='adapter_input'
    assert observer.views[0]['public_view']['messages'][0]['content']=='Actual public input'
    assert observer.views[0]['role']=='main_agent'


async def test_withdrawal_during_observation_is_rechecked_before_any_adapter_execution():
    observer=Observer(withdraw=True);adapter=Direct(observer);client=LLMClient(model='synthetic',registry={'synthetic':adapter})
    with pytest.raises(PyHError) as error:
        await client.chat([dict(role='user',content='Public')],ctx=SimpleNamespace(platform_runtime=observer))
    assert error.value.code=='GRD-403' and adapter.calls==0


async def test_observation_failure_does_not_fall_back_to_unobserved_dispatch():
    observer=Observer(fail=True);adapter=Direct(observer);client=LLMClient(model='synthetic',registry={'synthetic':adapter})
    with pytest.raises(RuntimeError):await client.chat([dict(role='user',content='Public')],ctx=SimpleNamespace(platform_runtime=observer))
    assert adapter.calls==0


async def test_legacy_runtime_does_not_gain_new_observation_calls():
    observer=Observer();observer.observes_public_context=False
    adapter=Direct(observer);client=LLMClient(model='synthetic',registry={'synthetic':adapter})
    await client.chat([dict(role='user',content='Public')],ctx=SimpleNamespace(platform_runtime=observer))
    assert observer.views==[] and observer.order==['authorize','adapter']


@pytest.mark.parametrize('streaming',[False,True])
async def test_compat_adapter_records_only_its_actual_sanitized_transport_request(streaming):
    observer=Observer();session=await _session();ctx=_ctx(session,counters=UsageCounters());ctx.platform_runtime=observer
    transport=FakeTransport(result=_response('Public response'),chunks=[_chunk(content='Public response'),_chunk(finish='stop')])
    adapter=OpenAICompatAdapter('deepseek-chat',transport=transport,limits=TimeoutLimits(10,60,180))
    client=LLMClient(model='deepseek-chat',registry={'deepseek-chat':adapter})
    tool=dict(type='function',function=dict(name='file.read',description='Read',parameters=dict(type='object',properties={})))
    with model_call('main_agent'):
        await getattr(client,'chat_stream' if streaming else 'chat')([dict(role='user',content='Public')],[tool],ctx=ctx)
    assert len(transport.requests)==len(observer.views)==1
    view=observer.views[0]
    assert view['layer']=='transport_payload' and view['request_seq']==2
    assert view['public_view']['tools'][0]['function']['name']==transport.requests[0]['tools'][0]['function']['name']=='file_read'
    assert view['public_view']['method']==('chat_stream' if streaming else 'chat')


async def test_each_fallback_adapter_attempt_has_its_own_actual_model_attribution():
    observer=Observer();cfg=_cfg();cfg.llm.model='model-a';cfg.llm.fallback_models=['model-b'];cfg.llm.retry.attempts=1
    first=FakeAdapter(always=PyHError('LLM-303'));second=FakeAdapter(name='model-b')
    chain=FallbackChain(adapters={'model-a':first,'model-b':second},chain=['model-a','model-b'],config=cfg)
    ctx=fallback_ctx(config=cfg);ctx.platform_runtime=observer
    client=LLMClient(cfg,chain=chain)
    await client.chat([dict(role='user',content='Public fallback input')],ctx=ctx)
    assert [v['model'] for v in observer.views]==['model-a','model-b']
    assert [v['attempt'] for v in observer.views]==[1,1]
    assert len(first.chats)==len(second.chats)==1


async def test_current_transport_key_and_provider_reasoning_never_reach_public_observation():
    observer=Observer();session=await _session();ctx=_ctx(session,counters=UsageCounters());ctx.platform_runtime=observer
    transport=_OpenAICompatHTTPTransport('http://127.0.0.1:1','tinycredential',TimeoutLimits(10,60,180))
    await transport._client.aclose();seen=[]
    async def receive(req):
        seen.append(dict(body=json.loads(req.content),authorization=req.headers['Authorization']))
        return httpx.Response(200,json=dict(choices=[dict(message=dict(content='Public reply',reasoning_content='opaque-private-chain'),finish_reason='stop')]))
    transport._client=httpx.AsyncClient(base_url='http://127.0.0.1:1',headers={'Authorization':'Bearer tinycredential'},transport=httpx.MockTransport(receive),trust_env=False)
    adapter=OpenAICompatAdapter('deepseek-chat',transport=transport,limits=TimeoutLimits(10,60,180))
    try:
        await LLMClient(model='deepseek-chat',registry={'deepseek-chat':adapter}).chat([dict(role='user',content='Echo includes tinycredential')],ctx=ctx)
    finally:await adapter.aclose()
    assert len(seen)==len(observer.views)==1 and seen[0]['authorization']=='Bearer tinycredential'
    assert 'tinycredential' in seen[0]['body']['messages'][0]['content']
    public=json.dumps(observer.views)
    assert 'tinycredential' not in public and 'opaque-private-chain' not in public
