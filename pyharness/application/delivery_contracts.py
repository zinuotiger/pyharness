"""Fixed delivery evidence and named handoffs, separate from execution authority."""
from typing import Annotated, Literal
from pydantic import Field, model_validator, field_validator
from pyharness.application.workspace_manifest import StrictModel
from pyharness.application.workspace_contracts import Digest
from pyharness.application.document_contracts import VersionOne
from pyharness.application.run_attempt_contracts import DeliveryReference
from pyharness.application.execution_contracts import ExecutionReference, AgentVersionReference, ExecutionId

PackageId = Annotated[str, Field(pattern=r'^dp-[0-9a-f]{16}$')]
RoleId = Annotated[str, Field(pattern=r'^rc-[0-9a-f]{16}$')]
HandoffId = Annotated[str, Field(pattern=r'^ho-[0-9a-f]{16}$')]
AckId = Annotated[str, Field(pattern=r'^ha-[0-9a-f]{16}$')]
AcceptanceId = Annotated[str, Field(pattern=r'^av-[0-9a-f]{16}$')]
ExportId = Annotated[str, Field(pattern=r'^xp-[0-9a-f]{16}$')]
ItemId = Annotated[str, Field(pattern=r'^[A-Za-z0-9_-]{1,40}$')]


class DeliveryOutputReference(StrictModel):
    kind: Literal['artifact', 'message']
    execution_id: ExecutionId
    session_id: str = Field(pattern=r'^s-[A-Za-z0-9_-]{1,120}$')
    task_id: str = Field(pattern=r'^(?:p-[0-9a-f]{32}|subagent:s-[A-Za-z0-9_-]{1,120})$')
    source_seq: int = Field(ge=1)
    record_digest: Digest
    sha256: Digest
    size_bytes: int = Field(ge=0, le=32*1024*1024)
    artifact_id: str | None = Field(default=None, pattern=r'^f-[0-9a-f]{32}$')

    @model_validator(mode='after')
    def coherent(self):
        if (self.kind == 'artifact') != (self.artifact_id is not None):
            raise ValueError('artifact handle applies only to generated files')
        if self.kind == 'message' and not self.size_bytes:
            raise ValueError('empty public message is not a delivery output')
        return self


def output_identity(value):
    """Public immutable identity; changing a digest cannot create another event."""
    return value.execution_id, value.session_id, value.task_id, value.source_seq, value.kind


def distinct_outputs(values):
    if len({output_identity(v) for v in values}) != len(values):
        raise ValueError('duplicate output event')
    return values


class DeliveryBinding(StrictModel):
    item_id: ItemId
    outputs: list[DeliveryOutputReference] = Field(min_length=1, max_length=40)

    @field_validator('outputs')
    @classmethod
    def distinct(cls, values):
        return distinct_outputs(values)


class DeliveryPackageCommand(VersionOne):
    schema_version: Literal[1] = Field(...)
    package_id: PackageId
    project_revision: int = Field(ge=1)
    execution_ref: ExecutionReference
    delivery_ref: DeliveryReference
    bindings: list[DeliveryBinding] = Field(min_length=1, max_length=30)

    @model_validator(mode='after')
    def bounded(self):
        if len({b.item_id for b in self.bindings}) != len(self.bindings):
            raise ValueError('duplicate delivery item')
        outputs = [o for b in self.bindings for o in b.outputs]
        if len(outputs)>60 or len({output_identity(o) for o in outputs})>40:
            raise ValueError('delivery output limit')
        return self


class FixedDeliveryReference(StrictModel):
    revision: int = Field(ge=1, le=1)
    digest: Digest
    source_seq: int = Field(ge=1)


class DeliveryPackageReference(FixedDeliveryReference):
    package_id: PackageId


class RoleContractReference(FixedDeliveryReference):
    revision: int = Field(ge=1, le=200)
    role_id: RoleId


class HandoffReference(FixedDeliveryReference):
    handoff_id: HandoffId


class DeliveryAcceptanceReference(FixedDeliveryReference):
    acceptance_id: AcceptanceId


class RoleContractCommand(VersionOne):
    schema_version: Literal[1] = Field(...)
    role_id: RoleId
    expected_revision: int = Field(ge=0, le=199)
    project_revision: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=100)
    parent_execution_ref: ExecutionReference
    recipient_username: str = Field(min_length=1, max_length=128)
    role: Literal['reviewer', 'consumer']
    consumer_agent_ref: AgentVersionReference | None = None
    max_context_bytes: int = Field(default=98304, ge=1, le=98304)
    retired: bool = False

    @field_validator('title', 'recipient_username')
    @classmethod
    def nonblank(cls, value):
        if not value.strip() or any(ord(c)<32 for c in value):
            raise ValueError('blank or control character in role label')
        return value

    @model_validator(mode='after')
    def fixed_consumer(self):
        if (self.role == 'consumer') != (self.consumer_agent_ref is not None):
            raise ValueError('only a consumer role selects a fixed published agent')
        return self


class HandoffCommand(VersionOne):
    schema_version: Literal[1] = Field(...)
    handoff_id: HandoffId
    role_ref: RoleContractReference
    package_ref: DeliveryPackageReference
    outputs: list[DeliveryOutputReference] = Field(min_length=1, max_length=40)
    minimum_context: str = Field(default='', max_length=98304)

    @field_validator('outputs')
    @classmethod
    def distinct(cls, values):
        return distinct_outputs(values)

    @field_validator('minimum_context')
    @classmethod
    def bounded_bytes(cls, value):
        if len(value.encode('utf-8'))>98304:
            raise ValueError('handoff context byte limit')
        return value


class HandoffAckCommand(VersionOne):
    schema_version: Literal[1] = Field(...)
    ack_id: AckId
    handoff_ref: HandoffReference


class CriterionDecision(StrictModel):
    item_id: ItemId
    criterion_index: int = Field(ge=0, le=19)
    verdict: Literal['pass', 'fail', 'unknown']
    note: str = Field(default='', max_length=1000)


class DeliveryAcceptanceCommand(VersionOne):
    schema_version: Literal[1] = Field(...)
    acceptance_id: AcceptanceId
    handoff_ref: HandoffReference
    package_ref: DeliveryPackageReference
    decisions: list[CriterionDecision] = Field(min_length=1, max_length=600)

    @model_validator(mode='after')
    def unique_criteria(self):
        keys = [(d.item_id, d.criterion_index) for d in self.decisions]
        if len(set(keys)) != len(keys):
            raise ValueError('duplicate criterion decision')
        return self


class DeliveryExportCommand(VersionOne):
    schema_version: Literal[1] = Field(...)
    export_id: ExportId
    purpose: Literal['review', 'delivery']
    handoff_ref: HandoffReference
    package_ref: DeliveryPackageReference
    outputs: list[DeliveryOutputReference] = Field(min_length=1, max_length=40)
    acceptance_ref: DeliveryAcceptanceReference | None = None

    @field_validator('outputs')
    @classmethod
    def distinct(cls, values):
        return distinct_outputs(values)

    @model_validator(mode='after')
    def independent_acceptance(self):
        if (self.purpose == 'delivery') != (self.acceptance_ref is not None):
            raise ValueError('delivery export requires exact independent acceptance')
        return self
