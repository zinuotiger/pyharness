"""Scoped metadata and original command lookup; no writer or execution owner."""
import time
from pyharness.application.experiment_plan import experiment_ref
from pyharness.errors import PyHError,raise_code


class ExperimentDirectoryStore:
    async def experiment_directory(self,project,kind,*,authorize,projects):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            if kind=='experiment_run':return self._experiment_control_directory(project,authorize)
            actor=authorize();resources,_,_=await self._execution_read_state();self._context_project(resources,project);items=[]
            for row in resources.values():
                value=row.get(kind)
                if not value or value['project_id']!=project or value['created_by']!=actor:continue
                if time.monotonic()-started>=self.MAX_WORKSPACE_CAPTURE_SECONDS:raise_code('EVT-104',reason='experiment_directory_time_limit')
                item=experiment_ref(row,kind)
                try:
                    if kind=='experiment_definition':
                        self._experiment_check_definition(resources,row,actor,authorize,started,projects);item['title']=value['selection']['title']
                    else:
                        run=row if kind=='experiment_run' else resources[value['selection']['experiment_run_ref']['experiment_run_id']]
                        self._experiment_run_current(resources,run,actor,authorize,started,projects)
                    item['status']='available'
                except PyHError as exc:
                    if exc.code not in ('GRD-403','EVT-101'):raise
                    item['status']='source_unavailable'
                items.append(item)
            self._context_finish(authorize,actor,resources)
            return dict(schema_version=1,project_id=project,items=items,total=len(items),execution_authorized=False,automatic_dispatch=False)

    def _experiment_control_directory(self,project,authorize):
        from pyharness.application.experiment_run_binding import cancel_requested,matching_graph
        actor=authorize();resources,_=self._cancellation_state()
        if not resources.get(project,{}).get('project'):raise_code('GRD-403',reason='experiment_control_project_unavailable')
        items=[]
        for row in resources.values():
            value=row.get('experiment_run')
            if not value or value['project_id']!=project or value['created_by']!=actor:continue
            reference=experiment_ref(row,'experiment_run');graph=matching_graph(self,resources,row)
            cancelled=cancel_requested(resources,reference) or bool(graph and self._graph_cancelled(resources,self._s05_ref(graph,'graph_run_id','task_graph_run')))
            items.append(dict(reference,status='available',cancel_requested=cancelled))
        if authorize()!=actor:raise_code('GRD-403',reason='experiment_control_subject_changed')
        return dict(schema_version=1,project_id=project,items=items,total=len(items),control_only=True,execution_authorized=False,automatic_dispatch=False)

    async def experiment_command_record(self,project,key,*,authorize):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,commands,_=await self._execution_read_state()
            if not resources.get(project,{}).get('project'):raise_code('GRD-403',reason='experiment_command_project_unavailable')
            old=commands.get((actor,key));row=old[1] if old else None
            if row:
                kind=next((k for k in ('experiment_preparation','experiment_run','experiment_report','experiment_cancellation') if k in row),None)
                if kind is None or row[kind]['project_id']!=project:raise_code('GRD-403',reason='experiment_command_unavailable')
            else:kind=None
            if authorize()!=actor:raise_code('GRD-403',reason='experiment_command_subject_changed')
            return row,kind,self._failed
