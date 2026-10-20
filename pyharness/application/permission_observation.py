"""Policy-only prediction: original contracts/Guard, no live Scope or Provider."""
import os
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace
from pyharness.application.platform_models import AgentDefinition
from pyharness.application.platform_tool_definitions import configure_file_definitions
from pyharness.application.execution_file_runtime import configure_context_reads
from pyharness.application.approval_policy import apply_rules
from pyharness.core.scope import Scope,ScopePolicy,BudgetLimits
from pyharness.core.tools_registry import ToolRegistry
from pyharness.core.tools_guard import ToolCall,from_config
from pyharness.core.tool_fs import register
from pyharness.core.sandbox import safe_path
from pyharness.engine_policy import _apply_preset
from pyharness.errors import PyHError


class DefinitionRegistry(ToolRegistry):
    """Use real schema compilation, discard every provider before registration."""
    def register_tool(self,defn,*,provider=None):
        return super().register_tool(defn)


async def predict_permission(snapshot,cfg,materials,name,args):
    cfg=cfg.model_copy(deep=True);definition=AgentDefinition.model_validate(snapshot)
    registry=DefinitionRegistry();register(registry)
    policy=ScopePolicy()
    policy.danger_marks=[dict(pattern=d.name,level=d.danger) for d in registry.iter_definitions() if d.danger!='none']
    _apply_preset(cfg,policy,registry)
    # Engine uses a per-session root. This disposable namespace is never created,
    # registered, opened as a workspace or pointed at a SourceGrant host root.
    policy.workspace_root=os.path.join(tempfile.gettempdir(),'pyharness-policy-view-'+uuid.uuid4().hex)
    scope=Scope(policy,BudgetLimits.from_cfg(cfg),'permission-preview')
    spine=SimpleNamespace(tool_registry=registry,scope=scope)
    configure_file_definitions(spine,definition,None)
    configure_context_reads(spine);apply_rules(spine,definition.approval_rules)
    # Unknown tools still inherit explicit prohibitions and the Agent allowlist.
    policy.deny_tools.update(set([name])-set(definition.allowed_tools))
    result=dict(schema_version=1,tool_name=name,prediction='unknown',reason='unmodeled_capability',
        guard_ids=[],policy_refs=[],execution_authorized=False,side_effects_executed=False,
        live_recheck_required=True,scope='fixed_selection_policy_only',
        limitations=['No live file state, symlink, container availability, runtime budget, plugin or approval decision is certified.'])
    if not scope.can_use(name):
        return dict(result,prediction='reject',reason='scope_hidden',guard_ids=['scope-hidden'],policy_refs=['GRD-401'])
    normalized={'file.read':'fs.read_file','file.list':'fs.list_dir','file.write':'fs.write_file','file.patch':'fs.patch'}.get(name,name)
    if normalized.startswith(('fs.','exec.','proc.')) and definition.sandbox_profile.mode=='disabled':
        return dict(result,prediction='reject',reason='sandbox_disabled')
    if not registry.has(name):return result
    try:checked=registry.validate_args(name,args)
    except PyHError:
        return dict(result,prediction='reject',reason='schema_invalid',guard_ids=['g-schema'],policy_refs=['TLB-803'])
    if normalized in ('fs.read_file','fs.list_dir','fs.write_file','fs.patch'):
        path=checked.get('path','.')
        if normalized=='fs.list_dir' and path is None:path='.'  # original optional root default
        try:
            if not (normalized=='fs.list_dir' and path in ('','.')):safe_path(Path(policy.workspace_root),path)
        except PyHError:
            return dict(result,prediction='reject',reason='invalid_workspace_path',guard_ids=['platform-file-boundary'])
        prefix='_pyharness_context';roots={f['path'].split('/')[0].casefold() for f in materials['bundle']['files']};n=0
        while prefix.casefold() in roots:n+=1;prefix='_pyharness_context_'+str(n)
        if normalized in ('fs.write_file','fs.patch') and (path.casefold()==prefix.casefold() or path.casefold().startswith(prefix.casefold()+'/')):
            return dict(result,prediction='reject',reason='execution_reference_read_only',guard_ids=['platform-file-boundary'])
    paths={os.path.normcase(os.path.normpath(os.path.join(policy.workspace_root,f['path']))) for f in materials['bundle']['files']}
    chain=from_config(cfg,validator=registry.validate_args,approval_channel=True,
        path_exists=lambda p:os.path.normcase(os.path.normpath(p)) in paths)
    evaluated=await chain.evaluate_detailed(ToolCall(name,raw_args=args,args=checked,defn=registry.lookup(name)),scope)
    # A Guard verdict is a prediction only; never a capability or approval receipt.
    return dict(result,prediction=evaluated.verdict,reason='builtin_policy_prediction',
        guard_ids=list(evaluated.guard_ids),policy_refs=list(evaluated.policy_refs))
