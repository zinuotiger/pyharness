"""Fixed paired inputs are data until an independent, funded run is confirmed."""
import copy
import pytest
from tests.unit.test_product_execution_start import running_product
from tests.unit.test_regression_transfer import dataset
from tests.unit.test_regression_dataset_api import BASE,reference,save_case

EID='exp-'+'1'*16
LIMITS=dict(input_tokens=2000000,output_tokens=100000,tool_calls=400,cost_microyuan=100000000)


async def selection(env):
    body,case,collection=await dataset(env)
    platform=env.app.service.platform
    other=platform.save_agent(dict(name='Published comparison version'))
    other=platform.publish_agent(other['agent_id'])
    return body,case,dict(schema_version=1,experiment_id=EID,expected_revision=0,project_revision=1,
        title='固定输入对照',dataset_ref=reference(collection,'dataset_id'),case_refs=[reference(case,'case_id')],
        left_agent_ref=env.selection['agent_ref'],right_agent_ref={k:other[k] for k in ('agent_id','version','sha256')},
        concurrency=2,limits=LIMITS)


async def prepare_preview(env,body):
    return await env.client.post(BASE+'/experiment-preparation-previews',json=body)


async def test_experiment_preparation_preview_has_exact_manual_input_and_writes_nothing(running_product):
    env=running_product;_,case,body=await selection(env);store=env.app.service.projects.store
    before=store.log_path.read_bytes();calls=env.adapter.n
    r=await prepare_preview(env,body);assert r.status_code==200,r.text
    value=r.json()
    assert value['status']=='confirmation_required' and not value['persisted'] and not value['execution_authorized']
    assert value['dataset_total']==value['selected_count']==1 and value['excluded_count']==0
    assert value['tasks'][0]['command']['objective']==case['selection']['input_text']
    assert value['case_refs']==body['case_refs'] and len(value['node_drafts'])==2
    assert len({n['task_id'] for n in value['node_drafts']})==1
    assert {n['side'] for n in value['node_drafts']}=={'left','right'}
    assert store.log_path.read_bytes()==before and env.adapter.n==calls


async def test_confirmed_preparation_reuses_original_task_graph_writer_and_original_key(running_product):
    env=running_product;_,case,body=await selection(env)
    r=await prepare_preview(env,body);assert r.status_code==200,r.text
    command=dict(body,confirmation_digest=r.json()['confirmation_digest'],confirmed=True)
    store=env.app.service.projects.store;calls=env.adapter.n
    one=await env.client.post(BASE+'/experiments',json=command,headers={'Idempotency-Key':'prepare-paired-experiment'})
    assert one.status_code==200,one.text
    before=store.log_path.read_bytes()
    two=await env.client.post(BASE+'/experiments',json=command,headers={'Idempotency-Key':'prepare-paired-experiment'})
    assert two.status_code==200 and two.json()==one.json(),two.text
    saved=one.json();assert saved['revision']==1 and not saved['execution_authorized']
    resources,_,_=await store._execution_read_state()
    assert sum('experiment_definition' in row for row in resources.values())==1
    graph=resources[saved['graph_ref']['graph_id']]['task_graph']
    assert len(graph['nodes'])==2 and all(not n['depends_on'] for n in graph['nodes'])
    assert graph['nodes'][0]['task_ref']==graph['nodes'][1]['task_ref']
    task=resources[graph['nodes'][0]['task_ref']['product_task_id']]['product_task']
    assert task['objective']==case['selection']['input_text']
    assert saved['selection']['case_refs']==body['case_refs']
    assert env.adapter.n==calls and store.log_path.read_bytes()==before
    current=await env.client.get(BASE+'/experiments/'+EID,params={'revision':1})
    assert current.status_code==200 and current.json()==saved,current.text


