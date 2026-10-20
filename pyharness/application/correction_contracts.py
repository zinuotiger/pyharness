"""Creator-authored supplementary instruction versions; never policy overrides."""
from typing import Annotated,Literal
from pydantic import Field,field_validator
from pyharness.application.document_contracts import VersionOne
from pyharness.application.execution_contracts import ExecutionReference
from pyharness.application.workspace_contracts import ProjectId
from pyharness.application.run_attempt_contracts import SubmissionKey

CorrectionId=Annotated[str,Field(pattern=r'^cr-[0-9a-f]{16}$')]


class SubmitCorrection(VersionOne):
    schema_version:Literal[1]=Field(...)
    correction_id:CorrectionId
    expected_revision:int=Field(ge=0,le=199)
    execution_ref:ExecutionReference
    text:str=Field(min_length=1,max_length=4000)

    @field_validator('text')
    @classmethod
    def nonblank(cls,value):
        if not value.strip():raise ValueError('supplement must not be blank')
        return value


class CorrectionRevision(VersionOne):
    schema_version:Literal[1]=Field(...)
    correction_id:CorrectionId
    revision:int=Field(ge=1,le=200)
    project_id:ProjectId
    project_revision:int=Field(ge=1)
    execution_ref:ExecutionReference
    text:str=Field(min_length=1,max_length=4000)
    created_by:str=Field(min_length=1,max_length=128)
    submission_key:SubmissionKey

    @field_validator('text')
    @classmethod
    def nonblank(cls,value):return SubmitCorrection.nonblank(value)
