import copy
import hashlib
import json
from types import SimpleNamespace as NS
import pytest
from pyharness.application.order_review import COLUMNS,KEY,policy,calculate,review
from pyharness.application.execution_quality import quality_projection


def fixture():
    data=[('甲',3,20,60,'已付款',60,'已发货','20'),('乙',5,12,60,'已付款',60,'已发货','20'),
        ('丙',10,3,30,'已付款',30,'已发货','21'),('丁',2,80,170,'已付款',170,'已发货','21'),
        ('戊',4,25,100,'未付款',0,'待发货','25'),('己',6,5,30,'已付款',30,'已发货','21'),
        ('庚',4,30,120,'已付款',0,'待发货','24'),('辛',8,6,48,'已付款',48,'已发货','22'),
        ('壬',5,10,50,'已付款',50,'待发货','20'),('癸',10,4,40,'未付款',0,'待发货','26'),
        ('',20,2,40,'已付款',40,'已发货','22'),('子',6,8,48,'已付款',48,'已发货','22')]
    text=','.join(COLUMNS.values())+'\n'+''.join(f'OD{n:03d},{c},商品,{q},{p},{a},{paid},{received},{ship},2026-10-{day}\n'
        for n,(c,q,p,a,paid,received,ship,day) in enumerate(data,1))
    files=[dict(path=name,text=body,content_digest=hashlib.sha256(body.encode()).hexdigest())
        for name,body in [('orders.csv',text),('rules.md','fixed rules')]]
    config=policy({KEY:dict(schema_version=1,csv_path='orders.csv',rules_path='rules.md',cutoff='2026-10-22',currency='CNY',columns=COLUMNS)})
    return dict(files=files),config


def answer(facts):
    return dict(summary=facts['summary'].copy(),issues=[dict(order_id=i['order_id'],code=i['code'],action='请核实原始记录') for i in facts['issues']],normal_orders=list(facts['normal_orders']))


def test_twelve_orders_compute_without_correcting_original_ledger():
    bundle,config=fixture();before=copy.deepcopy(bundle);facts=calculate(bundle,config)
    assert facts['summary']==dict(booked_total='796.00',calculated_total='786.00',received_total='536.00',outstanding_total='260.00',order_count=12,shipped=8,pending=4)
    assert [i['order_id'] for i in facts['issues']]==['OD004','OD007','OD009','OD011']
    assert '逾期2天' in facts['issues'][2]['detail'] and bundle==before
    body,result=review(json.dumps(answer(facts)),facts)
    assert result['status']=='passed' and '订单摘要' in body and '问题清单' in body
    assert 'OD005' in facts['normal_orders'] and 'OD010' in facts['normal_orders']


def test_historical_three_wrong_totals_are_exposed_and_not_silently_accepted():
    bundle,config=fixture();facts=calculate(bundle,config);raw=answer(facts)
    raw['summary'].update(booked_total='806',calculated_total='696',outstanding_total='270')
    body,result=review(json.dumps(raw),facts)
    assert result['status']=='failed'
    assert {d['field'] for d in result['differences']}=={'booked_total','calculated_total','outstanding_total'}
    assert '806' in body and '796.00' in body


@pytest.mark.parametrize('mutate',[lambda a:a.pop('issues'),lambda a:a['issues'].pop(),lambda a:a['issues'].append(a['issues'][0]),lambda a:a['normal_orders'].append('OD004'),lambda a:a['issues'][0].update(action='错算为806元')])
def test_stop_does_not_make_missing_or_conflicting_content_valid(mutate):
    bundle,config=fixture();facts=calculate(bundle,config);raw=answer(facts);mutate(raw)
    assert review(json.dumps(raw),facts)[1]['status']=='failed'


@pytest.mark.parametrize('old,new',[('170,已付款','NaN,已付款'),('170,已付款','170.001,已付款'),('OD012,','OD011,'),('已发货,2026','未知,2026')])
def test_bad_data_is_not_zero_or_silently_rounded(old,new):
    bundle,config=fixture();f=bundle['files'][0];f['text']=f['text'].replace(old,new);f['content_digest']=hashlib.sha256(f['text'].encode()).hexdigest()
    with pytest.raises(ValueError):calculate(bundle,config)


