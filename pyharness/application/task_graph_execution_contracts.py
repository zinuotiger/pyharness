"""Graph-owned node admissions extend the original attempt and authorization."""
from typing import Literal
from pydantic import field_validator,Field
from pyharness.application.workspace_manifest import StrictModel
from pyharness.application.workspace_contracts import Digest
from pyharness.application.task_graph_contracts import TaskGraphNodeId
from pyharness.application.task_graph_run_contracts import TaskGraphRunReference
from pyharness.application.run_attempt_contracts import RunAttempt
from pyharness.application.execution_contracts import StartExecution,ExecutionAuthorization


class GraphNodeBasis(StrictModel):
    graph_run_ref:TaskGraphRunReference
    node_id:TaskGraphNodeId
    intent_seq:int=Field(ge=1)
    intent_digest:Digest


def graph_attempt_digest(prerequisites,context,basis):
    from pyharness.application.workspace_store import canonical,digest_of
    base=digest_of(canonical(dict(prerequisites=prerequisites,context=context)))
    return digest_of(canonical(dict(base_binding_digest=base,graph_node=basis)))


class GraphRunAttempt(RunAttempt):
    schema_version:Literal[3]=3
    graph_node:GraphNodeBasis

    @field_validator('schema_version',mode='before')
    @classmethod
    def exact_version(cls,value):
        if type(value) is not int or value!=3:raise ValueError('graph node attempt requires integer version three')
        return value


class GraphNodeStartExecution(StartExecution):
    graph_node:GraphNodeBasis


class GraphNodeExecutionAuthorization(ExecutionAuthorization):
    schema_version:Literal[3]=3
    selection:GraphNodeStartExecution

    @field_validator('schema_version',mode='before')
    @classmethod
    def exact_version(cls,value):
        if type(value) is not int or value!=3:raise ValueError('graph node authorization requires integer version three')
        return value
