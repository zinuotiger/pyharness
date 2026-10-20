"""Private delivery inventory and content-free original-key reconciliation."""
import time
from pyharness.application.delivery_records import DELIVERY_MODELS
from pyharness.errors import raise_code


class DeliveryReadStore:
    async def delivery_submission(self,project,key,*,authorize):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,commands,_=await self._execution_read_state()
            old=commands.get((actor,key));row=old[1] if old else None;receipt=None
            if row:
                kind=next((kind for kind in DELIVERY_MODELS if kind in row),None)
                if kind is None and 'document' in row and row['document']['schema_version']==2:kind='document'
                if kind is None:raise_code('GRD-403',reason='delivery_submission_unavailable')
                value=row[kind]
                if value['project_id']!=project:raise_code('GRD-403',reason='delivery_submission_unavailable')
                ident=value['document_id'] if kind=='document' else value['selection'][DELIVERY_MODELS[kind][2]]
                receipt=dict(resource_kind=kind,resource_id=ident,revision=value['revision'],
                    digest=row['digest'],source_seq=row['source_seq'],request_digest=old[0],persisted=True)
            self._context_finish(authorize,actor,resources)
            return dict(schema_version=1,project_id=project,submission_key=key,status='committed' if row else 'not_found',
                receipt=receipt,writer_requires_reopen=self._failed,execution_authorized=False)

    async def delivery_center(self,project,*,authorize,service):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,_,_=await self._execution_read_state();self._context_project(resources,project)
            groups={kind:[] for kind in DELIVERY_MODELS}
            for row in resources.values():
                kind=next((kind for kind in DELIVERY_MODELS if kind in row),None)
                if kind is None:continue
                value=row[kind]
                command=value['selection'];key=DELIVERY_MODELS[kind][2]
                subjects={value['created_by'],value.get('recipient_id')}
                if kind in ('delivery_acceptance','handoff_ack'):
                    handoff=resources.get(command['handoff_ref']['handoff_id'],{}).get('role_handoff',{})
                    subjects.update((handoff.get('created_by'),handoff.get('recipient_id')))
                if value['project_id']!=project or actor not in subjects:continue
                groups[kind].append(dict(resource_id=command[key],revision=value['revision'],digest=row['digest'],
                    source_seq=row['source_seq'],created_at=row['created_at'],owned=value['created_by']==actor))
            self._context_finish(authorize,actor,resources)
            executions=[]
            for row in resources.values():
                value=row.get('execution_authorization')
                if not value or value['project_id']!=project or value['created_by']!=actor:continue
                attempt=resources[value['selection']['attempt_ref']['attempt_id']]['run_attempt']
                if attempt['context']['delivery_ref'] is None:continue
                executions.append(dict(execution_id=value['execution_id'],revision=value['revision'],digest=row['digest'],
                    source_seq=row['source_seq'],status='authorized',delivery_ref=attempt['context']['delivery_ref']))
            return dict(schema_version=1,project_id=project,items=groups,executions=sorted(executions,key=lambda r:r['source_seq'],reverse=True),execution_authorized=False)
