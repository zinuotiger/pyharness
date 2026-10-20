"""Regression versions on the existing owner; previews/reads never open a writer."""
import time
from pyharness.application.regression_records import REGRESSION_MODELS
from pyharness.application.regression_contracts import CandidateEvidence,current_regression_content,current_regression_evidence
from pyharness.application.regression_transfer_store import RegressionTransferStore
from pyharness.application.regression_support import (regression_request,regression_ref,regression_summary,
    exact_case,source_metadata,historical_business_failure,validate_regression_record,import_source_basis)
from pyharness.errors import PyHError,raise_code


class RegressionStore(RegressionTransferStore):
    def _validate_regression_record(self,definition,resources,previous,event,kind,history):
        validate_regression_record(self,definition,resources,previous,event,kind,history)

    def _regression_source_current(self,resources,project,origin,actor,authorize,started):
        self._context_project(resources,project)
        row=source_metadata(self,resources,project,origin,actor)
        summary=self._execution_summary(resources,row,actor,authorize,started)
        return self._execution_read_row(resources,row),summary

    def _regression_fresh_source(self,project,origin,actor,authorize,started):
        resources,_,_=self._delivery_read_snapshot()
        self._regression_source_current(resources,project,origin,actor,authorize,started)
        self._context_finish(authorize,actor,resources)

    def _regression_origin_evidence(self,resources,project,origin,actor,authorize,started,projects):
        from pyharness.application.product_execution_sessions import read_execution_events
        from pyharness.application.regression_candidates import event_prefix_digest,public_candidate
        source,_=self._regression_source_current(resources,project,origin,actor,authorize,started)
        events=read_execution_events(projects.service._tenant_sessions_dir(),source,self.tenant_id)
        if event_prefix_digest(events,origin['observed_seq'])!=origin['prefix_digest']:
            raise_code('EVT-101',reason='regression_source_prefix_changed')
        prefix=[e for e in events if e.seq<=origin['observed_seq']]
        phases=[e.payload['phase'] for e in prefix if e.type=='execution.lifecycle']
        expected='failed' if origin['failure_kind']=='execution_failed' else 'completed'
        if not phases or phases[-1]!=expected:raise_code('EVT-101',reason='regression_failure_prefix')
        if origin['failure_kind']=='business_failed':historical_business_failure(self,resources,origin)
        evidence=CandidateEvidence.model_validate(public_candidate(project,origin,prefix))
        self._regression_fresh_source(project,origin,actor,authorize,started)
        return evidence

    def _regression_candidate_evidence(self,resources,project,command,actor,authorize,started,projects):
        from pyharness.application.workspace_store import canonical,digest_of
        evidence=self._regression_origin_evidence(resources,project,command.origin.model_dump(),actor,authorize,started,projects)
        if digest_of(canonical(evidence.model_dump()))!=command.candidate_digest:
            raise_code('EVT-101',reason='regression_candidate_changed')
        return evidence

    def _regression_check_case(self,resources,row,actor,authorize,started,projects):
        from pyharness.application.workspace_store import canonical
        value=row['regression_case'];project=value['project_id'];origin=value['selection']['origin']
        self._context_owned(resources,value['case_id'],'regression_case',project,actor)
        current_regression_content(value['selection'],reading=True)
        current_regression_evidence(value['evidence'])
        evidence=self._regression_origin_evidence(resources,project,origin,actor,authorize,started,projects)
        if canonical(evidence.model_dump())!=canonical(value['evidence']):
            raise_code('EVT-101',reason='regression_public_evidence_changed')

    def _regression_check_record(self,resources,row,kind,actor,authorize,started,projects):
        value=row[kind];self._context_project(resources,value['project_id'])
        if kind=='regression_case':self._regression_check_case(resources,row,actor,authorize,started,projects)
        else:
            self._context_owned(resources,value['dataset_id'],kind,value['project_id'],actor)
            current_regression_content(value['selection'],reading=True)
            for ref in value['selection']['case_refs']:
                case=exact_case(self,resources,value['project_id'],ref,actor)
                self._regression_check_case(resources,case,actor,authorize,started,projects)
        self._context_finish(authorize,actor,resources)

    def _regression_revision(self,resources,project,ident,revision,kind,actor):
        if revision is None:return self._context_owned(resources,ident,kind,project,actor)
        return self._s05_history(resources,project,ident,revision,kind,actor)

    async def regression_record(self,project,ident,revision,kind,*,authorize,projects):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,_,_=await self._execution_read_state()
            row=self._regression_revision(resources,project,ident,revision,kind,actor)
            self._regression_check_record(resources,row,kind,actor,authorize,started,projects)
            return regression_summary(row,kind)

    async def regression_records(self,project,kind,*,authorize,projects):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,_,_=await self._execution_read_state();self._context_project(resources,project)
            items=[];key=REGRESSION_MODELS[kind][2]
            for row in resources.values():
                value=row.get(kind)
                if not value or value['project_id']!=project or value['created_by']!=actor:continue
                try:
                    self._regression_check_record(resources,row,kind,actor,authorize,started,projects)
                    item=dict(regression_ref(row,kind),title=value['selection']['title'],status='available')
                except PyHError as exc:
                    if exc.code not in ('GRD-403','EVT-101','PERS-202'):raise
                    item=dict(regression_ref(row,kind),status='source_unavailable')
                items.append(item)
            self._context_finish(authorize,actor,resources)
            return dict(schema_version=1,project_id=project,items=items,total=len(items),execution_authorized=False)

    async def save_regression(self,project,command,kind,*,command_id,authorize,projects):
        started=time.monotonic();model,_,key=REGRESSION_MODELS[kind];ident=getattr(command,key)
        async with self._workspace_capture_lock(started):
            actor=authorize()
            # Reject a caller-supplied foreign source before opening any writer.
            if kind=='regression_case' and (command.origin.tenant_id!=self.tenant_id or command.origin.subject_ref!=actor):
                raise_code('GRD-403',reason='regression_source_subject')
            current_regression_content(command)
            await self._open(initialize=False);resources,commands=self._grant_state()
            request=regression_request(project,kind,command)
            self._context_envelope(kind,ident,command.expected_revision+1,command_id,actor,request)
            if old:=commands.get((actor,command_id)):
                if old[0]!=request or kind not in old[1]:raise_code('EVT-100',reason='idempotency_conflict')
                self._regression_check_record(resources,old[1],kind,actor,authorize,started,projects)
                return regression_summary(old[1],kind)
            self._context_project(resources,project,command.project_revision)
            evidence={}
            if kind=='regression_case':evidence['evidence']=self._regression_candidate_evidence(resources,project,command,actor,authorize,started,projects)
            definition=model(**{key:ident},project_id=project,project_revision=command.project_revision,
                revision=command.expected_revision+1,created_by=actor,submission_key=command_id,selection=command,**evidence)
            def gate():
                current_regression_content(command)
                fresh,_=self._grant_state();self._context_project(fresh,project,command.project_revision)
                previous=fresh.get(ident)
                if previous:self._context_owned(fresh,ident,kind,project,actor)
                if (previous[kind]['revision'] if previous else 0)!=command.expected_revision:
                    raise_code('EVT-101',reason='stale_revision')
                self._context_quota(fresh,kind,project)
                if kind=='regression_case':
                    current=self._regression_candidate_evidence(fresh,project,command,actor,authorize,started,projects)
                    if current!=definition.evidence:raise_code('EVT-101',reason='regression_candidate_changed')
                else:
                    import_source_basis(self,fresh,project,command,actor)
                    for ref in command.case_refs:
                        case=exact_case(self,fresh,project,ref,actor)
                        self._regression_check_case(fresh,case,actor,authorize,started,projects)
                self._context_finish(authorize,actor,fresh)
            gate()
            resources,row=await self._append_context(definition,kind,ident,command_id,actor,request,gate)
            self._regression_check_record(resources,row,kind,actor,authorize,started,projects)
            return regression_summary(row,kind)
