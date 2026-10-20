"""Primitive typed delegation facts; the parent execution remains the authority."""
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator
from pyharness.events.payload_product_execution import _ExecutionFact,ExecutionId,Sha256


class ChildBudgetFacts(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    max_in_tokens:int=Field(ge=1)
    max_out_tokens:int=Field(ge=1)
    max_cost_yuan:float=Field(gt=0,allow_inf_nan=False)
    warn_ratio:float=Field(ge=0,le=1,allow_inf_nan=False)


class ExecutionChildPayload(_ExecutionFact):
    execution_id:ExecutionId
    phase:Literal['authorized','bound','running','completed','failed','cancelled']
    authorization_digest:Sha256
    authorization_seq:int=Field(ge=1)
    subject_ref:str=Field(min_length=1,max_length=128)
    root_session_id:str=Field(pattern=r'^s-pa-[0-9a-f]{32}$')
    child_session_id:str=Field(pattern=r'^s-pa-[0-9a-f]{32}$')
    invoker_session_id:str=Field(pattern=r'^s-pa-[0-9a-f]{32}$')
    invoker_task_id:str=Field(min_length=1,max_length=128)
    call_id:str=Field(min_length=1,max_length=256)
    tool_call_seq:int=Field(ge=1)
    inputs_digest:str=Field(pattern=r'^[0-9a-f]{40}$')
    decision_seq:int=Field(ge=1)
    effect_started_seq:int=Field(ge=1)
    spawn_seq:int=Field(ge=1)
    parent_input_seq:int=Field(ge=1)
    parent_admission_seq:int|None=Field(default=None,ge=1)
    depth:int=Field(ge=1,le=3)
    task_digest:Sha256
    spec_digest:Sha256
    materials_digest:Sha256
    scope_digest:Sha256
    parent_limits:ChildBudgetFacts
    child_limits:ChildBudgetFacts
    budget_ratio:float=Field(gt=0,le=1,allow_inf_nan=False)
    admission_seq:int|None=Field(default=None,ge=1)
    admission_digest:Sha256|None=None
    reason:str|None=Field(default=None,max_length=100)

    @model_validator(mode='after')
    def coherent(self):
        if self.child_session_id in (self.root_session_id,self.invoker_session_id):raise ValueError('cyclic child identity')
        if self.phase=='authorized':
            if self.admission_seq is not None or self.admission_digest is not None or self.reason is not None:
                raise ValueError('authorization is not an outcome')
        elif self.admission_seq is None or self.admission_digest is None:raise ValueError('child needs its exact authorization')
        if ((self.depth==1 and (self.invoker_session_id!=self.root_session_id or self.parent_admission_seq is not None)) or
                (self.depth>1 and (self.invoker_session_id==self.root_session_id or self.parent_admission_seq is None))):
            raise ValueError('parent depth mismatch')
        return self
