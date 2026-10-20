"""Formal product definitions and read-only attempt prerequisites, without execution."""
from typing import Literal
from pydantic import Field, field_validator

from pyharness.application.context_manifest import ProductTaskId, TaskVersionReference
from pyharness.application.workspace_contracts import ProjectId, Digest
from pyharness.application.workspace_manifest import StrictModel
from pyharness.application.workspace_owned_inputs import ManifestReference, WorkspaceInputId


class InputReference(StrictModel):
    input_id: WorkspaceInputId
    revision: int = Field(ge=1, le=1)
    digest: Digest
    source_seq: int = Field(ge=1)


class TaskInputBinding(StrictModel):
    manifest_ref: ManifestReference
    input_ref: InputReference


class ProductTaskFields(StrictModel):
    schema_version: Literal[2] = 2
    product_task_id: ProductTaskId
    project_revision: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=100)
    objective: str = Field(min_length=1, max_length=8000)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=30)
    input_binding: TaskInputBinding | None = None
    retired: bool = False

    @field_validator('schema_version', mode='before')
    @classmethod
    def strict_version(cls, value):
        if type(value) is not int or value != 2:
            raise ValueError('formal tasks require schema two')
        return value

    @field_validator('title', 'objective')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('blank task text')
        return value

    @field_validator('acceptance_criteria')
    @classmethod
    def bounded_criteria(cls, values):
        if any(not v.strip() or len(v) > 1000 for v in values):
            raise ValueError('invalid acceptance criterion')
        return values


class ProductTaskCommand(ProductTaskFields):
    expected_revision: int = Field(ge=0, le=199)


class ProductTask(ProductTaskFields):
    mode: Literal['product_task'] = 'product_task'
    execution_policy: Literal['governed-attempt-only'] = 'governed-attempt-only'
    project_id: ProjectId
    revision: int = Field(ge=1, le=200)
    created_by: str = Field(min_length=1, max_length=128)


class TaskPreflight(StrictModel):
    schema_version: Literal[1] = 1
    task_ref: TaskVersionReference

    @field_validator('schema_version', mode='before')
    @classmethod
    def strict_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError('explicit prerequisites version one')
        return value


class RunAttemptPrerequisites(StrictModel):
    """An internal binding to revalidate on submission, never an authorization token."""
    schema_version: Literal[1] = 1
    tenant_id: str = Field(min_length=1, max_length=128)
    subject_ref: str = Field(min_length=1, max_length=128)
    project_id: ProjectId
    project_revision: int = Field(ge=1)
    project_digest: Digest
    project_source_seq: int = Field(ge=1)
    task_ref: TaskVersionReference
    input_binding: TaskInputBinding
    execution_policy: Literal['governed-attempt-only'] = 'governed-attempt-only'
