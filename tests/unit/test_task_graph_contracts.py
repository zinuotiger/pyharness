"""S09 versioned DAG: exact bounded references, no executable instructions."""
import copy
import pytest
from pydantic import ValidationError

def diamond():
    ref=dict(product_task_id='pt-'+'1'*16,revision=1,digest='a'*64,source_seq=3)
    agent=dict(agent_id='a-'+'2'*32,version=1,sha256='b'*64)
    def node(ident,parents):return dict(node_id=ident,task_ref=copy.deepcopy(ref),agent_ref=copy.deepcopy(agent),depends_on=parents)
    return dict(schema_version=1,graph_id='tg-'+'3'*16,expected_revision=0,project_revision=1,title='Diamond',nodes=[node('A',[]),node('B',['A']),node('C',['A']),node('D',['B','C'])])

def test_diamond_is_valid_and_has_deterministic_topology():
    from pyharness.application.task_graph_contracts import TaskGraphCommand,topological_ids
    command=TaskGraphCommand.model_validate(diamond())
    assert topological_ids(command.nodes)==['A','B','C','D']
    assert command.limits.cost_microyuan==20000000 and command.concurrency==2
    assert 'created_by' not in TaskGraphCommand.model_fields

@pytest.mark.parametrize('case',['cycle','self','missing','duplicate-node','duplicate-edge'])
def test_invalid_graph_has_no_ambiguous_ready_set(case):
    from pyharness.application.task_graph_contracts import TaskGraphCommand
    data=diamond()
    if case=='cycle':data['nodes'][0]['depends_on']=['D']
    if case=='self':data['nodes'][0]['depends_on']=['A']
    if case=='missing':data['nodes'][1]['depends_on']=['foreign']
    if case=='duplicate-node':data['nodes'][1]['node_id']='A'
    if case=='duplicate-edge':data['nodes'][3]['depends_on']=['B','B']
    with pytest.raises(ValidationError):TaskGraphCommand.model_validate(data)

@pytest.mark.parametrize('case',['boolean-version','boolean-concurrency','unbounded-nodes','excess-concurrency','zero-budget','float-money','forged-actor','tool-code'])
def test_graph_schema_rejects_forged_authority_or_unbounded_input(case):
    from pyharness.application.task_graph_contracts import TaskGraphCommand
    data=diamond()
    if case=='boolean-version':data['schema_version']=True
    if case=='boolean-concurrency':data['concurrency']=True
    if case=='unbounded-nodes':data['nodes']=[dict(copy.deepcopy(data['nodes'][0]),node_id='n'+str(i)) for i in range(21)]
    if case=='excess-concurrency':data['concurrency']=5
    if case=='zero-budget':data['limits']=dict(cost_microyuan=0)
    if case=='float-money':data['limits']=dict(cost_microyuan=1.5)
    if case=='forged-actor':data['created_by']='admin'
    if case=='tool-code':data['nodes'][0]['command']='exec arbitrary'
    with pytest.raises(ValidationError):TaskGraphCommand.model_validate(data)

def test_money_limits_and_references_are_exact_without_json_float_ambiguity():
    from pyharness.application.task_graph_contracts import TaskGraphCommand,TaskGraphRevision,TaskGraphReference
    command=TaskGraphCommand.model_validate(diamond())
    value=command.model_dump();value.pop('expected_revision')
    revision=TaskGraphRevision(**value,project_id='p-'+'4'*16,revision=1,created_by='local-operator')
    assert revision.revision==1 and type(revision.limits.cost_microyuan) is int
    reference=TaskGraphReference(graph_id=revision.graph_id,revision=1,digest='c'*64,source_seq=5)
    with pytest.raises(ValidationError):TaskGraphReference.model_validate(dict(reference.model_dump(),source_seq=True))
