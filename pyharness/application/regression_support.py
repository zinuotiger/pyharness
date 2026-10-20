"""Historical control checks, independent of today's account or source permissions."""
from pyharness.application.regression_records import REGRESSION_MODELS
from pyharness.application.delivery_support import delivery_ref
from pyharness.errors import PyHError,raise_code


def regression_request(project,kind,command):
    from pyharness.application.workspace_store import canonical,digest_of
    return digest_of(canonical(dict(operation=kind+'.save',project_id=project,command=command.model_dump())))


def regression_ref(row,kind):
    key=REGRESSION_MODELS[kind][2];value=row[kind]
    return {key:value[key],'revision':value['revision'],'digest':row['digest'],'source_seq':row['source_seq']}


def regression_summary(row,kind):
    from pyharness.application.workspace_store import canonical
    return dict(row[kind],digest=row['digest'],source_seq=row['source_seq'],created_at=row['created_at'],
        record_canonical=canonical(row[kind]).decode('utf-8'),status='saved',persisted=True,execution_authorized=False)


def exact_case(store,resources,project,reference,actor):
    ref=reference.model_dump() if hasattr(reference,'model_dump') else reference
    row=store._s05_history(resources,project,ref['case_id'],ref['revision'],'regression_case',actor)
    if regression_ref(row,'regression_case')!=ref:raise_code('EVT-101',reason='regression_case_reference')
    return row


def source_metadata(store,resources,project,origin,actor):
    if origin['tenant_id']!=store.tenant_id or origin['subject_ref']!=actor:
        raise_code('GRD-403',reason='regression_source_subject')
    ref=origin['execution_ref'];row=store._context_owned(resources,ref['execution_id'],'execution_authorization',project,actor)
    if store._s05_ref(row,'execution_id','execution_authorization')!=ref:
        raise_code('EVT-101',reason='regression_execution_reference')
    if row['execution_authorization']['session_id']!=origin['session_id']:
        raise_code('GRD-403',reason='regression_source_session')
    return row


def historical_business_failure(store,resources,origin):
    ref=origin['acceptance_ref'];row=resources.get(ref['acceptance_id'])
    if not row or 'delivery_acceptance' not in row or delivery_ref(row,'delivery_acceptance')!=ref:
        raise_code('EVT-101',reason='regression_acceptance_reference')
    value=row['delivery_acceptance'];package_ref=value['selection']['package_ref']
    package=resources.get(package_ref['package_id'])
    if (not package or 'delivery_package' not in package or delivery_ref(package,'delivery_package')!=package_ref or
            package['delivery_package']['selection']['execution_ref']!=origin['execution_ref'] or
            store._acceptance_verdict(value)!='rejected'):
        raise_code('EVT-101',reason='regression_business_failure_reference')


def validate_regression_record(store,definition,resources,previous,event,kind,history):
    value=definition.model_dump();command=definition.selection;actor=value['created_by'];project=value['project_id']
    try:
        store._s05_base(value,resources,previous,event,kind)
        if value['submission_key']!=event.payload['command_id']:
            raise ValueError('regression command identity')
        if regression_request(project,kind,command)!=event.payload['request_digest']:
            raise ValueError('regression request differs')
        if kind=='regression_case':
            origin=value['selection']['origin'];source_metadata(store,resources,project,origin,actor)
            if origin['failure_kind']=='business_failed':historical_business_failure(store,resources,origin)
        else:
            imported=value['selection'].get('import_source')
            if imported:
                ref=imported['dataset_ref'];source=history.get(('regression_dataset',ref['dataset_id'],ref['revision']))
                if (source is None or source['regression_dataset']['project_id']!=project or
                        source['regression_dataset']['created_by']!=actor or regression_ref(source,'regression_dataset')!=ref or
                        source['regression_dataset']['selection']['case_refs']!=value['selection']['case_refs']):
                    raise ValueError('import must bind an earlier exact owned dataset')
            for ref in value['selection']['case_refs']:
                row=history.get(('regression_case',ref['case_id'],ref['revision']))
                if (row is None or row['regression_case']['project_id']!=project or
                        row['regression_case']['created_by']!=actor or regression_ref(row,'regression_case')!=ref):
                    raise ValueError('dataset must bind a prior exact owned case version')
    except (ValueError,KeyError,PyHError):raise_code('PERS-202',reason='regression_history_binding')


def import_source_basis(store,resources,project,command,actor):
    if not hasattr(command,'import_source'):return
    ref=command.import_source.dataset_ref.model_dump()
    row=store._s05_history(resources,project,ref['dataset_id'],ref['revision'],'regression_dataset',actor)
    if (regression_ref(row,'regression_dataset')!=ref or
            row['regression_dataset']['selection']['case_refs']!=[v.model_dump() for v in command.case_refs]):
        raise_code('EVT-101',reason='regression_import_source_changed')
