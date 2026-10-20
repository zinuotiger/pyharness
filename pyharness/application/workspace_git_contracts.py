"""Strict bounded public Git preview: no bodies, absolute roots or execution authority."""
from typing import Annotated, Literal
from pydantic import Field, field_validator, model_validator
from pyharness.application.source_grants import GrantModel, GrantId
from pyharness.application.workspace_contracts import ProjectId, Digest
from pyharness.application.source_snapshot import LIMIT

GitOid = Annotated[str, Field(pattern=r'^(?:[0-9a-f]{40}|[0-9a-f]{64})$')]


class GitLayer(GrantModel):
    git_oid: GitOid
    digest: Digest
    size_bytes: int = Field(ge=0, le=LIMIT)
    mode: Literal[0o100644, 0o100755]
    native_mode: int | None = Field(default=None, ge=0, le=511)


class GitPreviewItem(GrantModel):
    input_index: int = Field(ge=0, le=199)
    path: str = Field(min_length=1, max_length=1024)
    head: GitLayer | None
    index: GitLayer | None
    working: GitLayer | None
    deletion_marker: bool
    index_deletion_marker: bool
    selected_untracked: bool

    @model_validator(mode='after')
    def coherent(self):
        if (not any((self.head,self.index,self.working))
                or self.deletion_marker != (self.working is None)
                or self.index_deletion_marker != (self.head is not None and self.index is None)
                or self.selected_untracked != (self.index is None and self.working is not None)
                or any(layer is not None and layer.native_mode is not None for layer in (self.head,self.index))
                or (self.working is not None and self.working.native_mode is None)):
            raise ValueError('incoherent Git layer identity')
        return self


class GitSourcePreview(GrantModel):
    status: Literal['git_layers_observed'] = 'git_layers_observed'
    repository_kind: Literal['ordinary', 'linked_worktree']
    project_id: ProjectId
    grant_id: GrantId
    grant_revision: int = Field(ge=1)
    source_seq: int = Field(ge=1)
    metadata_grant_id: GrantId | None
    metadata_grant_revision: int | None = Field(ge=1)
    head_oid: GitOid
    index_digest: Digest
    items: list[GitPreviewItem] = Field(min_length=1,max_length=200)
    file_count: int = Field(ge=1,le=200)
    unique_content_size_bytes: int = Field(ge=0,le=LIMIT)
    policy: Literal['reject-secrets-utf8-v1'] = 'reject-secrets-utf8-v1'
    capture_stability: Literal['stable_observed'] = 'stable_observed'
    scope: Literal['selected_git_layers'] = 'selected_git_layers'
    persisted: Literal[False] = False
    executable: Literal[False] = False
    observation_digest: Digest

    @field_validator('persisted','executable',mode='before')
    @classmethod
    def false_boolean(cls,value):
        if value is not False:
            raise ValueError('preview flags must be false booleans')
        return value

    @model_validator(mode='after')
    def coherent(self):
        if ((self.metadata_grant_id is None) != (self.metadata_grant_revision is None)
                or (self.repository_kind=='linked_worktree' and self.metadata_grant_id is None)
                or self.file_count!=len(self.items)
                or [i.input_index for i in self.items]!=list(range(len(self.items)))
                or len({i.path.casefold() for i in self.items})!=self.file_count):
            raise ValueError('incoherent Git preview')
        sizes={}
        for item in self.items:
            for layer in (item.head,item.index,item.working):
                if layer is not None:
                    if layer.digest in sizes and sizes[layer.digest]!=layer.size_bytes:
                        raise ValueError('incoherent byte identity')
                    sizes[layer.digest]=layer.size_bytes
        if sum(sizes.values())!=self.unique_content_size_bytes:
            raise ValueError('incoherent unique byte count')
        return self
