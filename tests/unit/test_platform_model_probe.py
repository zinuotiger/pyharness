"""Connection generation uses the production client/adapter, synthetic transport only."""
import json
import pytest

from pyharness import cli
from pyharness.core import llm
from pyharness.desktop.app import DesktopApp
from tests.support import isolated_settings
from tests.unit.test_llm import FakeTransport, _raw, _usage, _tc


@pytest.mark.parametrize('expire',[False,True])
async def test_probe_deadline_cancels_transport_and_rejects_duplicate(tmp_path,monkeypatch,expire):
    import asyncio
    from pyharness.application import model_probe
    from pyharness.errors import PyHError
    app=DesktopApp(cli.assemble_ctx(isolated_settings(tmp_path)))
    app.service.save_model_profile({'id':'synthetic','model':'synthetic-model',
        'base_url':'https://synthetic.invalid/v1','api_key':'synthetic-deadline-secret'})
    entered,release,deadline=asyncio.Event(),asyncio.Event(),asyncio.Event()
    cancelled=[];closed=[]
    transport=FakeTransport()
    async def complete(req):
        transport.requests.append(req);entered.set()
        try:await release.wait()
        except asyncio.CancelledError:
            cancelled.append(True);raise
        return _raw('PYHARNESS_MODEL_OK',usage=_usage(11,4))
    async def close():closed.append(True)
    transport.complete=complete;transport.aclose=close
    monkeypatch.setattr(llm,'build_client',lambda *args:transport)
    real_wait_for=asyncio.wait_for
    async def controlled_expiry(awaitable,timeout):
        if timeout!=60:return await real_wait_for(awaitable,timeout)
        task=asyncio.create_task(awaitable)
        try:
            await deadline.wait()
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
            raise asyncio.TimeoutError('synthetic-deadline-secret')
        finally:
            if not task.done():task.cancel()
            await asyncio.gather(task,return_exceptions=True)
    if expire:monkeypatch.setattr(model_probe.asyncio,'wait_for',controlled_expiry)
    operation=asyncio.create_task(app.service.platform.connection_action('model:synthetic','test'))
    try:
        await real_wait_for(entered.wait(),5)
        with pytest.raises(PyHError,match='model_probe_busy'):
            await app.service.platform.connection_action('model:synthetic','test')
        assert len(transport.requests)==1
        if expire:deadline.set()
        else:release.set()
        result=await real_wait_for(operation,5)
        assert result['retried'] is False and len(transport.requests)==1 and transport.pings==0
        assert closed
        if expire:
            assert result['status']=='unavailable' and result['code']=='model_connection_timeout'
            assert cancelled
        else:
            assert result['status']=='available'
        assert 'synthetic-deadline-secret' not in json.dumps(result)
        assert 'synthetic-deadline-secret' not in json.dumps(app.service.platform.store.read())
    finally:
        if not operation.done():operation.cancel()
        await asyncio.gather(operation,return_exceptions=True)
        await app.service.shutdown()


@pytest.mark.parametrize('case', ['ok', 'authentication', 'protocol', 'tools'])
async def test_connection_test_one_bounded_generation(tmp_path, monkeypatch, case):
    app = DesktopApp(cli.assemble_ctx(isolated_settings(tmp_path)))
    app.service.save_model_profile({'id':'synthetic','label':'Synthetic','model':'synthetic-model',
        'base_url':'https://synthetic.invalid/v1','api_key':'synthetic-test-key'})
    error = {'authentication':llm.ProviderStatusError(401),
             'protocol':llm.ProviderProtocolError('synthetic-test-key')}.get(case)
    transport = FakeTransport(error=error, result=_raw('PYHARNESS_MODEL_OK',
        tool_calls=[_tc('x','exec.shell_run','{}')] if case=='tools' else None,
        usage=_usage(12,5)))
    closed = []
    async def close(): closed.append(True)
    transport.aclose = close
    def build(triple, limits):
        assert triple.model == 'synthetic-model'
        assert limits.total_s <= 60
        return transport
    monkeypatch.setattr(llm, 'build_client', build)
    try:
        result = await app.service.platform.connection_action('model:synthetic','test')
        assert len(transport.requests) == 1 and transport.pings == 0
        req = transport.requests[0]
        assert req['model'] == 'synthetic-model'
        assert req['max_tokens'] == 64 and req['temperature'] == 0
        assert not req['tools']
        assert len(req['messages']) == 1
        assert result['status'] == ('available' if case=='ok' else 'unavailable')
        assert result['retried'] is False and result['capabilities'] == 'not_probed'
        if case=='ok':
            assert result['input_tokens'] == 12 and result['output_tokens'] == 5
        assert 'synthetic-test-key' not in json.dumps(result)
        assert 'synthetic-test-key' not in json.dumps(app.service.platform.store.read())
        assert closed
    finally:
        await app.service.shutdown()
