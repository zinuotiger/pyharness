"""Explicit fixed-object export, independently reauthorized after byte reads."""
import time
from pyharness.errors import raise_code


class DeliveryExportStore:
    async def delivery_export_bytes(self,project,ident,index,*,authorize,service):
        from pyharness.application.delivery_contracts import DeliveryPackageCommand
        from pyharness.application.delivery_outputs import output_bytes
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,_,_=await self._execution_read_state();row=resources.get(ident,{})
            value=row.get('delivery_export')
            if not value or value['project_id']!=project or value['recipient_id']!=actor:
                raise_code('GRD-403',reason='delivery_export_grant_required')
            selected=value['selection']['outputs']
            if type(index) is not int or not 0<=index<len(selected):raise_code('EVT-100',reason='delivery_export_index_invalid')
            def gate():
                fresh,_,_=self._delivery_read_snapshot()
                self._role_summary(fresh,row,'delivery_export',actor,authorize,service)
                package=self._delivery_exact(fresh,project,'delivery_package',value['selection']['package_ref'])['delivery_package']
                execution,_,_=self._delivery_package_basis(fresh,project,DeliveryPackageCommand.model_validate(package['selection']),package['created_by'])
                return execution
            execution=gate();raw=output_bytes(service,execution,selected[index]);gate()
            filename='final-result.txt' if selected[index]['kind']=='message' else selected[index]['artifact_id']
            return raw,filename
