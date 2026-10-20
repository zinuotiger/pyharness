"""Bounded metadata discovery followed by authorized strict, read-only child replay."""
import os,stat
from types import SimpleNamespace
from pyharness.application.execution_child_graph import child_admissions,invocation_proof,validate_child_events
from pyharness.core.sandbox import safe_path
from pyharness.core.session import open_session
from pyharness.events import Envelope
from pyharness.events.vocab import validate_payload
from pyharness.persistence import detect_truncation,session_data_paths
from pyharness.persistence_reader import _iter_replay
from pyharness.errors import raise_code


def peek_child_binding(directory,sid,tenant):
    events=[]
    try:
        for path in session_data_paths(safe_path(directory,sid+'.jsonl')):
            path=safe_path(directory,path.name)
            fd=os.open(path,os.O_RDONLY|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_NOFOLLOW',0))
            with os.fdopen(fd,'rb') as stream:
                info=os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise ValueError('not regular')
                for _ in range(8):
                    line=stream.readline(65537)
                    if not line:break
                    if len(line)>65536 or not line.endswith(b'\n'):raise ValueError('header limit')
                    if not line.strip():continue
                    event=Envelope.model_validate_json(line)
                    canonical=validate_payload(event.type,event.payload)
                    event=event.model_copy(update={'payload':canonical})
                    if event.seq!=len(events)+1 or event.session_id!=sid or event.tenant_id!=tenant:raise ValueError('header identity')
                    events.append(event)
                    if len(events)==2:
                        if events[0].type!='session.created':raise ValueError('header start')
                        if event.type=='execution.lifecycle':return None
                        if (event.type!='execution.child' or event.payload['phase']!='bound' or event.actor!='system' or
                                event.task_id!='subagent:'+sid or event.payload['child_session_id']!=sid):raise ValueError('child header')
                        return event.payload
        raise ValueError('missing child binding')
    except (OSError,ValueError):raise_code('PERS-202',reason='product_child_header_unavailable')


def strict_child_events(directory,sid,tenant):
    paths=session_data_paths(safe_path(directory,sid+'.jsonl'));quarantine=set()
    try:
        for path in paths:
            safe_path(directory,path.name)
            if not path.is_file() or detect_truncation(path) is not None:raise ValueError('child incomplete')
        events=list(_iter_replay(paths,quarantine,tolerate_unreadable=False))
        if quarantine:raise ValueError('child corrupt')
        for seq,event in enumerate(events,1):
            if event.seq!=seq or event.session_id!=sid or event.tenant_id!=tenant:raise ValueError('child event identity')
        return events
    except (OSError,ValueError):raise_code('PERS-202',reason='product_child_log_unavailable')


async def read_child_log(directory,row,tenant,sid):
    from pyharness.application.product_execution_sessions import ReadOnlyExecutionLog,read_execution_log
    root=await read_execution_log(directory,row,tenant);root_events=list(root.events_after(0))
    admissions=child_admissions(root_events,row)
    async def read(ident,seen):
        if ident==root.sid:return root
        if ident in seen or len(seen)>=3:raise_code('PERS-202',reason='product_child_read_cycle')
        admission=admissions.get(ident)
        if admission is None:raise_code('PERS-202',reason='product_child_admission_missing')
        events=strict_child_events(directory,ident,tenant);validate_child_events(events,admission)
        parent=await read(admission.payload['invoker_session_id'],seen|{ident})
        try:invocation_proof(admission.payload,list(parent.events_after(0)),admission.payload['depth']-1)
        except (ValueError,KeyError):raise_code('PERS-202',reason='product_child_caller_changed')
        log=await open_session(ident,SimpleNamespace(replay=lambda:iter(events)),tenant_id=tenant)
        return ReadOnlyExecutionLog(log)
    return await read(sid,set())
