"""Fixed-role consumption and irreversible reservations before original admission."""
from pyharness.application.handoff_budget_contracts import HandoffBudget,HandoffAllowance
from pyharness.application.handoff_budget_observation import consumer_allowance,parent_allowance
from pyharness.application.delivery_support import delivery_ref
from pyharness.errors import raise_code,PyHError


def handoff_contexts(store,resources,attempt):
    value=attempt['run_attempt'] if 'run_attempt' in attempt else attempt
    refs=list(value['context']['document_refs'])
    for ref in value['context']['citation_refs']:
        cite=resources.get(ref['citation_id'],{}).get('citation')
        if cite:refs.append(cite['locator']['document_ref'])
    docs={ref['document_id']:resources[ref['document_id']]['document'] for ref in refs}
    return [d for d in docs.values() if d['schema_version']==2]


def lease_identity(execution_id,attempt_ref,role_ref):
    from pyharness.application.workspace_store import canonical,digest_of
    checksum=digest_of(canonical(dict(execution_id=execution_id,attempt_ref=attempt_ref,role_ref=role_ref)))
    return 'hb-'+checksum[:16],'handoff-budget.'+checksum


class HandoffExecutionStore:
    def _handoff_execution_binding(self,resources,attempt,agent_ref):
        value=attempt['run_attempt']
        return self._handoff_context_binding(resources,value['project_id'],value['context'],value['created_by'],agent_ref)

    def _handoff_context_binding(self,resources,project,context,actor,agent_ref):
        docs=[resources[ref['document_id']]['document'] for ref in context['document_refs']
            if resources[ref['document_id']]['document']['schema_version']==2]
        if not docs:return None
        bindings=[]
        for doc in docs:
            h,r,p=self._handoff_document_basis(resources,project,doc['source'],actor)
            role=r['role_contract'];selected=role['selection']
            if selected['role']!='consumer' or selected['consumer_agent_ref']!=agent_ref:
                raise_code('GRD-403',reason='handoff_fixed_consumer_required')
            bindings.append((h,r,p))
        first=bindings[0];role_ref=delivery_ref(first[1],'role_contract');package_ref=delivery_ref(first[2],'delivery_package')
        if any(delivery_ref(r,'role_contract')!=role_ref or delivery_ref(p,'delivery_package')!=package_ref for _,r,p in bindings):
            raise_code('GRD-403',reason='handoff_conflicting_budget_lineages')
        # Count the complete quoted context once materialized, including
        # repeated document copies and citation text repeated in the bundle.
        text_bytes=sum(len(doc['handoff_context'].encode('utf-8'))+
            sum(len(block['text'].encode('utf-8')) for block in doc['blocks']) for doc in docs)
        owned={doc['document_id'] for doc in docs}
        for reference in context['citation_refs']:
            citation=resources[reference['citation_id']]['citation']
            if citation['locator']['document_ref']['document_id'] in owned:
                _,_,quote=self._citation_document(resources,project,citation['locator'],actor,current=True)
                text_bytes+=len(quote.encode('utf-8'))
        if text_bytes>first[1]['role_contract']['selection']['max_context_bytes']:
            raise_code('GRD-403',reason='handoff_context_limit')
        refs={h['role_handoff']['selection']['handoff_id']:delivery_ref(h,'role_handoff') for h,_,_ in bindings}
        return dict(role_ref=role_ref,package_ref=package_ref,handoff_refs=[refs[k] for k in sorted(refs)],
            parent_execution_ref=first[2]['delivery_package']['selection']['execution_ref'])

    def _check_handoff_execution(self,resources,attempt,command,environment,actor,authorize,*,reserve=False):
        from pyharness.application.delivery_identity import consumer_ceiling
        binding=self._handoff_execution_binding(resources,attempt,command.agent_ref.model_dump())
        if binding is None:return None
        if attempt['run_attempt']['schema_version']==2:raise_code('GRD-403',reason='handoff_continuation_requires_budget_admission')
        service=self._handoff_owner();project=attempt['run_attempt']['project_id']
        for reference in binding['handoff_refs']:
            handoff,_,_=self._handoff_record(resources,project,reference)
            self._handoff_current(resources,handoff,actor,authorize,service)
        parent=self._s05_exact(resources,project,'execution_authorization',binding['parent_execution_ref'],'execution_id',
            resources[binding['parent_execution_ref']['execution_id']]['execution_authorization']['created_by'])
        try:consumer_ceiling(parent['execution_authorization']['environment']['agent_snapshot'],environment.agent_snapshot)
        except ValueError:raise_code('GRD-403',reason='handoff_consumer_capability_exceeded')
        # Metadata-only pricing check; no credential resolution or model calls.
        from pyharness.application.execution_configuration import compiled_configuration
        from types import SimpleNamespace
        from pyharness.core.llm_usage import _price_table
        _,cfg=compiled_configuration(SimpleNamespace(service=service),command.agent_ref)
        prices=_price_table(cfg.llm.usage.unit_price)
        if any(model not in prices for model in [cfg.llm.model,*(cfg.llm.fallback_models or [])]):
            raise_code('GRD-403',reason='handoff_consumer_pricing_unknown')
        limits,observed,capacity,witness=parent_allowance(service,parent)
        reserved=consumer_allowance(environment.agent_snapshot)
        exid=getattr(command,'execution_id',None)
        total={k:0 for k in HandoffAllowance.model_fields}
        for row in resources.values():
            lease=row.get('handoff_budget')
            if lease and lease['parent_execution_ref']==binding['parent_execution_ref']:
                if lease['execution_id']==exid:
                    if (lease['attempt_ref']!=command.attempt_ref.model_dump() or lease['role_ref']!=binding['role_ref'] or
                            lease['reserved']!=reserved.model_dump()):raise_code('EVT-101',reason='handoff_budget_lease_conflict')
                    continue
                for key in total:total[key]+=lease['reserved'][key]
        if any(total[k]+getattr(reserved,k)>getattr(capacity,k) for k in total):
            raise_code('GRD-403',reason='handoff_parent_budget_exhausted')
        self._context_finish(authorize,actor,resources)
        return dict(binding=binding,parent_limits=limits,observed=observed,capacity=capacity,reserved=reserved,observation_digest=witness)

    async def _reserve_handoff_budget(self,resources,attempt,command,environment,actor,authorize):
        observed=self._check_handoff_execution(resources,attempt,command,environment,actor,authorize,reserve=True)
        if observed is None:return resources
        binding=observed.pop('binding');project=attempt['run_attempt']['project_id']
        reference=self._s05_ref(attempt,'attempt_id','run_attempt')
        ident,key=lease_identity(command.execution_id,reference,binding['role_ref'])
        definition=HandoffBudget(budget_id=ident,project_id=project,project_revision=attempt['run_attempt']['project_revision'],
            created_by=actor,submission_key=key,execution_id=command.execution_id,attempt_ref=reference,
            agent_ref=command.agent_ref,**binding,**observed)
        def gate():
            fresh,_=self._grant_state();current=self._check_handoff_execution(fresh,attempt,command,environment,actor,authorize,reserve=True)
            if current is None:raise_code('EVT-101',reason='handoff_budget_binding_changed')
            prior=fresh.get(ident,{}).get('handoff_budget')
            if prior and prior!=definition.model_dump():raise_code('EVT-101',reason='handoff_budget_lease_conflict')
            self._context_quota(fresh,'handoff_budget',project)
        gate()
        if ident in resources:
            if resources[ident]['handoff_budget']!=definition.model_dump():raise_code('EVT-101',reason='handoff_budget_lease_conflict')
            return resources
        from pyharness.application.workspace_store import canonical,digest_of
        request=digest_of(canonical(definition.model_dump()))
        resources,_=await self._append_context(definition,'handoff_budget',ident,key,actor,request,gate)
        return resources

    def _validate_handoff_budget(self,definition,resources,previous,event):
        from pyharness.application.workspace_store import canonical,digest_of
        value=definition.model_dump();actor=definition.created_by;project=definition.project_id
        try:
            if previous or actor!=event.payload['actor_ref'] or definition.submission_key!=event.payload['command_id']:
                raise ValueError('immutable budget actor/key')
            self._context_project(resources,project,definition.project_revision);self._context_quota(resources,'handoff_budget',project)
            attempt=self._execution_attempt(resources,project,definition.attempt_ref,actor)
            binding=self._handoff_execution_binding(resources,attempt,definition.agent_ref.model_dump())
            if binding is None or any(value[k]!=v for k,v in binding.items()):raise ValueError('budget lineage')
            ident,key=lease_identity(definition.execution_id,definition.attempt_ref.model_dump(),definition.role_ref.model_dump())
            if ident!=definition.budget_id or key!=definition.submission_key or digest_of(canonical(value))!=event.payload['request_digest']:
                raise ValueError('budget deterministic identity')
            role=resources[definition.role_ref.role_id]['role_contract']
            if consumer_allowance(role['consumer_snapshot'])!=definition.reserved:raise ValueError('budget fixed consumer allowance')
            for dim in HandoffAllowance.model_fields:
                limit=getattr(definition.parent_limits,dim);spent=getattr(definition.observed,dim);capacity=getattr(definition.capacity,dim)
                if capacity>max(0,limit-spent):raise ValueError('budget capacity arithmetic')
                total=sum(r['handoff_budget']['reserved'][dim] for r in resources.values() if
                    r.get('handoff_budget',{}).get('parent_execution_ref')==value['parent_execution_ref'])
                if total+value['reserved'][dim]>capacity:raise ValueError('budget oversubscribed')
        except (ValueError,KeyError,PyHError):raise_code('PERS-202',reason='handoff_budget_definition_binding')

    def _validate_handoff_execution_lease(self,resources,definition,attempt):
        binding=self._handoff_execution_binding(resources,attempt,definition.selection.agent_ref.model_dump())
        if binding is None:return
        ident,_=lease_identity(definition.execution_id,definition.selection.attempt_ref.model_dump(),binding['role_ref'])
        lease=resources.get(ident,{}).get('handoff_budget')
        if (not lease or lease['created_by']!=definition.created_by or lease['execution_id']!=definition.execution_id or
                lease['attempt_ref']!=definition.selection.attempt_ref.model_dump() or
                any(lease[k]!=v for k,v in binding.items())):raise ValueError('handoff budget must precede execution authorization')
