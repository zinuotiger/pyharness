"""Immutable execution-intent reservations; no client-owned authority or effects."""
from typing import Annotated, Literal
from pydantic import Field, model_validator
from pyharness.application.document_contracts import VersionOne, DocumentReference, CitationReference, DeliveryId
from pyharness.application.workspace_manifest import StrictModel
from pyharness.application.context_manifest import TaskVersionReference
from pyharness.application.product_task_contracts import RunAttemptPrerequisites
from pyharness.application.workspace_contracts import ProjectId, Digest

AttemptId = Annotated[str, Field(pattern=r'^ra-[0-9a-f]{16}$')]
SubmissionKey = Annotated[str, Field(pattern=r'^[A-Za-z0-9._-]{1,128}$')]


class DeliveryReference(StrictModel):
    delivery_id: DeliveryId
    revision: int = Field(ge=1, le=200)
    digest: Digest
    source_seq: int = Field(ge=1)


class AttemptContext(VersionOne):
    document_refs: list[DocumentReference] = Field(default_factory=list, max_length=20)
    citation_refs: list[CitationReference] = Field(default_factory=list, max_length=20)
    delivery_ref: DeliveryReference | None = None

    @model_validator(mode='after')
    def distinct_ids(self):
        for values, key in ((self.document_refs, 'document_id'), (self.citation_refs, 'citation_id')):
            ids = [getattr(v, key) for v in values]
            if len(ids) != len(set(ids)):
                raise ValueError('duplicate context selection')
        return self


class SubmitAttempt(AttemptContext):
    schema_version: Literal[1] = Field(...)
    attempt_id: AttemptId
    task_ref: TaskVersionReference


class RunAttempt(VersionOne):
    attempt_id: AttemptId
    revision: Literal[1] = 1
    project_id: ProjectId
    project_revision: int = Field(ge=1)
    created_by: str = Field(min_length=1, max_length=128)
    submission_key: SubmissionKey
    selection: SubmitAttempt
    prerequisites: RunAttemptPrerequisites
    context: AttemptContext
    binding_digest: Digest
    dispatch_policy: Literal['explicit_governed_start'] = 'explicit_governed_start'

    @model_validator(mode='after')
    def coherent_bindings(self):
        p = self.prerequisites
        if (self.attempt_id != self.selection.attempt_id or self.selection.task_ref != p.task_ref
                or self.created_by != p.subject_ref or self.project_id != p.project_id
                or self.project_revision != p.project_revision or type(self.revision) is not int):
            raise ValueError('incoherent attempt binding')
        return self
