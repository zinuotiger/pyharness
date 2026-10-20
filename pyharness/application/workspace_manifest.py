"""Explicit version-two ContextManifest; schema-one contracts remain strict."""
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator
from pyharness.application.source_grants import GrantId
from pyharness.application.workspace_contracts import ProjectId,Digest
from pyharness.application.workspace_snapshot import WorkspaceSnapshotId
from pyharness.application.context_manifest import ManifestId,TaskVersionReference,ContextItem
from pyharness.application.workspace_git_contracts import GitOid
from pyharness.application.source_snapshot import LIMIT


class StrictModel(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True)


class WorkspaceSnapshotRef(StrictModel):
    snapshot_id: WorkspaceSnapshotId
    revision: Literal[1]
    digest: Digest
    source_seq: int = Field(ge=1)

    @field_validator('revision',mode='before')
    @classmethod
    def first_revision(cls,value):
        if type(value) is not int or value!=1: raise ValueError('snapshot revision must be integer one')
        return value


class WorkspaceSourceReference(WorkspaceSnapshotRef):
    captured_project_revision: int = Field(ge=1)
    grant_id: GrantId
    grant_revision: int = Field(ge=1)
    grant_source_seq: int = Field(ge=1)
    root_identity: Digest
    metadata_grant_id: GrantId | None
    metadata_grant_revision: int | None = Field(ge=1)
    metadata_grant_source_seq: int | None = Field(ge=1)
    metadata_root_identity: Digest | None
    head_oid: GitOid
    index_digest: Digest
    confirmation_digest: Digest
    source_kind: Literal['workspace_snapshot'] = 'workspace_snapshot'

    @model_validator(mode='after')
    def metadata_pair(self):
        if any((v is None)!=(self.metadata_grant_id is None) for v in
               (self.metadata_grant_revision,self.metadata_grant_source_seq,self.metadata_root_identity)):
            raise ValueError('incoherent metadata source')
        return self


class WorkspaceManifestFields(StrictModel):
    schema_version: Literal[2] = 2
    manifest_id: ManifestId
    project_revision: int = Field(ge=1)
    task_ref: TaskVersionReference | None = None

    @field_validator('schema_version',mode='before')
    @classmethod
    def second_version(cls,value):
        if type(value) is not int or value!=2: raise ValueError('explicit workspace manifest version two')
        return value


class WorkspaceManifestCommand(WorkspaceManifestFields):
    expected_revision: int = Field(ge=0,le=200)
    source_ref: WorkspaceSnapshotRef


class ContextManifestV2(WorkspaceManifestFields):
    revision: int = Field(ge=1,le=200)
    project_id: ProjectId
    source_ref: WorkspaceSourceReference
    items: list[ContextItem] = Field(max_length=200)
    total_size_bytes: int = Field(ge=0,le=LIMIT)
    created_by: str = Field(min_length=1,max_length=200)
    policy: Literal['reject-secrets-utf8-v1'] = 'reject-secrets-utf8-v1'

    @model_validator(mode='after')
    def coherent(self):
        indexes=[i.input_index for i in self.items]
        if (indexes!=sorted(set(indexes)) or len({i.path.casefold() for i in self.items})!=len(self.items)
                or sum(i.size_bytes for i in self.items)!=self.total_size_bytes):
            raise ValueError('incoherent workspace context items')
        return self
