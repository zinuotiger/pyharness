"""Fixed original prefixes reproduce finite checks without converting tests into release approval."""
import hashlib,json
from types import SimpleNamespace
import pytest
from tests.unit.test_product_execution_start import running_product
from tests.unit.test_experiment_runs import BASE,RID,start,finished
from tests.unit.test_experiment_preparation import selection,prepared,LIMITS
from tests.unit.test_regression_dataset_api import reference,save_case

REPORT_ID='epr-'+'1'*16


async def quality_batch(env,monkeypatch,*,different_schema=False):
    body,case,selected=await selection(env)
    one=await save_case(env,dict(body,case_id='case-'+'2'*16,title='Alpha finite answer',input_text='Case alpha',
        assertions=[dict(kind='output_equals',expected=json.dumps(dict(answer='expected answer')))]),'alpha-quality-case')
    two=await save_case(env,dict(body,case_id='case-'+'3'*16,title='Beta finite answer',input_text='Case beta',
        assertions=[dict(kind='output_equals',expected=json.dumps(dict(answer='expected answer')))]),'beta-quality-case')
    assert one.status_code==two.status_code==200
    refs=[reference(v.json(),'case_id') for v in (one,two)]
    r=await env.client.post(BASE+'/regression-datasets',json=dict(schema_version=1,dataset_id='ds-'+'2'*16,expected_revision=0,
        project_revision=1,title='Two exact quality cases',confirmed=True,case_refs=refs),headers={'Idempotency-Key':'paired-quality-library'})
    assert r.status_code==200,r.text
    if different_schema:
        platform=env.app.service.platform
        agent=platform.save_agent(dict(name='Different declared validator',output_schema=dict(type='object',
            properties=dict(answer=dict(type='string')),required=['answer'])))
        other=platform.publish_agent(agent['agent_id']);selected['right_agent_ref']={k:other[k] for k in ('agent_id','version','sha256')}
    selected=dict(selected,dataset_ref=reference(r.json(),'dataset_id'),case_refs=refs)
    _,definition=await prepared(env,selected,'paired-quality-definition');env.controls.after_chat=None
    right=selected['right_agent_ref']['agent_id'];store=env.app.service.projects.store
    async def actual_scripted_response(messages,tools=None,*,ctx):
        from pyharness.core import llm
        env.adapter.n+=1
        resources,_=store._cancellation_state()
        authority=next(v['execution_authorization'] for v in resources.values()
            if v.get('execution_authorization',{}).get('session_id')==ctx.session.sid)
        prompt=next(m['content'] for m in messages if m['role']=='user' and m['content'].startswith('Execute the objective'))
        objective=json.loads(prompt.split('\n',1)[1])['task']['objective']
        text='regressed answer' if authority['selection']['agent_ref']['agent_id']==right and objective=='Case beta' else 'expected answer'
        text=json.dumps(dict(answer=text))
        usage=SimpleNamespace(prompt_tokens=100,completion_tokens=20,prompt_cache_hit_tokens=0,prompt_cache_miss_tokens=100)
        await llm.report_usage(usage,env.adapter.model,ctx=ctx,counters=getattr(ctx,'counters',None))
        event=await ctx.session.append('llm.response',dict(model=env.adapter.model,finish_reason='stop',content=text,tool_calls=[]),actor='llm')
        return llm.LLMResponse(content=text,tool_calls=None,usage=usage,model=env.adapter.model,finish_reason='stop',seq=event.seq)
    monkeypatch.setattr(env.adapter,'chat',actual_scripted_response)
    _,receipt=await start(env,dict(schema_version=1,experiment_run_id=RID,experiment_ref=reference(definition,'experiment_id'),
        budget_grant=LIMITS,confirm_host_boundary=False))
    result=await finished(env);assert result['status']=='completed' and len(result['nodes'])==4,result
    return receipt,refs


async def report_preview(env,receipt):
    r=await env.client.post(BASE+'/experiment-report-previews',json=dict(schema_version=1,report_id=REPORT_ID,
        experiment_run_ref=reference(receipt,'experiment_run_id')))
    assert r.status_code==200,r.text
    return r.json()


def save_command(preview):
    return dict(schema_version=1,report_id=REPORT_ID,experiment_run_ref=preview['experiment_run_ref'],basis=preview['basis'],
        report_digest=preview['report_digest'],confirmation_digest=preview['confirmation_digest'],confirmed=True)


