"""Independent expiry boundary regression: only synthetic source, real HTTP/store."""
from datetime import datetime,timezone
import pytest
from tests.unit.test_source_grants import grant_app,issue,URL
from tests.unit.test_source_snapshot import command_for,SNAPS,PREVIEW,BODY
from tests.conftest import isolated_process_environment

@pytest.mark.asyncio
@pytest.mark.parametrize('operation',['capture','preview'])
async def test_expiry_during_final_source_io_must_reject(grant_app,monkeypatch,operation):
    from pyharness.application import snapshot_store as target
    app,c,source=grant_app
    assert (await c.post(URL,json=issue(source),headers={'Idempotency-Key':'grant'})).status_code==200
    command=await command_for(c)
    store=app.service.projects.store
    before=store.log_path.read_bytes()
    class Future(datetime):
        @classmethod
        def now(cls,tz=None):return datetime(2100,1,1,tzinfo=timezone.utc)
    if operation=='capture':
        original=target.verify_selection
        calls=0
        def verify(row,files,**kwargs):
            nonlocal calls
            calls+=1
            result=original(row,files,**kwargs)
            if calls==2:monkeypatch.setattr(target,'datetime',Future)
            return result
        monkeypatch.setattr(target,'verify_selection',verify)
        response=await c.post(SNAPS,json=command,headers={'Idempotency-Key':'review-expiry'})
        assert calls==2
    else:
        original=target.collect_selection
        def collect(row,command,**kwargs):
            result=original(row,command,**kwargs)
            monkeypatch.setattr(target,'datetime',Future)
            return result
        monkeypatch.setattr(target,'collect_selection',collect)
        response=await c.post(PREVIEW,json=BODY)
    assert response.status_code==403, f'grant expired during last source IO but returned HTTP {response.status_code}; log_changed={store.log_path.read_bytes()!=before}'
    assert store.log_path.read_bytes()==before
