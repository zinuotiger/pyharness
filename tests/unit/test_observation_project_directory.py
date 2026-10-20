"""Entering the observation workbench must not acquire the legacy control writer."""
import httpx
import pytest
from pyharness import cli
from pyharness.desktop.app import DesktopApp
from tests.support import isolated_settings
from tests.unit.test_source_grants import grant_app


async def test_empty_project_directory_does_not_initialize_a_control_journal(tmp_path):
    app=DesktopApp(cli.assemble_ctx(isolated_settings(tmp_path)))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.api),base_url='http://127.0.0.1',headers={'X-PyHarness-Token':app._api_token}) as client:
            response=await client.get('/api/v1/projects');assert response.status_code==200,response.text
            assert response.json()=={'projects':[],'total':0}
        store=app.service.projects.store
        assert store._store is None and not store.log_path.exists()
    finally:await app.service.shutdown()


@pytest.mark.parametrize('corrupt',[False,True])
async def test_cold_project_directory_validates_original_bytes_without_a_writer(grant_app,corrupt):
    app,_,_=grant_app;cfg=app.service.ctx.settings;path=app.service.projects.store.log_path
    await app.service.shutdown()
    if corrupt:path.write_bytes(path.read_bytes()+b'{invalid\n')
    before=path.read_bytes();cold=DesktopApp(cli.assemble_ctx(cfg))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=cold.api),base_url='http://127.0.0.1',headers={'X-PyHarness-Token':cold._api_token}) as client:
            response=await client.get('/api/v1/projects')
            assert response.status_code==(503 if corrupt else 200),response.text
            assert response.headers['cache-control']=='no-store'
        assert cold.service.projects.store._store is None and not cold.service._engines and not cold.service._queues
        assert path.read_bytes()==before
    finally:await cold.service.shutdown()
