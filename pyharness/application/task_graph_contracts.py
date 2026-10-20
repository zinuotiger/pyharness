"""Exact product-task DAG definitions; defining a graph never dispatches work."""
from typing import Annotated,Literal
from pydantic import Field,field_validator,model_validator
from pyharness.application.document_contracts import VersionOne
from pyharness.application.workspace_manifest import StrictModel
from pyharness.application.workspace_contracts import ProjectId,Digest
from pyharness.application.context_manifest import TaskVersionReference
from pyharness.application.execution_contracts import AgentVersionReference
from pyharness.application.run_attempt_contracts import AttemptContext

TaskGraphId=Annotated[str,Field(pattern=r'^tg-[0-9a-f]{16}$')]
TaskGraphRunId=Annotated[str,Field(pattern=r'^gr-[0-9a-f]{16}$')]
TaskGraphNodeId=Annotated[str,Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,31}$')]


class TaskGraphLimits(StrictModel):
    input_tokens:int=Field(default=2000000,ge=1,le=20000000)
    output_tokens:int=Field(default=100000,ge=1,le=2000000)
    tool_calls:int=Field(default=400,ge=1,le=4000)
    # Integer micro-yuan keep request and plan hashes exact across Python/JS.
    cost_microyuan:int=Field(default=20000000,ge=1,le=2000000000)


class TaskGraphNode(AttemptContext):
    node_id:TaskGraphNodeId
    label:str=Field(default='',max_length=100)
    task_ref:TaskVersionReference
    agent_ref:AgentVersionReference
    depends_on:list[TaskGraphNodeId]=Field(default_factory=list,max_length=20)

    @field_validator('label')
    @classmethod
    def nonblank_label(cls,value):
        if value and not value.strip():raise ValueError('blank node label')
        return value


def topological_ids(nodes):
    """Stable Kahn order; no inferred edges or executable expressions."""
    if not 1<=len(nodes)<=20:raise ValueError('graph node limit')
    byid={node.node_id:node for node in nodes}
    if len(byid)!=len(nodes):raise ValueError('duplicate graph node')
    degrees={};children={ident:[] for ident in byid}
    for node in nodes:
        parents=node.depends_on
        if len(set(parents))!=len(parents):raise ValueError('duplicate graph dependency')
        if node.node_id in parents or any(parent not in byid for parent in parents):raise ValueError('invalid graph dependency')
        degrees[node.node_id]=len(parents)
        for parent in parents:children[parent].append(node.node_id)
    ready=sorted(ident for ident,count in degrees.items() if count==0);ordered=[]
    while ready:
        ident=ready.pop(0);ordered.append(ident)
        for child in sorted(children[ident]):
            degrees[child]-=1
            if degrees[child]==0:ready.append(child)
        ready.sort()
    if len(ordered)!=len(nodes):raise ValueError('cyclic graph')
    return ordered


class TaskGraphFields(VersionOne):
    graph_id:TaskGraphId
    project_revision:int=Field(ge=1)
    title:str=Field(min_length=1,max_length=100)
    nodes:list[TaskGraphNode]=Field(min_length=1,max_length=20)
    concurrency:int=Field(default=2,ge=1,le=4)
    limits:TaskGraphLimits=Field(default_factory=TaskGraphLimits)
    retired:bool=False

    @field_validator('title')
    @classmethod
    def nonblank_title(cls,value):
        if not value.strip():raise ValueError('blank graph title')
        return value

    @model_validator(mode='after')
    def acyclic(self):
        topological_ids(self.nodes)
        return self


class TaskGraphCommand(TaskGraphFields):
    schema_version:Literal[1]=Field(...)
    expected_revision:int=Field(ge=0,le=199)


class TaskGraphRevision(TaskGraphFields):
    project_id:ProjectId
    revision:int=Field(ge=1,le=200)
    created_by:str=Field(min_length=1,max_length=128)


class TaskGraphReference(StrictModel):
    graph_id:TaskGraphId
    revision:int=Field(ge=1,le=200)
    digest:Digest
    source_seq:int=Field(ge=1)
