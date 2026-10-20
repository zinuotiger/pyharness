"""Deterministic, closed skill wiring exercise using the original engine seam.

This is an explicitly named controlled rehearsal, not a paid model quality run
or a ProductTask. It cannot load plugins/MCP, choose a model or execute arbitrary
client instructions. A real forbidden call checks the original governance path.
"""
from __future__ import annotations
import copy
import hashlib
import json
import asyncio
import uuid
from pathlib import Path
from pyharness.bus import EventBus
from pyharness.config import Settings
from pyharness.core import llm
from pyharness.core.session import open_session
from pyharness.core.skill import SkillManager
from pyharness.core.task_queue import TaskQueue
from pyharness.engine import build_runner_components
from pyharness.engine_runner import make_runner
from pyharness.persistence import open_store


class _ControlledSkillPlanner:
    model = 'controlled-skill-wiring'

    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.requests = []

    async def _respond(self, messages, tools=None, *, ctx):
        self.requests.append(copy.deepcopy(messages))
        index = len(self.requests)
        calls = []
        if index == 1:
            name, args = 'skill.load', {'name': self.snapshot['name']}
        elif index == 2:
            # A valid, real built-in request must be stopped before its Provider.
            name, args = 'exec.shell_run', {'command': 'echo CONTROLLED-SHOULD-NOT-RUN'}
        else:
            name, args = None, None
        if name:
            calls = [llm.ToolCall(id='controlled-skill-'+str(index), index=0,
                                 name=name, raw_args=args, raw_json=json.dumps(args))]
        response = llm.LLMResponse(content='' if calls else 'Controlled wiring exercise complete.',
            tool_calls=calls or None, model=self.model,
            finish_reason='tool_calls' if calls else 'stop')
        event = await ctx.session.append('llm.response', {
            'model': self.model, 'finish_reason': response.finish_reason,
            'content': response.content, 'tool_calls': [
                {'id': c.id, 'name': c.name, 'arguments': c.raw_json} for c in calls]}, actor='llm')
        response.seq = event.seq
        return response

    async def chat(self, messages, tools=None, *, ctx):
        return await self._respond(messages, tools, ctx=ctx)

    async def chat_stream(self, messages, tools=None, *, ctx):
        return await self._respond(messages, tools, ctx=ctx)

    async def ping(self):
        return 0.0


async def _closed_extensions(spine):
    if spine.settings.plugins.mcp_servers or spine.settings.plugins.enabled:
        raise ValueError('controlled rehearsal requires no external extensions')
    # This is the original runner's explicit startup hook. All original scope,
    # guards, executor, approvals, queue and event persistence stay assembled.


async def _finish_cleanup(spine, store, runtime):
    async def owned():
        try:
            if spine is not None:
                await spine.close()
            else:
                store.close()
        finally:
            await runtime.aclose()
    task = asyncio.create_task(owned())
    cancelled = None
    while True:
        try:
            await asyncio.shield(task)
            break
        except asyncio.CancelledError as exc:
            if task.cancelled():
                raise
            cancelled = exc
    if cancelled is not None:
        raise cancelled


