"""Product-scoped file ranges and bounded working snapshots; old tool schemas stay intact."""
import copy
from pyharness.core.sandbox import safe_path,read_regular,fail
from pyharness.core.tools_registry import ToolDefinition


def configure_context_reads(spine):
    registry=spine.tool_registry
    async def platform_required(args,ctx):
        # The published runtime owns these reads. A missing runtime cannot fall
        # back to an ordinary session workspace or acquire a registered Provider.
        fail('platform_runtime_required')
    for name in ('fs.read_file','file.read'):
        if not registry.has(name):continue
        old=registry.lookup(name)
        data=old.model_dump(by_alias=True);schema=copy.deepcopy(data.pop('schema_',data.get('schema',{})))
        schema['properties'].update(char_start=dict(type='integer',minimum=0,maximum=65536),
            char_limit=dict(type='integer',minimum=1,maximum=1500))
        data['schema']=schema
        data['description']='Read private workspace UTF8 text. Use char_start and char_limit=1200 for complete bounded Unicode ranges; next range starts after the characters returned.'
        registry.unregister(name);registry.register_tool(ToolDefinition.model_validate(data),provider=platform_required)


def bounded_read(value,args):
    if args.get('char_start') is None and args.get('char_limit') is None:return value
    start=args.get('char_start');limit=args.get('char_limit')
    if start is None:start=0
    if limit is None:limit=1200
    if not isinstance(value,str) or type(start) is not int or type(limit) is not int or not 0<=start<=65536 or not 1<=limit<=1500:
        fail('invalid_context_range')
    return value[start:start+limit]


def checked_context_read(boundary,path,value):
    prefix=boundary.workspace_plan['context_directory']
    if path.casefold().startswith(prefix.casefold()+'/'):
        expected=next((raw for name,raw in boundary.workspace_plan['contents'].items() if name.casefold()==path.casefold()),None)
        if expected is None or not isinstance(value,str) or value.encode('utf-8')!=expected:
            fail('execution_reference_changed')
    return value


def visible_output(boundary,name):
    plan=boundary.workspace_plan
    if name==plan['context_directory'] or name.startswith(plan['context_directory']+'/'):return False
    originals={i['path'] for i in boundary.materials['bundle']['files']}
    return name in originals or not any(p.startswith('.') or p=='__pycache__' for p in name.split('/'))


def text_snapshot(boundary,root):
    result={};total=0;count=0
    for item in root.rglob('*'):
        name=item.relative_to(root).as_posix()
        if not visible_output(boundary,name):continue
        path=safe_path(root,name)
        if not path.is_file():continue
        count+=1
        if count>250:fail('artifact_limit')
        if path.stat().st_size>1048576:continue
        raw=read_regular(path,1048576);total+=len(raw)
        if total>20*1048576:fail('artifact_limit')
        try:result[name]=raw.decode('utf-8')
        except UnicodeError:continue
    return result
