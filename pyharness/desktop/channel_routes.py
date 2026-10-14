"""Versioned HTTP channel routes."""
from __future__ import annotations
from fastapi import Body, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from pyharness.core.sandbox import fail
from pyharness.errors import PyHError

def mount_channels(router, desktop, service):
    @router.get('/channels')
    async def channels():
        desktop.service.channels._local_only()
        desktop.service.channels.start()
        return desktop.service.channels.status()

    @router.get('/executors/codex')
    async def codex_status():
        desktop.service.channels._local_only()
        import asyncio
        from pyharness.integrations.codex_transport import codex_command
        try:
            command = codex_command()
            process = await asyncio.create_subprocess_exec(*command[:-1], '--version',
                stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
            try:
                output,_ = await asyncio.wait_for(process.communicate(),5)
            except BaseException:
                process.kill()
                await process.communicate()
                raise
            return {'installed':process.returncode==0,'executable':command[-2],
                    'version':output.decode(errors='replace').strip()[:100],'task_tested':False}
        except (OSError, ValueError, asyncio.TimeoutError):
            return {'installed':False,'version':None,'task_tested':False}

    @router.post('/channels/feishu')
    async def configure_feishu(body: dict=Body(...)):
        channels = desktop.service.channels
        result = channels.save(body)
        old = channels.clients.pop(body.get('name'), None)
        if old:
            await old.close()
        channels.start()
        return result

    @router.get('/channels/deliveries')
    async def channel_deliveries(after: int=0, limit: int=100):
        return desktop.service.channels.deliveries(after=after,limit=limit)

    @router.post('/channels/deliveries/{ident}/retry')
    async def retry_channel_delivery(ident: str):
        return desktop.service.channels.retry_delivery(ident)

    @router.post('/channels/wechat/relay')
    async def wechat_relay(body: dict=Body(...)):
        desktop.service.channels._local_only()
        from pyharness.integrations.wechat import ManualWechatRequest
        message = ManualWechatRequest.model_validate(body)
        return await desktop.service.platform.start_run(message.run_request())

    @router.post('/channels/wechat')
    async def configure_wechat(body: dict=Body(...)):
        return await desktop.service.channels.wechat.configure(body)

    @router.post('/channels/wechat/login')
    async def login_wechat():
        return await desktop.service.channels.wechat.begin_login()

    @router.post('/channels/wechat/login/{ident}')
    async def poll_wechat(ident: str, body: dict=Body(default={})):
        code = body.get('verification_code')
        if code is not None and (not isinstance(code,str) or not code.isdigit() or not 4<=len(code)<=12):
            fail('wechat_verification_code_invalid')
        return await desktop.service.channels.wechat.login_status(ident,code)

    @desktop.api.post('/hooks/feishu/{name}')
    async def feishu_callback(name: str, request: Request):
        # No caller-supplied tenant, cookie or operator token is trusted here.
        from pyharness.integrations.feishu import FeishuRejected
        raw = bytearray()
        async for part in request.stream():
            raw.extend(part)
            if len(raw) > 1024*1024:
                return JSONResponse({'code':'event_too_large'},status_code=413)
        try:
            owner = desktop._service_registry.get('default')
            from pyharness.core.lifecycle import admitted
            @admitted
            async def receive(service):
                return await service.channels.receive(name,bytes(raw),request.headers)
            return await receive(owner)
        except (FeishuRejected, ValidationError, PyHError):
            return JSONResponse({'code':'channel_event_rejected'},status_code=403)

    from contextlib import asynccontextmanager
    previous_lifespan = desktop.api.router.lifespan_context
    @asynccontextmanager
    async def channels_lifespan(app):
        async with previous_lifespan(app):
            channels = desktop._service_registry.get('default').channels
            channels.start()
            try:
                yield
            finally:
                await channels.close()
    desktop.api.router.lifespan_context = channels_lifespan
