"""Strict report provenance and confirmation; history does not run today's checker."""
from pyharness.application.experiment_run_binding import control_run,run_request
from pyharness.errors import PyHError,raise_code


def canonical(value):
    from pyharness.application.workspace_store import canonical as encode
    return encode(value)


def digest_of(raw):
    import hashlib
    return hashlib.sha256(raw).hexdigest()


def report_confirmation(tenant,actor,project,ident,reference,basis,digest,*,export=False):
    return digest_of(canonical(dict(operation='experiment.report.export' if export else 'experiment.report.save',
        tenant_id=tenant,actor=actor,project_id=project,report_id=ident,experiment_run_ref=reference,basis=basis,report_digest=digest)))


def validate_report_record(store,definition,resources,previous,event,history):
    try:
        value=definition.model_dump();selected=definition.selection;actor=value['created_by'];project=value['project_id']
        if previous or actor!=event.payload['actor_ref'] or value['submission_key']!=event.payload['command_id']:
            raise ValueError('report owner')
        run=control_run(store,resources,project,selected.experiment_run_ref.model_dump(),actor)['experiment_run']
        store._context_project(resources,project,value['project_revision']);store._context_quota(resources,'experiment_report',project)
        if value['project_revision']!=run['project_revision'] or definition.report_data.experiment_ref.model_dump()!=run['selection']['experiment_ref']:
            raise ValueError('report experiment scope')
        ref=run['selection']['experiment_ref'];source=history.get(('experiment_definition',ref['experiment_id'],ref['revision']))
        if source is None:raise ValueError('report definition missing')
        original=source['experiment_definition'];data=definition.report_data
        if ([c.case_ref.model_dump() for c in data.cases]!=original['selection']['case_refs'] or
                data.dataset_ref.model_dump()!=original['selection']['dataset_ref'] or data.dataset_total!=original['dataset_total'] or
                selected.basis.control.observed_seq<selected.experiment_run_ref.source_seq or selected.basis.control.observed_seq>=event.seq):
            raise ValueError('report fixed definition')
        for pair,binding in zip(data.cases,original['case_nodes'],strict=True):
            for side in ('left','right'):
                measurement=getattr(pair,side)
                if (measurement.node_id!=binding[side+'_node_id'] or measurement.agent_ref.model_dump()!=original['selection'][side+'_agent_ref']):
                    raise ValueError('report node binding')
        expected=report_confirmation(store.tenant_id,actor,project,selected.report_id,selected.experiment_run_ref.model_dump(),
            selected.basis.model_dump(),selected.report_digest)
        if selected.confirmation_digest!=expected or run_request(project,selected,'experiment.report.save')!=event.payload['request_digest']:
            raise ValueError('report confirmation binding')
    except (ValueError,KeyError,PyHError):raise_code('PERS-202',reason='experiment_report_history_binding')


def prefix_gate(store,projects,basis):
    from pyharness.application.regression_candidates import event_prefix_digest
    from pyharness.persistence import session_data_paths,detect_truncation
    from pyharness.persistence_reader import _iter_replay
    if event_prefix_digest(store._strict_control_events(),basis.control.observed_seq)!=basis.control.prefix_digest:
        raise_code('EVT-101',reason='experiment_report_control_changed')
    for values in basis.nodes.values():
        for ref in values or []:
            paths=session_data_paths(projects.service._tenant_sessions_dir()/(ref.session_id+'.jsonl'));quarantine=set()
            if any(not p.is_file() or detect_truncation(p) is not None for p in paths):raise_code('PERS-202',reason='experiment_report_log_incomplete')
            events=[]
            for event in _iter_replay(paths,quarantine,tolerate_unreadable=False):
                if event.tenant_id!=store.tenant_id or event.session_id!=ref.session_id or event.seq!=len(events)+1:
                    raise_code('PERS-202',reason='experiment_report_log_scope')
                events.append(event)
                if event.seq>=ref.observed_seq:break
            if quarantine or event_prefix_digest(events,ref.observed_seq)!=ref.prefix_digest:
                raise_code('EVT-101',reason='experiment_report_prefix_changed')
