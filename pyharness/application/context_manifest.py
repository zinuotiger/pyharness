"""Immutable bounded context identities; never an executable ProductTask or model request."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pyharness.application.source_grants import GrantModel, GrantId
from pyharness.application.source_snapshot import SnapshotId, LIMIT
from pyharness.application.workspace_contracts import ProjectId, Digest

ManifestId = Annotated[str, Field(pattern=r'^cm-[0-9a-f]{16}$')]
ProductTaskId = Annotated[str, Field(pattern=r'^pt-[0-9a-f]{16}$')]


class TaskVersionReference(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    product_task_id: ProductTaskId
    revision: int = Field(ge=1)
    digest: Digest
    source_seq: int = Field(ge=1)


class TaskReferenceCommand(GrantModel):
    product_task_id: ProductTaskId
    expected_revision: int = Field(ge=0, le=200)
    project_revision: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=100)
    retired: bool = False

    @field_validator('title')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('blank context target')
        return value


class TaskContextReference(GrantModel):
    """A real persisted reference seam, deliberately not a runnable task definition."""
    mode: Literal['context_reference_only'] = 'context_reference_only'
    product_task_id: ProductTaskId
    project_id: ProjectId
    project_revision: int = Field(ge=1)
    revision: int = Field(ge=1, le=200)
    title: str = Field(min_length=1, max_length=100)
    retired: bool = False
    created_by: str = Field(min_length=1, max_length=200)


class ManifestCommand(GrantModel):
    manifest_id: ManifestId
    expected_revision: int = Field(ge=0, le=200)
    project_revision: int = Field(ge=1)
    grant_id: GrantId
    grant_revision: int = Field(ge=1)
    snapshot_id: SnapshotId
    snapshot_digest: Digest
    task_ref: TaskVersionReference | None = None


class ContextItem(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    input_index: int = Field(ge=0, le=199)
    path: str = Field(min_length=1, max_length=1024)
    size_bytes: int = Field(ge=0, le=LIMIT)
    content_digest: Digest


class ContextManifest(GrantModel):
    manifest_id: ManifestId
    revision: int = Field(ge=1, le=200)
    project_id: ProjectId
    project_revision: int = Field(ge=1)
    grant_id: GrantId
    grant_revision: int = Field(ge=1)
    snapshot_id: SnapshotId
    snapshot_revision: Literal[1] = 1
    snapshot_digest: Digest
    snapshot_source_seq: int = Field(ge=1)
    task_ref: TaskVersionReference | None = None
    policy: Literal['reject-secrets-utf8-v1'] = 'reject-secrets-utf8-v1'
    items: list[ContextItem] = Field(min_length=1, max_length=200)
    total_size_bytes: int = Field(ge=0, le=LIMIT)
    created_by: str = Field(min_length=1, max_length=200)

    @field_validator('snapshot_revision', mode='before')
    @classmethod
    def strict_snapshot_revision(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError('snapshot revision must be integer one')
        return value

    @model_validator(mode='after')
    def coherent(self):
        if ([i.input_index for i in self.items] != list(range(len(self.items)))
                or len({i.path.casefold() for i in self.items}) != len(self.items)
                or sum(i.size_bytes for i in self.items) != self.total_size_bytes):
            raise ValueError('incoherent bounded context')
        return self


def task_identity(value):
    return value['product_task_id'] if value else None
