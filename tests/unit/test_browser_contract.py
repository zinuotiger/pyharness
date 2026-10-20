"""Metadata-only contract tests; no browser or external process."""
import io
import json
import pytest
from pyharness.core.browser_contract import allowed_origin, validate_call, TOOLS
from pyharness.core.browser_mcp import serve
from pyharness.core.mcp import McpClient, InlineTransport
from pyharness.core.tools_registry import ToolRegistry


@pytest.mark.parametrize('value',['file:///tmp/a','https://u:p@host','https://host/a','https://host?q=x','https://host#x','https://host.','javascript:alert(1)','https://host:bad','http://host:0','http://ho\nst','http://host\\path'])
def test_origin_refuses_ambient_paths_and_credentials(value):
    with pytest.raises(ValueError): allowed_origin(value)


def test_origin_is_exact_and_canonical():
    assert allowed_origin('HTTP://LOCALHOST:80/') == 'http://localhost'
    assert allowed_origin('https://[::1]:8443') == 'https://[::1]:8443'


@pytest.mark.parametrize('name,args',[('evaluate',{}),('close',{'script':'anything'}),('observe',{'offset':True}),
    ('act',dict(session='x',generation=0,target='y',label='send',action='click',text='no')),
    ('act',dict(session='x',generation=0,target='y',label='send',action='fill'))])
def test_call_schema_does_not_offer_arbitrary_code_or_coercions(name,args):
    with pytest.raises(ValueError):validate_call(name,args)


def test_original_registry_accepts_all_four_high_danger_tools():
    client=McpClient('controlled_browser',InlineTransport())
    client._tools=TOOLS
    reg=ToolRegistry()
    names=client.register_into(reg)
    assert len(names)==4
    assert all(reg.lookup(name).danger=='high' for name in names)
    raw=dict(session='a',generation=0,target='b',label='Send',action='click')
    compiled=reg.validate_args('mcp.controlled_browser.act',raw)
    assert compiled['text'] is None
    assert validate_call('act',compiled)==raw


def test_protocol_discovery_does_not_acquire_carrier_and_bad_json_does_not_leak():
    class Metadata:
        closed=False
        def call(self,*args):raise AssertionError('discovery acquired browser')
        def close(self):self.closed=True
    carrier=Metadata();out=io.BytesIO()
    source=io.BytesIO(b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}\nprivate-invalid-json\n')
    serve(carrier,source,out)
    lines=[json.loads(line) for line in out.getvalue().splitlines()]
    assert lines[0]['result']['tools']==TOOLS and carrier.closed
    assert lines[1]['error']['message']=='browser_request_failed'
    assert b'private-invalid-json' not in out.getvalue()



def test_metadata_jsonrpc_unicode_identifier_has_a_valid_utf8_wire():
    class Metadata:
        closed=False
        def call(self,*args):raise AssertionError('metadata acquired browser')
        def close(self):self.closed=True
    ident='unicode-\ud800';carrier=Metadata();out=io.BytesIO()
    packet=json.dumps(dict(jsonrpc='2.0',id=ident,method='tools/list'),ensure_ascii=True).encode()+b'\n'
    serve(carrier,io.BytesIO(packet),out)
    data=json.loads(out.getvalue().decode('utf-8'))
    assert data['id']==ident and data['result']['tools']==TOOLS and carrier.closed
