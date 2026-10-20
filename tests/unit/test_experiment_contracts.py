"""Bounded selections cannot manufacture funding, change sides or repeat cases."""
import copy
import pytest
from pydantic import ValidationError


def valid():
    return dict(schema_version=1,experiment_id='exp-'+'1'*16,expected_revision=0,project_revision=1,
        title='Confirmed input comparison',dataset_ref=dict(dataset_id='ds-'+'1'*16,revision=1,digest='1'*64,source_seq=1),
        case_refs=[dict(case_id='case-'+'1'*16,revision=1,digest='2'*64,source_seq=2)],
        left_agent_ref=dict(agent_id='a-'+'1'*32,version=1,sha256='3'*64),
        right_agent_ref=dict(agent_id='a-'+'1'*32,version=2,sha256='4'*64),concurrency=2,
        limits=dict(input_tokens=10000,output_tokens=1000,tool_calls=10,cost_microyuan=1000000))


@pytest.mark.parametrize('fault',['duplicate-case','eleven-cases','same-version','extra-authority','boolean-schema'],ids=str)
def test_invalid_pair_selections_are_not_definitions(fault):
    from pyharness.application.experiment_contracts import PrepareExperiment
    body=valid()
    if fault=='duplicate-case':body['case_refs']*=2
    if fault=='eleven-cases':
        body['case_refs']=[dict(body['case_refs'][0],case_id='case-'+format(i,'016x')) for i in range(11)]
    if fault=='same-version':body['right_agent_ref']=copy.deepcopy(body['left_agent_ref'])
    if fault=='extra-authority':body['created_by']='someone-else'
    if fault=='boolean-schema':body['schema_version']=True
    with pytest.raises(ValidationError):PrepareExperiment.model_validate(body)


def test_published_versions_and_case_order_are_fixed_without_execution_authority():
    from pyharness.application.experiment_contracts import PrepareExperiment,ConfirmExperimentPreparation
    body=valid();selected=PrepareExperiment.model_validate(body)
    assert selected.model_dump()==body
    with pytest.raises(ValidationError):ConfirmExperimentPreparation.model_validate(dict(body,confirmed=False,confirmation_digest='0'*64))
