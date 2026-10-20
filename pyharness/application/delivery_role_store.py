"""Same-owner named delivery facts; no signing by client supplied identities."""
import time
from pyharness.application.delivery_roles import DeliveryRoles
from pyharness.application.delivery_records import DELIVERY_MODELS
from pyharness.application.delivery_support import delivery_request,delivery_ref
from pyharness.errors import PyHError,raise_code


class DeliveryRoleStore(DeliveryRoles):
    def _role_extra(self,resources,project,kind,command,actor,service):
        if kind=='role_contract':
            from pyharness.application.delivery_identity import recipient_id
            old=resources.get(command.role_id,{}).get('role_contract')
            if command.retired and old:
                prior=dict(old['selection']);selected=command.model_dump()
                for key in ('expected_revision','retired'):prior.pop(key);selected.pop(key)
                if prior!=selected:raise_code('EVT-100',reason='role_retirement_must_only_reduce_permission')
                return dict(recipient_id=old['recipient_id'],consumer_snapshot=old['consumer_snapshot'])
            recipient=recipient_id(service,command.recipient_username);snapshot=None
            if command.consumer_agent_ref:
                selected=command.consumer_agent_ref
                version=service.platform.new_binding_version(selected.agent_id,selected.version)
                if version['sha256']!=selected.sha256:raise_code('EVT-101',reason='handoff_consumer_version_changed')
                snapshot=version['snapshot']
            return dict(recipient_id=recipient,consumer_snapshot=snapshot)
        if kind in ('role_handoff','delivery_export'):
            if kind=='role_handoff':role,_=self._handoff_basis(resources,project,command)
            else:_,role,_=self._handoff_record(resources,project,command.handoff_ref.model_dump())
            return dict(recipient_id=role['role_contract']['recipient_id'])
        return {}

    def _role_write_gate(self,resources,definition,kind,actor,authorize,service):
        from pyharness.application.delivery_identity import require_member
        command=definition.selection;project=definition.project_id
        if kind=='role_contract':
            parent=self._role_parent(resources,project,command,actor)['execution_authorization']
            # Retirement is permission decrease; do not require still-readable
            # source files, a live recipient or a remaining execution budget.
            if not command.retired:
                require_member(service,actor)
                attempt=self._execution_attempt(resources,project,parent['selection']['attempt_ref'],actor)['run_attempt']
                self._context_finish(authorize,actor,resources,self._delivery_source_grants(resources,attempt))
        elif kind=='role_handoff':
            role,package=self._handoff_basis(resources,project,command)
            if actor!=role['role_contract']['created_by']:raise_code('GRD-403',reason='handoff_grantor_required')
            self._delivery_package_gate(resources,package,actor,authorize,service)
        else:
            handoff,_,_=self._handoff_record(resources,project,command.handoff_ref.model_dump())
            h=handoff['role_handoff']
            if kind in ('handoff_ack','delivery_acceptance') and actor!=h['recipient_id']:
                raise_code('GRD-403',reason='handoff_recipient_required')
            self._handoff_current(resources,handoff,actor,authorize,service)
            if kind=='delivery_export' and command.acceptance_ref:
                accepted=self._delivery_exact(resources,project,'delivery_acceptance',command.acceptance_ref.model_dump())
                if self._acceptance_current(resources,accepted,actor,authorize,service)!='passed':
                    raise_code('GRD-403',reason='independent_acceptance_required')
        self._context_finish(authorize,actor,resources)

    async def save_role_delivery(self,project,kind,command,*,command_id,authorize,service):
        started=time.monotonic();model,_,key,_=DELIVERY_MODELS[kind];ident=getattr(command,key)
        async with self._workspace_capture_lock(started):
            actor=authorize();await self._open(initialize=False);resources,commands=self._grant_state()
            request=delivery_request(project,kind,command)
            revision=command.expected_revision+1 if kind=='role_contract' else 1
            self._context_envelope(kind,ident,revision,command_id,actor,request)
            if old:=commands.get((actor,command_id)):
                if old[0]!=request:raise_code('EVT-100',reason='idempotency_conflict')
                self._role_write_gate(resources,model.model_validate(old[1][kind]),kind,actor,authorize,service)
                return self._role_summary(resources,old[1],kind,actor,authorize,service)
            parent=self._context_project(resources,project)
            definition=model(project_id=project,project_revision=parent['revision'],revision=revision,
                created_by=actor,submission_key=command_id,selection=command,
                **self._role_extra(resources,project,kind,command,actor,service))
            def gate():
                fresh,_=self._grant_state();self._context_project(fresh,project,definition.project_revision)
                self._context_quota(fresh,kind,project);previous=fresh.get(ident)
                if kind=='role_contract':
                    if previous:
                        self._context_owned(fresh,ident,kind,project,actor)
                        if previous[kind]['selection']['retired']:raise_code('GRD-403',reason='role_retired')
                    if (previous[kind]['revision'] if previous else 0)!=command.expected_revision:
                        raise_code('EVT-101',reason='stale_revision')
                elif previous:raise_code('EVT-101',reason='delivery_fact_exists')
                extra=self._role_extra(fresh,project,kind,command,actor,service)
                if extra!={k:getattr(definition,k) for k in extra}:
                    raise_code('EVT-101',reason='delivery_recipient_or_configuration_changed')
                try:self._validate_role_delivery(definition,fresh,previous,kind)
                except ValueError:raise_code('EVT-100',reason='delivery_role_binding_invalid')
                self._role_write_gate(fresh,definition,kind,actor,authorize,service)
            gate()
            resources,row=await self._append_context(definition,kind,ident,command_id,actor,request,gate)
            try:return self._role_summary(resources,row,kind,actor,authorize,service)
            except PyHError:raise_code('PERS-202',reason='workspace_commit_receipt_unavailable')

    def _role_summary(self,resources,row,kind,actor,authorize,service):
        value=row[kind];command=value['selection']
        result=dict(command,revision=value['revision'],project_id=value['project_id'],digest=row['digest'],
            source_seq=row['source_seq'],created_at=row['created_at'],persisted=True,
            request_digest=row['request_digest'],execution_authorized=False,export_authorized=False)
        if kind=='role_contract':
            if actor not in (value['created_by'],value['recipient_id']):raise_code('GRD-403',reason='role_private')
            latest=resources[command['role_id']]['role_contract']
            status='retired' if latest['selection']['retired'] else 'superseded' if latest['revision']!=value['revision'] else 'defined'
            result.update(status=status,current_revision=latest['revision'],recipient_id=value['recipient_id'])
        elif kind=='role_handoff':
            role,package,outputs=self._handoff_current(resources,row,actor,authorize,service)
            from pyharness.application.delivery_contracts import DeliveryPackageCommand
            _,_,definition=self._delivery_package_basis(resources,value['project_id'],
                DeliveryPackageCommand.model_validate(package['delivery_package']['selection']),package['delivery_package']['created_by'])
            result.update(status='handed_off',acknowledged=self._handoff_acknowledged(resources,row,value['recipient_id']),outputs=outputs,
                delivery_items=definition['delivery_definition']['items'],delivery_ref=package['delivery_package']['selection']['delivery_ref'],
                role=role['role_contract']['selection']['role'],recipient_id=value['recipient_id'],producer_id=value['created_by'])
        else:
            handoff,_,_=self._handoff_record(resources,value['project_id'],command['handoff_ref'])
            self._handoff_current(resources,handoff,actor,authorize,service)
            if kind=='delivery_acceptance':
                verdict=self._acceptance_verdict(value)
                result.update(status='reviewed',historical_verdict=verdict,business_acceptance=self._acceptance_current(resources,row,actor,authorize,service))
            elif kind=='handoff_ack':result.update(status='acknowledged')
            elif kind=='delivery_export':
                if command['acceptance_ref']:
                    accepted=self._delivery_exact(resources,value['project_id'],'delivery_acceptance',command['acceptance_ref'])
                    if self._acceptance_current(resources,accepted,actor,authorize,service)!='passed':raise_code('GRD-403',reason='delivery_export_acceptance_invalid')
                result.update(status='granted',export_authorized=True,recipient_id=value['recipient_id'])
        self._context_finish(authorize,actor,resources)
        return result

    async def role_delivery(self,project,kind,ident,*,authorize,service):
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            actor=authorize();resources,_,_=await self._execution_read_state();row=resources.get(ident,{})
            if not row.get(kind) or row[kind]['project_id']!=project:raise_code('GRD-403',reason='delivery_fact_unavailable')
            return self._role_summary(resources,row,kind,actor,authorize,service)
