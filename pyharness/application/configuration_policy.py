"""Conservative differences requiring a separate configuration confirmation."""


def permission_increases(current, target):
    result = []
    if current['permission_policy'] == 'strict' and target['permission_policy'] != 'strict':
        result.append('permission_policy_widened')
    for name in ('allowed_tools', 'mcp_connections', 'skills', 'knowledge_sources'):
        if set(target[name]) - set(current[name]):
            result.append(name+'_added')
    if set(current['approval_rules']) - set(target['approval_rules']):
        result.append('approval_requirements_changed')
    for name in ('require_evidence', 'strict_knowledge'):
        if current[name] and not target[name]:
            result.append(name+'_weakened')
    if current['review_mode'] == 'manual' and target['review_mode'] != 'manual':
        result.append('review_mode_changed')
    if current['memory_scope'] != 'team' and target['memory_scope'] == 'team':
        result.append('team_memory_enabled')
    for name in ('executor', 'codex_tools', 'codex_model', 'codex_executable', 'model_connection', 'output_schema'):
        if current[name] != target[name]:
            result.append(name+'_changed')
    for name in ('max_rounds', 'timeout'):
        if target[name] > current[name]:
            result.append(name+'_increased')
    for name, value in target['budget'].items():
        if value > current['budget'][name]:
            result.append('budget_'+name+'_increased')
    before, after = current['sandbox_profile'], target['sandbox_profile']
    for name in ('mode', 'remote_connection', 'workspace_mode', 'network', 'image'):
        if before[name] != after[name]:
            result.append('sandbox_'+name+'_changed')
    for name in ('cpus', 'memory_mb', 'pids_limit', 'timeout', 'output_limit'):
        if after[name] > before[name]:
            result.append('sandbox_'+name+'_increased')
    return result
