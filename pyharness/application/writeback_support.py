"""Metadata history and current request authority are separate writeback gates."""
import json
import sqlite3
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
from pyharness.application.writeback_records import WRITEBACK_MODELS
from pyharness.application.writeback_contracts import MergePreviewCommand
from pyharness.application.source_grants import authorize_root,parse_expiry
from pyharness.application.source_snapshot import checked_private_bytes
from pyharness.application.delivery_support import selected_outputs
from pyharness.errors import PyHError,raise_code


def wb_request(project,operation,command):
    from pyharness.application.workspace_store import canonical,digest_of
    return digest_of(canonical(dict(operation='writeback.'+operation,project_id=project,command=command)))


def wb_id(value,kind):return getattr(value.selection,WRITEBACK_MODELS[kind][1])


def require_write_owner(service,subject):
    from pyharness.application.accounts import Accounts
    if subject=='local-operator' and service.tenant_id=='default' and not Accounts.manages_tenant(service._storage_root(),'default'):return
    path=service._storage_root()/'accounts.sqlite3'
    if not path.is_file():raise_code('GRD-403',reason='writeback_owner_required')
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        found=db.execute("SELECT 1 FROM members WHERE user_id=? AND tenant=? AND role='owner'",(subject,service.tenant_id)).fetchone()
    if not found:raise_code('GRD-403',reason='writeback_owner_required')


def approval_digest(row):
    from pyharness.application.workspace_store import canonical,digest_of
    value=row['merge_plan']
    return digest_of(canonical(dict(operation='writeback.apply',actor=value['created_by'],
        plan_ref=dict(plan_id=value['selection']['plan_id'],revision=value['revision'],digest=row['digest'],source_seq=row['source_seq']),
        basis_digest=value['basis_digest'])))


def merge_selection(command):
    raw=command.model_dump() if hasattr(command,'model_dump') else dict(command)
    return MergePreviewCommand.model_validate({k:raw[k] for k in MergePreviewCommand.model_fields})


