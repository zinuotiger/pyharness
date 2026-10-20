"""CMv1 selected inputs use the same control owner and guarded private-copy protocol."""
import time
from pyharness.application.selected_owned_inputs import PrepareSelectedInput,SelectedInputDescriptor,derive_selected_inputs,selected_parts
from pyharness.errors import PyHError,raise_code


class SelectedInputSupport:
    def _selected_input_source(self,resources,value):
        ref=value['source_ref'];row=resources.get(ref['snapshot_id'],{});s=row.get('snapshot');grant=resources.get(ref['grant_id'],{});g=grant.get('grant',{})
        if (not s or s['project_id']!=value['project_id'] or s['grant_id']!=ref['grant_id']
            or s['grant_revision']!=ref['grant_revision'] or s['root_identity']!=ref['root_identity']
            or s['confirmation_digest']!=ref['confirmation_digest'] or row['digest']!=ref['digest'] or row['source_seq']!=ref['source_seq']
            or g.get('project_id')!=value['project_id'] or g.get('revision')!=ref['grant_revision']
            or grant.get('source_seq')!=ref['grant_source_seq'] or g.get('root_identity')!=ref['root_identity']):
            raise_code('GRD-403',reason='selected_input_source_unavailable')
        self._snapshot_grant(resources,value['project_id'],ref['grant_id'],ref['grant_revision']);return row

    def _validate_selected_input_definition(self,definition,resources,previous,event):
        value=definition.model_dump()
        try:
            if previous is not None or value['created_by']!=event.payload['actor_ref']:raise ValueError('immutable selected input owner')
            self._context_project(resources,value['project_id'],value['project_revision'])
            self._context_quota(resources,'workspace_input',value['project_id'])
            cm=self._input_manifest(resources,value['project_id'],value['manifest_ref'],value['created_by'],manifest_schema=1)
            source=resources[cm['manifest']['snapshot_id']];grant=resources[cm['manifest']['grant_id']]
            if grant['grant']['revoked'] or cm['manifest']['project_revision']!=value['project_revision']:raise ValueError('stale selected source')
            if value['task_ref']:self._context_task(resources,value['task_ref'],value['project_id'],value['project_revision'],value['created_by'])
            command=PrepareSelectedInput.model_validate({k:value[k] for k in ('schema_version','input_id','project_revision','manifest_ref')})
            if derive_selected_inputs(value['project_id'],command,cm,source,grant,value['created_by']).model_dump()!=value:raise ValueError('selected input differs from source')
        except (PyHError,ValueError,KeyError,IndexError):raise_code('PERS-202',reason='selected_input_definition_binding')

    async def selected_input_descriptor(self,project_id,manifest_id,revision,*,authorize):
        from pyharness.application.workspace_store import canonical,digest_of
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();await self._open(initialize=False);resources,_=self._grant_state()
            cm=self._context_owned(resources,manifest_id,'manifest',project_id,actor)
            if cm['manifest']['schema_version']!=1:raise_code('GRD-403',reason='selected_input_manifest_unavailable')
            if cm['manifest']['revision']!=revision:raise_code('EVT-101',reason='workspace_input_manifest_revision')
            self._manifest_access(resources,cm,actor)
            if self._manifest_summary(resources,cm)['status']!='current':raise_code('EVT-101',reason='workspace_input_stale')
            m=cm['manifest'];parts=selected_parts(cm,resources[m['snapshot_id']],resources[m['grant_id']])
            ref={k:(m[k] if k in ('manifest_id','revision') else cm[k]) for k in ('manifest_id','revision','digest','source_seq')}
            authority={'schema_version':2,'project_id':project_id,'project_revision':m['project_revision'],'manifest_ref':ref,'created_by':actor,**parts}
            self._input_gate(resources,authority,actor,authorize,started)
            raw={'schema_version':1,'status':'selected_inputs_observed','project_id':project_id,'project_revision':m['project_revision'],
                 'manifest_ref':ref,**parts,'file_count':len(parts['files']),'deletion_count':0,'persisted':False,'executable':False}
            result=SelectedInputDescriptor(**raw,descriptor_digest=digest_of(canonical(raw))).model_dump()
            if len(canonical(result))>self.MAX_CONTEXT_BYTES:raise_code('EVT-100',reason='selected_input_descriptor_limit')
            fresh,_=self._grant_state();self._input_gate(fresh,authority,actor,authorize,started)
            return result
