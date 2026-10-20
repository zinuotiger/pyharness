"""Historical role/handoff/review binding and current exact grant checks."""
from pyharness.application.delivery_support import delivery_ref,selected_outputs
from pyharness.application.delivery_records import DELIVERY_MODELS
from pyharness.errors import raise_code


def subset_outputs(values,allowed):
    from pyharness.application.workspace_store import canonical
    keys={canonical(v) for v in allowed}
    if any(canonical(v) not in keys for v in values):raise_code('GRD-403',reason='handoff_output_outside_grant')


class DeliveryRoles:
    def _role_parent(self,resources,project,command,actor):
        return self._s05_exact(resources,project,'execution_authorization',command.parent_execution_ref.model_dump(),'execution_id',actor)

    def _handoff_basis(self,resources,project,selection):
        command=selection.model_dump() if hasattr(selection,'model_dump') else selection
        role=self._delivery_exact(resources,project,'role_contract',command['role_ref'])
        package=self._delivery_exact(resources,project,'delivery_package',command['package_ref'])
        r=role['role_contract'];p=package['delivery_package']
        if (r['selection']['retired'] or r['created_by']!=p['created_by'] or
                r['selection']['parent_execution_ref']!=p['selection']['execution_ref']):
            raise_code('GRD-403',reason='handoff_role_package_mismatch')
        subset_outputs(command['outputs'],selected_outputs(p))
        if len(command['minimum_context'].encode('utf-8'))>r['selection']['max_context_bytes']:
            raise_code('EVT-100',reason='handoff_context_limit')
        return role,package

    def _handoff_record(self,resources,project,ref):
        row=self._delivery_exact(resources,project,'role_handoff',ref)
        role,package=self._handoff_basis(resources,project,row['role_handoff']['selection'])
        if row['role_handoff']['recipient_id']!=role['role_contract']['recipient_id']:
            raise_code('PERS-202',reason='handoff_recipient_binding')
        return row,role,package

    @staticmethod
    def _handoff_acknowledged(resources,handoff,actor):
        reference=delivery_ref(handoff,'role_handoff')
        return any(row.get('handoff_ack',{}).get('created_by')==actor and
            row['handoff_ack']['selection']['handoff_ref']==reference for row in resources.values())

    def _review_basis(self,resources,project,command,actor):
        handoff,role,package=self._handoff_record(resources,project,command.handoff_ref.model_dump())
        p=package['delivery_package'];h=handoff['role_handoff'];r=role['role_contract']
        if (actor==p['created_by'] or actor!=h['recipient_id'] or r['selection']['role']!='reviewer' or
                command.package_ref.model_dump()!=delivery_ref(package,'delivery_package') or
                not self._handoff_acknowledged(resources,handoff,actor)):
            raise_code('GRD-403',reason='independent_acknowledged_reviewer_required')
        from pyharness.application.delivery_contracts import DeliveryPackageCommand
        _,_,definition=self._delivery_package_basis(resources,project,DeliveryPackageCommand.model_validate(p['selection']),p['created_by'])
        items={item['item_id']:item for item in definition['delivery_definition']['items']}
        required={item['item_id'] for item in items.values() if item['required']}
        required_outputs=[o for binding in p['selection']['bindings'] if binding['item_id'] in required for o in binding['outputs']]
        subset_outputs(required_outputs,h['selection']['outputs'])
        expected={(key,i) for key in required for i in range(len(items[key]['acceptance_criteria']))}
        decisions={(d.item_id,d.criterion_index) for d in command.decisions}
        if not expected.issubset(decisions) or any(key not in items or i>=len(items[key]['acceptance_criteria']) for key,i in decisions):
            raise_code('EVT-100',reason='delivery_criterion_decisions_incomplete')
        return handoff,role,package

    @staticmethod
    def _acceptance_verdict(value):
        verdicts=[d['verdict'] for d in value['selection']['decisions']]
        return 'rejected' if 'fail' in verdicts else 'pending' if 'unknown' in verdicts else 'passed'

    def _validate_role_delivery(self,definition,resources,previous,kind):
        value=definition.model_dump();command=definition.selection;actor=value['created_by'];project=value['project_id']
        if kind=='role_contract':
            parent=self._role_parent(resources,project,command,actor)['execution_authorization']
            if value['project_revision']!=parent['project_revision'] or definition.revision!=command.expected_revision+1:
                raise ValueError('role parent project revision')
            if previous:
                prior=previous[kind]
                if prior['created_by']!=actor or prior['project_id']!=project or prior['selection']['retired']:
                    raise ValueError('role ownership/retirement')
            if command.role=='consumer':
                from pyharness.application.delivery_identity import consumer_ceiling
                from pyharness.application.workspace_store import digest_of
                import json
                child=consumer_ceiling(parent['environment']['agent_snapshot'],definition.consumer_snapshot)
                ref=command.consumer_agent_ref
                if (child['agent_id']!=ref.agent_id or child['current_version']!=ref.version or
                        digest_of(json.dumps(child,sort_keys=True).encode())!=ref.sha256):
                    raise ValueError('role fixed consumer snapshot')
            elif definition.consumer_snapshot is not None:raise ValueError('reviewer has no execution snapshot')
        elif kind=='role_handoff':
            role,package=self._handoff_basis(resources,project,command)
            if actor!=role['role_contract']['created_by'] or definition.recipient_id!=role['role_contract']['recipient_id']:
                raise ValueError('handoff grantor/recipient')
        elif kind=='handoff_ack':
            handoff,_,_=self._handoff_record(resources,project,command.handoff_ref.model_dump())
            if actor!=handoff['role_handoff']['recipient_id']:raise ValueError('ack real subject')
        elif kind=='delivery_acceptance':
            self._review_basis(resources,project,command,actor)
        elif kind=='delivery_export':
            handoff,role,package=self._handoff_record(resources,project,command.handoff_ref.model_dump())
            h=handoff['role_handoff'];p=package['delivery_package']
            if (actor!=p['created_by'] or command.package_ref.model_dump()!=delivery_ref(package,'delivery_package') or
                    definition.recipient_id!=h['recipient_id'] or not self._handoff_acknowledged(resources,handoff,h['recipient_id'])):
                raise ValueError('export grantor/package/recipient/ack')
            subset_outputs([o.model_dump() for o in command.outputs],h['selection']['outputs'])
            if command.acceptance_ref:
                accepted=self._delivery_exact(resources,project,'delivery_acceptance',command.acceptance_ref.model_dump())
                a=accepted['delivery_acceptance']
                if a['selection']['package_ref']!=command.package_ref.model_dump() or self._acceptance_verdict(a)!='passed':
                    raise ValueError('export independent acceptance')
        else:raise ValueError('unsupported delivery fact')

    def _handoff_current(self,resources,handoff,actor,authorize,service,*,package_granted=False):
        from pyharness.application.delivery_identity import recipient_id,require_member
        h=handoff['role_handoff'];role,package=self._handoff_basis(resources,h['project_id'],h['selection'])
        r=role['role_contract']
        if actor not in (h['created_by'],h['recipient_id']) and not package_granted:raise_code('GRD-403',reason='handoff_private')
        if recipient_id(service,r['selection']['recipient_username'])!=h['recipient_id']:
            raise_code('GRD-403',reason='handoff_recipient_changed')
        require_member(service,h['created_by'])
        observed=self._delivery_package_gate(resources,package,actor,authorize,service,granted=True)
        subset_outputs(h['selection']['outputs'],selected_outputs(package['delivery_package']))
        from pyharness.application.workspace_store import canonical
        allowed={canonical(o) for o in h['selection']['outputs']}
        return role,package,[o for o in observed if canonical(o['reference']) in allowed]

    def _acceptance_current(self,resources,row,actor,authorize,service,*,package_granted=False):
        from pyharness.application.delivery_contracts import DeliveryAcceptanceCommand
        value=row['delivery_acceptance'];command=DeliveryAcceptanceCommand.model_validate(value['selection'])
        handoff,_,package=self._review_basis(resources,value['project_id'],command,value['created_by'])
        self._handoff_current(resources,handoff,actor,authorize,service,package_granted=package_granted)
        later=any(r.get('delivery_acceptance',{}).get('selection',{}).get('package_ref')==command.package_ref.model_dump()
            and r['source_seq']>row['source_seq'] for r in resources.values())
        superseded=any(r.get('delivery_package',{}).get('selection',{}).get('execution_ref')==package['delivery_package']['selection']['execution_ref']
            and r['source_seq']>package['source_seq'] for r in resources.values())
        if later or superseded:raise_code('EVT-101',reason='delivery_acceptance_superseded')
        return self._acceptance_verdict(value)
