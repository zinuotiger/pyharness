"""Bounded public request projection; never a model history or execution token.

Unknown objects are withheld, not stringified. This fixed, conservative policy
is independent of optional log redaction. It is finite-pattern filtering, not a
claim to detect every possible secret or to preserve an entire raw request.
"""
import json
import math
import re
from itertools import islice

MAX_PUBLIC_BYTES=40960  # Leave room for strict typed normalization in the fact.
MAX_ITEMS=64
MAX_TEXT=2048
MAX_SCAN=65536
_SENSITIVE=re.compile(r'(?i)secret|password|passwd|passphrase|credential|authorization|api.?key|token|cookie|reasoning|analysis|private|think')
_PRIVATE_TEXT=re.compile(
    r'(?i)sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_-]{20,}|github_pat_[A-Za-z0-9_-]{20,}'
    r'|-----BEGIN [A-Z ]*PRIVATE KEY-----|<\s*(?:think|analysis|reasoning)\b'
    r'|(?:api[_ -]?key|token|cookie|password|passwd|passphrase|authorization|credential|secret|reasoning[_ -]?content)\s*["\x27]?\s*[:=]')
_OPAQUE=re.compile(r'[A-Za-z0-9_-]{32,}')
_ROLES={'system','developer','user','assistant','tool'}