def test_decimal_arithmetic_is_exact_and_config_does_not_authorize_paths():
    bundle,config=fixture();f=bundle['files'][0];f['text']=','.join(COLUMNS.values())+'\nX,甲,商品,3,0.10,0.30,已付款,0.30,已发货,2026-10-20\n';f['content_digest']=hashlib.sha256(f['text'].encode()).hexdigest()
    assert calculate(bundle,config)['summary']['booked_total']=='0.30'
    with pytest.raises(ValueError):policy({KEY:dict(config,csv_path='../orders.csv')})
    assert policy({}) is None


@pytest.mark.parametrize('precision',[6,28,60])
def test_allowed_order_limits_keep_exact_cents_and_preserve_decimal_context(precision):
    from decimal import getcontext,localcontext
    bundle,config=fixture();source=bundle['files'][0]
    source['text']=','.join(COLUMNS.values())+'\n'+''.join(
        f'{n},甲,商品,999999999,99999999999999.99,0,未付款,0,已发货,2026-10-20\n'
        for n in range(10000))
    raw=source['text'].encode()
    assert len(raw)<=1048576
    source['content_digest']=hashlib.sha256(raw).hexdigest()
    before=copy.deepcopy(bundle)
    # Integer cents give an independent oracle without Decimal context rounding.
    cents=999999999*9999999999999999*10000
    expected=f'{cents//100}.{cents%100:02d}'
    with localcontext() as ambient:
        ambient.prec=precision
        facts=calculate(bundle,config)
        assert getcontext().prec==precision
    assert facts['summary']['calculated_total']==expected
    assert facts['summary']['order_count']==10000
    assert facts['summary']['booked_total']=='0.00'
    assert len(facts['issues'])==10000 and bundle==before


@pytest.mark.parametrize('finish,expected',[('length','incomplete'),('stop','complete'),('unknown','unknown'),('tool_calls','unknown')])
def test_output_truncation_is_independent_of_display_length(finish,expected):
    events=[NS(type='llm.request',seq=1,payload=dict(effective_output_limit=1200,configured_output_limit=1200)),
        NS(type='llm.response',seq=2,payload=dict(finish_reason=finish,request_seq=1)),
        NS(type='agent.message',seq=3,trace={'parent_seq':2},payload={'content':'short'})]
    result=quality_projection(events)
    assert result['output_completeness']==expected and result['effective_output_limit']==1200
    assert (result['delivery_readiness']=='ready_for_independent_review')==(finish=='stop')


def test_legacy_missing_response_is_unknown_not_fabricated_success():
    assert quality_projection([NS(type='agent.message',seq=1,trace=None,payload=dict(content='legacy'))])['output_completeness']=='unknown'


def test_failed_computation_blocks_ready_even_when_provider_stopped_normally():
    events=[NS(type='llm.response',seq=1,payload=dict(finish_reason='stop')),
        NS(type='agent.message',seq=2,trace={'parent_seq':1},payload=dict(content='result',quality={'calculation':{'status':'failed'}}))]
    assert quality_projection(events)['delivery_readiness']=='needs_review'


def test_budget_diagnostics_report_ancestor_input_reservation_and_release():
    from pyharness.core.llm import UsageCounters
    from pyharness.core.scope import BudgetExhausted
    from tests.unit.test_task_budget_isolation import limits
    parent=UsageCounters().new_task(limits());a,b=[parent.new_task(limits(),parent=parent) for _ in range(2)]
    _,release=a.reserve_output(20,input_tokens=900)
    with pytest.raises(BudgetExhausted) as caught:b.reserve_output(10,input_tokens=101)
    d=caught.value.diagnostics
    assert (d['account'],d['dimension'],d['reserved'],d['shortfall'],d['request_sent'])==('parent','input',900,1,False)
    assert d['basis']=='conservative_utf8_bytes'
    release();release()
    assert parent._reserved_in==0
    _,release=b.reserve_output(10,input_tokens=101);release()


