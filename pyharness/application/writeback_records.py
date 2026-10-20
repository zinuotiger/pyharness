"""Writeback facts on the original control journal; no effect retry authority."""
from typing import Literal
from pydantic import Field,model_validator
from pyharness.application.document_contracts import VersionOne
from pyharness.application.workspace_contracts import ProjectId,Digest
from pyharness.application.workspace_manifest import StrictModel
from pyharness.application.run_attempt_contracts import SubmissionKey
from pyharness.application.writeback_contracts import IssueWriteGrant,SaveMergePlan,ApplyMergePlan,AuthorizeMergeExport


class WritebackRecord(VersionOne):
    project_id:ProjectId
    project_revision:int=Field(ge=1)
    created_by:str=Field(min_length=1,max_length=128)
    submission_key:SubmissionKey
    revision:int=Field(default=1,ge=1,le=1)


class WriteGrant(WritebackRecord):
    revision:int=Field(ge=1,le=2)
    selection:IssueWriteGrant
    recipient_id:str=Field(min_length=1,max_length=128)
    canonical_root:str=Field(min_length=1,max_length=1024)
    root_identity:Digest
    device:int=Field(ge=0)
    inode:int=Field(gt=0)
    revoked:bool=False

    @model_validator(mode='after')
    def root_binding(self):
        from pyharness.application.source_grants import root_identity
        if self.root_identity!=root_identity(self.canonical_root,self.device,self.inode) or self.revoked!=(self.revision==2):
            raise ValueError('invalid write grant identity or transition')
        return self


class FixedValidation(StrictModel):
    version:Literal['text-policy-syntax-v1']
    status:Literal['passed','failed']
    checks:list[str]=Field(min_length=1,max_length=4)
    reason:str|None=None


class WritebackTarget(VersionOne):
    path:str=Field(min_length=1,max_length=1024)
    size_bytes:int=Field(ge=0,le=10*1024*1024)
    sha256:Digest
    mtime_ns:str=Field(pattern=r'^[0-9]{1,24}$')
    mode:int=Field(ge=0,le=511)
    # NTFS volume/file identifiers are not JavaScript-safe integers. Decimal
    # strings preserve exact approval hashes and the complete OS identity.
    device:str=Field(pattern=r'^[0-9]{1,40}$')
    inode:str=Field(pattern=r'^[1-9][0-9]{0,39}$')


class MergeFile(StrictModel):
    target_path:str=Field(min_length=1,max_length=1024)
    target:WritebackTarget|None
    base_digest:Digest|None
    delivery_digest:Digest|None
    merged_digest:Digest|None
    merged_size:int=Field(ge=0,le=10*1024*1024)
    action:Literal['none','replace','create','delete']
    validation:FixedValidation


class MergePlan(WritebackRecord):
    selection:SaveMergePlan
    merge_version:Literal['exact-lines-three-way-v1']='exact-lines-three-way-v1'
    files:list[MergeFile]=Field(min_length=1,max_length=100)
    basis_digest:Digest


class WritebackFileEffect(StrictModel):
    target_path:str=Field(min_length=1,max_length=1024)
    action:Literal['none','replace','create','delete']
    status:Literal['verified','not_applied','unknown']
    before_digest:Digest|None
    expected_digest:Digest|None
    observed_digest:Digest|None


class WritebackApplication(WritebackRecord):
    revision:int=Field(ge=1,le=2)
    selection:ApplyMergePlan
    origin_key:SubmissionKey
    status:Literal['admitted','completed','partial','failed','unknown']
    effects:list[WritebackFileEffect]=Field(default_factory=list,max_length=100)

    @model_validator(mode='after')
    def fact_phase(self):
        if (self.revision==1)!=(self.status=='admitted') or self.revision==1 and self.effects:
            raise ValueError('application phase mismatch')
        return self


class MergeExport(WritebackRecord):
    selection:AuthorizeMergeExport


WRITEBACK_MODELS={
    'writeback_grant':(WriteGrant,'grant_id','wg'),
    'merge_plan':(MergePlan,'plan_id','mp'),
    'writeback_application':(WritebackApplication,'application_id','wa'),
    'merge_export':(MergeExport,'export_id','we'),
}
