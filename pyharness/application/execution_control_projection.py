"""A permission-reducing projection of the same journal; never source-read authority."""
from pyharness.errors import PyHError,raise_code
from pyharness.persistence import detect_truncation,session_data_paths
from pyharness.persistence_reader import _iter_replay


class ControlReadResources(dict):
    """One disposable, validated read snapshot; never a writer or persisted cache."""
    def __init__(self,resources,events):
        super().__init__(resources);self.control_events=tuple(events)


class ExecutionControlProjection:
    def _history_events(self,resources):
        if isinstance(resources,ControlReadResources):return iter(resources.control_events)
        return self._session.events_after(0)

    def _cancellation_state(self):
        return self._project_cancellation_control(self._strict_control_events())

    def _strict_control_events(self):
        if self._closed:raise_code('EVT-104',reason='workspace_closed')
        paths=session_data_paths(self.log_path);quarantine=set()
        try:
            for path in paths:
                if not path.is_file() or detect_truncation(path) is not None:
                    raise_code('PERS-202',reason='execution_control_incomplete')
            events=list(_iter_replay(paths,quarantine,tolerate_unreadable=False))
        except OSError:raise_code('PERS-202',reason='execution_control_unavailable')
        if quarantine or not events:raise_code('PERS-202',reason='execution_control_corrupt')
        self._validate_control_sequence(events)
        if self._session is not None and not self._failed and (
                [e.model_dump() for e in events]!=[e.model_dump() for e in self._session.events_after(0)]):
            raise_code('PERS-202',reason='execution_control_unconfirmed')
        return events

    def _project_cancellation_control(self,events):
        resources={};commands={};seen={}
        selected={'project','run_attempt','execution_authorization','execution_cancellation','task_graph_run','task_graph_dispatch','task_graph_cancellation','experiment_run','experiment_cancellation'}
        for event in events:
            if event.type=='session.created':continue
            p=event.payload;ident=p['resource_id'];kind=p['resource_kind'];key=(p['actor_ref'],p['command_id'])
            previous=seen.get(ident)
            if (key in commands or (previous and previous[0]!=kind) or
                    p['parent_revision']!=(previous[1] if previous else 0)):
                raise_code('PERS-202',reason='execution_control_conflict')
            seen[ident]=(kind,p['revision'])
            row=dict(resource_kind=kind,digest=p['digest'],source_seq=event.seq,
                created_at=event.ts,created_by=p['actor_ref'],request_digest=p['request_digest'])
            if kind in selected:
                definition=self._read_definition(p);value=definition.model_dump()
                if kind in {'experiment_run','experiment_cancellation'}:
                    from pyharness.application.experiment_run_binding import validate_run_record
                    validate_run_record(self,definition,resources,resources.get(ident),event,kind)
                if kind=='task_graph_run':
                    from pyharness.application.task_graph_control_checks import validate_minimal_graph_run
                    validate_minimal_graph_run(self,definition,resources,event,previous)
                if kind in {'task_graph_dispatch','task_graph_cancellation'}:self._validate_graph_control(definition,resources,resources.get(ident),event,kind)
                if kind=='run_attempt':self._validate_stop_attempt(value,resources,event,previous)
                if kind=='execution_authorization':self._validate_stop_authorization(definition,resources,event,previous)
                if kind=='execution_cancellation':self._validate_execution_cancellation(definition,resources,resources.get(ident),event)
                row[kind]=value;resources[ident]=row
            commands[key]=(p['request_digest'],row)
        return resources,commands

    def _validate_stop_attempt(self,value,resources,event,previous):
        from pyharness.application.workspace_store import canonical,digest_of
        from pyharness.application.continuation_binding import attempt_authority_digest,validate_continued_attempt
        try:
            actor=value['created_by'];project=value['project_id'];p=value['prerequisites']
            parent=self._context_project(resources,project,value['project_revision'])
            if (previous or actor!=event.payload['actor_ref'] or value['submission_key']!=event.payload['command_id'] or
                    p['tenant_id']!=self.tenant_id or p['project_digest']!=resources[project]['digest'] or
                    p['project_source_seq']!=resources[project]['source_seq'] or parent['revision']!=p['project_revision'] or
                    attempt_authority_digest(value)!=value['binding_digest']):raise ValueError('attempt metadata')
            if value['schema_version']==2:
                from pyharness.application.continuation_execution_contracts import ContinuedRunAttempt
                validate_continued_attempt(self,ContinuedRunAttempt.model_validate(value),resources,event)
            if value['schema_version']==3:
                from pyharness.application.task_graph_execution_contracts import GraphRunAttempt
                from pyharness.application.task_graph_binding import validate_graph_attempt
                validate_graph_attempt(self,GraphRunAttempt.model_validate(value),resources,event)
            expected=digest_of(canonical(dict(operation='run_attempt.submit',project_id=project,command=value['selection'])))
            if expected!=event.payload['request_digest']:raise ValueError('attempt request')
        except (ValueError,KeyError,PyHError):raise_code('PERS-202',reason='execution_control_attempt_binding')

    def _validate_stop_authorization(self,definition,resources,event,previous):
        value=definition.model_dump();actor=value['created_by'];project=value['project_id'];command=definition.selection
        try:
            self._context_project(resources,project,value['project_revision'])
            attempt=self._execution_attempt(resources,project,command.attempt_ref,actor)
            from pyharness.application.continuation_binding import validate_continued_authorization
            validate_continued_authorization(self,definition,resources,attempt)
            from pyharness.application.task_graph_binding import validate_graph_authorization
            validate_graph_authorization(self,definition,resources,attempt)
            if (previous or actor!=event.payload['actor_ref'] or value['submission_key']!=event.payload['command_id'] or
                    attempt['run_attempt']['project_revision']!=value['project_revision'] or
                    attempt['run_attempt']['binding_digest']!=value['attempt_binding_digest'] or
                    command.confirmation_digest!=self._execution_confirmation(project,command,actor,attempt,definition.environment) or
                    self._execution_request(project,command)!=event.payload['request_digest'] or
                    any(r.get('execution_authorization',{}).get('selection',{}).get('attempt_ref')==command.attempt_ref.model_dump()
                        for r in resources.values())):raise ValueError('authorization metadata')
        except (ValueError,KeyError,PyHError):raise_code('PERS-202',reason='execution_control_authorization_binding')
