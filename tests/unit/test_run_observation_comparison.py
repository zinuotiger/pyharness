"""Comparability and missing measurements must remain explicit."""
import copy
from types import SimpleNamespace as Event


def basis(attempt='ra-first'):
    return dict(schema_version=1,attempt_id=attempt,prerequisites=dict(task_ref={'digest':'task-fixed'},input_binding={'digest':'input-fixed'}),
        context=dict(document_refs=[],citation_refs=[],delivery_ref=None))


def test_distinct_attempt_ids_do_not_make_identical_fixed_inputs_incomparable():
    from pyharness.application.run_observation_comparison import fixed_basis
    a=fixed_basis(basis('ra-first'),{'output_schema':None},[])
    b=fixed_basis(basis('ra-second'),{'output_schema':None},[])
    assert a==b and a['available']


def test_input_and_validator_changes_are_independently_identified():
    from pyharness.application.run_observation_comparison import fixed_basis,alignment
    a=fixed_basis(basis(),{'output_schema':None},[])
    changed=basis();changed['prerequisites']['input_binding']['digest']='changed'
    b=fixed_basis(changed,{'output_schema':None},[])
    assert alignment(a,b)==['fixed_inputs_differ']
    c=fixed_basis(basis(),{'output_schema':{'type':'object'}},[])
    assert alignment(a,c)==['validators_differ']


def test_continued_or_corrected_history_is_not_silently_treated_as_fresh_input():
    from pyharness.application.run_observation_comparison import fixed_basis,alignment
    a=fixed_basis(basis(),{},[]);continued=dict(basis(),schema_version=2)
    assert alignment(a,fixed_basis(continued,{},[]))==['input_alignment_unavailable']
    assert alignment(a,fixed_basis(basis(),{},[{'execution_correction':{'text':'changed'}}]))==['input_alignment_unavailable']


def facts(usage=True,**changes):
    items=[Event(type='llm.request',payload={'model':'fixed'}),Event(type='llm.response',payload={'model':'fixed'})]
    if usage:items.append(Event(type='llm.usage',payload=dict(model='fixed',in_tokens=17,out_tokens=3,cost_est=None,input_unknown=False,estimated=False,**changes)))
    return items


def test_missing_price_is_null_while_actual_token_measurements_remain_known():
    from pyharness.application.run_observation_comparison import usage_metrics
    result=usage_metrics([facts()])
    assert result['input_tokens']==17 and result['output_tokens']==3 and result['cost_est'] is None


def test_absent_usage_and_unknown_input_do_not_become_zero():
    from pyharness.application.run_observation_comparison import usage_metrics
    assert usage_metrics([facts(False)])['input_tokens'] is None
    values=facts();values[-1].payload.update(in_tokens=0,input_unknown=True,cost_est=0)
    result=usage_metrics([values]);assert result['input_tokens'] is None and result['cost_est'] is None
    assert usage_metrics([[]])['input_tokens'] is None


def test_partial_or_cross_model_usage_is_not_presented_as_a_complete_total():
    from pyharness.application.run_observation_comparison import usage_metrics
    values=facts();values.append(Event(type='llm.request',payload={'model':'fallback'}))
    assert usage_metrics([values])['input_tokens'] is None
    values=facts();values[-1].payload['model']='foreign'
    assert usage_metrics([values])['input_tokens'] is None


def test_two_journals_are_summed_once_and_unknown_child_cost_propagates():
    from pyharness.application.run_observation_comparison import usage_metrics
    a=facts();a[-1].payload['cost_est']=0.25
    value=usage_metrics([a,facts()])
    assert value['input_tokens']==34 and value['cost_est'] is None and value['usage_records']==2
