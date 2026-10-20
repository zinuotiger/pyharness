"""Product execution facts; primitive references keep events independent of application."""
from typing import Annotated, Literal
from pathlib import PurePosixPath
from pydantic import BaseModel,ConfigDict, Field, field_validator, model_validator
from pyharness.events.payload_base import _PayloadBase

ExecutionId = Annotated[str, Field(pattern=r'^ex-[0-9a-f]{16}$')]
Sha256 = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]


class _ExecutionFact(_PayloadBase):
    model_config = ConfigDict(extra='forbid', strict=True)
    schema_version: Literal[1]

    @field_validator('schema_version', mode='before')
    @classmethod
    def exact_version(cls, value):
        if type(value) is not int or value != 1: raise ValueError('integer schema one required')
        return value


class ExecutionLifecyclePayload(_ExecutionFact):
    execution_id: ExecutionId
    phase: Literal['bound', 'running', 'completed', 'failed', 'cancelled', 'interrupted']
    authorization_digest: Sha256
    authorization_seq: int = Field(ge=1)
    subject_ref: str = Field(min_length=1, max_length=128)
    reason: str | None = Field(default=None, max_length=100)

class ExecutionFileChange(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    path:str=Field(min_length=1,max_length=1000)
    kind:Literal['created','modified','deleted']
    before_sha256:Sha256|None=None
    after_sha256:Sha256|None=None

    @model_validator(mode='after')
    def coherent(self):
        parts=self.path.split('/')
        if (PurePosixPath(self.path).is_absolute() or any(c in self.path for c in ('\\',':','\x00')) or
                any(p in ('','.','..') for p in parts)):raise ValueError('workspace relative path required')
        before=self.before_sha256;after=self.after_sha256
        if ((self.kind=='created' and (before is not None or after is None)) or
                (self.kind=='deleted' and (before is None or after is not None)) or
                (self.kind=='modified' and (before is None or after is None or before==after))):
            raise ValueError('invalid observed change')
        return self


class ExecutionEffectPayload(_ExecutionFact):
    execution_id: ExecutionId
    phase: Literal['started', 'returned', 'unknown']
    tool: str = Field(min_length=1, max_length=160)
    call_id: str = Field(min_length=1, max_length=256)
    tool_call_seq: int = Field(ge=1)
    inputs_digest: str = Field(pattern=r'^[0-9a-f]{40}$')
    decision_seq: int = Field(ge=1)
    started_seq: int | None = Field(default=None, ge=1)
    result_digest: Sha256 | None = None
    provider_ok: bool | None = None
    reason: Literal['provider_raised', 'waiting_cancelled','observation_failed'] | None = None
    file_observation:Literal['observed','unavailable']|None=None
    observation_scope:Literal['target','workspace']|None=None
    observation_reason:Literal['snapshot_failed','authorization_changed','process_running']|None=None
    file_changes:list[ExecutionFileChange]|None=Field(default=None,max_length=250)

    @model_validator(mode='after')
    def coherent_outcome(self):
        if self.phase == 'started':
            if any(v is not None for v in (self.started_seq, self.result_digest, self.provider_ok, self.reason)):
                raise ValueError('started is not an outcome')
        elif self.started_seq is None:
            raise ValueError('outcome must reference its start')
        elif self.phase == 'returned':
            if self.result_digest is None or self.reason is not None: raise ValueError('invalid return')
        elif self.reason is None or self.result_digest is not None or self.provider_ok is not None:
            raise ValueError('unknown is not a confirmed result')
        if self.file_observation is None:
            if any(v is not None for v in (self.observation_scope,self.observation_reason,self.file_changes)):
                raise ValueError('file evidence needs an observation')
        elif self.phase!='returned' or self.observation_scope is None:raise ValueError('only a returned call can report files')
        elif self.file_observation=='observed':
            if self.file_changes is None or self.observation_reason is not None:raise ValueError('invalid observed files')
            names=[c.path for c in self.file_changes]
            if names!=sorted(set(names)):raise ValueError('unique sorted file changes required')
        elif self.file_changes is not None or self.observation_reason is None:raise ValueError('unavailable is not absence of changes')
        return self