async def test_actual_two_case_pair_identifies_only_beta_quality_regression_with_exact_denominators(running_product,monkeypatch):
    env=running_product;receipt,refs=await quality_batch(env,monkeypatch);store=env.app.service.projects.store
    before=store.log_path.read_bytes();calls=env.adapter.n;preview=await report_preview(env,receipt);data=preview['report_data']
    assert data['selected_count']==2 and data['dataset_total']==2 and data['excluded_count']==0
    assert data['execution_counts']['total']==data['execution_counts']['completed']==4
    assert data['quality_counts']==dict(total=4,evaluable=4,passed=3,failed=1,unknown=0)
    assert data['business_counts']['not_evaluated']==4 and data['business_counts']['accepted']==0
    assert len(data['regressions'])==1 and data['regressions'][0]['case_ref']==refs[1]
    assert data['cases'][0]['comparison_status']==data['cases'][1]['comparison_status']=='aligned'
    assert all(case[side]['usage']['cost_est'] is None for case in data['cases'] for side in ('left','right'))
    assert store.log_path.read_bytes()==before and env.adapter.n==calls and not preview['export_authorized']


async def test_saved_report_recomputes_fixed_prefix_and_export_requires_separate_confirmation(running_product,monkeypatch):
    env=running_product;receipt,_=await quality_batch(env,monkeypatch);preview=await report_preview(env,receipt);calls=env.adapter.n
    command=save_command(preview)
    r=await env.client.post(BASE+'/experiment-reports',json=command,headers={'Idempotency-Key':'save-fixed-experiment-report'})
    assert r.status_code==200,r.text
    saved=r.json();assert saved['report_data']==preview['report_data']
    resources,_,_=await env.app.service.projects.store._execution_read_state()
    for node in saved['report_data']['cases']:
        for side in ('left','right'):
            execution_id=node[side]['execution_ref']['execution_id'];row=resources[execution_id]
            log=env.app.service.surface_mgr()._logs[row['execution_authorization']['session_id']]
            await log.append('agent.message',dict(content='Later tail must not replace fixed answer'),actor='agent',task_id=row['execution_authorization']['task_id'])
    store=env.app.service.projects.store;before=store.log_path.read_bytes()
    r=await env.client.get(BASE+'/experiment-reports/'+REPORT_ID)
    assert r.status_code==200 and r.json()['report_data']==preview['report_data'] and r.json()['recomputed'],r.text
    from pyharness import config
    redact=config.redact
    with monkeypatch.context() as patch:
        patch.setattr(config,'redact',lambda value:redact(value).replace('finite_comparison','[redacted]'))
        blocked=await env.client.post(BASE+'/experiment-reports/'+REPORT_ID+'/export-previews',json=dict(schema_version=1))
        assert blocked.status_code==403 and 'report_canonical' not in blocked.text,blocked.text
    r=await env.client.post(BASE+'/experiment-reports/'+REPORT_ID+'/export-previews',json=dict(schema_version=1))
    assert r.status_code==200 and not r.json()['export_authorized'],r.text
    export=r.json()
    r=await env.client.post(BASE+'/experiment-reports/'+REPORT_ID+'/exports',json=dict(schema_version=1,confirmed=False,confirmation_digest=export['confirmation_digest']))
    assert r.status_code==422,r.text
    r=await env.client.post(BASE+'/experiment-reports/'+REPORT_ID+'/exports',json=dict(schema_version=1,confirmed=True,confirmation_digest=export['confirmation_digest']))
    assert r.status_code==200 and r.json()['export_authorized'],r.text
    assert hashlib.sha256(r.json()['report_canonical'].encode()).hexdigest()==r.json()['report_digest']
    assert json.loads(r.json()['report_canonical'])==preview['report_data']
    assert preview['report_data']['checker_implementation'] in r.json()['report_canonical'] and 'verified-digest' not in r.json()['report_canonical']
    assert store.log_path.read_bytes()==before and env.adapter.n==calls


async def test_different_declared_validators_allow_descriptive_scores_but_no_hard_regression(running_product,monkeypatch):
    env=running_product;receipt,_=await quality_batch(env,monkeypatch,different_schema=True)
    preview=await report_preview(env,receipt);data=preview['report_data']
    assert data['regressions']==[] and all(c['comparison_status']=='not_comparable' for c in data['cases'])
    assert all('validators_differ' in c['incomparable_reasons'] for c in data['cases'])
