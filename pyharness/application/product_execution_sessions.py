"""Strict, read-only access to private execution journals through legacy readers."""
from types import SimpleNamespace
from pydantic import TypeAdapter
from pyharness.application.execution_contracts import ExecutionSessionId
from pyharness.core.lifecycle import admitted
from pyharness.core.session import open_session
from pyharness.errors import raise_code
from pyharness.persistence import detect_truncation, session_data_paths
from pyharness.persistence_reader import _iter_replay


def is_product_session(sid):
    return isinstance(sid, str) and sid.startswith('s-pa-')


def reject_product_mutation(sid):
    if is_product_session(sid):raise_code('GRD-403', reason='product_execution_entry_required')


class ReadOnlyExecutionLog:
    """Derived snapshot; no append, writer construction, repair or queue capabilities."""
    def __init__(self, log):
        self._log = log
        self.sid = log.sid
        self.tenant_id = log.tenant_id

    def events_after(self, *args, **kwargs):return self._log.events_after(*args, **kwargs)
    def events_between(self, *args, **kwargs):return self._log.events_between(*args, **kwargs)
    def derive_messages(self, *args, **kwargs):return self._log.derive_messages(*args, **kwargs)
    def stats(self):return self._log.stats()
    def get(self, seq):return self._log.get(seq)
    async def append(self, *args, **kwargs):reject_product_mutation(self.sid)


def read_execution_events(directory, row, tenant):
    value = row['execution_authorization']; sid = value['session_id']
    TypeAdapter(ExecutionSessionId).validate_python(sid, strict=True)
    main = directory / (sid + '.jsonl'); quarantine = set()
    try:
        paths = session_data_paths(main)
        for path in paths:
            if not path.is_file() or detect_truncation(path) is not None:
                raise_code('PERS-202', reason='product_execution_log_incomplete')
        events = list(_iter_replay(paths, quarantine, tolerate_unreadable=False))
    except OSError:raise_code('PERS-202', reason='product_execution_log_unavailable')
    if quarantine or len(events) < 2:raise_code('PERS-202', reason='product_execution_log_unbound')
    for seq, event in enumerate(events, 1):
        if event.seq != seq or event.session_id != sid or event.tenant_id != tenant:
            raise_code('PERS-202', reason='product_execution_log_identity')
    first, bound = events[:2]
    expected = dict(schema_version=1, execution_id=value['execution_id'], phase='bound',
        authorization_digest=row['digest'], authorization_seq=row['source_seq'], subject_ref=value['created_by'], reason=None)
    if (first.type != 'session.created' or bound.type != 'execution.lifecycle' or
            bound.actor != 'system' or bound.task_id != value['task_id'] or bound.payload != expected):
        raise_code('PERS-202', reason='product_execution_log_binding')
    from pyharness.application.execution_replay import validate_execution_graph
    validate_execution_graph(events, row)
    return events


async def read_execution_log(directory, row, tenant):
    events = read_execution_events(directory, row, tenant)
    sid = row['execution_authorization']['session_id']
    # Reuse the existing reducer over this exact validated snapshot. The source
    # exposes replay only; ReadOnlyExecutionLog never exposes its writer surface.
    log = await open_session(sid, SimpleNamespace(replay=lambda: iter(events)), tenant_id=tenant)
    return ReadOnlyExecutionLog(log)


class ProductExecutionSessions:
    @admitted
    async def product_session(self, sid):
        self._authorize(write=True)
        sid = TypeAdapter(ExecutionSessionId).validate_python(sid, strict=True)
        from pyharness.application.execution_child_reader import peek_child_binding,read_child_log
        child=peek_child_binding(self.service._tenant_sessions_dir(),sid,self.service.tenant_id)
        if child is not None:
            async def read_child(row):return await read_child_log(self.service._tenant_sessions_dir(),row,self.service.tenant_id,sid)
            return await self.store.read_product_child(child,authorize=lambda:self._authorize(write=True),read=read_child)
        async def read(row):
            return await read_execution_log(self.service._tenant_sessions_dir(), row, self.service.tenant_id)
        return await self.store.read_product_session(sid, authorize=lambda: self._authorize(write=True), read=read)
