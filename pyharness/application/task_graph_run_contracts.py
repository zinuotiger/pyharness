"""Frozen graph authorization and bounded node plans; never client authority."""
import hashlib,json
from decimal import Decimal,ROUND_CEILING
from typing import Annotated,Literal
from pydantic import Field,model_validator
from pyharness.application.document_contracts import VersionOne
from pyharness.application.workspace_manifest import StrictModel
from pyharness.application.workspace_contracts import ProjectId,Digest
from pyharness.application.task_graph_contracts import TaskGraphReference,TaskGraphRunId,TaskGraphNodeId,TaskGraphLimits
from pyharness.application.execution_contracts import AgentVersionReference,ExecutionEnvironment,ExecutionId
from pyharness.application.run_attempt_contracts import AttemptId,AttemptContext,SubmissionKey
from pyharness.application.product_task_contracts import RunAttemptPrerequisites
from pyharness.application.platform_models import AgentDefinition

GraphSessionId=Annotated[str,Field(pattern=r'^s-pg-[0-9a-f]{32}$')]


class PreviewTaskGraph(VersionOne):
    schema_version:Literal[1]=Field(...)
    graph_ref:TaskGraphReference


class StartTaskGraph(PreviewTaskGraph):
    graph_run_id:TaskGraphRunId
    confirmation_digest:Digest
    confirm_host_boundary:bool=False


class TaskGraphRunReference(StrictModel):
    graph_run_id:TaskGraphRunId
    revision:int=Field(ge=1,le=1)
    digest:Digest
    source_seq:int=Field(ge=1)


def node_allocation(environment):
    budget=AgentDefinition.model_validate(environment.agent_snapshot).budget
    micro=int((Decimal(str(budget.cost))*1000000).to_integral_value(rounding=ROUND_CEILING))
    return TaskGraphLimits(input_tokens=budget.input_tokens,output_tokens=budget.output_tokens,tool_calls=budget.tool_calls,cost_microyuan=micro)


class CompiledGraphNode(StrictModel):
    node_id:TaskGraphNodeId
    depends_on:list[TaskGraphNodeId]=Field(max_length=20)
    agent_ref:AgentVersionReference
    prerequisites:RunAttemptPrerequisites
    context:AttemptContext
    environment:ExecutionEnvironment
    allocated_budget:TaskGraphLimits

    @model_validator(mode='after')
    def published_allocation(self):
        snapshot=self.environment.agent_snapshot;reference=self.agent_ref
        checksum=hashlib.sha256(json.dumps(snapshot,sort_keys=True).encode()).hexdigest()
        if (snapshot.get('status')!='published' or snapshot.get('agent_id')!=reference.agent_id or
                snapshot.get('current_version')!=reference.version or checksum!=reference.sha256):raise ValueError('graph Agent reference')
        if self.allocated_budget!=node_allocation(self.environment):raise ValueError('graph allocation differs from published Agent')
        return self


class GraphNodePlan(CompiledGraphNode):
    attempt_id:AttemptId
    execution_id:ExecutionId


def total_allocation(nodes):
    return TaskGraphLimits(**{key:sum(getattr(node.allocated_budget,key) for node in nodes) for key in TaskGraphLimits.model_fields})


class TaskGraphRunAuthorization(VersionOne):
    graph_run_id:TaskGraphRunId
    revision:int=Field(default=1,ge=1,le=1)
    tenant_id:str=Field(min_length=1,max_length=128)
    project_id:ProjectId
    project_revision:int=Field(ge=1)
    created_by:str=Field(min_length=1,max_length=128)
    submission_key:SubmissionKey
    selection:StartTaskGraph
    session_id:GraphSessionId
    graph_task_id:str=Field(pattern=r'^p-[0-9a-f]{32}$')
    nodes:list[GraphNodePlan]=Field(min_length=1,max_length=20)
    concurrency:int=Field(ge=1,le=4)
    limits:TaskGraphLimits
    allocated_budget:TaskGraphLimits
    dispatch_policy:Literal['governed_ready_nodes']='governed_ready_nodes'

    @model_validator(mode='after')
    def exact_plans(self):
        if self.selection.graph_run_id!=self.graph_run_id:raise ValueError('graph run identity')
        for key in ('node_id','attempt_id','execution_id'):
            if len({getattr(node,key) for node in self.nodes})!=len(self.nodes):raise ValueError('duplicate graph node admission identity')
        for node in self.nodes:
            p=node.prerequisites
            if (p.tenant_id!=self.tenant_id or p.subject_ref!=self.created_by or p.project_id!=self.project_id or
                    p.project_revision!=self.project_revision):raise ValueError('graph node authority scope')
            if node.environment.boundary=='host_unrestricted' and not self.selection.confirm_host_boundary:raise ValueError('graph host confirmation missing')
        if total_allocation(self.nodes)!=self.allocated_budget:raise ValueError('graph allocation sum')
        if any(getattr(self.allocated_budget,key)>getattr(self.limits,key) for key in TaskGraphLimits.model_fields):raise ValueError('graph budget exceeded')
        return self


class GraphDispatch(VersionOne):
    dispatch_id:Annotated[str,Field(pattern=r'^gd-[0-9a-f]{16}$')]
    revision:int=Field(default=1,ge=1,le=1)
    project_id:ProjectId
    created_by:str=Field(min_length=1,max_length=128)
    submission_key:SubmissionKey
    graph_run_ref:TaskGraphRunReference
    node_id:TaskGraphNodeId


class CancelTaskGraph(VersionOne):
    schema_version:Literal[1]=Field(...)
    cancellation_id:Annotated[str,Field(pattern=r'^gc-[0-9a-f]{16}$')]
    graph_run_ref:TaskGraphRunReference


class GraphCancellation(CancelTaskGraph):
    revision:int=Field(default=1,ge=1,le=1)
    project_id:ProjectId
    created_by:str=Field(min_length=1,max_length=128)
    submission_key:SubmissionKey
