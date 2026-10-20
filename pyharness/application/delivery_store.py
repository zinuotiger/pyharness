"""Delivery writes share the original strong control commit and capture lock."""
import time
from pyharness.application.delivery_support import DeliverySupport,delivery_request,selected_outputs
from pyharness.application.delivery_records import DeliveryPackage
from pyharness.application.delivery_role_store import DeliveryRoleStore
from pyharness.application.delivery_export_store import DeliveryExportStore
from pyharness.application.handoff_document_store import HandoffDocumentStore
from pyharness.application.handoff_execution_store import HandoffExecutionStore
from pyharness.application.delivery_read_store import DeliveryReadStore
from pyharness.errors import raise_code


class DeliveryStore(DeliverySupport,DeliveryRoleStore,DeliveryExportStore,HandoffDocumentStore,HandoffExecutionStore,DeliveryReadStore):
    async def delivery_outputs(self,project,ident,*,authorize,service):
        from pyharness.application.delivery_outputs import actual_outputs
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,_,_=await self._execution_read_state()
            row=self._context_owned(resources,ident,'execution_authorization',project,actor)
            self._execution_summary(resources,row,actor,authorize,started)
            def check():
                fresh,_,_=self._delivery_read_snapshot()
                self._context_project(fresh,project)
                attempt=self._execution_attempt(fresh,project,row['execution_authorization']['selection']['attempt_ref'],actor)['run_attempt']
                self._context_finish(authorize,actor,fresh,self._delivery_source_grants(fresh,attempt))
            outputs=await actual_outputs(service,row,check);check()
            return dict(schema_version=1,execution_ref=self._s05_ref(row,'execution_id','execution_authorization'),
                outputs=outputs,execution_authorized=False,export_authorized=False)

    def _delivery_read_snapshot(self):
        from pyharness.application.execution_control_projection import ControlReadResources
        events=self._strict_control_events();resources,commands=self._project(events)
        return ControlReadResources(resources,events),commands,events

    async def save_delivery_package(self,project,command,*,command_id,authorize,service):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();await self._open(initialize=False);resources,commands=self._grant_state()
            request=delivery_request(project,'delivery_package',command);ident=command.package_id
            self._context_envelope('delivery_package',ident,1,command_id,actor,request)
            if old:=commands.get((actor,command_id)):
                if old[0]!=request:raise_code('EVT-100',reason='idempotency_conflict')
                self._delivery_package_gate(resources,old[1],actor,authorize,service)
                return self._package_summary(old[1],resources=resources,actor=actor,authorize=authorize,service=service)
            value=DeliveryPackage(project_id=project,project_revision=command.project_revision,
                created_by=actor,submission_key=command_id,selection=command)
            def gate():
                fresh,_=self._grant_state();self._context_quota(fresh,'delivery_package',project)
                if ident in fresh:raise_code('EVT-101',reason='delivery_package_exists')
                self._delivery_package_gate(fresh,{'delivery_package':value.model_dump()},actor,authorize,service)
            gate()
            resources,row=await self._append_context(value,'delivery_package',ident,command_id,actor,request,gate)
            return self._package_summary(row,resources=resources,actor=actor,authorize=authorize,service=service)

    async def delivery_package(self,project,ident,*,authorize,service):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,_,_=await self._execution_read_state()
            row=self._context_owned(resources,ident,'delivery_package',project,actor)
            self._delivery_package_gate(resources,row,actor,authorize,service)
            return self._package_summary(row,resources=resources,actor=actor,authorize=authorize,service=service)
