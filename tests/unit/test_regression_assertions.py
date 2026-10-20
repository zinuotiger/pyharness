"""Finite data assertions: no expressions, coercion or success from missing evidence."""
import pytest
from pydantic import ValidationError


def evaluate(command, output='answer', status='completed', complete=True):
    from pyharness.application.regression_assertions import Assertion,evaluate_assertions
    return evaluate_assertions([Assertion.model_validate(command)],output=output,
        execution_status=status,output_complete=complete)


@pytest.mark.parametrize('command,output,verdict',[
    ({'kind':'output_contains','expected':'雪山'},'看到雪山','passed'),
    ({'kind':'output_contains','expected':'雪山'},'看到大海','failed'),
    ({'kind':'output_excludes','expected':'删除'},'仅预览','passed'),
    ({'kind':'output_equals','expected':'a'},'a\n','failed'),
    ({'kind':'json_pointer_equals','pointer':'/a~1b/~0c','expected':3},'{"a/b":{"~c":3}}','passed'),
    ({'kind':'json_pointer_equals','pointer':'/ok','expected':True},'{"ok":1}','failed'),
    ({'kind':'json_pointer_equals','pointer':'/ok','expected':None},'{}','failed'),
    ({'kind':'json_pointer_equals','pointer':'/ok','expected':1},'{"ok":1,"ok":2}','failed'),
    ({'kind':'json_pointer_equals','pointer':'/ok','expected':1},'not JSON','failed'),
])
def test_finite_checks_keep_exact_types_and_unambiguous_json(command,output,verdict):
    result=evaluate(command,output)
    assert result['verdict']==verdict and result['version']=='regression-assertions-v1'
    assert result['assertions'][0]['status']==verdict


@pytest.mark.parametrize('output,complete',[(None,True),('[withheld:private-content]',False),('answer',False),('x'*65537,True)],
    ids=['missing','withheld','incomplete','oversize'])
def test_absent_withheld_incomplete_or_oversize_output_cannot_pass(output,complete):
    assert evaluate({'kind':'output_excludes','expected':'danger'},output,complete=complete)['verdict']=='unknown'


@pytest.mark.parametrize('command',[
    {'kind':'python','expected':'__import__("os").system("echo wrong")'},
    {'kind':'output_contains','expected':'a','eval':'anything'},
    {'kind':'json_pointer_equals','pointer':'$.a[?(@.run())]','expected':1},
    {'kind':'json_pointer_equals','pointer':'/a~2b','expected':1},
    {'kind':'output_contains','expected':''},
    {'kind':'execution_status','expected':'cancelled'},
    {'kind':'output_equals','expected':True},
])
def test_expressions_bad_paths_empty_checks_and_type_confusion_are_rejected(command):
    with pytest.raises(ValidationError):evaluate(command)


def test_cancelled_and_missing_execution_evidence_are_never_success():
    command={'kind':'execution_status','expected':'completed'}
    assert evaluate(command,status='cancelled')['verdict']=='unknown'
    assert evaluate(command,status=None)['verdict']=='unknown'
    assert evaluate(command,status='failed')['verdict']=='failed'


@pytest.mark.parametrize('expected,output,verdict',[
    (1,'{"n":1.0}','passed'),(1.0,'{"n":1}','passed'),
    (True,'{"n":1.0}','failed'),(9007199254740993,'{"n":9007199254740992}','failed'),
])
def test_json_numeric_comparison_is_portable_without_boolean_or_large_integer_coercion(expected,output,verdict):
    assert evaluate(dict(kind='json_pointer_equals',pointer='/n',expected=expected),output)['verdict']==verdict


def test_json_beyond_checker_limits_is_unknown_rather_than_a_quality_failure():
    output='['+','.join(['0']*2049)+']'
    assert evaluate(dict(kind='json_pointer_equals',pointer='/0',expected=0),output)['verdict']=='unknown'
