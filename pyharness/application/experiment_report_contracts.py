"""Finite report data and exact original journal-prefix selections."""
from typing import Annotated,Literal
from pydantic import Field,model_validator,field_validator
from pyharness.application.document_contracts import VersionOne
from pyharness.application.workspace_manifest import StrictModel
from pyharness.application.workspace_contracts import Digest,ProjectId
from pyharness.application.experiment_run_contracts import ExperimentRunReference
from pyharness.application.experiment_contracts import ExperimentReference,explicit_true
from pyharness.application.regression_contracts import CaseReference,DatasetReference
from pyharness.application.execution_contracts import ExecutionReference,ExecutionSessionId,AgentVersionReference
from pyharness.application.run_attempt_contracts import SubmissionKey
from pyharness.application.task_graph_run_contracts import TaskGraphRunReference
from pyharness.application.task_graph_contracts import TaskGraphNodeId

ExperimentReportId=Annotated[str,Field(pattern=r'^epr-[0-9a-f]{16}$')]
Status=Literal['completed','failed','cancelled','unknown','interrupted','not_dispatched','nonterminal']


class ControlPrefix(StrictModel):
    observed_seq:int=Field(ge=2)
    prefix_digest:Digest


class ReportJournalPrefix(ControlPrefix):
    session_id:ExecutionSessionId


class ExperimentReportBasis(StrictModel):
    control:ControlPrefix
    nodes:dict[TaskGraphNodeId,list[ReportJournalPrefix]|None]=Field(min_length=2,max_length=20)

    @model_validator(mode='after')
    def finite_prefixes(self):
        for values in self.nodes.values():
            if values is not None and (not 1<=len(values)<=32 or len({v.session_id for v in values})!=len(values)):
                raise ValueError('bounded unique root and child prefixes required')
        return self


class PreviewExperimentReport(VersionOne):
    schema_version:Literal[1]=Field(...)
    report_id:ExperimentReportId
    experiment_run_ref:ExperimentRunReference
    basis:ExperimentReportBasis|None=None


class SaveExperimentReport(VersionOne):
    schema_version:Literal[1]=Field(...)
    report_id:ExperimentReportId
    experiment_run_ref:ExperimentRunReference
    basis:ExperimentReportBasis
    report_digest:Digest
    confirmed:Literal[True]
    confirmation_digest:Digest
    _confirmation=field_validator('confirmed',mode='before')(explicit_true)


class PreviewExperimentReportExport(VersionOne):
    schema_version:Literal[1]=Field(...)


class ConfirmExperimentReportExport(PreviewExperimentReportExport):
    confirmed:Literal[True]
    confirmation_digest:Digest
    _confirmation=field_validator('confirmed',mode='before')(explicit_true)


class FiniteAssertionResult(StrictModel):
    kind:Literal['output_contains','output_excludes','output_equals','json_pointer_equals','execution_status']
    status:Literal['passed','failed','unknown']
    reason:Literal['evidence_unavailable','terminal_status','finite_comparison','checker_limit']


class FiniteQuality(StrictModel):
    version:Literal['regression-assertions-v1']
    verdict:Literal['passed','failed','unknown']
    assertions:list[FiniteAssertionResult]=Field(min_length=1,max_length=16)


class ReportUsage(StrictModel):
    request_records:int=Field(ge=0)
    usage_records:int=Field(ge=0)
    usage_complete:bool
    input_tokens:int|None=Field(ge=0)
    output_tokens:int|None=Field(ge=0)
    cost_est:float|None=Field(ge=0,allow_inf_nan=False)
    usage_precision:Literal['estimated','recorded','unavailable']
    price_status:Literal['recorded_estimate','unavailable']


class ReportComparisonBasis(StrictModel):
    available:bool
    input_digest:Digest|None
    validator_digest:Digest|None


class ReportMeasurement(StrictModel):
    node_id:TaskGraphNodeId
    agent_ref:AgentVersionReference
    execution_ref:ExecutionReference|None
    execution_status:Status
    unknown_effects:bool
    output_state:Literal['available','withheld','truncated','unavailable']
    output_digest:Digest|None
    quality:FiniteQuality
    basis:ReportComparisonBasis
    usage:ReportUsage
    business_acceptance:Literal['accepted','rejected','invalidated','not_evaluated']


