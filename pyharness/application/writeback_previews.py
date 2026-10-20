"""Exact original baseline and selected delivery bytes; preview writes nothing."""
import difflib
import os
import time
from pathlib import Path
from pyharness.application.writeback_records import MergeFile,FixedValidation
from pyharness.application.writeback_merge import merge_bytes,validate_result,MERGE_VERSION,VALIDATOR_VERSION
from pyharness.application.writeback_io import observe_target,target_metadata
from pyharness.application.source_snapshot import LIMIT,check_content
from pyharness.errors import PyHError,raise_code


def preview_basis(project,actor,command,files):
    from pyharness.application.workspace_store import canonical,digest_of
    return digest_of(canonical(dict(operation='writeback.merge.preview',project_id=project,actor=actor,
        selection=command.model_dump(),files=files,merge_version=MERGE_VERSION,validator_version=VALIDATOR_VERSION)))


def deleted_output(service,execution,reference):
    from pyharness.application.product_execution_sessions import read_execution_events
    from pyharness.application.workspace_store import canonical,digest_of
    if reference.session_id!=execution['execution_authorization']['session_id']:
        raise_code('GRD-403',reason='writeback_deletion_requires_root_receipt')
    events=read_execution_events(service._tenant_sessions_dir(),execution,service.tenant_id)
    changes=[(e,c) for e in events if e.type=='execution.effect' and e.payload['phase']=='returned'
        and e.payload.get('file_observation')=='observed' for c in e.payload.get('file_changes') or [] if c['path']==reference.path]
    if not changes:raise_code('EVT-101',reason='writeback_deletion_receipt_unavailable')
    event,change=changes[-1]
    if (event.seq!=reference.source_seq or event.actor!='system' or digest_of(canonical(event.payload))!=reference.record_digest
            or change['kind']!='deleted' or change['after_sha256'] is not None):
        raise_code('EVT-101',reason='writeback_deletion_receipt_changed')


def preview_diff(current,merged):
    a=(current or b'').decode('utf-8').splitlines(keepends=True)
    b=(merged or b'').decode('utf-8').splitlines(keepends=True)
    lines=[];length=0
    for line in difflib.unified_diff(a,b,fromfile='current-target',tofile='fixed-merged-result',n=3):
        if len(lines)>=80 or length+len(line)>2048:
            lines.append('\n[差异显示已截断；批准仍绑定完整字节摘要]\n');break
        lines.append(line);length+=len(line)
    return ''.join(lines)


class WritebackPreviews:
    def _wb_collect(self,resources,project,command,actor,authorize,service,started):
        from pyharness.application.workspace_store import canonical,digest_of
        from pyharness.application.delivery_outputs import output_bytes
        grant,_,_,execution,wi=self._wb_current(resources,project,command,actor,authorize,service)
        root=Path(grant['writeback_grant']['canonical_root']);blobs={};metadata=[];public=[];total=0
        def gate():
            if time.monotonic()-started>=self.MAX_WORKSPACE_CAPTURE_SECONDS:raise_code('EVT-104',reason='writeback_preview_time_limit')
            fresh,_,_=self._delivery_read_snapshot()
            self._wb_current(fresh,project,command,actor,authorize,service)
        for selected in command.files:
            gate();base=None
            if selected.input_path is not None:
                original=next(i for i in wi['files'] if i['path']==selected.input_path)
                base=self._read_input_blob(original,gate)
            target,current=observe_target(root,selected.target_path,gate)
            if current is not None:
                try:check_content(current)
                except PyHError:raise_code('GRD-403',reason='writeback_target_content_rejected')
            delivery=None
            if selected.output_ref:
                gate();delivery=output_bytes(service,execution,selected.output_ref.model_dump());gate()
            else:deleted_output(service,execution,selected.deletion_ref)
            merged=merge_bytes(base,current,delivery);raw=merged['merged']
            validation=validate_result(selected.target_path,raw) if merged['status']=='mergeable' else dict(version=VALIDATOR_VERSION,status='failed',checks=['conflict-blocker'],reason='merge_conflict')
            before=digest_of(current) if current is not None else None;after=digest_of(raw) if raw is not None else None
            action='none' if before==after else 'create' if current is None else 'delete' if raw is None else 'replace'
            if merged['status']=='mergeable' and action!='none' and action not in grant['writeback_grant']['selection']['actions']:
                raise_code('GRD-403',reason='writeback_action_not_granted')
            item=MergeFile(target_path=selected.target_path,target=target_metadata(target) if target else None,base_digest=digest_of(base) if base is not None else None,
                delivery_digest=digest_of(delivery) if delivery is not None else None,merged_digest=after,merged_size=len(raw) if raw is not None else 0,
                action=action,validation=FixedValidation.model_validate(validation)).model_dump()
            metadata.append(item)
            public.append(dict(item,status=merged['status'],conflicts=merged['conflicts'],
                diff=preview_diff(current,raw) if merged['status']=='mergeable' and validation['status']=='passed' else None))
            for value in (base,current,delivery,raw):
                if value is not None:blobs[digest_of(value)]=value;total+=len(value)
            if total>4*LIMIT:raise_code('EVT-100',reason='writeback_merge_total_limit')
        gate()
        # Read every target again after all other target and source I/O. Saving
        # the plan later repeats this observation; application locks them all.
        for item in metadata:
            now,_=observe_target(root,item['target_path'],gate)
            if (target_metadata(now) if now else None)!=item['target']:raise_code('EVT-101',reason='writeback_target_changed')
        gate();status='conflict' if any(f['status']=='conflict' for f in public) else 'validation_failed' if any(f['validation']['status']!='passed' for f in public) else 'mergeable'
        result=dict(schema_version=1,project_id=project,status=status,files=public,confirmation_digest=preview_basis(project,actor,command,metadata),
            merge_version=MERGE_VERSION,validator_version=VALIDATOR_VERSION,applicable=False,execution_authorized=False,
            application_backend='windows-exclusive-handles' if os.name=='nt' else 'preview-and-explicit-export-only')
        if len(canonical(result))>self.MAX_CONTEXT_BYTES:raise_code('EVT-100',reason='writeback_preview_limit')
        return result,metadata,blobs