class WritebackSupport:
    def _wb_exact(self,resources,project,kind,reference):
        key=WRITEBACK_MODELS[kind][1];row=resources.get(reference[key],{});value=row.get(kind)
        if not value or value['project_id']!=project:raise_code('GRD-403',reason='writeback_reference_unavailable')
        if kind=='writeback_grant' and value['revoked']:raise_code('GRD-403',reason='writeback_grant_revoked')
        actual={key:value['selection'][key],'revision':value['revision'],'digest':row['digest'],'source_seq':row['source_seq']}
        if actual!=reference:raise_code('EVT-101',reason='writeback_reference_changed')
        return row

    def _wb_grant_gate(self,resources,project,reference,actor,authorize,service):
        from pyharness.application.delivery_identity import require_member
        row=self._wb_exact(resources,project,'writeback_grant',reference);value=row['writeback_grant']
        self._context_project(resources,project,value['project_revision'])
        if actor!=value['recipient_id']:raise_code('GRD-403',reason='writeback_recipient_required')
        require_write_owner(service,value['created_by']);require_member(service,actor)
        if authorize_root(value['canonical_root'])!={k:value[k] for k in ('canonical_root','device','inode','root_identity')}:
            raise_code('EVT-101',reason='writeback_root_changed')
        private=service._storage_root().resolve();root=Path(value['canonical_root'])
        if root.is_relative_to(private) or private.is_relative_to(root):raise_code('GRD-403',reason='writeback_private_storage_overlap')
        self._context_finish(authorize,actor,resources)
        if parse_expiry(value['selection']['expires_at'])<=datetime.now(timezone.utc):raise_code('GRD-403',reason='writeback_grant_expired')
        return row

    def _wb_basis(self,resources,project,command,actor):
        grant=self._wb_exact(resources,project,'writeback_grant',command.grant_ref.model_dump())
        package=self._delivery_exact(resources,project,'delivery_package',command.package_ref.model_dump())
        accepted=self._delivery_exact(resources,project,'delivery_acceptance',command.acceptance_ref.model_dump())
        if grant['writeback_grant']['recipient_id']!=actor or package['delivery_package']['created_by']!=actor:
            raise_code('GRD-403',reason='writeback_subject_mismatch')
        if accepted['delivery_acceptance']['selection']['package_ref']!=command.package_ref.model_dump() or self._acceptance_verdict(accepted['delivery_acceptance'])!='passed':
            raise_code('GRD-403',reason='writeback_independent_acceptance_required')
        from pyharness.application.delivery_contracts import DeliveryPackageCommand
        selection=DeliveryPackageCommand.model_validate(package['delivery_package']['selection'])
        execution,attempt,_=self._delivery_package_basis(resources,project,selection,actor)
        task=self._s05_exact(resources,project,'product_task',attempt['prerequisites']['task_ref'],'product_task_id',actor)['product_task']
        wi=self._task_bound_input(resources,task,actor)['workspace_input']
        paths=set(grant['writeback_grant']['selection']['paths']);outputs=selected_outputs(package['delivery_package'])
        for f in command.files:
            if f.target_path not in paths:raise_code('GRD-403',reason='writeback_path_not_granted')
            if f.output_ref and f.output_ref.model_dump() not in outputs:raise_code('GRD-403',reason='writeback_output_not_in_package')
            if f.input_path is not None and f.input_path not in {i['path'] for i in wi['files']}:
                raise_code('GRD-403',reason='writeback_baseline_path_unavailable')
        return grant,package,accepted,execution,wi

    def _wb_current(self,resources,project,command,actor,authorize,service):
        self._context_project(resources,project,command.project_revision)
        grant,package,accepted,execution,wi=self._wb_basis(resources,project,command,actor)
        self._wb_grant_gate(resources,project,command.grant_ref.model_dump(),actor,authorize,service)
        self._delivery_package_gate(resources,package,actor,authorize,service)
        if self._acceptance_current(resources,accepted,actor,authorize,service)!='passed':
            raise_code('GRD-403',reason='writeback_independent_acceptance_required')
        for selected in command.files:
            if selected.deletion_ref:
                from pyharness.application.writeback_previews import deleted_output
                deleted_output(service,execution,selected.deletion_ref)
        self._context_finish(authorize,actor,resources)
        return grant,package,accepted,execution,wi

    def _wb_blob(self,digest,gate):
        from pyharness.application.workspace_store import digest_of
        gate();raw=checked_private_bytes(self._snapshot_content(digest),before_read=gate)
        if digest_of(raw)!=digest:raise_code('PERS-202',reason='writeback_blob_changed')
        gate();return raw

    def _wb_summary(self,row,kind):
        value=row[kind]
        result=dict(value['selection'],project_id=value['project_id'],revision=value['revision'],digest=row['digest'],
            source_seq=row['source_seq'],created_at=row['created_at'],persisted=True,request_digest=row['request_digest'],execution_authorized=False)
        result.update(project_revision=value['project_revision'],submission_key=value['submission_key'])
        if kind=='writeback_grant':
            result.update(status='revoked' if value['revoked'] else 'granted',recipient_id=value['recipient_id'],root_identity=value['root_identity'])
        if kind=='merge_plan':result.update(status='frozen',files=value['files'],mappings=value['selection']['files'],
            basis_digest=value['basis_digest'],merge_version=value['merge_version'],approval_digest=approval_digest(row),applicable=False)
        if kind=='writeback_application':
            status='unknown' if value['status']=='admitted' else value['status']
            result.update(status=status,record_status=value['status'],origin_key=value['origin_key'],complete=status=='completed',effects=value['effects'],replay_allowed=False)
        if kind=='merge_export':result.update(status='authorized',export_authorized=True)
        return result

    def _wb_commit_summary(self,row,kind,actor,authorize,*,gate=None):
        """A committed fact is not a safe response after authority changed."""
        try:
            self._context_finish(authorize,actor,{})
            if gate is not None:gate()
            resources,_,_=self._delivery_read_snapshot()
            self._context_finish(authorize,actor,resources)
        except PyHError:
            # Preserve the original-key ambiguity; a 4xx would incorrectly tell
            # clients this already committed operation had never been accepted.
            raise_code('PERS-202',reason='writeback_commit_receipt_unavailable')
        return self._wb_summary(row,kind)

    def _validate_writeback_record(self,definition,resources,previous,event,kind):
        from pyharness.application.workspace_store import canonical,digest_of
        from pyharness.application.writeback_previews import preview_basis
        value=definition.model_dump();actor=value['created_by'];project=value['project_id']
        try:
            if event.payload['actor_ref']!=actor or event.payload['command_id']!=value['submission_key']:
                raise ValueError('writeback actor/key mismatch')
            self._context_project(resources,project)
            if kind=='writeback_grant':
                if previous is None:
                    if definition.revision!=1:raise ValueError('initial write grant revision')
                    operation='grant';command=value['selection']
                else:
                    old=previous[kind]
                    if old['revoked'] or any(value[k]!=v for k,v in old.items() if k not in ('revision','revoked','submission_key')):
                        raise ValueError('write grant can only revoke')
                    operation='revoke';command=dict(grant_id=value['selection']['grant_id'],schema_version=1,expected_revision=old['revision'])
            elif kind=='merge_plan':
                if previous is not None:raise ValueError('immutable merge plan')
                command=value['selection'];operation='plan';selected=merge_selection(command)
                grant,_,_,_,wi=self._wb_basis(resources,project,selected,actor)
                if len(value['files'])!=len(selected.files):raise ValueError('merge file count')
                for item,selection in zip(value['files'],selected.files):
                    base=next((f for f in wi['files'] if f['path']==selection.input_path),None)
                    if (item['target_path']!=selection.target_path or item['target'] and item['target']['path']!=selection.target_path
                            or item['base_digest']!=(base['content_digest'] if base else None)
                            or item['delivery_digest']!=(selection.output_ref.sha256 if selection.output_ref else None)
                            or item['validation']['status']!='passed' or item['merged_digest'] is None and item['merged_size']!=0):
                        raise ValueError('merge file binding')
                    before=item['target']['sha256'] if item['target'] else None;after=item['merged_digest']
                    action='none' if before==after else 'create' if before is None else 'delete' if after is None else 'replace'
                    if item['action']!=action or action!='none' and action not in grant['writeback_grant']['selection']['actions']:
                        raise ValueError('merge action not granted')
                if preview_basis(project,actor,selected,value['files'])!=value['basis_digest'] or value['basis_digest']!=command['confirmation_digest']:
                    raise ValueError('merge confirmation mismatch')
            elif kind=='writeback_application':
                command=value['selection'];operation='apply'
                plan=self._wb_exact(resources,project,'merge_plan',command['plan_ref'])
                if plan['merge_plan']['created_by']!=actor or approval_digest(plan)!=command['approval_digest']:
                    raise ValueError('application approval mismatch')
                if previous is None:
                    if value['revision']!=1 or value['submission_key']!=value['origin_key']:raise ValueError('initial application')
                    if any(r.get(kind,{}).get('selection',{}).get('plan_ref')==command['plan_ref'] for r in resources.values()):
                        raise ValueError('plan already has an application')
                else:
                    old=previous[kind]
                    if old['revision']!=1 or any(value[k]!=old[k] for k in ('selection','origin_key','created_by','project_id','project_revision')):
                        raise ValueError('application result transition')
                    if len(value['effects'])!=len(plan['merge_plan']['files']):raise ValueError('application effect count')
                    for effect,item in zip(value['effects'],plan['merge_plan']['files']):
                        if any(effect[k]!=item[k] for k in ('target_path','action')) or effect['expected_digest']!=item['merged_digest'] or effect['before_digest']!=(item['target']['sha256'] if item['target'] else None):
                            raise ValueError('application effect binding')
                        if effect['status']=='verified' and effect['observed_digest']!=effect['expected_digest']:
                            raise ValueError('unverified application')
                    if (value['status']=='completed')!=all(f['status']=='verified' for f in value['effects']):raise ValueError('application completion mismatch')
            else:
                if previous is not None:raise ValueError('immutable merge export')
                command=value['selection'];operation='export';plan=self._wb_exact(resources,project,'merge_plan',command['plan_ref'])
                if plan['merge_plan']['created_by']!=actor:raise ValueError('merge export owner')
            if event.payload['request_digest']!=wb_request(project,operation,command):raise ValueError('writeback request mismatch')
        except (ValueError,KeyError,PyHError):raise_code('PERS-202',reason='writeback_record_binding')
