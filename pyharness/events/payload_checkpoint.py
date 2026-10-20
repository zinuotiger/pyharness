"""A durable historical observation; never a bearer permission to replay tools."""
from typing import Literal
from pydantic import Field,model_validator
from pyharness.events.payload_product_execution import _ExecutionFact,ExecutionId,Sha256


class ExecutionCheckpointPayload(_ExecutionFact):
    checkpoint_id:str=Field(pattern=r'^ck-[0-9a-f]{16}$')
    execution_id:ExecutionId
    authorization_digest:Sha256
    authorization_seq:int=Field(ge=1)
    boundary:Literal['before_model']
    prefix_seq:int=Field(ge=1)
    prefix_digest:Sha256
    materialized_messages_digest:Sha256
    observation_scope:Literal['session_effects']
    effect_count:int=Field(ge=0)
    pending_effect_count:int=Field(ge=0)
    unknown_effect_count:int=Field(ge=0)
    effect_replay_blocked:bool
    replay_authorized:Literal[False]

    @model_validator(mode='after')
    def coherent_observation(self):
        if (self.pending_effect_count+self.unknown_effect_count>self.effect_count or
                self.effect_replay_blocked!=bool(self.pending_effect_count+self.unknown_effect_count)):
            raise ValueError('incoherent historical effect observation')
        return self
