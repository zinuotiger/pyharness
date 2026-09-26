"""Bounded model diagnostics. Never serialize exceptions, bodies or headers.

Call role and attempt are coroutine-local: auxiliary/concurrent work cannot
change the main agent's attribution. Unknown means absent, never inferred.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import asyncio
import re
from urllib.parse import urlsplit

import httpx

call_role = ContextVar('llm_call_role', default='other')
call_attempt = ContextVar('llm_call_attempt', default=1)
response_observation = ContextVar('llm_response_observation', default=None)
ROLES = {'main_agent', 'title_generation', 'probe', 'other'}
CATEGORIES = {'authentication', 'permission', 'rate_limit', 'provider_server',
              'timeout', 'connection', 'protocol', 'invalid_response', 'unknown'}


async def observed_wait(awaitable, timeout, *, observation=None):
    """Share only this request's safe observations with wait_for's child task.

wait_for replaces cancellation with TimeoutError; retain headers received before
that cancellation without putting mutable state on a shared adapter/transport.
"""
    if observation is None:
        observation = {}
    token = response_observation.set(observation)
    try:
        return await asyncio.wait_for(awaitable, timeout=timeout)
    except Exception as exc:
        exc.diagnostics = {**observation, **getattr(exc, 'diagnostics', {})}
        raise
    finally:
        response_observation.reset(token)


@contextmanager
def model_call(role):
    token = call_role.set(role if role in ROLES else 'other')
    try:
        yield
    finally:
        call_role.reset(token)


def atom(value, limit=128):
    """Reject free text, paths, credentials and oversized opaque metadata."""
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return 'unknown'
    text = str(value)
    if (len(text) > limit or not re.fullmatch(r'[A-Za-z0-9_.:+-]+', text)
            or re.search(r'(?i)sk-|bearer|authorization|secret|password|api.?key', text)):
        return 'unknown'
    return text or 'unknown'


def endpoint_origin(value):
    try:
        url = urlsplit(str(value))
        if url.scheme in {'https', 'http'} and url.hostname:
            host = atom(url.hostname, 253)
            return f'{url.scheme}://{host}' if host != 'unknown' else 'unknown'
    except ValueError:
        pass
    return 'unknown'


def response_metadata(response):
    """Only three response headers and a bounded JSON error code are inspected."""
    headers = response.headers
    code = 'unknown'
    # Streaming responses may not have been read; do not buffer arbitrary bodies.
    try:
        content = response.content
    except httpx.ResponseNotRead:
        content = None
    if content is not None and len(content) <= 16384:
        try:
            body = response.json()
            error = body.get('error') if isinstance(body, dict) else None
            if isinstance(error, dict):
                code = atom(error.get('code'))
        except (ValueError, httpx.ResponseNotRead):
            pass
    retry = headers.get('retry-after')
    # Retry-After accepts delta seconds or an HTTP date, bounded and grammar checked.
    if not isinstance(retry, str) or not re.fullmatch(r'(\d{1,9}|[A-Za-z]{3}, \d{2} [A-Za-z]{3} \d{4} \d{2}:\d{2}:\d{2} GMT)', retry):
        retry = 'unknown'
    return {'http_status': response.status_code, 'provider_code': code,
            'request_id': atom(headers.get('x-request-id') or headers.get('request-id')),
            'retry_after': retry}


def public_diagnostics(value=None, **defaults):
    """Revalidate even persisted/legacy metadata at public boundaries."""
    d = {**defaults, **(value if isinstance(value, dict) else {})}
    category = d.get('category') if d.get('category') in CATEGORIES else 'unknown'
    status = d.get('http_status')
    attempt = d.get('attempt', 'unknown')
    seq = d.get('request_seq', 'unknown')
    retry = d.get('retry_after')
    if not isinstance(retry, str) or not re.fullmatch(r'(\d{1,9}|[A-Za-z]{3}, \d{2} [A-Za-z]{3} \d{4} \d{2}:\d{2}:\d{2} GMT)', retry):
        retry = 'unknown'
    return dict(code=atom(d.get('code')), category=category,
        http_status=status if type(status) is int and 100 <= status <= 599 else 'unknown',
        provider_code=atom(d.get('provider_code')), request_id=atom(d.get('request_id')),
        retry_after=retry, endpoint=endpoint_origin(d.get('endpoint')),
        model=atom(d.get('model')), role=d.get('role') if d.get('role') in ROLES else 'other',
        request_seq=seq if type(seq) is int and seq > 0 else 'unknown',
        attempt=attempt if type(attempt) is int and attempt > 0 else 'unknown',
        retried=d.get('retried') if type(d.get('retried')) is bool else 'unknown',
        safe_to_retry=d.get('safe_to_retry') if type(d.get('safe_to_retry')) is bool else 'unknown',
        summary=f'Model request failed ({category})')


def diagnostics(exc, *, code, endpoint=None, model=None, request_seq=None):
    meta = dict(getattr(exc, 'diagnostics', {}) or {})
    response = getattr(exc, 'response', None)
    if isinstance(response, httpx.Response):
        meta.update(response_metadata(response))
    status = meta.get('http_status', getattr(exc, 'status_code', None))
    category = {401:'authentication', 403:'permission', 429:'rate_limit'}.get(status)
    if category is None and isinstance(status, int):
        category = 'provider_server' if status >= 500 else 'protocol' if status >= 400 else None
    if category is None:
        if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
            category = 'timeout'
        elif isinstance(exc, httpx.ProtocolError):
            category = 'protocol'
        elif isinstance(exc, httpx.TransportError):
            category = 'connection'
        elif type(exc).__name__ == 'ProviderProtocolError':
            category = 'invalid_response'
        else:
            category = 'unknown'
    return public_diagnostics(meta, code=code, http_status=status, category=category,
        endpoint=endpoint, model=model, role=call_role.get(), request_seq=request_seq,
        attempt=call_attempt.get(), retried=call_attempt.get() > 1,
        safe_to_retry=category in {'rate_limit', 'provider_server', 'timeout', 'connection'})
