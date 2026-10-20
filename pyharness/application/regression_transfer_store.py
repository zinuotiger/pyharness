"""Explicit transfer of locally provable versions, without cloning cases or running tools."""
import time
from pyharness.application.regression_contracts import ImportedSaveDataset
from pyharness.application.regression_transfer_contracts import RegressionBundle
from pyharness.application.regression_support import exact_case,regression_ref
from pyharness.application.regression_assertions import strict_json
from pyharness.errors import raise_code


def export_confirmation(tenant,actor,project,reference,bundle_digest):
    from pyharness.application.workspace_store import canonical,digest_of
    return digest_of(canonical(dict(operation='regression_dataset.export',tenant_id=tenant,
        actor_ref=actor,project_id=project,dataset_ref=reference,bundle_digest=bundle_digest)))


def import_confirmation(tenant,actor,project,command):
    from pyharness.application.regression_transfer_contracts import DatasetImportPreview
    from pyharness.application.workspace_store import canonical,digest_of
    selected=DatasetImportPreview.model_validate(command.model_dump(include=set(DatasetImportPreview.model_fields)))
    return digest_of(canonical(dict(operation='regression_dataset.import',tenant_id=tenant,
        actor_ref=actor,project_id=project,selection=selected.model_dump())))


class RegressionTransferStore:
    async def regression_export(self,project,ident,command,*,authorize,projects,confirmed=False):
        from pyharness.application.workspace_store import canonical,digest_of
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,_,_=await self._execution_read_state()
            row=self._regression_revision(resources,project,ident,command.revision,'regression_dataset',actor)
            self._regression_check_record(resources,row,'regression_dataset',actor,authorize,started,projects)
            cases=[]
            for ref in row['regression_dataset']['selection']['case_refs']:
                case=exact_case(self,resources,project,ref,actor)
                cases.append(dict(reference=ref,record=case['regression_case']))
            reference=regression_ref(row,'regression_dataset')
            bundle=RegressionBundle(tenant_id=self.tenant_id,subject_ref=actor,project_id=project,
                dataset_ref=reference,dataset=row['regression_dataset'],cases=cases)
            raw=canonical(bundle.model_dump())
            if len(raw)>65536:raise_code('EVT-100',reason='regression_bundle_limit')
            strict_json(raw.decode('utf-8'),max_nodes=8192,max_depth=20)
            digest=digest_of(raw);confirmation=export_confirmation(self.tenant_id,actor,project,reference,digest)
            self._regression_check_record(resources,row,'regression_dataset',actor,authorize,started,projects)
            if confirmed:
                if command.confirmation_digest!=confirmation:raise_code('EVT-101',reason='regression_export_changed')
                return dict(schema_version=1,project_id=project,dataset_ref=reference,bundle_digest=digest,
                    bundle_canonical=raw.decode('utf-8'),content_type='application/json',
                    suggested_filename='pyharness-'+ident+'-r'+str(command.revision)+'.json',
                    persisted=False,execution_authorized=False,export_authorized=True)
            return dict(schema_version=1,project_id=project,dataset_ref=reference,bundle_digest=digest,
                confirmation_digest=confirmation,case_count=len(cases),case_refs=[c['reference'] for c in cases],
                title=row['regression_dataset']['selection']['title'],status='confirmation_required',
                persisted=False,execution_authorized=False,export_authorized=False)

    async def regression_import_basis(self,project,command,*,authorize,projects,for_commit=False):
        from pyharness.application.workspace_store import canonical,digest_of
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,_,_=await self._execution_read_state()
            self._context_project(resources,project,None if for_commit else command.project_revision)
            try:raw=command.bundle_canonical.encode('utf-8')
            except UnicodeError:raise_code('EVT-100',reason='regression_bundle_invalid')
            if len(raw)>65536:raise_code('EVT-100',reason='regression_bundle_limit')
            try:parsed=strict_json(command.bundle_canonical,max_nodes=8192,max_depth=20)
            except (ValueError,RecursionError,UnicodeError):raise_code('EVT-100',reason='regression_bundle_invalid')
            if type(parsed) is not dict:raise_code('EVT-100',reason='regression_bundle_invalid')
            if parsed.get('tenant_id')!=self.tenant_id or parsed.get('subject_ref')!=actor or parsed.get('project_id')!=project:
                raise_code('GRD-403',reason='regression_import_scope')
            bundle=RegressionBundle.model_validate(parsed)
            if bundle.tenant_id!=self.tenant_id or bundle.subject_ref!=actor or bundle.project_id!=project:
                raise_code('GRD-403',reason='regression_import_scope')
            ref=bundle.dataset_ref.model_dump()
            source=self._regression_revision(resources,project,ref['dataset_id'],ref['revision'],'regression_dataset',actor)
            if regression_ref(source,'regression_dataset')!=ref or canonical(source['regression_dataset'])!=canonical(bundle.dataset.model_dump()):
                raise_code('EVT-101',reason='regression_import_dataset_unproven')
            self._regression_check_record(resources,source,'regression_dataset',actor,authorize,started,projects)
            for entry in bundle.cases:
                case=exact_case(self,resources,project,entry.reference,actor)
                if canonical(case['regression_case'])!=canonical(entry.record.model_dump()):
                    raise_code('EVT-101',reason='regression_import_case_unproven')
            previous=resources.get(command.dataset_id)
            if previous:self._context_owned(resources,command.dataset_id,'regression_dataset',project,actor)
            if not for_commit and (previous['regression_dataset']['revision'] if previous else 0)!=command.expected_revision:
                raise_code('EVT-101',reason='stale_revision')
            selection=ImportedSaveDataset(dataset_id=command.dataset_id,expected_revision=command.expected_revision,
                project_revision=command.project_revision,title=command.title,confirmed=True,
                case_refs=bundle.dataset.selection.case_refs,
                import_source=dict(dataset_ref=ref,bundle_digest=digest_of(raw)))
            self._regression_check_record(resources,source,'regression_dataset',actor,authorize,started,projects)
            self._context_finish(authorize,actor,resources)
            preview=dict(schema_version=1,project_id=project,dataset_id=command.dataset_id,
                source_dataset_ref=ref,bundle_digest=digest_of(raw),case_count=len(bundle.cases),
                case_refs=[v.model_dump() for v in selection.case_refs],
                confirmation_digest=import_confirmation(self.tenant_id,actor,project,command),
                status='confirmation_required',persisted=False,execution_authorized=False)
            return preview,selection

    async def regression_submission(self,project,key,*,authorize,projects):
        from pyharness.application.regression_records import REGRESSION_MODELS
        from pyharness.application.regression_support import regression_summary
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,commands,_=await self._execution_read_state()
            self._context_project(resources,project);old=commands.get((actor,key));row=old[1] if old else None
            receipt=None;kind=None
            if row:
                kind=next((name for name in REGRESSION_MODELS if name in row),None)
                if not kind or row[kind]['project_id']!=project:raise_code('GRD-403',reason='regression_submission_unavailable')
                self._regression_check_record(resources,row,kind,actor,authorize,started,projects)
                receipt=regression_summary(row,kind)
            self._context_finish(authorize,actor,resources)
            return dict(schema_version=1,project_id=project,submission_key=key,resource_kind=kind,
                status='committed' if row else 'not_found',receipt=receipt,writer_requires_reopen=self._failed,
                execution_authorized=False)
