"""Real wall-clock expiration, real ASGI HTTP, actual control event: no clock substitution."""
from datetime import datetime,timedelta,timezone
import time,json
from pathlib import Path
import pytest
from tests.conftest import isolated_process_environment
from tests.unit.test_source_grants import grant_app,issue,URL
from tests.unit.test_source_snapshot import command_for,SNAPS

@pytest.mark.asyncio
async def test_capture_rejects_grant_expired_during_final_read(grant_app,monkeypatch):
    from pyharness.application import snapshot_store as target
    app,c,source=grant_app
    deadline=datetime.now(timezone.utc)+timedelta(seconds=3)
    body={**issue(source),'expires_at':deadline.isoformat()}
    assert (await c.post(URL,json=body,headers={'Idempotency-Key':'grant'})).status_code==200
    command=await command_for(c)
    original=target.verify_selection
    calls=0
    def verify(row,files,**kwargs):
        nonlocal calls
        calls+=1
        if calls==2:
            time.sleep(max(0,(deadline-datetime.now(timezone.utc)).total_seconds())+0.08)
        return original(row,files,**kwargs)
    monkeypatch.setattr(target,'verify_selection',verify)
    store=app.service.projects.store;before=store.log_path.read_bytes()
    response=await c.post(SNAPS,json=command,headers={'Idempotency-Key':'review-wall-expiry'})
    events=[json.loads(line) for line in store.log_path.read_text().splitlines()]
    evidence={'expires_at':deadline.isoformat(),'response_status':response.status_code,'final_verify_calls':calls,'last_event_type':events[-1]['type'],'last_event_ts':events[-1]['ts'],'last_event_kind':events[-1].get('payload',{}).get('resource_kind'),'log_changed':store.log_path.read_bytes()!=before}
    # Evidence is asserted below; no writes outside the synthetic fixture.
    assert response.status_code==403,str(evidence)
    assert store.log_path.read_bytes()==before
