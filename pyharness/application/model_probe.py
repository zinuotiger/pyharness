"""Explicit bounded generation probe through the existing LLM call chain."""
from __future__ import annotations

import asyncio
import time
import uuid
from types import SimpleNamespace

from pyharness.application.platform_projection import project
from pyharness.core.llm import LLMClient, LLMRuntime
from pyharness.core.llm_diagnostics import model_call, public_diagnostics
from pyharness.core.session import SessionLog
from pyharness.errors import PyHError

PROMPT = '这是一个连接测试。请只回复：PYHARNESS_MODEL_OK'


async def probe(service, profile):
    cfg = service.effective_settings()
    cfg.llm.model = profile['model']
    cfg.llm.base_url = profile['base_url']
    cfg.llm.api_key = f"tenant:{service.tenant_id}:{profile['id']}"
    cfg.llm.temperature = 0
    cfg.llm.max_tokens = 64
    cfg.llm.retry.attempts = 0
    cfg.llm.fallback_models = []
    cfg.llm.degrade.enabled = False
    cfg.llm.usage.unit_price = {}
    cfg.llm.timeout.connect_s = 10
    cfg.llm.timeout.first_token_s = 30
    cfg.llm.timeout.total_s = 60
    runtime = LLMRuntime()
    runtime.registry.clear()
    session = SessionLog(sid='s-probe-'+uuid.uuid4().hex)
    await session.append('session.created', {'title':'Model connection probe','model':profile['model']}, actor='system')
    ctx = SimpleNamespace(session=session, config=cfg, scope=None, task_id=None)
    result = {'status':'unavailable', 'probe':'bounded generation', 'capabilities':'not_probed',
              'profile_id':profile['id'], 'model':profile['model'], 'retried':False,
              'input_tokens':None, 'output_tokens':None}
    started = time.monotonic()
    try:
        from pyharness.engine import register_default_llm
        register_default_llm(cfg, registry=runtime.registry)
        client = LLMClient(cfg, registry=runtime.registry, chain=None)
        with model_call('probe'):
            text = await asyncio.wait_for(client.mini(PROMPT, ctx=ctx), 60)
        events = list(session.events_after(0))
        usage = next((e.payload for e in events if e.type == 'llm.usage'), None)
        if usage is not None:
            result['input_tokens'] = usage['in_tokens']
            result['output_tokens'] = usage['out_tokens']
        unexpected_tools = any(e.type == 'llm.response' and e.payload.get('tool_calls') for e in events)
        if unexpected_tools or 'PYHARNESS_MODEL_OK' not in text:
            result['code'] = 'model_probe_contract_failed'
        elif result['output_tokens'] is not None and result['output_tokens'] > 64:
            result['code'] = 'model_probe_budget_exceeded'
        else:
            result['status'] = 'available'
    except asyncio.TimeoutError:
        result.update(code='model_connection_timeout', message='连接测试已超时；未自动重试。')
    except PyHError as exc:
        result.update(code=exc.code, diagnostics=public_diagnostics(
            (exc.ctx or {}).get('diagnostics', {}), code=exc.code, role='probe'))
    except Exception:
        # Exception text, headers, endpoint paths and model output never leave this boundary.
        result['code'] = 'model_connection_failed'
    finally:
        await runtime.aclose()
    result['latency_ms'] = round((time.monotonic()-started)*1000, 2)
    # Ephemeral raw response journal is discarded. Persist only whitelisted public spans.
    result['trace'] = project(session.sid, session.events_after(0))['trace']
    return result
