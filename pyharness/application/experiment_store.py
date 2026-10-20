"""Prepared experiments share the original capture owner, history and poisoned writer."""
import time
from pyharness.application.experiment_contracts import ConfirmExperimentPreparation
from pyharness.application.experiment_records import ExperimentPreparation,ExperimentRevision
from pyharness.application.experiment_plan import (preparation_plan,preparation_identity,
    experiment_request,experiment_ref,experiment_summary)
from pyharness.application.experiment_support import (fixed_sources,finish_key,finish_request,
    check_definition_plan,validate_experiment_record)
from pyharness.application.regression_contracts import current_regression_content
from pyharness.errors import PyHError,raise_code
from pyharness.application.experiment_run_store import ExperimentRunStore
from pyharness.application.experiment_report_store import ExperimentReportStore
from pyharness.application.experiment_directory_store import ExperimentDirectoryStore


class ExperimentStore(ExperimentRunStore,ExperimentReportStore,ExperimentDirectoryStore):
    def _validate_experiment_record(self,definition,resources,previous,event,kind,history):
        validate_experiment_record(self,definition,resources,previous,event,kind,history)

    def _experiment_lookup(self,resources,project,actor):
        return lambda kind,ident,revision:self._s05_history(resources,project,ident,revision,kind,actor)

    def _experiment_current_plan(self,resources,project,selection,actor,authorize,started,projects,*,check_revision):
        self._context_project(resources,project,selection.project_revision);current_regression_content(selection,reading=not check_revision)
        if check_revision:
            old=resources.get(selection.experiment_id)
            if old:self._context_owned(resources,selection.experiment_id,'experiment_definition',project,actor)
            if (old['experiment_definition']['revision'] if old else 0)!=selection.expected_revision:
                raise_code('EVT-101',reason='stale_revision')
        dataset,cases,attempts=fixed_sources(self,resources,project,selection,actor,self._experiment_lookup(resources,project,actor))
        self._regression_check_record(resources,dataset,'regression_dataset',actor,authorize,started,projects)
        plan=preparation_plan(self,project,selection,dataset,cases,attempts,actor)
        for side in ('left','right'):
            reference=getattr(selection,side+'_agent_ref')
            if check_revision:projects._graph_agent_version(reference)
            else:
                # Historical observation proves an immutable published snapshot;
                # retirement limits new bindings, not still-authorized evidence.
                version=projects.service.platform.version(reference.agent_id,reference.version)
                if version['sha256']!=reference.sha256 or version['snapshot'].get('status')!='published':
                    raise_code('EVT-101',reason='experiment_agent_version_reference')
        for task in plan['tasks']:
            value=dict(task['command'],project_id=project)
            wi=self._task_bound_input(resources,value,actor)['workspace_input']
            self._input_gate(resources,wi,actor,authorize,started)
        for draft in plan['node_drafts']:
            context={k:draft[k] for k in ('schema_version','document_refs','citation_refs','delivery_ref')}
            self._handoff_context_binding(resources,project,context,actor,draft['agent_ref'])
        self._context_finish(authorize,actor,resources);return plan

    async def experiment_preparation_preview(self,project,selection,*,authorize,projects):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,_,_=await self._execution_read_state()
            return self._experiment_current_plan(resources,project,selection,actor,authorize,started,projects,check_revision=True)

    async def ensure_experiment_preparation(self,project,selection,*,command_id,authorize,projects):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();await self._open(initialize=False);resources,commands=self._grant_state()
            request=experiment_request(project,selection);ident=preparation_identity(self.tenant_id,actor,project,command_id)
            self._context_envelope('experiment_preparation',ident,1,command_id,actor,request)
            if old:=commands.get((actor,command_id)):
                if old[0]!=request or 'experiment_preparation' not in old[1]:raise_code('EVT-100',reason='idempotency_conflict')
                return experiment_ref(old[1],'experiment_preparation')
            def gate():
                fresh,_=self._grant_state()
                plan=self._experiment_current_plan(fresh,project,selection,actor,authorize,started,projects,check_revision=True)
                if plan['confirmation_digest']!=selection.confirmation_digest:raise_code('EVT-101',reason='experiment_plan_changed')
                for row in fresh.values():
                    value=row.get('experiment_preparation')
                    if value and value['project_id']==project and value['selection']['experiment_id']==selection.experiment_id and value['selection']['expected_revision']==selection.expected_revision:
                        raise_code('EVT-101',reason='experiment_preparation_original_key_required')
                self._context_quota(fresh,'experiment_preparation',project)
            gate()
            definition=ExperimentPreparation(preparation_id=ident,project_id=project,project_revision=selection.project_revision,
                created_by=actor,submission_key=command_id,selection=selection,plan_digest=selection.confirmation_digest)
            _,row=await self._append_context(definition,'experiment_preparation',ident,command_id,actor,request,gate)
            return experiment_ref(row,'experiment_preparation')

    async def experiment_existing_task(self,project,command,*,authorize):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,_,_=await self._execution_read_state();row=resources.get(command['product_task_id'])
            if row is None:return None
            value=self._context_owned(resources,command['product_task_id'],'product_task',project,actor)['product_task']
            if value['revision']!=1 or {k:value[k] for k in command if k!='expected_revision'}!={k:v for k,v in command.items() if k!='expected_revision'}:
                raise_code('EVT-101',reason='experiment_prepared_task_changed')
            reasons=self._task_access(resources,row,actor,authorize,started)
            if reasons:raise_code('EVT-101',reason='experiment_prepared_task_stale')
            return self._s05_ref(row,'product_task_id','product_task')

    def _experiment_check_definition(self,resources,row,actor,authorize,started,projects):
        value=row['experiment_definition'];project=value['project_id']
        self._context_owned(resources,value['experiment_id'],'experiment_definition',project,actor)
        selection=ConfirmExperimentPreparation.model_validate(value['selection'])
        plan=self._experiment_current_plan(resources,project,selection,actor,authorize,started,projects,check_revision=False)
        ref=value['preparation_ref'];prep=self._s05_history(resources,project,ref['preparation_id'],1,'experiment_preparation',actor)
        prep=dict(prep,experiment_preparation=dict(prep['experiment_preparation'],selection_model=selection))
        check_definition_plan(self,resources,value,prep,plan,self._experiment_lookup(resources,project,actor))
        graph=self._graph_revision(resources,project,value['graph_ref']['graph_id'],value['graph_ref']['revision'],actor)
        self._graph_summary(resources,graph,actor,authorize,started)
        self._context_finish(authorize,actor,resources)

    async def finish_experiment(self,project,selection,preparation_ref,graph_ref,case_nodes,dataset_total,*,authorize,projects):
        started=time.monotonic();ident=selection.experiment_id;key=finish_key(preparation_ref['preparation_id'])
        async with self._workspace_capture_lock(started):
            actor=authorize();await self._open(initialize=False);resources,commands=self._grant_state()
            definition=ExperimentRevision(experiment_id=ident,project_id=project,project_revision=selection.project_revision,
                revision=selection.expected_revision+1,created_by=actor,submission_key=key,selection=selection,
                preparation_ref=preparation_ref,graph_ref=graph_ref,case_nodes=case_nodes,dataset_total=dataset_total)
            request=finish_request(definition);self._context_envelope('experiment_definition',ident,definition.revision,key,actor,request)
            if old:=commands.get((actor,key)):
                if old[0]!=request or 'experiment_definition' not in old[1]:raise_code('EVT-100',reason='idempotency_conflict')
                self._experiment_check_definition(resources,old[1],actor,authorize,started,projects)
                return experiment_summary(old[1])
            def gate():
                fresh,_=self._grant_state()
                self._experiment_current_plan(fresh,project,selection,actor,authorize,started,projects,check_revision=True)
                self._context_quota(fresh,'experiment_definition',project)
                self._experiment_check_definition(dict(fresh,**{ident:dict(experiment_definition=definition.model_dump())}),
                    dict(experiment_definition=definition.model_dump()),actor,authorize,started,projects)
            gate()
            resources,row=await self._append_context(definition,'experiment_definition',ident,key,actor,request,gate)
            self._experiment_check_definition(resources,row,actor,authorize,started,projects);return experiment_summary(row)

    async def experiment_record(self,project,ident,revision,*,authorize,projects):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,_,_=await self._execution_read_state()
            row=self._s05_history(resources,project,ident,revision,'experiment_definition',actor) if revision is not None else self._context_owned(resources,ident,'experiment_definition',project,actor)
            self._experiment_check_definition(resources,row,actor,authorize,started,projects);return experiment_summary(row)

    async def experiment_submission(self,project,key,*,authorize,projects):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,commands,_=await self._execution_read_state();self._context_project(resources,project)
            old=commands.get((actor,key));prep=old[1] if old else None;receipt=None;status='not_found'
            if prep:
                if 'experiment_preparation' not in prep or prep['experiment_preparation']['project_id']!=project:
                    raise_code('GRD-403',reason='experiment_submission_unavailable')
                value=prep['experiment_preparation'];sel=ConfirmExperimentPreparation.model_validate(value['selection'])
                self._experiment_current_plan(resources,project,sel,actor,authorize,started,projects,check_revision=False)
                status='preparing'
                latest=resources.get(sel.experiment_id,{}).get('experiment_definition')
                if latest and latest['revision']>=sel.expected_revision+1:
                    row=self._s05_history(resources,project,sel.experiment_id,sel.expected_revision+1,'experiment_definition',actor)
                    if row['experiment_definition']['preparation_ref']==experiment_ref(prep,'experiment_preparation'):
                        self._experiment_check_definition(resources,row,actor,authorize,started,projects)
                        receipt=experiment_summary(row);status='committed'
            self._context_finish(authorize,actor,resources)
            return dict(schema_version=1,project_id=project,submission_key=key,status=status,receipt=receipt,
                preparation=prep['experiment_preparation'] if prep else None,writer_requires_reopen=self._failed,execution_authorized=False)
