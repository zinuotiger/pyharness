"""Explicit write scope and exact merge/apply commands; read grants stay read-only."""
import re
from typing import Annotated,Literal
from pydantic import Field,field_validator,model_validator
from pyharness.application.document_contracts import VersionOne
from pyharness.application.workspace_manifest import StrictModel
from pyharness.application.workspace_contracts import Digest
from pyharness.application.delivery_contracts import DeliveryOutputReference,DeliveryPackageReference,DeliveryAcceptanceReference
from pyharness.application.source_selection import selection_name_reason
from pyharness.application.source_grants import parse_expiry

WriteGrantId=Annotated[str,Field(pattern=r'^wg-[0-9a-f]{16}$')]
MergePlanId=Annotated[str,Field(pattern=r'^mp-[0-9a-f]{16}$')]
WriteApplicationId=Annotated[str,Field(pattern=r'^wa-[0-9a-f]{16}$')]
MergeExportId=Annotated[str,Field(pattern=r'^we-[0-9a-f]{16}$')]


def relative_name(value):
    if (selection_name_reason(value,set()) or len(value)>1024 or
            any(p.endswith(('.', ' ')) or re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?',p) for p in value.split('/'))):
        raise ValueError('invalid or sensitive writeback path')
    return value


class WriteGrantReference(StrictModel):
    grant_id:WriteGrantId
    revision:int=Field(ge=1,le=2)
    digest:Digest
    source_seq:int=Field(ge=1)


class MergePlanReference(StrictModel):
    plan_id:MergePlanId
    revision:int=Field(ge=1,le=1)
    digest:Digest
    source_seq:int=Field(ge=1)


class IssueWriteGrant(VersionOne):
    schema_version:Literal[1]=Field(...)
    grant_id:WriteGrantId
    project_revision:int=Field(ge=1)
    root_path:str=Field(min_length=1,max_length=1024)
    recipient_username:str=Field(min_length=1,max_length=128)
    paths:list[Annotated[str,Field(min_length=1,max_length=1024)]]=Field(min_length=1,max_length=100)
    actions:list[Literal['replace','create','delete']]=Field(min_length=1,max_length=3)
    expires_at:str=Field(min_length=1,max_length=40)

    @field_validator('expires_at')
    @classmethod
    def expiry(cls,value):parse_expiry(value);return value

    @field_validator('paths')
    @classmethod
    def exact_paths(cls,values):
        for value in values:relative_name(value)
        if len({p.casefold() for p in values})!=len(values):raise ValueError('duplicate writeback path')
        return values

    @field_validator('actions')
    @classmethod
    def unique_actions(cls,values):
        if len(set(values))!=len(values):raise ValueError('duplicate writeback action')
        return values


class RevokeWriteGrant(VersionOne):
    schema_version:Literal[1]=Field(...)
    expected_revision:int=Field(ge=1,le=1)


class DeletedFileReference(StrictModel):
    session_id:str=Field(pattern=r'^s-[A-Za-z0-9_-]{1,120}$')
    source_seq:int=Field(ge=1)
    record_digest:Digest
    path:str=Field(min_length=1,max_length=1024)


class MergeFileSelection(StrictModel):
    target_path:str=Field(min_length=1,max_length=1024)
    input_path:str|None=Field(default=None,min_length=1,max_length=1024)
    output_ref:DeliveryOutputReference|None=None
    deletion_ref:DeletedFileReference|None=None

    @model_validator(mode='after')
    def exact_names(self):
        relative_name(self.target_path)
        if self.input_path is not None:relative_name(self.input_path)
        if (self.output_ref is None)==(self.deletion_ref is None):raise ValueError('select an actual output or deletion receipt')
        if self.deletion_ref and self.deletion_ref.path!=self.input_path:raise ValueError('deletion requires the exact baseline path')
        return self


class MergePreviewCommand(VersionOne):
    schema_version:Literal[1]=Field(...)
    project_revision:int=Field(ge=1)
    grant_ref:WriteGrantReference
    package_ref:DeliveryPackageReference
    acceptance_ref:DeliveryAcceptanceReference
    files:list[MergeFileSelection]=Field(min_length=1,max_length=100)

    @model_validator(mode='after')
    def distinct_targets(self):
        if len({f.target_path.casefold() for f in self.files})!=len(self.files):raise ValueError('duplicate merge target')
        if sum(f.output_ref.size_bytes for f in self.files if f.output_ref)>10*1024*1024:
            raise ValueError('delivery merge byte limit')
        return self


class SaveMergePlan(MergePreviewCommand):
    plan_id:MergePlanId
    confirmation_digest:Digest


class ApplyMergePlan(VersionOne):
    schema_version:Literal[1]=Field(...)
    application_id:WriteApplicationId
    plan_ref:MergePlanReference
    approval_digest:Digest


class AuthorizeMergeExport(VersionOne):
    schema_version:Literal[1]=Field(...)
    export_id:MergeExportId
    plan_ref:MergePlanReference
