"""Private observed state bytes; no resume permission or arbitrary client paths."""
import base64,hashlib,re
from pathlib import PurePosixPath
from typing import Literal
from pydantic import Field,field_validator,model_validator
from pyharness.application.document_contracts import VersionOne
from pyharness.application.execution_contracts import ExecutionId,ExecutionSessionId
from pyharness.application.workspace_contracts import Digest
from pyharness.application.workspace_manifest import StrictModel

MAX_STATE_BYTES=32*1048576
MAX_STATE_FILE_BYTES=10*1048576
MAX_STATE_TOTAL_FILE_BYTES=20*1048576


class StateFile(StrictModel):
    path:str=Field(min_length=1,max_length=4096)
    mode:int=Field(ge=0,le=511)
    size:int=Field(ge=0,le=MAX_STATE_FILE_BYTES)
    sha256:Digest
    data_b64:str=Field(max_length=14*1048576)

    @field_validator('path')
    @classmethod
    def relative_path(cls,value):
        p=PurePosixPath(value)
        if (p.is_absolute() or p.as_posix()!=value or '\\' in value or ':' in value or '\x00' in value or
                any(n in ('','.','..') or n.endswith(('.', ' ')) or re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?',n) for n in value.split('/'))):
            raise ValueError('portable relative state path required')
        return value

    @model_validator(mode='after')
    def actual_bytes(self):
        raw=base64.b64decode(self.data_b64,validate=True)
        if len(raw)!=self.size or hashlib.sha256(raw).hexdigest()!=self.sha256:raise ValueError('state file bytes mismatch')
        return self


class StateDirectory(StrictModel):
    path:str=Field(min_length=1,max_length=4096)
    mode:int=Field(ge=0,le=511)

    @field_validator('path')
    @classmethod
    def relative_path(cls,value):return StateFile.relative_path(value)


class StateBudgetLimits(StrictModel):
    max_in_tokens:int=Field(ge=0)
    max_out_tokens:int=Field(ge=0)
    max_cost_yuan:float=Field(ge=0,allow_inf_nan=False)
    warn_ratio:float=Field(ge=0,le=1,allow_inf_nan=False)


class StateUsage(StrictModel):
    in_tokens:int=Field(ge=0)
    out_tokens:int=Field(ge=0)
    cost_est:float|None=Field(ge=0,allow_inf_nan=False)


class StatePolicy(StrictModel):
    role:str=Field(min_length=1,max_length=128)
    deny_tools:list[str]=Field(max_length=4096)
    allowed_tools:list[str]|None=Field(max_length=4096)
    danger_marks:list[dict[str,str]]=Field(max_length=4096)
    allowed_domains:list[str]=Field(max_length=4096)
    workspace_root:str=Field(max_length=32768)
    read_extra_dirs:list[str]=Field(max_length=4096)
    sandbox_level:Literal['strict','basic','off']


class StateBudget(StrictModel):
    counter_injected:bool
    limits:StateBudgetLimits
    usage:StateUsage
    policy:StatePolicy
    cost_observation:Literal['runtime_estimate']='runtime_estimate'


class StateContinuationContract(VersionOne):
    config_fingerprint:Digest
    runtime_digest:Digest
    tools_digest:Digest
    tool_definitions_digest:Digest
    models:list[str]=Field(min_length=1,max_length=64)
    input_upper_bound:int=Field(ge=0,le=64*1048576)
    output_upper_bound:int=Field(ge=0,le=32768)
    pricing_known:bool
    input_rate:float|None=Field(ge=0,allow_inf_nan=False)
    output_rate:float|None=Field(ge=0,allow_inf_nan=False)
    remaining_timeout_seconds:float=Field(gt=0,le=600,allow_inf_nan=False)
    completed_rounds:int=Field(ge=0)
    tool_calls_used:int=Field(ge=0)

    @model_validator(mode='after')
    def coherent_prices(self):
        if self.pricing_known!=(self.input_rate is not None and self.output_rate is not None):raise ValueError('unknown price cannot be a zero-cost promise')
        if not self.pricing_known and (self.input_rate is not None or self.output_rate is not None):raise ValueError('incomplete model price observation')
        if len(set(self.models))!=len(self.models):raise ValueError('duplicate model observation')
        return self


class ExecutionStateSnapshot(VersionOne):
    capture_scope:Literal['session_local_state']='session_local_state'
    execution_id:ExecutionId
    authorization_digest:Digest
    authorization_seq:int=Field(ge=1)
    session_id:ExecutionSessionId
    task_id:str=Field(min_length=1,max_length=128)
    checkpoint_id:str=Field(pattern=r'^ck-[0-9a-f]{16}$')
    checkpoint_seq:int=Field(ge=1)
    checkpoint_digest:Digest
    materials_digest:Digest
    messages:list[dict]=Field(max_length=5000)
    messages_digest:Digest
    budget:StateBudget
    continuation_contract:StateContinuationContract|None=None
    workspace_backend:Literal['disabled','host_approved','isolated','remote']
    filesystem_capture:Literal['files_only_legacy','files_and_directories','no_workspace']='files_only_legacy'
    files:list[StateFile]=Field(max_length=4096)
    directories:list[StateDirectory]=Field(default_factory=list,max_length=4096)
    kv_present:bool
    kv_data_b64:str|None=Field(default=None,max_length=6*1048576)
    replay_authorized:Literal[False]=False

    @model_validator(mode='after')
    def coherent_state(self):
        names={f.path.casefold() for f in self.files}
        directories={d.path.casefold() for d in self.directories}
        if len(names)!=len(self.files) or sum(f.size for f in self.files)>MAX_STATE_TOTAL_FILE_BYTES:
            raise ValueError('state file scope limit or collision')
        if len(directories)!=len(self.directories) or names&directories:raise ValueError('state directory collision')
        for f in self.files:
            if any(p.as_posix().casefold() in names for p in PurePosixPath(f.path).parents if p.as_posix()!='.'):
                raise ValueError('file directory state collision')
        for d in self.directories:
            if any(p.as_posix().casefold() in names for p in PurePosixPath(d.path).parents if p.as_posix()!='.'):
                raise ValueError('directory file state collision')
        if self.filesystem_capture=='files_and_directories':
            for item in [*self.files,*self.directories]:
                if any(p.as_posix().casefold() not in directories for p in PurePosixPath(item.path).parents if p.as_posix()!='.'):
                    raise ValueError('missing captured parent directory')
        if self.filesystem_capture=='no_workspace' and (self.files or self.directories or self.workspace_backend!='disabled'):
            raise ValueError('no workspace cannot claim files')
        if self.kv_present!=(self.kv_data_b64 is not None):raise ValueError('KV presence not confirmed')
        if self.kv_data_b64 is not None:
            import json
            value=json.loads(base64.b64decode(self.kv_data_b64,validate=True).decode('utf-8'))
            if not isinstance(value,dict) or any(not isinstance(k,str) or not isinstance(v,str) or len(v)>65536 for k,v in value.items()):
                raise ValueError('KV state shape')
        from pyharness.application.workspace_store import canonical,digest_of
        if digest_of(canonical(self.messages))!=self.messages_digest:raise ValueError('state messages digest')
        return self