def _bytes(value):return len(json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8'))


class _PublicCapture:
    def __init__(self,known_secrets):
        self.withheld=False;self.truncated=False;self.images=0;self.images_known=True;self.nodes_left=2048;self.scan_left=2*1024*1024
        self.secrets=tuple(v for v in known_secrets[:32] if type(v) is str and v) if type(known_secrets) in (tuple,list) else ()

    def hide(self,reason):
        self.withheld=True
        return '[withheld:'+reason+']'

    def text(self,value):
        if type(value) is not str:return self.hide('unsupported-content')
        self.scan_left-=len(value)
        if len(value)>MAX_SCAN or self.scan_left<0:
            self.truncated=True;return self.hide('analysis-limit')
        # Inspect the complete bounded string before any display clipping.
        if any(secret in value for secret in self.secrets) or _PRIVATE_TEXT.search(value):
            return self.hide('private-content')
        if any((ord(ch)<32 and ch not in '\n\r\t') or 0xD800<=ord(ch)<=0xDFFF for ch in value):
            return self.hide('unsupported-character')
        value=_OPAQUE.sub(lambda _:self.hide('opaque-token'),value)
        if len(value)>MAX_TEXT:self.truncated=True;return value[:MAX_TEXT]
        return value

    def atom(self,value,limit=128):
        safe=self.text(value)
        if not re.fullmatch(r'[A-Za-z0-9_.:+/-]{1,'+str(limit)+'}',safe):
            return self.hide('metadata')
        return safe

    def tree(self,value,depth=0):
        self.nodes_left-=1
        if self.nodes_left<0:
            self.truncated=True;return self.hide('node-limit')
        if depth>6:return self.hide('depth')
        if value is None or type(value) is bool or type(value) is int:return value
        if type(value) is float:return value if math.isfinite(value) else self.hide('nonfinite-number')
        if type(value) is str:return self.text(value)
        if type(value) is list:
            if len(value)>32:self.truncated=True
            return [self.tree(v,depth+1) for v in value[:32]]
        if type(value) is dict:
            if len(value)>32:self.truncated=True
            result={}
            for key,item in islice(value.items(),32):
                if type(key) is not str:
                    self.withheld=True;continue
                name=self.text(key)
                if len(name)>128:
                    self.truncated=True;name=name[:128]
                result[name]=self.hide('private-field') if _SENSITIVE.search(key) else self.tree(item,depth+1)
            return result
        return self.hide('unsupported-value')

    def content(self,value):
        if type(value) is not list:
            if value is not None and type(value) is not str:self.images_known=False
            return self.text(value)
        parts=[]
        if len(value)>32:self.truncated=True
        for part in value[:32]:
            if type(part) is dict and part.get('type')=='text':parts.append(self.text(part.get('text')))
            elif type(part) is dict and part.get('type') in ('image_url','input_image'):
                self.images+=1;parts.append(self.hide('image'))
            else:self.images_known=False;parts.append(self.hide('content-part'))
        return '\n'.join(parts)

    def message(self,value):
        if type(value) is not dict:
            self.images_known=False;return dict(role='user',content=self.hide('message-shape'))
        role=value.get('role')
        if type(role) is not str or role not in _ROLES:
            self.images_known=False;return dict(role='user',content=self.hide('message-role'))
        result=dict(role=role,content=self.content(value.get('content','')))
        for key in ('name','tool_call_id'):
            if key in value:result[key]=self.atom(value[key])
        calls=value.get('tool_calls')
        if calls is not None:
            if type(calls) is not list:result['tool_calls']=[dict(withheld=self.hide('tool-calls'))]
            else:
                if len(calls)>16:self.truncated=True
                result['tool_calls']=[]
                for call in calls[:16]:
                    if type(call) is not dict or type(call.get('function')) is not dict:
                        result['tool_calls'].append(dict(withheld=self.hide('tool-call')));continue
                    function=call['function'];raw=function.get('arguments')
                    try:
                        if type(raw) is not str or len(raw)>MAX_SCAN:raise ValueError('unsupported arguments')
                        arguments=json.loads(raw)
                    except (ValueError,RecursionError):arguments=self.hide('malformed-arguments')
                    else:arguments=self.tree(arguments)
                    result['tool_calls'].append(dict(id=self.atom(call.get('id')),type='function',
                        function=dict(name=self.atom(function.get('name')),arguments=arguments)))
        return result

    def tool(self,value):
        if type(value) is not dict or value.get('type')!='function' or type(value.get('function')) is not dict:
            return dict(withheld=self.hide('tool-schema'))
        function=value['function']
        return dict(type='function',function=dict(name=self.atom(function.get('name')),
            description=self.text(function.get('description','')),parameters=self.tree(function.get('parameters',{}))))


def public_request(request,*,layer,known_secrets=()):
    """A lossy, explicitly marked public view of the supplied actual request."""
    if layer not in ('adapter_input','transport_payload'):raise ValueError('Unknown observation layer')
    capture=_PublicCapture(known_secrets)
    if type(request) is not dict:
        request={};capture.withheld=True
    messages=request.get('messages');tools=request.get('tools')
    if type(messages) is not list:messages=[];capture.withheld=True;capture.images_known=False
    if tools is None:tools=[]
    elif type(tools) is not list:tools=[];capture.withheld=True
    result=dict(schema_version=1,layer=layer,model=capture.atom(request.get('model')),
        method='chat_stream' if request.get('stream') is True else 'chat',messages=[],tools=[],
        counts=dict(messages=len(messages),tools=len(tools),images=0),truncated=False,withheld=False)
    remaining=MAX_PUBLIC_BYTES-_bytes(result)-2048
    for values,key,project in ((messages,'messages',capture.message),(tools,'tools',capture.tool)):
        if len(values)>MAX_ITEMS:capture.truncated=True
        for value in values[:MAX_ITEMS]:
            item=project(value);size=_bytes(item)+1
            if size>remaining:
                capture.truncated=True;break
            result[key].append(item);remaining-=size
    result['counts']['images']=capture.images
    result['counts']['images_exact']=not capture.truncated and capture.images_known
    result.update(truncated=capture.truncated,withheld=capture.withheld)
    # This is a programmer invariant, never a fallback to the raw request.
    if _bytes(result)>MAX_PUBLIC_BYTES:raise ValueError('Public context projection exceeded its fixed bound')
    return result


def observes_context(ctx):
    return getattr(getattr(ctx,'platform_runtime',None),'observes_public_context',False) is True


async def _observe(ctx,request,layer,request_seq,view):
    import inspect
    from pyharness.errors import raise_code
    observer=getattr(ctx.platform_runtime,'observe_model_request',None)
    if not callable(observer):raise_code('CFG-601',reason='public_context_observer_missing')
    result=observer(request,ctx=ctx,layer=layer,request_seq=request_seq,public_view=view)
    if not inspect.isawaitable(result):raise_code('CFG-601',reason='public_context_observer_contract')
    await result


async def observe_adapter_request(ctx,model,method,messages,tools):
    if not observes_context(ctx):return
    request=dict(model=model,messages=messages,tools=tools,stream=method=='chat_stream')
    await _observe(ctx,request,'adapter_input',None,public_request(request,layer='adapter_input'))


async def observe_transport_request(ctx,request,transport,request_seq):
    if not observes_context(ctx):return
    from pyharness.core.llm_transport import _OpenAICompatHTTPTransport
    # Only the owned transport may supply the credential-aware public view.
    # Never invoke an arbitrary plugin's extra callback while observing it.
    view=(transport._public_context_view(request) if type(transport) is _OpenAICompatHTTPTransport
        else public_request(request,layer='transport_payload'))
    await _observe(ctx,request,'transport_payload',request_seq,view)