async def test_unconfirmed_changed_plan_and_foreign_case_prepare_no_extra_resources(running_product):
    env=running_product;_,_,body=await selection(env);store=env.app.service.projects.store
    r=await prepare_preview(env,body);assert r.status_code==200,r.text
    command=dict(body,confirmation_digest=r.json()['confirmation_digest'],confirmed=True)
    before=store.log_path.read_bytes();calls=env.adapter.n
    for changes in [dict(confirmed=False),dict(confirmation_digest='0'*64),dict(title='password=never-save-this')]:
        r=await env.client.post(BASE+'/experiments',json=dict(command,**changes),headers={'Idempotency-Key':'invalid-prepare'})
        assert r.status_code in (403,409,422),r.text
        assert r.headers['cache-control']=='no-store' and store.log_path.read_bytes()==before
    bad=copy.deepcopy(body);bad['case_refs'][0]['digest']='0'*64
    r=await prepare_preview(env,bad)
    assert r.status_code in (403,409,422),r.text
    assert store.log_path.read_bytes()==before and env.adapter.n==calls


async def test_experiment_stays_on_old_case_when_library_case_is_edited(running_product):
    env=running_product;case_body,case,body=await selection(env)
    r=await prepare_preview(env,body);assert r.status_code==200,r.text
    r=await env.client.post(BASE+'/experiments',json=dict(body,confirmation_digest=r.json()['confirmation_digest'],confirmed=True),headers={'Idempotency-Key':'frozen-paired-experiment'})
    assert r.status_code==200,r.text
    original=r.json();calls=env.adapter.n
    newer=await save_case(env,dict(case_body,expected_revision=1,title='后来编辑的用例'),'case-after-experiment')
    assert newer.status_code==200,newer.text
    r=await env.client.get(BASE+'/experiments/'+EID,params={'revision':1})
    assert r.status_code==200 and r.json()==original,r.text
    assert r.json()['selection']['case_refs']==[reference(case,'case_id')] and env.adapter.n==calls


async def prepared(env,body,key):
    r=await prepare_preview(env,body);assert r.status_code==200,r.text
    command=dict(body,confirmation_digest=r.json()['confirmation_digest'],confirmed=True)
    r=await env.client.post(BASE+'/experiments',json=command,headers={'Idempotency-Key':key})
    assert r.status_code==200,r.text
    return command,r.json()


async def test_definition_edit_keeps_old_experiment_and_explicit_subset_denominators(running_product):
    env=running_product;case_body,case,body=await selection(env)
    command,original=await prepared(env,body,'experiment-version-one')
    _,new=await prepared(env,dict(body,expected_revision=1,title='明确第二版对照'),'experiment-version-two')
    assert new['revision']==2 and new['digest']!=original['digest']
    r=await env.client.get(BASE+'/experiments/'+EID,params={'revision':1})
    assert r.status_code==200 and r.json()==original,r.text
    key=await env.client.get(BASE+'/experiment-submissions/experiment-version-one')
    assert key.status_code==200 and key.json()['receipt']==original,key.text
    second=await save_case(env,dict(case_body,case_id='case-'+'2'*16,input_text='A separate explicitly confirmed manual objective.'),'other-manual-case')
    assert second.status_code==200,second.text
    collection=await env.client.post(BASE+'/regression-datasets',json=dict(schema_version=1,dataset_id='ds-'+'2'*16,
        expected_revision=0,project_revision=1,title='Two cases',confirmed=True,
        case_refs=[reference(case,'case_id'),reference(second.json(),'case_id')]),headers={'Idempotency-Key':'two-case-library'})
    assert collection.status_code==200,collection.text
    r=await prepare_preview(env,dict(body,experiment_id='exp-'+'2'*16,dataset_ref=reference(collection.json(),'dataset_id'),case_refs=[reference(second.json(),'case_id')]))
    assert r.status_code==200,r.text
    assert r.json()['dataset_total']==2 and r.json()['selected_count']==r.json()['excluded_count']==1
    assert r.json()['tasks'][0]['command']['objective']=='A separate explicitly confirmed manual objective.'


