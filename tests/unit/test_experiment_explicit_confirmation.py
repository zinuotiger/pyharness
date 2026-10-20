"""A numeric one must never normalize into explicit execution/export consent."""
import pytest
from pydantic import ValidationError
from pyharness.application.experiment_run_contracts import StartExperimentRun,ConfirmExperimentResumption,ExperimentRunReference
from pyharness.application.experiment_report_contracts import SaveExperimentReport,ConfirmExperimentReportExport
from pyharness.application.experiment_contracts import PreparationReference

H='1'*64
RUN=dict(experiment_run_id='er-'+'1'*16,revision=1,digest=H,source_seq=2)
EXP=dict(experiment_id='exp-'+'1'*16,revision=1,digest=H,source_seq=1)
BASIS=dict(control=dict(observed_seq=2,prefix_digest=H),nodes=dict(c0_left=None,c0_right=None))
COMMANDS=[
    (StartExperimentRun,dict(schema_version=1,experiment_run_id=RUN['experiment_run_id'],experiment_ref=EXP,
        budget_grant=dict(input_tokens=1,output_tokens=1,tool_calls=1,cost_microyuan=1),confirmation_digest=H)),
    (ConfirmExperimentResumption,dict(schema_version=1,experiment_run_ref=RUN,confirmation_digest=H)),
    (SaveExperimentReport,dict(schema_version=1,report_id='epr-'+'1'*16,experiment_run_ref=RUN,basis=BASIS,report_digest=H,confirmation_digest=H)),
    (ConfirmExperimentReportExport,dict(schema_version=1,confirmation_digest=H)),
]


@pytest.mark.parametrize('model,body',COMMANDS,ids=['run','resume','report','export'])
@pytest.mark.parametrize('invalid',[False,1,'true',None],ids=['false','integer-one','text-true','null'])
def test_consent_requires_actual_boolean_true(model,body,invalid):
    assert model.model_validate(dict(body,confirmed=True)).confirmed is True
    with pytest.raises(ValidationError):model.model_validate(dict(body,confirmed=invalid))


@pytest.mark.parametrize('model,body',[(ExperimentRunReference,RUN),
    (PreparationReference,dict(preparation_id='prp-'+'1'*16,revision=1,digest=H,source_seq=1))],ids=['run-ref','preparation-ref'])
def test_fixed_revision_reference_rejects_boolean_one(model,body):
    with pytest.raises(ValidationError):model.model_validate(dict(body,revision=True))