def test_budget_output_and_cost_have_separate_numeric_diagnostics():
    from pyharness.core.llm import UsageCounters
    from pyharness.core.scope import BudgetExhausted
    from tests.unit.test_task_budget_isolation import limits
    account=UsageCounters().new_task(limits())
    _,release=account.reserve_output(100)
    with pytest.raises(BudgetExhausted) as caught:account.reserve_output(1)
    assert caught.value.diagnostics['dimension']=='output'
    release()
    with pytest.raises(BudgetExhausted) as caught:account.reserve_output(1,input_tokens=20,input_rate=1)
    assert caught.value.diagnostics['dimension']=='cost'


async def test_platform_length_response_is_preserved_but_run_fails():
    from pyharness.application.platform_runtime import PlatformRuntime
    from pyharness.application.platform_models import AgentDefinition
    runtime=PlatformRuntime(None,AgentDefinition(name='quality'),'test')
    content,quality=runtime.review_answer('半句话',None,NS(finish_reason='length'))
    assert content=='半句话' and quality['output_completeness']=='incomplete'


def test_new_quality_fields_do_not_change_historical_payload_digests():
    from pyharness.events.payload_execution import AgentMessagePayload,LlmRequestPayload
    assert AgentMessagePayload(content='old',model=None).model_dump()==dict(content='old',model=None)
    assert 'effective_output_limit' not in LlmRequestPayload(model='old').model_dump()
    assert AgentMessagePayload(content='new',quality={'output_completeness':'incomplete'}).model_dump()['quality']['output_completeness']=='incomplete'


def test_report_marker_is_configured_and_unmapped_currency_is_rejected():
    bundle,config=fixture();config['report_marker']='ORDER-BUSINESS-20261022';facts=calculate(bundle,config)
    body,result=review(json.dumps(answer(facts)),facts)
    assert body.startswith('验收标记：ORDER-BUSINESS-20261022') and '待核实事项' in body
    f=bundle['files'][0];f['text']=f['text'].replace('订单号,','币种,订单号,');f['content_digest']=hashlib.sha256(f['text'].encode()).hexdigest()
    with pytest.raises(ValueError,match='未映射列'):calculate(bundle,config)


def test_earlier_truncated_selection_cannot_use_later_complete_readiness(monkeypatch):
    from pyharness.application.execution_quality import require_package_quality
    from pyharness.errors import PyHError
    events=[NS(type='llm.response',seq=1,payload=dict(finish_reason='length')),
        NS(type='agent.message',seq=2,trace={'parent_seq':1},payload=dict(content='partial')),
        NS(type='llm.response',seq=3,payload=dict(finish_reason='stop')),
        NS(type='agent.message',seq=4,trace={'parent_seq':3},payload=dict(content='done'))]
    monkeypatch.setattr('pyharness.application.product_execution_sessions.read_execution_events',lambda *a:events)
    service=NS(_tenant_sessions_dir=lambda:None,tenant_id='test')
    row={'execution_authorization':{'session_id':'root'}}
    assert quality_projection(events)['delivery_readiness']=='ready_for_independent_review'
    with pytest.raises(PyHError):require_package_quality(service,row,selected=[dict(session_id='root',kind='message',source_seq=2)])


@pytest.mark.parametrize('mode,expected',[('exact','complete'),('wrong_task','unknown'),('wrong_turn','unknown'),('clipped','incomplete')])
def test_codex_public_terminal_compatibility_without_fabricated_llm_finish(mode,expected):
    text='x'*32001 if mode=='clipped' else 'public final'
    def fact(seq,action,record):return NS(seq=seq,type='executor.event',actor='system',task_id='task',payload=dict(executor='codex',action=action,record=record))
    events=[fact(1,'turn_started',{'turn_id':'turn'}),fact(2,'item_completed',dict(type='agentMessage',text=text)),
        fact(3,'turn_completed',dict(turn_id='other' if mode=='wrong_turn' else 'turn',status='completed')),
        NS(seq=4,type='agent.message',trace=None,task_id='other' if mode=='wrong_task' else 'task',payload=dict(content=text[:32000]))]
    result=quality_projection(events)
    assert result['output_completeness']==expected
    assert (result['delivery_readiness']=='ready_for_independent_review')==(expected=='complete')
