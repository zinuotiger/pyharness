"""Explicit output grant to quoted document, rechecked at every original use."""
from contextvars import ContextVar
from pyharness.application.delivery_roles import subset_outputs
from pyharness.application.delivery_support import delivery_ref
from pyharness.errors import raise_code,PyHError

_handoff_reads=ContextVar('handoff_document_reads',default=())


class HandoffDocumentSupport:
    def _handoff_dependencies_current(self,resources,attempt,requester,authorize,service):
        """Verify transitive provenance without exposing upstream contexts or
        substituting their stored owners for the real current requester."""
        from pyharness.application.handoff_execution_store import handoff_contexts
        for value in handoff_contexts(self,resources,attempt):
            ident=value['document_id'];seen=_handoff_reads.get()
            if ident in seen or len(seen)>=20:raise_code('GRD-403',reason='handoff_dependency_limit')
            token=_handoff_reads.set((*seen,ident))
            try:
                source=value['source']
                handoff,role,package=self._handoff_document_basis(resources,value['project_id'],source,value['created_by'])
                # The outer fixed-output grant permits provenance checking,
                # while only its selected downstream output is returned.
                self._handoff_current(resources,handoff,requester,authorize,service,package_granted=True)
                if role['role_contract']['selection']['role']=='consumer':
                    accepted=self._package_acceptance(resources,package,requester,authorize,service)
                    if accepted!=source['acceptance_ref']:raise_code('EVT-101',reason='handoff_upstream_acceptance_changed')
            finally:_handoff_reads.reset(token)

    def _handoff_owner(self):
        service=getattr(self,'_delivery_service',None)
        if service is None or service.tenant_id!=self.tenant_id or self.root!=service.tenant_root()/'workspace-v1':
            raise_code('GRD-403',reason='handoff_document_owner_unbound')
        return service

    def _package_acceptance(self,resources,package,actor,authorize,service):
        reference=delivery_ref(package,'delivery_package')
        rows=[r for r in resources.values() if r.get('delivery_acceptance',{}).get('selection',{}).get('package_ref')==reference]
        if not rows:raise_code('GRD-403',reason='independent_acceptance_required')
        row=max(rows,key=lambda r:r['source_seq'])
        if self._acceptance_current(resources,row,actor,authorize,service,package_granted=True)!='passed':
            raise_code('GRD-403',reason='independent_acceptance_required')
        return delivery_ref(row,'delivery_acceptance')

    def _handoff_document_basis(self,resources,project,source,actor):
        handoff,role,package=self._handoff_record(resources,project,source['handoff_ref'])
        h=handoff['role_handoff']
        if actor!=h['recipient_id'] or not self._handoff_acknowledged(resources,handoff,actor):
            raise_code('GRD-403',reason='acknowledged_handoff_recipient_required')
        subset_outputs([source['output_ref']],h['selection']['outputs'])
        return handoff,role,package

    def _handoff_document_gate(self,resources,row,actor,authorize,started):
        service=self._handoff_owner();value=row['document'];source=value['source'];ident=value['document_id']
        seen=_handoff_reads.get()
        if ident in seen or len(seen)>=20:raise_code('GRD-403',reason='handoff_dependency_limit')
        token=_handoff_reads.set((*seen,ident))
        try:
            self._context_owned({ident:row},ident,'document',value['project_id'],actor)
            self._context_project(resources,value['project_id'],value['project_revision'])
            handoff,role,package=self._handoff_document_basis(resources,value['project_id'],source,actor)
            self._handoff_current(resources,handoff,actor,authorize,service)
            if role['role_contract']['selection']['role']=='consumer':
                accepted=self._package_acceptance(resources,package,actor,authorize,service)
                if source['acceptance_ref']!=accepted:raise_code('EVT-101',reason='handoff_document_acceptance_changed')
            if value['handoff_context']!=handoff['role_handoff']['selection']['minimum_context']:
                raise_code('PERS-202',reason='handoff_document_context_binding')
            from pyharness.application.document_text import parser_version
            if value['parser_version']!=parser_version(value['format']):raise_code('EVT-101',reason='handoff_document_parser_changed')
            from pyharness.application.delivery_contracts import DeliveryPackageCommand
            _,attempt,_=self._delivery_package_basis(resources,value['project_id'],
                DeliveryPackageCommand.model_validate(package['delivery_package']['selection']),package['delivery_package']['created_by'])
            grants=self._delivery_source_grants(resources,attempt)
            self._context_finish(authorize,actor,resources,grants)
            if value['retired']:return ['document_retired']
            import time
            if time.monotonic()-started>=self.MAX_WORKSPACE_CAPTURE_SECONDS:raise_code('EVT-104',reason='document_observation_time_limit')
            return []
        finally:_handoff_reads.reset(token)

    def _handoff_document_grants(self,resources,value,actor):
        from pyharness.application.delivery_contracts import DeliveryPackageCommand
        seen=_handoff_reads.get();ident=value['document_id']
        if ident in seen or len(seen)>=20:raise_code('GRD-403',reason='handoff_dependency_limit')
        token=_handoff_reads.set((*seen,ident))
        try:
            _,_,package=self._handoff_document_basis(resources,value['project_id'],value['source'],actor)
            p=package['delivery_package']
            _,attempt,_=self._delivery_package_basis(resources,value['project_id'],DeliveryPackageCommand.model_validate(p['selection']),p['created_by'])
            return self._delivery_source_grants(resources,attempt)
        finally:_handoff_reads.reset(token)

    def _validate_handoff_document(self,definition,resources,previous,event):
        from pyharness.application.workspace_store import canonical,digest_of
        from pyharness.application.handoff_document_contracts import HandoffDocumentCommand
        value=definition.model_dump();source=value['source'];actor=value['created_by']
        try:
            if previous is not None:raise ValueError('handoff document immutable')
            self._s05_base(value,resources,previous,event,'document')
            handoff,role,package=self._handoff_document_basis(resources,value['project_id'],source,actor)
            if role['role_contract']['selection']['role']=='consumer':
                if source['acceptance_ref'] is None:raise ValueError('consumer independent acceptance missing')
                accepted=self._delivery_exact(resources,value['project_id'],'delivery_acceptance',source['acceptance_ref'])
                if (self._acceptance_verdict(accepted['delivery_acceptance'])!='passed' or
                        accepted['delivery_acceptance']['selection']['package_ref']!=delivery_ref(package,'delivery_package')):
                    raise ValueError('consumer acceptance binding')
            elif source['acceptance_ref'] is not None:raise ValueError('reviewer document has no execution acceptance')
            if value['source_metadata'] is not None or value['retired'] or value['handoff_context']!=handoff['role_handoff']['selection']['minimum_context']:
                raise ValueError('handoff document provenance')
            command=HandoffDocumentCommand(schema_version=2,document_id=value['document_id'],project_revision=value['project_revision'],
                title=value['title'],handoff_ref=source['handoff_ref'],output_ref=source['output_ref'],format=value['format'],
                text_selection=source['text_selection'],confirmation_digest=source['confirmation_digest'])
            expected_digest=(source['text_selection']['quote_digest'] if source['text_selection'] else source['output_ref']['sha256'])
            if value['content_digest']!=expected_digest:raise ValueError('handoff selected bytes digest')
            if source['text_selection'] is None and value['size_bytes']!=source['output_ref']['size_bytes']:
                raise ValueError('handoff selected bytes size')
            text_bytes=sum(len(b['text'].encode('utf-8')) for b in value['blocks'])+len(value['handoff_context'].encode('utf-8'))
            if text_bytes>role['role_contract']['selection']['max_context_bytes']:raise ValueError('handoff context ceiling')
            if value['format']=='pdf':
                from pyharness.application.handoff_document_store import pdf_confirmation
                parsed={k:value[k] for k in ('format','parser_version','page_count','unlocatable_pages','blocks','capability','capability_reason')}
                if source['confirmation_digest']!=pdf_confirmation(value['project_id'],actor,command,parsed):
                    raise ValueError('handoff PDF preview binding')
            request=digest_of(canonical(dict(operation='handoff_document.save',project_id=value['project_id'],command=command.model_dump())))
            if request!=event.payload['request_digest']:raise ValueError('handoff document request binding')
        except (ValueError,KeyError,PyHError):raise_code('PERS-202',reason='handoff_document_definition_binding')