async def test_lost_task_acknowledgement_resumes_preparation_from_original_key_without_duplicate_task(running_product,monkeypatch):
    from pyharness.errors import raise_code
    env=running_product;_,_,body=await selection(env);store=env.app.service.projects.store
    r=await prepare_preview(env,body);assert r.status_code==200,r.text
    command=dict(body,confirmation_digest=r.json()['confirmation_digest'],confirmed=True)
    save_task=store.save_product_task;lost=[]
    async def lose(*args,**kwargs):
        result=await save_task(*args,**kwargs)
        if not lost:
            lost.append(True);raise_code('PERS-202',reason='synthetic_task_ack_lost')
        return result
    calls=env.adapter.n
    with monkeypatch.context() as patch:
        patch.setattr(store,'save_product_task',lose)
        r=await env.client.post(BASE+'/experiments',json=command,headers={'Idempotency-Key':'lost-prepare-key'})
        assert r.status_code==503,r.text
    r=await env.client.get(BASE+'/experiment-submissions/lost-prepare-key')
    assert r.status_code==200 and r.json()['status']=='preparing' and not r.json()['execution_authorized'],r.text
    r=await env.client.post(BASE+'/experiments',json=command,headers={'Idempotency-Key':'lost-prepare-key'})
    assert r.status_code==200,r.text
    resources,_,events=await store._execution_read_state()
    assert sum('product_task' in row for row in resources.values())==2
    assert sum('experiment_preparation' in row for row in resources.values())==1
    assert sum('experiment_definition' in row for row in resources.values())==1 and env.adapter.n==calls


async def test_cold_experiment_read_creates_no_writer_and_source_withdrawal_denies_original_copy(running_product):
    from pyharness import cli
    from pyharness.desktop.app import DesktopApp
    from tests.unit.test_source_grants import GID
    import httpx
    env=running_product;_,_,body=await selection(env);_,original=await prepared(env,body,'cold-experiment')
    cfg=env.app.service.ctx.settings;calls=env.adapter.n;root=env.app.service.storage_root
    await env.app.service.shutdown();journals={p:p.read_bytes() for p in root.rglob('*.jsonl')}
    cold=DesktopApp(cli.assemble_ctx(cfg))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=cold.api),base_url='http://127.0.0.1',headers={'X-PyHarness-Token':cold._api_token}) as client:
            r=await client.get(BASE+'/experiments/'+EID,params={'revision':1})
            assert r.status_code==200 and r.json()==original,r.text
            assert cold.service.projects.store._store is None and not cold.service._engines and not cold.service._queues
            assert all(p.read_bytes()==raw for p,raw in journals.items()) and env.adapter.n==calls
            r=await client.post('/api/v1/source-grants/'+GID+'/revoke',json=dict(expected_revision=1),headers={'Idempotency-Key':'withdraw-experiment-source'})
            assert r.status_code==200,r.text
            r=await client.get(BASE+'/experiments/'+EID)
            assert r.status_code==403 and r.headers['cache-control']=='no-store' and 'record_canonical' not in r.text,r.text
    finally:await cold.service.shutdown()


async def test_new_privacy_rule_restricts_experiment_title_without_corrupting_old_control_history(running_product,monkeypatch):
    from pyharness import config
    env=running_product;_,_,body=await selection(env);_,original=await prepared(env,body,'private-title-experiment')
    store=env.app.service.projects.store;before=store.log_path.read_bytes();redact=config.redact
    with monkeypatch.context() as patch:
        patch.setattr(config,'redact',lambda value:redact(value).replace(body['title'],'[redacted]'))
        r=await env.client.get('/api/v1/projects');assert r.status_code==200,r.text
        r=await env.client.get(BASE+'/experiments/'+EID)
        assert r.status_code==403 and 'record_canonical' not in r.text,r.text
        assert not store._failed and store.log_path.read_bytes()==before
    r=await env.client.get(BASE+'/experiments/'+EID)
    assert r.status_code==200 and r.json()==original,r.text


async def test_common_audit_accepts_case_dataset_preparation_and_experiment_metadata(running_product):
    env=running_product;_,_,body=await selection(env);await prepared(env,body,'audited-experiment')
    store=env.app.service.projects.store;before=store.log_path.read_bytes();calls=env.adapter.n
    r=await env.client.get('/api/v1/workspace/audit',params={'pagination_version':1,'limit':200})
    assert r.status_code==200,r.text
    kinds={v['resource_kind'] for v in r.json()['events']}
    assert {'regression_case','regression_dataset','experiment_preparation','experiment_definition'}<=kinds
    assert store.log_path.read_bytes()==before and env.adapter.n==calls
