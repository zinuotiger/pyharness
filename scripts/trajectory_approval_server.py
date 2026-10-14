"""Synthetic session and real live ApprovalProvider; no ToolExecutor or model."""
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import uvicorn
from pyharness import cli
from pyharness.desktop.app import DesktopApp
from pyharness.core.approval import ApprovalProvider
from tests.support import isolated_settings

async def main():
    root=Path('tmp/trajectory-routing-'+uuid.uuid4().hex[:8]).resolve()
    root.mkdir(parents=True)
    app=DesktopApp(cli.assemble_ctx(isolated_settings(root)))
    sid=await app.service.create_session()
    log=await app.service.require_session(sid)
    await log.append('user.message',{'content':'【模拟验收】审批定位；不执行工具'},actor='user',sync=True)
    await log.append('tool.call',{'name':'fs.write_file','args':{},'raw_args':{},'call_id':'routing-only'},actor='tool')
    provider=ApprovalProvider(session=log,channel='web')
    app.service._approvals[sid]=provider
    pending=asyncio.create_task(provider.request(SimpleNamespace(name='fs.write_file',args={},call_id='routing-only'),
        '【模拟验收】仅验证定位，不执行或裁决',SimpleNamespace(channel='web',session=log,headless=False),ttl_ms=600000))
    for _ in range(100):
        if provider.pending_count:break
        if pending.done():await pending
        await asyncio.sleep(.01)
    assert provider.pending_count
    metadata={'token':app._api_token,'port':60472,'pid':os.getpid(),'scenarios':{'approval':{'sid':sid}}}
    Path('tmp/trajectory-routing-ui.json').write_text(json.dumps(metadata),encoding='utf-8')
    print('Live synthetic approval routing server ready on 127.0.0.1:60472',flush=True)
    try:
        await uvicorn.Server(uvicorn.Config(app.api,host='127.0.0.1',port=60472,log_level='warning',access_log=False)).serve()
    finally:
        pending.cancel()
        await asyncio.gather(pending,return_exceptions=True)
        await app.service.shutdown()

if __name__=='__main__':asyncio.run(main())