class ReportCaseComparison(StrictModel):
    case_ref:CaseReference
    left:ReportMeasurement
    right:ReportMeasurement
    comparison_status:Literal['aligned','not_comparable']
    incomparable_reasons:list[Literal['input_alignment_unavailable','fixed_inputs_differ','validators_differ']]=Field(max_length=3)


class ExecutionCounts(StrictModel):
    total:int=Field(ge=0,le=20)
    completed:int=Field(ge=0,le=20)
    failed:int=Field(ge=0,le=20)
    cancelled:int=Field(ge=0,le=20)
    unknown:int=Field(ge=0,le=20)
    interrupted:int=Field(ge=0,le=20)
    not_dispatched:int=Field(ge=0,le=20)
    nonterminal:int=Field(ge=0,le=20)


class QualityCounts(StrictModel):
    total:int=Field(ge=0,le=20)
    evaluable:int=Field(ge=0,le=20)
    passed:int=Field(ge=0,le=20)
    failed:int=Field(ge=0,le=20)
    unknown:int=Field(ge=0,le=20)


class BusinessCounts(StrictModel):
    total:int=Field(ge=0,le=20)
    evaluated:int=Field(ge=0,le=20)
    accepted:int=Field(ge=0,le=20)
    rejected:int=Field(ge=0,le=20)
    invalidated:int=Field(ge=0,le=20)
    not_evaluated:int=Field(ge=0,le=20)


class ReportRegression(StrictModel):
    case_ref:CaseReference
    kind:Literal['quality_regression','execution_regression']


class ExperimentReportData(VersionOne):
    experiment_run_ref:ExperimentRunReference
    experiment_ref:ExperimentReference
    graph_run_ref:TaskGraphRunReference|None
    dataset_ref:DatasetReference
    dataset_total:int=Field(ge=1,le=32)
    selected_count:int=Field(ge=1,le=10)
    excluded_count:int=Field(ge=0,le=31)
    basis:ExperimentReportBasis
    checker_version:Literal['regression-assertions-v1']
    checker_implementation:Digest
    comparison_contract:Literal['fixed-experiment-input-and-declared-validator-v1']='fixed-experiment-input-and-declared-validator-v1'
    status_basis:Literal['fixed-recorded-prefix-not-current-process-liveness']='fixed-recorded-prefix-not-current-process-liveness'
    cases:list[ReportCaseComparison]=Field(min_length=1,max_length=10)
    regressions:list[ReportRegression]=Field(max_length=20)
    execution_counts:ExecutionCounts
    quality_counts:QualityCounts
    business_counts:BusinessCounts
    release_authorized:Literal[False]=False

    @model_validator(mode='after')
    def finite_denominators(self):
        if self.selected_count!=len(self.cases) or self.dataset_total!=self.selected_count+self.excluded_count:
            raise ValueError('report selected denominator')
        from pyharness.application.experiment_report_evaluation import report_counts
        counts=report_counts([getattr(c,side).model_dump() for c in self.cases for side in ('left','right')])
        if any(getattr(self,key).model_dump()!=value for key,value in counts.items()):raise ValueError('report denominators differ')
        return self


class ExperimentReport(VersionOne):
    report_id:ExperimentReportId
    revision:Literal[1]=1
    project_id:ProjectId
    project_revision:int=Field(ge=1)
    created_by:str=Field(min_length=1,max_length=128)
    submission_key:SubmissionKey
    selection:SaveExperimentReport
    report_data:ExperimentReportData

    @model_validator(mode='after')
    def exact_selected_report(self):
        from pyharness.application.workspace_store import canonical,digest_of
        if (self.report_id!=self.selection.report_id or self.report_data.experiment_run_ref!=self.selection.experiment_run_ref or
                self.report_data.basis!=self.selection.basis or digest_of(canonical(self.report_data.model_dump()))!=self.selection.report_digest):
            raise ValueError('confirmed fixed report differs')
        return self
