"""Server-authored control facts for delivery; old resource schemas stay intact."""
from pydantic import Field
from pyharness.application.document_contracts import VersionOne
from pyharness.application.workspace_contracts import ProjectId
from pyharness.application.run_attempt_contracts import SubmissionKey
from pyharness.application.delivery_contracts import (
    DeliveryPackageCommand, RoleContractCommand, HandoffCommand,
    HandoffAckCommand, DeliveryAcceptanceCommand, DeliveryExportCommand)


class DeliveryRecord(VersionOne):
    project_id: ProjectId
    project_revision: int = Field(ge=1)
    revision: int = Field(default=1, ge=1, le=1)
    created_by: str = Field(min_length=1, max_length=128)
    submission_key: SubmissionKey


class DeliveryPackage(DeliveryRecord):
    selection: DeliveryPackageCommand


class RoleContract(DeliveryRecord):
    revision: int = Field(ge=1, le=200)
    selection: RoleContractCommand
    recipient_id: str = Field(min_length=1, max_length=128)
    consumer_snapshot: dict | None = None


class RoleHandoff(DeliveryRecord):
    selection: HandoffCommand
    recipient_id: str = Field(min_length=1, max_length=128)


class HandoffAck(DeliveryRecord):
    selection: HandoffAckCommand


class DeliveryAcceptance(DeliveryRecord):
    selection: DeliveryAcceptanceCommand


class DeliveryExport(DeliveryRecord):
    selection: DeliveryExportCommand
    recipient_id: str = Field(min_length=1, max_length=128)


# One registry shared by decoding, projection, commands and audit integration.
DELIVERY_MODELS = {
    'delivery_package': (DeliveryPackage, DeliveryPackageCommand, 'package_id', 'dp'),
    'role_contract': (RoleContract, RoleContractCommand, 'role_id', 'rc'),
    'role_handoff': (RoleHandoff, HandoffCommand, 'handoff_id', 'ho'),
    'handoff_ack': (HandoffAck, HandoffAckCommand, 'ack_id', 'ha'),
    'delivery_acceptance': (DeliveryAcceptance, DeliveryAcceptanceCommand, 'acceptance_id', 'av'),
    'delivery_export': (DeliveryExport, DeliveryExportCommand, 'export_id', 'xp'),
}


def delivery_record_id(value, kind):
    return getattr(value.selection, DELIVERY_MODELS[kind][2])
