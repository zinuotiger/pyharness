"""UI save/test -> real adapter/HTTP transport -> controlled loopback provider."""
import asyncio
import socket
from fastapi import FastAPI
import uvicorn
from scripts.platform_browser_smoke import navigate


async def verify_connection(page,output):
    provider=FastAPI();requests=[]
    @provider.post('/v1/chat/completions')
    async def completion(body:dict):
        assert body['model']=='audit-synthetic'
        assert body.get('max_tokens',body.get('max_completion_tokens'))==64
        assert not body.get('tools') and not body.get('functions')
        assert body.get('temperature')==0
        assert body['messages'][-1]['content']=='这是一个连接测试。请只回复：PYHARNESS_MODEL_OK'
        requests.append({'model':body['model'],'max_output':64,'tools':False})
        return {'id':'controlled-probe','object':'chat.completion','created':0,'model':'audit-synthetic',
                'choices':[{'index':0,'message':{'role':'assistant','content':'PYHARNESS_MODEL_OK'},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(provider,log_level='critical',access_log=False));task=asyncio.create_task(server.serve(sockets=[sock]))
    try:
        while not server.started:
            if task.done():await task;raise AssertionError('provider did not start')
            await asyncio.sleep(.01)
        await navigate(page,'connections');await page.get_by_role('button',name='添加模型',exact=True).click()
        for field,value in [('label','audit local protocol'),('model','audit-synthetic'),('base_url',f'http://127.0.0.1:{port}/v1'),('api_key','synthetic-local-only-credential')]:
            await page.locator('#model_'+field).fill(value)
        page.once('dialog',lambda dialog:dialog.accept())
        await page.get_by_role('button',name='保存并测试连接',exact=True).click()
        await page.locator('#dialog pre').filter(has_text='"status": "available"').wait_for(timeout=20000)
        assert len(requests)==1
        assert 'synthetic-local-only-credential' not in await page.locator('#dialog').inner_text()
        await page.screenshot(path=str(output/'model-real-transport-local-fixture.png'),full_page=True)
        await page.locator('#dialogClose').click()
        return {'area':'model save/test','result':'passed','chain':'UI -> saved profile -> model_probe -> LLMClient -> OpenAICompatAdapter -> real HTTP Transport -> loopback fixture -> usage/Trace',
                'controlled_http_requests':len(requests),'real_external_provider_requests':0,'not_a_provider_acceptance':True}
    finally:
        server.should_exit=True
        await asyncio.wait_for(task,10)
