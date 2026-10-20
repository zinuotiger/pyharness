"""Read-only wrappers over the actual reducers; immutable request-local counters."""
from contextlib import contextmanager
from contextvars import ContextVar
from typing import NamedTuple
import hashlib
import json


def messages_digest(messages):
    return hashlib.sha256(json.dumps(messages,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')).hexdigest()


def derive_observed_messages(session,max_tokens):
    """Call the original history API exactly once and observe the same log state."""
    from pyharness.core.session import SessionLog
    messages=session.derive_messages(max_tokens)
    if type(session) is not SessionLog:return messages,dict(available=False)
    total=len(session._fold_history())
    return messages,dict(available=True,source_seq=session.stats()['seq'],input_messages=total,
        kept_messages=len(messages),dropped_messages=total-len(messages),window_tokens=max_tokens)


def assemble_observed(assembler,history,ctx):
    """No second assembly and no reading the assembler's mutable last_report."""
    from pyharness.core.system_prompt import SystemPromptAssembler
    result=assembler.assemble(history,ctx=ctx)
    known=(type(assembler) is SystemPromptAssembler and type(result) is list and bool(result)
        and type(result[0]) is dict and result[0].get('role')=='system')
    kept=len(result)-1 if known else -1
    suffix=history[-kept:] if kept>0 else []
    if not known or kept>len(history) or result[1:]!=suffix:return result,dict(available=False)
    return result,dict(available=True,input_messages=len(history),kept_messages=kept,
        dropped_messages=len(history)-kept,window_tokens=assembler._window(ctx),system_messages=1)


class _RequestWindow(NamedTuple):
    session_id:str
    tenant_id:str|None
    role:str
    messages_digest:str
    history_window:tuple
    system_window:tuple


_REQUEST_WINDOW=ContextVar('public_request_window',default=None)
_WINDOW_KEYS=('source_seq','input_messages','kept_messages','dropped_messages','window_tokens','system_messages')


def _freeze_window(value):
    if type(value) is not dict or value.get('available') is not True:return (('available',False),)
    fields=[('available',True)]
    for key in _WINDOW_KEYS:
        if key not in value:continue
        item=value[key]
        if item is not None and (type(item) is not int or item<0):return (('available',False),)
        fields.append((key,item))
    return tuple(fields)


@contextmanager
def request_context_scope(ctx,messages,history_window,system_window,*,enabled=True):
    """Metadata is usable only by this exact session, tenant, role and payload."""
    if not enabled:
        yield;return
    from pyharness.core.llm_diagnostics import call_role
    value=_RequestWindow(ctx.session.sid,getattr(ctx.session,'tenant_id',None),call_role.get(),
        messages_digest(messages),_freeze_window(history_window),_freeze_window(system_window))
    token=_REQUEST_WINDOW.set(value)
    try:yield
    finally:_REQUEST_WINDOW.reset(token)


def request_context_metadata(ctx,messages):
    from pyharness.core.llm_diagnostics import call_role
    value=_REQUEST_WINDOW.get()
    if (value is None or value.session_id!=ctx.session.sid or value.tenant_id!=getattr(ctx.session,'tenant_id',None)
            or value.role!=call_role.get() or value.messages_digest!=messages_digest(messages)):
        return None
    return dict(history_window=dict(value.history_window),system_window=dict(value.system_window))
