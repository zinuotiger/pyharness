"""Shared platform file contracts; metadata collectors may discard Provider references."""
from pyharness.core.tools_registry import ToolDefinition


def configure_file_definitions(spine, agent_definition, boundary_only):
    aliases={'file.read':('fs.read_file','low'),'file.write':('fs.write_file','high'),'file.list':('fs.list_dir','low')}
    for alias,(original,danger) in aliases.items():
        old=spine.tool_registry.lookup(original)
        data=old.model_dump(by_alias=True)
        data['schema']=data.pop('schema_',data.get('schema',{}))
        data.update(name=alias,danger=danger,approval='always' if danger=='high' else 'policy')
        # Read aliases preserve the original approval enum.
        if danger=='low': data['approval']=old.approval
        spine.tool_registry.register_tool(ToolDefinition.model_validate(data),provider=boundary_only)
    for name in ('file.patch','fs.patch'):
        spine.tool_registry.register_tool(ToolDefinition(name=name,description='Replace one exact text occurrence in the private workspace',owner='builtin',danger='high',approval='always',
            schema={'type':'object','properties':{k:{'type':'string'} for k in ('path','old','new')},'required':['path','old','new'],'additionalProperties':False}),provider=boundary_only)
    # Narrow the existing Scope; never broaden an existing denial.
    all_names = spine.tool_registry.names()
    for name in ('fs.read_file','fs.list_dir','fs.write_file','fs.patch','file.read','file.list','file.write','file.patch'):
        if spine.tool_registry.has(name):
            definition=spine.tool_registry.lookup(name)
            definition.schema_['properties']['path']['description']='Relative workspace path only, using /. Example: calculator.py (or . for directory listing). No absolute paths, .., drive letters, UNC or URI.'
    spine.scope.policy.deny_tools.update(set(all_names) - set(agent_definition.allowed_tools))
    for name in ('fs.write_file',):
        if spine.tool_registry.has(name):
            old = spine.tool_registry.lookup(name)
            from pyharness.core.tool_fs import write_file
            data = old.model_dump(by_alias=True)
            data['schema'] = data.pop('schema_', data.get('schema', {}))
            data.update(danger='high', approval='always')
            spine.tool_registry.unregister(name)
            spine.tool_registry.register_tool(ToolDefinition.model_validate(data), provider=write_file)
