"""Real TCP/HTTP transport -> original queue/log -> authenticated public API.

The provider is an owned loopback fixture, never a paid external model.
"""
import asyncio
import json
import pytest
from tests.unit.test_product_execution_start import running_product,wait_terminal
from tests.unit.test_source_grants import PID


@pytest.mark.parametrize('running_product',[dict(raw=b'Public fixture with tinywirekey\n',
    script=[dict(id='unused-script',name='file.read',args=dict(path='unchanged.txt'))],
    agent=dict(permission_policy='standard'))],indirect=True)
async def test_actual_original_queue_transmits_real_http_and_records_only_its_safe_public_copy(running_product):
    env=running_product;requests=[];tasks=set()
    async def handle(reader,writer):
        task=asyncio.current_task();tasks.add(task)
        try:
            head=await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),5)
            lines=head.decode('latin1').split('\r\n');headers=dict(line.split(':',1) for line in lines[1:] if ':' in line)
            size=int(next(v for k,v in headers.items() if k.lower()=='content-length'));assert 0<size<=262144
            body=json.loads(await asyncio.wait_for(reader.readexactly(size),5));requests.append(dict(path=lines[0],headers=headers,body=body))
            content={'id':'loopback-response','model':'loopback-wire-fixture','choices':[{'index':0,'message':{
                'role':'assistant','content':'Public loopback result','reasoning_content':'hidden-loopback-chain-fixture'},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':17,'completion_tokens':4,'total_tokens':21}}
            raw=json.dumps(content).encode()
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: '+str(len(raw)).encode()+b'\r\n\r\n'+raw)
            await writer.drain()
        finally:
            writer.close();await writer.wait_closed();tasks.discard(task)
    server=await asyncio.start_server(handle,'127.0.0.1',0)
    port=server.sockets[0].getsockname()[1]
    try:
        service=env.app.service
        service.save_model_profile(dict(id='loopback-wire-profile',model='loopback-wire-fixture',
            base_url='http://127.0.0.1:'+str(port),api_key='tinywirekey',fallback_models=[]))
        service.activate_model_profile('loopback-wire-profile')
        projects=service.projects;preview=await projects.execution_preview(PID,env.selection);ident='ex-'+'9'*16
        await projects.start_execution(PID,dict(env.selection,execution_id=ident,confirmation_digest=preview['confirmation_digest']),'actual-loopback-wire')
        result=await wait_terminal(projects,ident)
        assert result['status']=='completed',result
        assert len(requests)==1 and requests[0]['path']=='POST /chat/completions HTTP/1.1'
        actual=requests[0];assert next(v.strip() for k,v in actual['headers'].items() if k.lower()=='authorization')=='Bearer tinywirekey'
        assert 'tinywirekey' in json.dumps(actual['body']['messages']) and env.adapter.n==0
        names=[t['function']['name'] for t in actual['body']['tools']];assert 'file_read' in names and 'file.read' not in names
        base='/api/v1/projects/'+PID+'/executions/'+ident+'/contexts'
        response=await env.client.get(base);assert response.status_code==200,response.text
        items=response.json()['items'];assert len(items)==1 and items[0]['layer']=='transport_payload'
        response=await env.client.get(base+'/'+items[0]['observation_id']);assert response.status_code==200,response.text
        public=response.json();assert public['dispatch_confirmation']=='response_recorded' and public['request_seq'] is not None
        assert public['derivation']['history_window']['available'] and public['derivation']['system_window']['available']
        assert public['public_view']['tools'][0]['function']['name'] in names
        text=json.dumps(public);assert 'tinywirekey' not in text and 'hidden-loopback-chain-fixture' not in text
        assert public['public_view']['withheld'] and response.headers['cache-control']=='no-store'
    finally:
        server.close();await server.wait_closed()
        if tasks:await asyncio.gather(*tuple(tasks),return_exceptions=False)
