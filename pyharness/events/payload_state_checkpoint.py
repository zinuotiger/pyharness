"""Metadata-only companion to one exact logical-round checkpoint."""
from typing import Literal
from pydantic import Field,model_validator
from pyharness.events.payload_product_execution import _ExecutionFact,ExecutionId,Sha256


class ExecutionStateCheckpointPayload(_ExecutionFact):
    execution_id:ExecutionId
    authorization_digest:Sha256
    authorization_seq:int=Field(ge=1)
    checkpoint_id:str=Field(pattern=r'^ck-[0-9a-f]{16}$')
    checkpoint_seq:int=Field(ge=1)
    checkpoint_digest:Sha256
    status:Literal['captured','unavailable']
    capture_scope:Literal['session_local_state']
    state_digest:Sha256|None
    state_size:int=Field(ge=0,le=32*1048576)
    file_count:int=Field(ge=0,le=4096)
    kv_present:bool|None
    unavailable_reason:Literal['unsupported_backend','shared_memory_not_captured','snapshot_limit','unsafe_state_path','snapshot_io_failed','snapshot_state_invalid','snapshot_quota']|None
    replay_authorized:Literal[False]

    @model_validator(mode='after')
    def coherent(self):
        if self.status=='captured':
            if self.state_digest is None or self.state_size==0 or self.kv_present is None or self.unavailable_reason is not None:
                raise ValueError('unconfirmed captured state')
        elif self.state_digest is not None or self.state_size!=0 or self.file_count!=0 or self.kv_present is not None or self.unavailable_reason is None:
            raise ValueError('unavailable state cannot claim contents')
        return self
