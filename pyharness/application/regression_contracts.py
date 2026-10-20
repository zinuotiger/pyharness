"""Explicit regression selections; source references never grant execution rights."""
from typing import Annotated,Literal
from pydantic import Field,field_validator,model_validator
from pyharness.application.document_contracts import VersionOne
from pyharness.application.execution_contracts import ExecutionId,ExecutionReference,ExecutionSessionId
from pyharness.application.delivery_contracts import DeliveryAcceptanceReference
from pyharness.application.workspace_contracts import Digest,ProjectId
from pyharness.application.workspace_manifest import StrictModel
from pyharness.application.regression_assertions import Assertion


class PreviewFailure(VersionOne):
    execution_id:ExecutionId


class FailureOrigin(StrictModel):
    tenant_id:str=Field(min_length=1,max_length=128)
    subject_ref:str=Field(min_length=1,max_length=128)
    execution_ref:ExecutionReference
    session_id:ExecutionSessionId
    observed_seq:int=Field(ge=2)
    prefix_digest:Digest
    failure_kind:Literal['execution_failed','business_failed']
    acceptance_ref:DeliveryAcceptanceReference|None=None

    @model_validator(mode='after')
    def failure_basis(self):
        if (self.failure_kind=='business_failed')!=(self.acceptance_ref is not None):
            raise ValueError('business failure requires exact independent acceptance')
        return self


CaseId=Annotated[str,Field(pattern=r'^case-[0-9a-f]{16}$')]
DatasetId=Annotated[str,Field(pattern=r'^ds-[0-9a-f]{16}$')]
TextState=Literal['available','withheld','truncated','unavailable']


class CandidateEvidence(VersionOne):
    project_id:ProjectId
    origin:FailureOrigin
    observed_input_text:str|None=Field(max_length=2048)
    observed_input_state:TextState
    observed_output_text:str|None=Field(max_length=2048)
    observed_output_state:TextState
    root_tool_call_count:int=Field(ge=0,le=10000)
    policy:Literal['bounded-public-regression-v1']='bounded-public-regression-v1'
    assertion_version:Literal['regression-assertions-v1']='regression-assertions-v1'


def public_manual_text(value):
    from pyharness.application.source_snapshot import check_content
    from pyharness.core.context_observation import public_request
    from pyharness.errors import PyHError
    try:check_content(value.encode('utf-8'))
    except (UnicodeError,PyHError):raise ValueError('private or invalid regression text')
    view=public_request(dict(model='manual-regression-text',messages=[dict(role='user',content=value)]),layer='adapter_input')
    if view['withheld'] or view['truncated'] or '[withheld:' in value:
        raise ValueError('regression text requires an explicit usable public value')
    return value


def schema_manual_text(value):
    """Stable stored shape; today's confidentiality policy is an access gate."""
    try:value.encode('utf-8')
    except UnicodeError:raise ValueError('invalid regression text')
    if any(ord(c)<32 and c not in '\t\r\n' for c in value):
        raise ValueError('invalid regression text')
    return value


def public_evidence_text_allowed(value):
    from pyharness.application.source_snapshot import check_content
    from pyharness.errors import PyHError
    if value is None:return True
    try:check_content(value.encode('utf-8'))
    except (UnicodeError,PyHError):return False
    return True


def current_regression_evidence(evidence):
    from pyharness.errors import raise_code
    if not all(public_evidence_text_allowed(evidence[key]) for key in ('observed_input_text','observed_output_text')):
        raise_code('GRD-403',reason='regression_evidence_not_public')


def current_regression_content(command,*,reading=False):
    from pyharness.errors import raise_code
    value=command.model_dump() if hasattr(command,'model_dump') else command
    values=[value['title']]
    if 'input_text' in value:
        values.append(value['input_text'])
        for item in value['assertions']:
            if type(item['expected']) is str:values.append(item['expected'])
            if item.get('pointer') is not None:values.append(item['pointer'])
    try:
        for text in values:public_manual_text(text)
    except ValueError:
        raise_code('GRD-403' if reading else 'EVT-100',reason='regression_content_not_public' if reading else 'regression_content_rejected')


class ConfirmRegression(VersionOne):
    project_revision:int=Field(ge=1)
    expected_revision:int=Field(ge=0,le=199)
    title:str=Field(min_length=1,max_length=100)
    confirmed:bool

    @field_validator('confirmed')
    @classmethod
    def confirmation(cls,value):
        if value is not True:raise ValueError('explicit confirmation required')
        return value

    @field_validator('title')
    @classmethod
    def public_title(cls,value):return schema_manual_text(value)


class SaveCase(ConfirmRegression):
    case_id:CaseId
    origin:FailureOrigin
    candidate_digest:Digest
    input_text:str=Field(min_length=1,max_length=2048)
    assertions:list[Assertion]=Field(min_length=1,max_length=16)

    @field_validator('input_text')
    @classmethod
    def public_input(cls,value):return schema_manual_text(value)

    @field_validator('assertions')
    @classmethod
    def public_expectations(cls,values):
        for item in values:
            if type(item.expected) is str:schema_manual_text(item.expected)
            if item.pointer is not None:schema_manual_text(item.pointer)
        return values


class CaseReference(StrictModel):
    case_id:CaseId
    revision:int=Field(ge=1,le=200)
    digest:Digest
    source_seq:int=Field(ge=1)


class DatasetReference(StrictModel):
    dataset_id:DatasetId
    revision:int=Field(ge=1,le=200)
    digest:Digest
    source_seq:int=Field(ge=1)


class SaveDataset(ConfirmRegression):
    dataset_id:DatasetId
    case_refs:list[CaseReference]=Field(min_length=1,max_length=32)

    @field_validator('case_refs')
    @classmethod
    def unique_cases(cls,values):
        if len({v.case_id for v in values})!=len(values):raise ValueError('duplicate case identity')
        return values


class DatasetImportSource(StrictModel):
    dataset_ref:DatasetReference
    bundle_digest:Digest


class ImportedSaveDataset(SaveDataset):
    import_source:DatasetImportSource
