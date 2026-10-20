"""Stop an already admitted child using current control authority only."""
from pydantic import TypeAdapter
from pyharness.application.execution_contracts import ExecutionSessionId
from pyharness.application.execution_child_reader import peek_child_binding
from pyharness.application.execution_child_graph import child_admissions,validate_child_events,event_digest
from pyharness.application.execution_effects import flush_confirmed
from pyharness.application.product_execution_sessions import read_execution_log
from pyharness.errors import raise_code


async def cancel_product_child(service,sid,sub_id):
    service._ensure_open();projects=service.projects;projects._authorize(write=True)
    sid=TypeAdapter(ExecutionSessionId).validate_python(sid,strict=True)
    sub_id=TypeAdapter(ExecutionSessionId).validate_python(sub_id,strict=True)
    directory=service._tenant_sessions_dir();binding=peek_child_binding(directory,sid,service.tenant_id)
    root_sid=binding['root_session_id'] if binding else sid
    row,target=await projects.store.execution_session_stop_target(root_sid,authorize=lambda:projects._authorize(write=True))
    queue=projects._owned_stop_queue(target)
    if queue is None:return False
    spine=service._engines[root_sid];proxy=getattr(spine.platform_runtime,'product_children',None)
    if proxy is None or proxy.spine is not spine or proxy.manager is not spine.subagent:
        raise_code('GRD-403',reason='product_child_stop_runtime_changed')
    root=await read_execution_log(directory,row,service.tenant_id)
    admissions=child_admissions(list(root.events_after(0)),row);admission=admissions.get(sub_id)
    if binding is not None:
        caller=admissions.get(sid)
        if caller is None or dict(caller.payload,phase='bound',admission_seq=caller.seq,
                admission_digest=event_digest(caller))!=binding:
            raise_code('GRD-403',reason='product_child_stop_caller_changed')
        if admission is not None and admission.payload['invoker_session_id']!=sid:
            raise_code('GRD-403',reason='product_child_stop_foreign_descendant')
    if admission is None:return False
    handle=spine.subagent._children.get(sub_id)
    if handle is None:return False
    runtime=proxy.runtimes.get(sub_id);log=proxy.logs.get(sub_id)
    if (runtime is None or log is None or handle.env.session is not log or runtime.session_id!=sub_id or
            getattr(runtime,'product_children',None) is not proxy or runtime.platform is not service.platform or
            runtime.product_execution.root is not proxy.root):
        raise_code('GRD-403',reason='product_child_stop_identity_changed')
    validate_child_events(await flush_confirmed(log),admission)
    if projects._owned_stop_queue(target) is not queue:raise_code('GRD-403',reason='product_child_stop_owner_changed')
    return bool(await spine.subagent.cancel(sub_id,by=service.owner_for(root_sid)))
