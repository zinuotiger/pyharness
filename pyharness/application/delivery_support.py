"""Historical delivery binding and explicit current requester gates."""
from pyharness.application.delivery_records import DELIVERY_MODELS
from pyharness.errors import PyHError,raise_code


def delivery_request(project,kind,command):
    from pyharness.application.workspace_store import canonical,digest_of
    return digest_of(canonical(dict(operation=kind+'.save',project_id=project,command=command.model_dump())))


def delivery_ref(row,kind):
    key=DELIVERY_MODELS[kind][2];value=row[kind]
    return {key:value['selection'][key],'revision':value['revision'],'digest':row['digest'],'source_seq':row['source_seq']}


def selected_outputs(package):
    from pyharness.application.workspace_store import canonical
    values={canonical(o):o for b in package['selection']['bindings'] for o in b['outputs']}
    return list(values.values())


class DeliverySupport:
    def _delivery_exact(self,resources,project,kind,ref):
        key=DELIVERY_MODELS[kind][2];row=resources.get(ref[key],{});value=row.get(kind)
        if not value or value['project_id']!=project:raise_code('GRD-403',reason='delivery_reference_unavailable')
        if delivery_ref(row,kind)!=ref:raise_code('EVT-101',reason='delivery_reference_changed')
        return row

    def _delivery_package_basis(self,resources,project,command,producer):
        raw=command.model_dump();execution=self._s05_exact(resources,project,'execution_authorization',raw['execution_ref'],'execution_id',producer)
        attempt=self._execution_attempt(resources,project,execution['execution_authorization']['selection']['attempt_ref'],producer)['run_attempt']
        definition=self._s05_exact(resources,project,'delivery_definition',raw['delivery_ref'],'delivery_id',producer)
        delivery=definition['delivery_definition']
        if (attempt['context']['delivery_ref']!=raw['delivery_ref'] or delivery['task_ref']!=attempt['prerequisites']['task_ref'] or
                command.project_revision!=attempt['project_revision'] or delivery['retired']):
            raise_code('EVT-101',reason='delivery_execution_definition_mismatch')
        items={item['item_id']:item for item in delivery['items']};bound={b.item_id for b in command.bindings}
        if not bound.issubset(items) or any(item['required'] and key not in bound for key,item in items.items()):
            raise_code('EVT-100',reason='delivery_required_output_missing')
        if any(o.execution_id!=command.execution_ref.execution_id for b in command.bindings for o in b.outputs):
            raise_code('EVT-100',reason='delivery_foreign_execution_output')
        return execution,attempt,definition

    def _delivery_source_grants(self,resources,attempt):
        """Stored owner locates metadata; it never substitutes request identity."""
        producer=attempt['created_by'];project=attempt['project_id']
        task=self._s05_exact(resources,project,'product_task',attempt['prerequisites']['task_ref'],'product_task_id',producer)['product_task']
        if task['retired']:raise_code('GRD-403',reason='delivery_source_task_retired')
        wi=self._task_bound_input(resources,task,producer)['workspace_input']
        grants=set(self._input_grant_ids(wi))
        context=attempt['context']
        documents=[self._s05_exact(resources,project,'document',ref,'document_id',producer) for ref in context['document_refs']]
        for ref in context['citation_refs']:
            cite=self._s05_exact(resources,project,'citation',ref,'citation_id',producer)['citation']
            documents.append(self._s05_exact(resources,project,'document',cite['locator']['document_ref'],'document_id',producer))
        for row in documents:
            if row['document']['retired']:raise_code('GRD-403',reason='delivery_source_document_retired')
            grants.update(self._document_grants(resources,row['document'],producer))
        return grants

    def _delivery_package_gate(self,resources,row,requester,authorize,service,*,granted=False):
        from pyharness.application.delivery_contracts import DeliveryPackageCommand
        from pyharness.application.delivery_outputs import current_selected
        value=row['delivery_package'];project=value['project_id']
        if requester!=value['created_by'] and not granted:raise_code('GRD-403',reason='delivery_package_private')
        self._context_project(resources,project,value['project_revision'])
        command=DeliveryPackageCommand.model_validate(value['selection'])
        execution,attempt,definition=self._delivery_package_basis(resources,project,command,value['created_by'])
        grants=self._delivery_source_grants(resources,attempt)
        grants.update(self._reference_grants(resources,definition,'delivery_definition',value['created_by']))
        self._context_finish(authorize,requester,resources,grants)
        self._handoff_dependencies_current(resources,attempt,requester,authorize,service)
        outputs=current_selected(service,execution,selected_outputs(value))
        self._context_finish(authorize,requester,resources,grants)
        return outputs

    def _validate_delivery_record(self,definition,resources,previous,event,kind):
        try:
            value=definition.model_dump();actor=value['created_by'];project=value['project_id']
            self._context_project(resources,project,value['project_revision']);self._context_quota(resources,kind,project)
            if actor!=event.payload['actor_ref'] or value['submission_key']!=event.payload['command_id']:
                raise ValueError('delivery actor/key mismatch')
            if delivery_request(project,kind,definition.selection)!=event.payload['request_digest']:
                raise ValueError('delivery request mismatch')
            if kind!='role_contract' and previous is not None:raise ValueError('immutable delivery fact')
            if kind=='delivery_package':
                self._delivery_package_basis(resources,project,definition.selection,actor)
            else:
                self._validate_role_delivery(definition,resources,previous,kind)
        except (ValueError,KeyError,PyHError):raise_code('PERS-202',reason='delivery_definition_binding')

    def _package_business(self,resources,row,actor,authorize,service):
        value=row['delivery_package'];reference=delivery_ref(row,'delivery_package')
        if any(r.get('delivery_package',{}).get('selection',{}).get('execution_ref')==value['selection']['execution_ref']
                and r['source_seq']>row['source_seq'] for r in resources.values()):return 'invalidated'
        reviews=[r for r in resources.values() if r.get('delivery_acceptance',{}).get('selection',{}).get('package_ref')==reference]
        if not reviews:return 'not_evaluated'
        latest=max(reviews,key=lambda r:r['source_seq'])
        try:return self._acceptance_current(resources,latest,actor,authorize,service)
        except PyHError as exc:
            if exc.code not in ('GRD-403','EVT-101','PERS-202'):raise
            return 'invalidated'

    def _delivery_business_status(self,resources,row,actor,authorize):
        execution=self._s05_ref(row,'execution_id','execution_authorization')
        packages=[r for r in resources.values() if r.get('delivery_package',{}).get('selection',{}).get('execution_ref')==execution]
        if not packages:return None
        latest=max(packages,key=lambda r:r['source_seq']);service=self._handoff_owner()
        try:
            self._delivery_package_gate(resources,latest,actor,authorize,service)
            return self._package_business(resources,latest,actor,authorize,service)
        except PyHError as exc:
            if exc.code not in ('GRD-403','EVT-101','PERS-202'):raise
            return 'invalidated'

    def _package_summary(self,row,*,resources=None,actor=None,authorize=None,service=None):
        value=row['delivery_package'];command=value['selection']
        return dict(command,revision=value['revision'],project_id=value['project_id'],
            digest=row['digest'],source_seq=row['source_seq'],created_at=row['created_at'],
            persisted=True,status='frozen',business_acceptance=self._package_business(resources,row,actor,authorize,service) if resources is not None else 'not_evaluated',
            execution_authorized=False,export_authorized=False,request_digest=row['request_digest'])