async def exercise(snapshot, directory: Path, tenant_id: str):
    directory.mkdir(parents=True, exist_ok=False)
    cfg = Settings()
    for name, value in {'root': '.', 'sessions_dir': 'sessions',
                        'workspaces_dir': 'workspaces', 'spill_dir': 'spill',
                        'db_path': 'index.db'}.items():
        setattr(cfg.storage, name, str(directory/value))
    cfg.skills.dir = str(directory/'empty-skills')
    cfg.plugins.dir = str(directory/'empty-plugins')
    cfg.plugins.enabled = []
    cfg.plugins.mcp_servers = []
    cfg.security.credentials.file = str(directory/'no-credentials.yaml')
    cfg.security.policy.preset = 'strict'
    cfg.security.policy.deny_tools_extra = ['exec.*', 'fs.*', 'web.*', 'net.*', 'mcp.*', 'plugin.*', 'job.*', 'subagent.*']
    cfg.llm.model = _ControlledSkillPlanner.model
    cfg.llm.fallback_models = []
    cfg.llm.degrade.enabled = False
    cfg.llm.retry.attempts = 1
    cfg.loop.max_turns = 4
    runtime = llm.LLMRuntime()
    runtime.registry.clear()
    planner = _ControlledSkillPlanner(snapshot)
    runtime.registry[planner.model] = planner
    # Original process ownership is keyed by SID within this interpreter.
    # Never let cleanup share a key with another service/lab/tenant session.
    sid = 's-skill-' + uuid.uuid4().hex
    store = open_store(sid, dir=directory/'sessions')
    spine = None
    try:
        session = await open_session(sid, store, tenant_id=tenant_id)
        spine = build_runner_components(cfg, log_=session, bus=EventBus(),
            sessions_dir=directory/'sessions', store=store, channel='desktop',
            llm_runtime=runtime, register_model=False)
        spine.skills = SkillManager.from_loaded_snapshot(snapshot)
        await session.append('session.created', {'title': 'Controlled skill wiring rehearsal',
            'model': planner.model}, actor='system', sync=True)
        await session.append('session.renamed', {'new_title': 'Controlled skill wiring rehearsal',
            'by': 'user'}, actor='user', sync=True)
        queue = TaskQueue(session, runner=make_runner(spine, preload_plugins=_closed_extensions))
        spine.task_queue = queue
        task_id = await queue.submit('Controlled rehearsal: load the fixed skill view, then test the closed execution boundary.')
        result = await queue.wait_for(task_id)
        events = list(session.events_after(0))
        used = [e for e in events if e.type == 'skill.used' and e.payload.get('name') == snapshot['name']]
        next_messages = planner.requests[1] if len(planner.requests) >= 2 else []
        body = snapshot['body']
        reached = any(m.get('role') == 'tool' and body in str(m.get('content', ''))
                      for m in next_messages)
        denied = [e for e in events if e.type == 'guard.rejected' and e.payload.get('tool') == 'exec.shell_run']
        forbidden_results = [e for e in events if e.type == 'tool.result' and e.payload.get('name') == 'exec.shell_run']
        loaded_results = [e for e in events if e.type == 'tool.result' and e.payload.get('name') == 'skill.load']
        projected = bool(loaded_results) and any(m.get('role') == 'tool' and
            loaded_results[0].payload['summary'] in str(m.get('content', '')) for m in next_messages)
        proof = {'status': 'completed' if result.ok and len(used) == 1 and projected and denied and not forbidden_results else 'failed',
                 'mode': 'deterministic_controlled_wiring', 'paid_model_calls': 0,
                 'quality_evaluated': False, 'loaded_sha256': snapshot['loaded_sha256'],
                 'source_sha256': snapshot['source_sha256'], 'skill_used_sequences': [e.seq for e in used],
                 'next_context_contains_loaded_view': reached,
                 'next_context_contains_original_tool_projection': projected,
                 'context_delivery': 'complete_loaded_view' if reached else 'partial_spill_or_summary',
                 'spill_reference': loaded_results[0].payload.get('spill_ref') if loaded_results else None,
                 'next_context_sha256': hashlib.sha256(json.dumps(next_messages, ensure_ascii=False,
                     sort_keys=True).encode('utf-8')).hexdigest(),
                 'forbidden_call_rejected': bool(denied),
                 'forbidden_provider_execution_authorized': False if denied else None,
                 'forbidden_provider_calls_measured': False,
                 'forbidden_execution_evidence': 'original_guard_rejection' if denied else 'unconfirmed',
                 'forbidden_tool_result_count': len(forbidden_results),
                 'model_rounds': len(planner.requests), 'session_id': sid,
                 'events': [{'seq': e.seq, 'type': e.type} for e in events],
                 'attempted_tool_calls': [e.payload.get('name') for e in events if e.type == 'tool.call']}
    finally:
        await _finish_cleanup(spine, store, runtime)
    proof['carrier_closed'] = True
    return proof
