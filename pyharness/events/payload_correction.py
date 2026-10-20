"""The exact trusted correction prepared at a root logical context boundary."""
from pydantic import BaseModel,ConfigDict,Field
from pyharness.events.payload_product_execution import _ExecutionFact,ExecutionId,Sha256


class CorrectionReference(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    correction_id:str=Field(pattern=r'^cr-[0-9a-f]{16}$')
    revision:int=Field(ge=1,le=200)
    digest:Sha256
    source_seq:int=Field(ge=1)


class ExecutionCorrectionAppliedPayload(_ExecutionFact):
    execution_id:ExecutionId
    authorization_digest:Sha256
    authorization_seq:int=Field(ge=1)
    correction_ref:CorrectionReference
    user_message_seq:int=Field(ge=1)
    text_digest:Sha256
    materialized_messages_digest:Sha256
