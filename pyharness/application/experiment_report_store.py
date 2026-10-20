"""Bounded reports use the original journal and re-read their exact frozen evidence."""
import time
from pyharness.application.experiment_report_contracts import ExperimentReportData,ExperimentReportBasis,ExperimentReport
from pyharness.application.experiment_report_binding import report_confirmation,prefix_gate,canonical,digest_of
from pyharness.application.experiment_run_binding import control_run,matching_graph,run_request,cancel_requested
from pyharness.application.experiment_plan import experiment_ref
from pyharness.application.regression_support import exact_case
from pyharness.errors import raise_code


class ExperimentReportStore:
    async def _experiment_report_data(self,resources,events,project,reference,basis,actor,authorize,started,projects):
        from pyharness.application.experiment_report_evaluation import read_node,case_comparison,report_counts,checker_implementation,VERSION
        from pyharness.application.regression_candidates import event_prefix_digest
        current=control_run(self,resources,project,reference.model_dump(),actor)
        self._experiment_run_current(resources,current,actor,authorize,started,projects)
        fixed=basis is not None;through=basis.control.observed_seq if fixed else events[-1].seq
        digest=event_prefix_digest(events,through)
        if through<reference.source_seq or fixed and digest!=basis.control.prefix_digest:raise_code('EVT-101',reason='experiment_report_control_prefix')
        historical=[e for e in events if e.seq<=through];values,_=self._project(historical)
        from pyharness.application.execution_control_projection import ControlReadResources
        values=ControlReadResources(values,historical);run=control_run(self,values,project,reference.model_dump(),actor)
        graph=matching_graph(self,values,run);value=run['experiment_run'];ref=value['selection']['experiment_ref']
        definition=self._s05_history(values,project,ref['experiment_id'],ref['revision'],'experiment_definition',actor)['experiment_definition']
        plans={n['node_id']:n for n in graph['task_graph_run']['nodes']} if graph else {n['node_id']:dict(n,execution_id=None) for n in value['graph_plan']['nodes']}
        if fixed and set(basis.nodes)!=set(plans):raise_code('EVT-101',reason='experiment_report_node_coverage')
        prefixes={};pairs=[];regressions=[];scan_budget=dict(events=50000,bytes=32*1024*1024,physical_bytes=32*1024*1024)
        cancelled=cancel_requested(values,reference.model_dump())
        for binding in definition['case_nodes']:
            case=exact_case(self,values,project,binding['case_ref'],actor)['regression_case'];measurements={}
            for side in ('left','right'):
                if time.monotonic()-started>=self.MAX_WORKSPACE_CAPTURE_SECONDS:raise_code('EVT-104',reason='experiment_report_time_limit')
                ident=binding[side+'_node_id'];expected=[p.model_dump() for p in basis.nodes[ident]] if fixed and basis.nodes[ident] is not None else None
                result,observed=await read_node(projects,values,plans[ident],case['selection']['assertions'],expected,
                    fixed=fixed,actor=actor,authorize=authorize,started=started,scan_budget=scan_budget,validation_resources=resources)
                if cancelled and result['execution_status']=='not_dispatched':result['execution_status']='cancelled'
                measurements[side]=result;prefixes[ident]=observed
            pair,found=case_comparison(binding['case_ref'],measurements['left'],measurements['right']);pairs.append(pair);regressions.extend(found)
        frozen=ExperimentReportBasis(control=dict(observed_seq=through,prefix_digest=digest),nodes=prefixes)
        data=ExperimentReportData(experiment_run_ref=reference,experiment_ref=value['selection']['experiment_ref'],
            graph_run_ref=self._s05_ref(graph,'graph_run_id','task_graph_run') if graph else None,
            dataset_ref=definition['selection']['dataset_ref'],dataset_total=definition['dataset_total'],selected_count=len(pairs),
            excluded_count=definition['dataset_total']-len(pairs),basis=frozen,checker_version=VERSION,checker_implementation=checker_implementation(),
            cases=pairs,regressions=regressions,**report_counts([c[s] for c in pairs for s in ('left','right')]))
        if len(canonical(data.model_dump()))>262144:raise_code('EVT-100',reason='experiment_report_size_limit')
        # Source and current policy are rechecked after all original root/child I/O.
        fresh,_,_=await self._execution_read_state()
        self._experiment_run_current(fresh,current,actor,authorize,started,projects)
        prefix_gate(self,projects,frozen)
        if time.monotonic()-started>=self.MAX_WORKSPACE_CAPTURE_SECONDS:raise_code('EVT-104',reason='experiment_report_time_limit')
        self._context_finish(authorize,actor,fresh)
        return data,value['project_revision']

    async def experiment_report_preview(self,project,command,*,authorize,projects):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,_,events=await self._execution_read_state()
            data,_=await self._experiment_report_data(resources,events,project,command.experiment_run_ref,command.basis,actor,authorize,started,projects)
            digest=digest_of(canonical(data.model_dump()));basis=data.basis.model_dump()
            return dict(schema_version=1,report_id=command.report_id,experiment_run_ref=command.experiment_run_ref.model_dump(),basis=basis,
                report_data=data.model_dump(),report_canonical=canonical(data.model_dump()).decode('utf-8'),report_digest=digest,confirmation_digest=report_confirmation(self.tenant_id,actor,project,
                command.report_id,command.experiment_run_ref.model_dump(),basis,digest),status='confirmation_required',persisted=False,
                execution_authorized=False,export_authorized=False,release_authorized=False)

    async def save_experiment_report(self,project,command,key,*,authorize,projects):
        from pyharness.application.experiment_report_evaluation import checker_implementation
        started=time.monotonic();kind='experiment_report';ident=command.report_id
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,commands,events=await self._execution_read_state();request=run_request(project,command,'experiment.report.save')
            self._context_envelope(kind,ident,1,key,actor,request)
            if old:=commands.get((actor,key)):
                if old[0]!=request or kind not in old[1]:raise_code('EVT-100',reason='idempotency_conflict')
                await self._experiment_report_check(resources,events,old[1],actor,authorize,started,projects)
                return self._experiment_report_summary(old[1])
            data,revision=await self._experiment_report_data(resources,events,project,command.experiment_run_ref,command.basis,actor,authorize,started,projects)
            digest=digest_of(canonical(data.model_dump()))
            confirmation=report_confirmation(self.tenant_id,actor,project,ident,command.experiment_run_ref.model_dump(),command.basis.model_dump(),digest)
            if digest!=command.report_digest or confirmation!=command.confirmation_digest:raise_code('EVT-101',reason='experiment_report_preview_changed')
            definition=ExperimentReport(report_id=ident,project_id=project,project_revision=revision,created_by=actor,submission_key=key,selection=command,report_data=data)
            await self._open(initialize=False)
            def gate():
                fresh,_=self._grant_state()
                if ident in fresh:raise_code('EVT-101',reason='experiment_report_identity_reserved')
                row=control_run(self,fresh,project,command.experiment_run_ref.model_dump(),actor)
                self._experiment_run_current(fresh,row,actor,authorize,started,projects)
                if checker_implementation()!=data.checker_implementation:raise_code('EVT-101',reason='experiment_report_checker_changed')
                prefix_gate(self,projects,data.basis);self._context_quota(fresh,kind,project)
            gate();resources,row=await self._append_context(definition,kind,ident,key,actor,request,gate)
            return self._experiment_report_summary(row)

    def _experiment_report_summary(self,row):
        value=row['experiment_report'];data=value['report_data']
        return dict(schema_version=1,report_id=value['report_id'],revision=1,project_id=value['project_id'],digest=row['digest'],source_seq=row['source_seq'],
            report_data=data,report_canonical=canonical(data).decode('utf-8'),report_digest=digest_of(canonical(data)),persisted=True,recomputed=True,
            record_canonical=canonical(value).decode('utf-8'),request_digest=row['request_digest'],submission_key=value['submission_key'],
            execution_authorized=False,export_authorized=False,release_authorized=False)

    async def _experiment_report_check(self,resources,events,row,actor,authorize,started,projects):
        from pyharness.application.experiment_report_evaluation import checker_implementation
        value=row['experiment_report'];command=ExperimentReport.model_validate(value).selection
        if value['report_data']['checker_implementation']!=checker_implementation():raise_code('EVT-101',reason='experiment_report_checker_changed')
        data,_=await self._experiment_report_data(resources,events,value['project_id'],command.experiment_run_ref,command.basis,actor,authorize,started,projects)
        if canonical(data.model_dump())!=canonical(value['report_data']):raise_code('EVT-101',reason='experiment_report_evidence_changed')

    async def experiment_report(self,project,ident,*,authorize,projects,export=None,confirmed=False):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,_,events=await self._execution_read_state()
            row=self._context_owned(resources,ident,'experiment_report',project,actor)
            await self._experiment_report_check(resources,events,row,actor,authorize,started,projects)
            result=self._experiment_report_summary(row)
            if export is not None:
                from pyharness.application.experiment_report_public import public_report_bytes
                raw=public_report_bytes(result['report_data']);digest=digest_of(raw);value=row['experiment_report']
                confirmation=report_confirmation(self.tenant_id,actor,project,ident,value['selection']['experiment_run_ref'],
                    value['selection']['basis'],digest,export=True)
                if confirmed and export.confirmation_digest!=confirmation:raise_code('EVT-101',reason='experiment_report_export_changed')
                result=dict(schema_version=1,report_id=ident,report_digest=digest,confirmation_digest=confirmation,
                    export_authorized=confirmed,persisted=False,execution_authorized=False,release_authorized=False)
                if confirmed:result.update(report_canonical=raw.decode('utf-8'),content_type='application/json',suggested_filename='pyharness-'+ident+'.json')
                else:result['status']='confirmation_required'
            self._context_finish(authorize,actor,resources);return result
