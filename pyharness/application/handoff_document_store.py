"""Selected output imports use the original bounded text/PDF parsing paths."""
import time
from pyharness.application.handoff_document_support import HandoffDocumentSupport
from pyharness.application.handoff_document_contracts import HandoffDocumentRevision,HandoffOutputSource
from pyharness.application.delivery_contracts import DeliveryPackageCommand
from pyharness.errors import raise_code


def pdf_confirmation(project,actor,command,parsed):
    from pyharness.application.workspace_store import canonical,digest_of
    return digest_of(canonical(dict(operation='handoff_pdf.preview',project_id=project,subject=actor,
        command=command.model_dump(exclude={'confirmation_digest'}),parsed={k:([b.model_dump() if hasattr(b,'model_dump') else b for b in v] if k=='blocks' else v) for k,v in parsed.items()})))


class HandoffDocumentStore(HandoffDocumentSupport):
    async def handoff_document_import(self,project,command,*,command_id,authorize,service,preview=False):
        from pyharness.application.workspace_store import canonical,digest_of
        from pyharness.application.delivery_outputs import output_bytes
        from pyharness.application.document_text import parse_text
        from pyharness.application.document_pdf import parse_pdf
        started=time.monotonic();ident=command.document_id
        async with self._workspace_capture_lock(started):
            actor=authorize()
            if preview:resources,commands,_=await self._execution_read_state()
            else:await self._open(initialize=False);resources,commands=self._grant_state()
            request=digest_of(canonical(dict(operation='handoff_document.save',project_id=project,command=command.model_dump())))
            source=dict(kind='handoff_output',handoff_ref=command.handoff_ref.model_dump(),output_ref=command.output_ref.model_dump(),
                text_selection=command.text_selection.model_dump() if command.text_selection else None,
                acceptance_ref=None,confirmation_digest=command.confirmation_digest)
            def gate():
                fresh,_,_=self._delivery_read_snapshot();self._context_project(fresh,project,command.project_revision)
                handoff,role,package=self._handoff_document_basis(fresh,project,source,actor)
                self._handoff_current(fresh,handoff,actor,authorize,service)
                accepted=(self._package_acceptance(fresh,package,actor,authorize,service)
                    if role['role_contract']['selection']['role']=='consumer' else None)
                if source['acceptance_ref'] is not None and source['acceptance_ref']!=accepted:
                    raise_code('EVT-101',reason='handoff_acceptance_changed')
                source['acceptance_ref']=accepted
                self._context_finish(authorize,actor,fresh)
                return fresh,handoff,role,package
            fresh,handoff,role,package=gate()
            if not preview:
                self._context_envelope('document',ident,1,command_id,actor,request)
                if old:=commands.get((actor,command_id)):
                    if old[0]!=request:raise_code('EVT-100',reason='idempotency_conflict')
                    row=old[1];check=lambda:self._handoff_document_gate(fresh,row,actor,authorize,started)
                    self._document_raw(row['document'],check)
                    return self._document_summary(row,check())
                if ident in fresh:raise_code('EVT-101',reason='handoff_document_exists')
            p=package['delivery_package']
            execution,_,_=self._delivery_package_basis(fresh,project,DeliveryPackageCommand.model_validate(p['selection']),p['created_by'])
            raw=output_bytes(service,execution,source['output_ref']);gate()
            if command.format=='pdf':
                if not raw.startswith(b'%PDF-'):raise_code('EVT-100',reason='handoff_pdf_required')
                parsed=await parse_pdf(self.root/'document-staging',raw);gate()
                confirmation=pdf_confirmation(project,actor,command,parsed)
                if preview:return dict(schema_version=2,status='confirmation_required',persisted=False,
                    page_count=parsed['page_count'],unlocatable_pages=parsed['unlocatable_pages'],
                    capability=parsed['capability'],parser_version=parsed['parser_version'],confirmation_digest=confirmation)
                if confirmation!=command.confirmation_digest:raise_code('EVT-101',reason='handoff_pdf_confirmation_required')
            else:
                if preview:raise_code('EVT-100',reason='handoff_preview_pdf_only')
                if command.text_selection:
                    try:text=raw.decode('utf-8-sig')
                    except UnicodeDecodeError:raise_code('EVT-100',reason='handoff_text_required')
                    selection=command.text_selection
                    if selection.end>len(text):raise_code('EVT-100',reason='handoff_text_selection_invalid')
                    raw=text[selection.start:selection.end].encode('utf-8')
                    if digest_of(raw)!=selection.quote_digest:raise_code('EVT-101',reason='handoff_quote_digest_mismatch')
                parsed=parse_text(raw)
            context=handoff['role_handoff']['selection']['minimum_context']
            text_bytes=sum(len(b.text.encode('utf-8')) for b in parsed['blocks'])+len(context.encode('utf-8'))
            if text_bytes>role['role_contract']['selection']['max_context_bytes']:raise_code('EVT-100',reason='handoff_role_context_limit')
            definition=HandoffDocumentRevision(document_id=ident,project_id=project,project_revision=command.project_revision,
                created_by=actor,title=command.title,source=HandoffOutputSource.model_validate(source),
                handoff_context=context,content_digest=digest_of(raw),size_bytes=len(raw),**parsed)
            self._write_snapshot_blob(self._snapshot_content(definition.content_digest),raw)
            def commit_gate():
                fresh,_,_,_=gate();self._context_quota(fresh,'document',project)
                if ident in fresh:raise_code('EVT-101',reason='handoff_document_exists')
                if time.monotonic()-started>=self.MAX_WORKSPACE_CAPTURE_SECONDS:raise_code('EVT-104',reason='document_observation_time_limit')
            commit_gate()
            fresh,row=await self._append_context(definition,'document',ident,command_id,actor,request,commit_gate)
            check=lambda:self._handoff_document_gate(fresh,row,actor,authorize,started)
            self._document_raw(row['document'],check)
            return self._document_summary(row,check())
