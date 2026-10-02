"""Small, tighten-only Platform policy; no expressions or approval bypasses."""
TOOLS = frozenset({'fs.read_file','fs.list_dir','fs.write_file','file.read','file.list','file.write',
    'fs.patch','file.patch','exec.shell_run','exec.python_run','proc.start','proc.wait','proc.kill',
    'storage.kv','subagent.spawn','workspace.apply_patch','workspace.export_artifact'})
CATEGORIES = {
    'file_write': {'fs.write_file','file.write','fs.patch','file.patch'},
    'command': {'exec.shell_run','exec.python_run','proc.start'},
}


def validate_rules(rules):
    if len(rules)>50:
        raise ValueError('approval_rules_limit')
    for rule in rules:
        if rule in CATEGORIES:
            continue
        action, _, name = rule.partition(':')
        if action not in {'approve','deny'} or name not in TOOLS:
            raise ValueError('unsupported_approval_rule')
    return list(dict.fromkeys(rules))


def apply_rules(spine, rules):
    registry = spine.tool_registry
    for rule in validate_rules(rules):
        action, _, name = rule.partition(':')
        names = CATEGORIES[rule] if rule in CATEGORIES else {name}
        for tool in names:
            if action == 'deny':
                spine.scope.policy.deny_tools.add(tool)
                continue
            if not registry.has(tool):
                continue
            registry.require_approval(tool)
