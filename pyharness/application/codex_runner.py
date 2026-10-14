"""Codex TaskRunner inside the existing queue, policy, approval and artifact chain."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
import uuid

from pyharness.core.task_queue import Task
from pyharness.core.tools_registry import ToolDefinition
from pyharness.core.provider_result import ProviderOutcome
from pyharness.errors import PyHError
from pyharness.integrations.codex_transport import CodexTransport


@dataclass(frozen=True)
class CodexResult:
    reason: str
    error_code: str | None = None


class CodexRunner:
    def __init__(self, spine, runtime, *, transport_factory=None):
        self.spine, self.runtime = spine, runtime
        self.transport_factory = transport_factory or (lambda: CodexTransport(executable=runtime.definition.codex_executable))
        self.transport = None
        self.thread_id = self.turn_id = None
        self.task_id = None
        self.result = None
        self.dynamic_tools = {}
        # All privileged execution crosses ToolExecutor and its governance re-entry.
        for name in ('codex.run', 'codex.approve_command', 'codex.approve_file'):
            spine.tool_registry.register_tool(ToolDefinition(name=name,
                description='Authorize the external Codex operation in the private workspace',
                owner='builtin', danger='high', approval='always', timeout_s=600,
                schema={'type':'object','properties':{'operation':{'type':'string','maxLength':32000}},
                        'required':['operation'],'additionalProperties':False}),
                provider=self._invoke if name == 'codex.run' else self._approved)
            if name in runtime.definition.allowed_tools:
                spine.scope.policy.deny_tools.discard(name)

    async def _approved(self, args, ctx):
        return 'Authorized once'

    async def _fact(self, action, record):
        await self.spine.session.append('executor.event', {'executor':'codex', 'action':action,
            'record':record}, actor='system', task_id=self.task_id, sync=True)

    async def _authorize(self, name, operation, ctx, ident=None):
        call = SimpleNamespace(name=name, raw_args={'operation':operation[:32000]},
                               call_id=ident or uuid.uuid4().hex)
        return await self.spine.tools.execute(call, ctx)

    async def run_for_task(self, task: Task) -> CodexResult:
        from pyharness.engine import agent_context, _owned_task_message
        event = _owned_task_message(self.spine.session, task)
        if event is None:
            raise PyHError('codex_input_missing')
        ctx = await agent_context(self.spine, event)
        previous = getattr(ctx, 'task_id', None)
        ctx.task_id, self.task_id = task.id, task.id
        self.result = None
        try:
            async def invoke():
                authorized = await self._authorize('codex.run', task.intent, ctx)
                if self.result is not None:
                    return self.result
                if not authorized.ok:
                    return CodexResult('failed', 'codex_governance_denied')
                return self.result or CodexResult('failed', 'codex_missing_terminal')
            return await self.runtime.run_task(event, ctx, invoke)
        finally:
            ctx.task_id = previous
            self.task_id = None

    async def _invoke(self, args, ctx):
        ident = await self.runtime.sandbox(ctx)
        workspace = self.runtime.platform.sandboxes.workspace(ident)
        # Resume the last durable binding on the next explicit task only. A lost turn
        # is never restarted automatically, because it may already have side effects.
        previous = [e.payload['record'] for e in ctx.session.events_after(0)
                    if e.type == 'executor.event' and e.payload['action'] == 'thread_bound']
        thread = previous[-1]['thread_id'] if previous else None
        self.thread_id = self.turn_id = None
        transport = None
        try:
            async with self.transport_factory() as transport:
                self.transport = transport
                delegated = self.runtime.definition.codex_tools == 'harness'
                params = {'cwd':str(workspace), 'approvalPolicy':'untrusted',
                          'sandbox':'read-only' if delegated else 'workspace-write', 'config':{'mcp_servers':{}, 'web_search':'disabled'},
                          'developerInstructions':self.runtime.definition.system_prompt}
                if delegated:
                    specs = []
                    for name in self.runtime.definition.allowed_tools:
                        if name.startswith(('codex.','mcp.','plugin.')) or not self.spine.tool_registry.has(name):
                            continue
                        key = 'harness_'+name.replace('.','_')
                        if key in self.dynamic_tools and self.dynamic_tools[key]!=name:
                            raise PyHError('codex_tool_name_collision')
                        self.dynamic_tools[key] = name
                        definition = self.spine.tool_registry.lookup(name)
                        specs.append({'type':'function','name':key,'description':definition.description,
                                      'inputSchema':definition.schema})
                    params['dynamicTools'] = specs
                    params['developerInstructions'] += '\nUse only harness_ client tools for files and commands. They enforce the published permissions and human approvals. Native shell/file tools and MCP are unavailable; do not attempt them. Paths are relative to the private task workspace.'
                if self.runtime.definition.codex_model:
                    params['model'] = self.runtime.definition.codex_model
                if thread:
                    params['threadId'] = thread
                    params.pop('dynamicTools',None)  # Persisted tool contracts restore on resume.
                binding = await transport.request('thread/resume' if thread else 'thread/start', params)
                self.thread_id = binding['thread']['id']
                old_usage = [e.payload['record'] for e in ctx.session.events_after(0)
                    if e.type=='executor.event' and e.payload['action']=='usage'
                    and e.payload['record'].get('thread_id')==self.thread_id]
                self.usage_baseline = old_usage[-1] if old_usage else {}
                await self._fact('thread_bound', {'thread_id':self.thread_id, 'resumed':bool(thread),
                    'client_version':getattr(transport,'client_version',None),
                    'tools_mode':self.runtime.definition.codex_tools,
                    'disabled_mcp_count':len(getattr(transport,'disabled_mcp_servers',()))})
                turn = await transport.request('turn/start', {'threadId':self.thread_id,
                    'cwd':str(workspace), 'approvalPolicy':'untrusted',
                    'sandboxPolicy':{'type':'readOnly','networkAccess':False} if delegated else
                                    {'type':'workspaceWrite','writableRoots':[str(workspace)],'networkAccess':False},
                    'input':[{'type':'text','text':args['operation']}]})
                self.turn_id = turn['turn']['id']
                await self._fact('turn_started', {'thread_id':self.thread_id,'turn_id':self.turn_id})
                try:
                    self.result = await self._stream(ctx)
                finally:
                    if self.result is None:
                        try:
                            await asyncio.wait_for(self.cancel_current(), 2)
                        except Exception:
                            pass  # Transport close below terminates the owned process.
        except asyncio.CancelledError:
            self.result = CodexResult('cancelled', 'cancelled')
            await self._fact('interrupted', {'turn_id':self.turn_id})
            raise
        except Exception as exc:
            self.result = CodexResult('failed', 'codex_disconnected')
            await self._fact('disconnected', {'turn_id':self.turn_id,'error_type':type(exc).__name__,
                'message':getattr(exc,'public_message',None)})
            raise PyHError('codex_disconnected') from exc
        finally:
            self.transport = None
            if transport is not None:
                process = getattr(transport,'process',None)
                self.last_process_closed = process is None or process.returncode is not None
        return ProviderOutcome({'ok':self.result.reason == 'complete', 'status':self.result.reason})

    async def _stream(self, ctx):
        completed, started, answers, finals = set(), set(), [], []
        self.server_responses = {}
        tool_count = output_chars = event_count = 0
        previous_input = getattr(self,'usage_baseline',{}).get('cumulative_input',0)
        previous_output = getattr(self,'usage_baseline',{}).get('cumulative_output',0)
        task_input = task_output = 0
        while True:
            message = await self.transport.next_event()
            event_count += 1
            if event_count > 10000:
                await self.cancel_current()
                return CodexResult('budget','codex_event_limit_exceeded')
            method, p = message['method'], message.get('params', {})
            if p.get('threadId') not in (None, self.thread_id) or p.get('turnId') not in (None, self.turn_id):
                continue
            if 'id' in message:
                await self._server_request(message, ctx)
                continue
            if method == 'turn/completed' and p.get('turn', {}).get('id') == self.turn_id:
                status = p['turn']['status']
                await self._fact('turn_completed', {'turn_id':self.turn_id, 'status':status})
                if status == 'completed':
                    if finals or answers:
                        await ctx.session.append('agent.message', {'content':'\n\n'.join(finals or answers)},
                            actor='agent', task_id=self.task_id, sync=True)
                    return CodexResult('complete')
                return CodexResult('cancelled' if status == 'interrupted' else 'failed',
                                   'cancelled' if status == 'interrupted' else 'codex_turn_failed')
            if method in ('item/started', 'item/completed'):
                item = p.get('item', {})
                kind, ident = item.get('type'), item.get('id')
                if kind not in {'agentMessage','commandExecution','fileChange','plan','mcpToolCall','webSearch','dynamicToolCall'}:
                    continue
                if kind in {'mcpToolCall','webSearch'}:
                    await self._fact('capability_rejected',{'type':kind,'server':item.get('server'),
                        'tool':item.get('tool'),'plugin_id':item.get('pluginId')})
                    await self.cancel_current()
                    return CodexResult('failed','codex_external_capability_denied')
                if method == 'item/started':
                    if ident in started:
                        continue
                    started.add(ident)
                    if kind in {'commandExecution','fileChange','mcpToolCall','webSearch'}:
                        tool_count += 1
                        if tool_count > self.runtime.definition.budget.tool_calls:
                            await self.cancel_current()
                            return CodexResult('budget', 'codex_tool_budget_exceeded')
                else:
                    if ident in completed:
                        continue
                    completed.add(ident)
                    if kind == 'agentMessage':
                        answers.append(str(item.get('text',''))[:32000])
                        if item.get('phase')=='final':
                            finals.append(str(item.get('text',''))[:32000])
                    if kind == 'commandExecution':
                        self.runtime.command_results.setdefault(str(self.task_id), []).append({
                            'tool':'codex.command','exit_code':item.get('exitCode', -1),
                            'output':str(item.get('aggregatedOutput',''))[:4000]})
                # Explicit whitelist; never retain reasoning items or arbitrary nested data.
                public = {k:item[k] for k in ('id','type','status','command','exitCode','durationMs','text','changes') if k in item}
                from pyharness.application.platform_projection import summary
                if kind=='commandExecution' and method=='item/completed':
                    public['output_summary'] = summary(item.get('aggregatedOutput',''),4000)
                await self._fact('item_started' if method == 'item/started' else 'item_completed',
                                 {'item_id':ident,'type':kind,'detail':summary(public, 12000)})
            elif method == 'item/agentMessage/delta':
                delta = str(p.get('delta',''))[:4000]
                output_chars += len(delta)
                if output_chars > min(100000, self.runtime.definition.budget.output_tokens*8):
                    await self.cancel_current()
                    return CodexResult('budget','codex_output_limit_exceeded')
                if delta:
                    await self._fact('message_delta', {'item_id':p.get('itemId'), 'delta':delta})
            elif method == 'error':
                from pyharness.application.platform_projection import summary
                error = p.get('error') or {}
                await self._fact('error', {'message':summary(error.get('message','Codex reported an execution error'),2000)})
            elif method == 'thread/tokenUsage/updated':
                usage = (p.get('tokenUsage') or {}).get('total') or {}
                input_tokens, output_tokens = usage.get('inputTokens',0), usage.get('outputTokens',0)
                if type(input_tokens) is int and type(output_tokens) is int and input_tokens >= 0 and output_tokens >= 0:
                    delta_input,delta_output = max(0,input_tokens-previous_input),max(0,output_tokens-previous_output)
                    previous_input,previous_output = input_tokens,output_tokens
                    task_input += delta_input
                    task_output += delta_output
                    await self._fact('usage',{'thread_id':self.thread_id,'input_tokens':delta_input,
                        'output_tokens':delta_output,'cumulative_input':input_tokens,'cumulative_output':output_tokens,'cost':'unknown'})
                    if task_input > self.runtime.definition.budget.input_tokens or task_output > self.runtime.definition.budget.output_tokens:
                        await self.cancel_current()
                        return CodexResult('budget','codex_token_budget_exceeded')

    async def _server_request(self, message, ctx):
        method, p = message['method'], message.get('params', {})
        import hashlib
        import json
        fingerprint = hashlib.sha256(json.dumps({'method':method,'params':p},sort_keys=True).encode()).hexdigest()
        cached = getattr(self,'server_responses',{}).get(message['id'])
        if cached:
            if cached[0] != fingerprint:
                raise PyHError('codex_conflicting_server_request')
            await self.transport.send(cached[1])
            return
        # Do not approve privilege escalation, session-wide amendments, dynamic tools,
        # user-input prompts or unknown future protocol methods.
        decision = 'decline'
        if method == 'item/tool/call':
            name = self.dynamic_tools.get(p.get('tool'))
            if self.runtime.definition.codex_tools!='harness' or name not in self.runtime.definition.allowed_tools or p.get('namespace'):
                result = {'success':False,'contentItems':[{'type':'inputText','text':'Tool not authorized'}]}
            else:
                call = SimpleNamespace(name=name,raw_args=p.get('arguments'),call_id='codex-'+str(p.get('callId')))
                executed = await self.spine.tools.execute(call,ctx)
                result = {'success':executed.ok,'contentItems':[{'type':'inputText','text':executed.summary}]}
            response = {'id':message['id'],'result':result}
            self.server_responses[message['id']] = (fingerprint,response)
            await self.transport.send(response)
            return
        if method == 'item/commandExecution/requestApproval' and self.runtime.definition.codex_tools=='native' and not p.get('networkApprovalContext') and not p.get('proposedNetworkPolicyAmendments'):
            # A proposal is advisory. Accept this exact invocation only; never
            # return acceptForSession or an execpolicy amendment.
            operation = str(p.get('command') or p.get('reason') or '')
            operation += '\nWorking directory: '+str(p.get('cwd') or '')
            result = await self._authorize('codex.approve_command', operation, ctx)
            decision = 'accept' if result.ok else 'decline'
        elif method == 'item/fileChange/requestApproval' and self.runtime.definition.codex_tools=='native' and not p.get('grantRoot'):
            result = await self._authorize('codex.approve_file', str(p.get('reason') or 'Codex file change'), ctx)
            decision = 'accept' if result.ok else 'decline'
        if method == 'item/permissions/requestApproval':
            result = {'permissions':{},'scope':'turn'}
        elif method in {'item/commandExecution/requestApproval', 'item/fileChange/requestApproval'}:
            result = {'decision':decision}
        else:
            await self.transport.send({'id':message['id'],'error':{'code':-32601,'message':'Unsupported request'}})
            return
        response = {'id':message['id'],'result':result}
        if not hasattr(self,'server_responses'):
            self.server_responses = {}
        self.server_responses[message['id']] = (fingerprint,response)
        await self.transport.send(response)

    async def cancel_current(self, task_id: str | None = None):
        if task_id and task_id != self.task_id:
            return
        if self.transport and self.thread_id and self.turn_id:
            await self.transport.request('turn/interrupt', {'threadId':self.thread_id,'turnId':self.turn_id})
