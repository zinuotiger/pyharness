"""Provider observations on the original runtime log, never a second result store."""
import asyncio
import hashlib
from types import SimpleNamespace
from pyharness.core.execution_result import _approval_binding
from pyharness.core.invocation_binding import current_invocation
from pyharness.core.provider_result import ProviderOutcome
from pyharness.core.result_text import render_result_text
from pyharness.errors import raise_code
from pyharness.persistence import detect_truncation, session_data_paths


def confirmed_events(session):
    return _confirmed_events(session,allow_pending=False)


def confirmed_prefix_events(session):
    """Allow only the ordinary buffered suffix, never an unconfirmed strong fact."""
    return _confirmed_events(session,allow_pending=True)


def _confirmed_events(session,*,allow_pending):
    """Do not mistake EventBus-isolated persistence errors for a commit receipt."""
    store = getattr(session, '_persistence', None)
    if store is None or not getattr(store, 'path', None):
        raise_code('PERS-202', reason='execution_persistence_required')
    for path in session_data_paths(store.path):
        if not path.is_file() or detect_truncation(path) is not None:
            raise_code('PERS-202', reason='execution_log_incomplete')
    events = list(store.replay())
    if store._quarantine:
        raise_code('PERS-202', reason='execution_log_corrupt')
    actual=[e.model_dump() for e in events];memory=list(session.events_after(0))
    expected=[e.model_dump() for e in memory]
    from pyharness.events.vocab import SYNC_TYPES
    if (actual!=expected[:len(actual)] or len(actual)>len(expected) or
            (not allow_pending and len(actual)!=len(expected)) or
            any(e.type in SYNC_TYPES for e in memory[len(actual):])):
        raise_code('PERS-202', reason='execution_log_unconfirmed')
    return events


async def flush_confirmed(session):
    store = getattr(session, '_persistence', None)
    if store is None:raise_code('PERS-202', reason='execution_persistence_required')
    await store.flush(session.stats()['seq'])
    return confirmed_events(session)


def invocation_decision(events, binding, task_id):
    """One checked decision binding for live invocation and historical replay."""
    def denied():raise_code('GRD-403', reason='execution_invocation_unbound')
    call = next((e for e in events if e.seq == binding.tool_call_seq), None)
    if (call is None or call.type != 'tool.call' or call.actor != 'tool' or
            call.task_id not in (None, str(task_id)) or call.payload['name'] != binding.tool or
            call.payload['call_id'] != binding.call_id or
            _approval_binding(SimpleNamespace(name=binding.tool), call.payload['args']) != binding.inputs_digest):denied()
    decisions = [e for e in events if e.type == 'decision.issued' and e.seq > call.seq
                 and (e.trace or {}).get('call_id') == binding.call_id]
    if not decisions:denied()
    decision = decisions[-1]
    if (decision.actor != 'system' or decision.payload['tool'] != binding.tool or
            decision.payload['inputs_digest'] != binding.inputs_digest or
            decision.payload['verdict'] not in ('allow', 'approval')):denied()
    if decision.payload['verdict'] == 'approval':
        approval_ref = decision.payload.get('approval_ref')
        request = next((e for e in events if e.seq == approval_ref), None)
        if (request is None or request.type != 'approval.requested' or
                (request.trace or {}).get('call_id') != binding.call_id or
                request.payload['tool'] != binding.tool):denied()
        outcomes = [e for e in events if e.type in ('approval.granted', 'approval.denied', 'approval.timeout')
                    and e.payload['approval_id'] == approval_ref and request.seq < e.seq < decision.seq]
        if len(outcomes) != 1 or outcomes[0].type != 'approval.granted':denied()
    return decision


class EffectRecorder:
    def __init__(self, execution_id, authorization_digest, authorize):
        self.execution_id = execution_id
        self.authorization_digest = authorization_digest
        self.authorize = authorize

    async def _append(self, ctx, payload):
        event = await ctx.session.append('execution.effect', payload,
            actor='system', task_id=str(ctx.task_id), sync=True)
        events = confirmed_events(ctx.session)
        if not any(e == event for e in events):
            raise_code('PERS-202', reason='execution_effect_unconfirmed')
        return event

    def _binding(self, ctx, binding, events):
        def denied(): raise_code('GRD-403', reason='execution_invocation_unbound')
        if binding is None: denied()
        from pyharness.application.execution_delegation import own_lifecycle
        lifecycle=own_lifecycle(events,ctx.session.sid)
        bound = [e for e in lifecycle if
                 e.payload['execution_id'] == self.execution_id and
                 e.payload['phase'] == 'bound' and e.task_id == str(ctx.task_id) and
                 e.payload['authorization_digest'] == self.authorization_digest]
        if len(bound) != 1: denied()
        if lifecycle[-1].payload['phase'] != 'running': denied()
        decision = invocation_decision(events, binding, ctx.task_id)
        if any(e.type == 'execution.effect' and e.payload['tool_call_seq'] == binding.tool_call_seq for e in events):
            raise_code('GRD-402', reason='execution_effect_already_started')
        return decision

    async def invoke(self, ctx, original):
        return await self._invoke_observed(ctx,original,None)

    async def invoke_observed(self,ctx,original,observer):
        return await self._invoke_observed(ctx,original,observer)

    async def _invoke_observed(self,ctx,original,observer):
        binding = current_invocation.get()
        if binding is None: raise_code('GRD-403', reason='execution_invocation_unbound')
        # The journal owns this lock: distinct recorder instances cannot race the
        # same durable call identity. Assignment has no await on the owning loop.
        if not hasattr(ctx.session, '_execution_effect_lock'):
            ctx.session._execution_effect_lock = asyncio.Lock()
        async with ctx.session._execution_effect_lock:
            self.authorize()
            decision = self._binding(ctx, binding, confirmed_events(ctx.session))
            payload = dict(schema_version=1, execution_id=self.execution_id,
                phase='started', tool=binding.tool, call_id=binding.call_id,
                tool_call_seq=binding.tool_call_seq, inputs_digest=binding.inputs_digest,
                decision_seq=decision.seq)
            started = await self._append(ctx, payload)
            self.authorize()
        from pyharness.application.execution_delegation import current_delegation,DelegationInvocation
        token=current_delegation.set(DelegationInvocation(ctx,binding,decision.seq,started.seq,self.execution_id,self.authorization_digest))
        try:return await self._invoke_provider_observed(ctx,original,payload,started,observer)
        finally:current_delegation.reset(token)

    async def _invoke_provider(self,ctx,original,payload,started):
        return await self._invoke_provider_observed(ctx,original,payload,started,None)

    async def _invoke_provider_observed(self,ctx,original,payload,started,observer):
        entered=False;files={}
        try:
            if observer is not None:await observer.before()
            self.authorize();entered=True
            result = await original()
            if observer is not None:files=await observer.after(result)
        except BaseException as exc:
            # An exception cannot prove the absence of a partial external effect.
            outcome = dict(payload, phase='unknown', started_seq=started.seq,
                reason='waiting_cancelled' if isinstance(exc, asyncio.CancelledError) else 'provider_raised' if entered else 'observation_failed')
            try: await self._append(ctx, outcome)
            except BaseException as receipt_error:
                exc.add_note('effect outcome unavailable: ' + type(receipt_error).__name__)
            raise
        outcome = dict(payload, phase='returned', started_seq=started.seq,
            result_digest=hashlib.sha256(render_result_text(result).encode('utf-8')).hexdigest(),
            provider_ok=bool(result.get('ok')) if isinstance(result, ProviderOutcome) else None)
        outcome.update(files)
        await self._append(ctx, outcome)
        return result
