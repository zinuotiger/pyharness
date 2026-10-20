"""Strict public observation facts; no raw response, headers or execution token."""
import hashlib,json
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,JsonValue,model_validator
from pyharness.events.payload_product_execution import _ExecutionFact,ExecutionId,Sha256


def _json(value):return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')


class _PublicModel(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,allow_inf_nan=False)


class PublicWithheld(_PublicModel):
    withheld:str=Field(min_length=1,max_length=128)


class PublicFunctionArguments(_PublicModel):
    name:str=Field(min_length=1,max_length=128)
    arguments:JsonValue


class PublicToolCall(_PublicModel):
    id:str=Field(min_length=1,max_length=128)
    type:Literal['function']
    function:PublicFunctionArguments


class PublicMessage(_PublicModel):
    role:Literal['system','developer','user','assistant','tool']
    content:str=Field(max_length=49152)
    name:str|None=Field(default=None,max_length=128)
    tool_call_id:str|None=Field(default=None,max_length=128)
    tool_calls:list[PublicToolCall|PublicWithheld]|None=Field(default=None,max_length=16)


class PublicFunctionSignature(_PublicModel):
    name:str=Field(min_length=1,max_length=128)
    description:str=Field(max_length=2048)
    parameters:JsonValue


class PublicToolSchema(_PublicModel):
    type:Literal['function']
    function:PublicFunctionSignature


class PublicCounts(_PublicModel):
    messages:int=Field(ge=0,le=1000000)
    tools:int=Field(ge=0,le=1000000)
    images:int=Field(ge=0,le=2048)
    images_exact:bool


class PublicRequestView(_PublicModel):
    schema_version:Literal[1]
    layer:Literal['adapter_input','transport_payload']
    model:str=Field(min_length=1,max_length=128)
    method:Literal['chat','chat_stream']
    messages:list[PublicMessage]=Field(max_length=64)
    tools:list[PublicToolSchema|PublicWithheld]=Field(max_length=64)
    counts:PublicCounts
    truncated:bool
    withheld:bool

    @model_validator(mode='after')
    def bounded(self):
        if self.counts.messages<len(self.messages) or self.counts.tools<len(self.tools):
            raise ValueError('Public request counts cannot understate the visible items')
        if len(_json(self.model_dump()))>49152:raise ValueError('Public request copy exceeds byte bound')
        return self


class ContextWindow(_PublicModel):
    available:bool
    source_seq:int|None=Field(default=None,ge=1)
    input_messages:int|None=Field(default=None,ge=0,le=1000000)
    kept_messages:int|None=Field(default=None,ge=0,le=1000000)
    dropped_messages:int|None=Field(default=None,ge=0,le=1000000)
    window_tokens:int|None=Field(default=None,ge=0)
    system_messages:int|None=Field(default=None,ge=0,le=16)

    @model_validator(mode='after')
    def coherent(self):
        data=self.model_dump();known=[v for k,v in data.items() if k!='available' and v is not None]
        if not self.available and known:raise ValueError('Unknown window must not invent counters')
        if self.available:
            if any(v is None for v in (self.input_messages,self.kept_messages,self.dropped_messages)):
                raise ValueError('Known window requires its actual input and retained counts')
            if self.input_messages-self.kept_messages!=self.dropped_messages:
                raise ValueError('Window count mismatch')
        return self


class ContextDerivation(_PublicModel):
    history_window:ContextWindow
    system_window:ContextWindow
    final_messages:int=Field(ge=0,le=1000000)


class ContextCheckpointReference(_PublicModel):
    checkpoint_id:str=Field(pattern=r'^ck-[0-9a-f]{16}$')
    source_seq:int=Field(ge=1)


class ExecutionContextPayload(_ExecutionFact):
    observation_id:str=Field(pattern=r'^co-[0-9a-f]{16}$')
    execution_id:ExecutionId
    authorization_digest:Sha256
    authorization_seq:int=Field(ge=1)
    prefix_seq:int=Field(ge=1)
    phase:Literal['prepared']
    layer:Literal['adapter_input','transport_payload']
    model:str=Field(min_length=1,max_length=128)
    model_digest:Sha256
    method:Literal['chat','chat_stream']
    role:Literal['main_agent','title_generation','probe','other']
    attempt:int=Field(ge=1,le=1000)
    request_seq:int|None=Field(default=None,ge=1)
    checkpoint_ref:ContextCheckpointReference|None=None
    messages_digest:Sha256
    public_view_digest:Sha256
    public_view:PublicRequestView
    derivation:ContextDerivation
    source_content_state:Literal['injected_fixed_prompt','available_as_workspace','delegated_context','not_confirmed']
    execution_authorized:Literal[False]

    @model_validator(mode='after')
    def bound_copy(self):
        view=self.public_view
        if any(getattr(view,k)!=getattr(self,k) for k in ('layer','model','method')):
            raise ValueError('Public request metadata mismatch')
        if hashlib.sha256(_json(view.model_dump())).hexdigest()!=self.public_view_digest:
            raise ValueError('Public request digest mismatch')
        if (self.layer=='transport_payload')!=(self.request_seq is not None):
            raise ValueError('Only an actual transport request has llm.request provenance')
        if self.role=='main_agent' and self.checkpoint_ref is None:
            raise ValueError('Main context requires its actual materialized checkpoint')
        if self.derivation.final_messages!=view.counts.messages:
            raise ValueError('Final context count mismatch')
        if len(_json(self.model_dump()))>61440:raise ValueError('Public context fact exceeds byte bound')
        return self
