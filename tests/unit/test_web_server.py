"""The headless Web entry has explicit storage and no inherited model credentials."""
from pyharness import web


def test_web_settings_are_explicit_and_isolated(tmp_path,monkeypatch):
    monkeypatch.setenv('PH_LLM__API_KEY','synthetic-unwanted-environment')
    cfg=web.settings_for(tmp_path)
    assert str(cfg.storage.root)==str(tmp_path.resolve())
    assert cfg.llm.api_key=='tenant:default:unconfigured'
    assert cfg.llm.fallback_models==[]
    assert cfg.plugins.enabled==[] and cfg.plugins.mcp_servers==[]
    assert str(cfg.skills.dir)==str(tmp_path.resolve()/'skills')
    assert cfg.llm.retry.attempts==0


def test_parser_has_no_public_bind_or_implicit_data_directory():
    import pytest
    with pytest.raises(SystemExit): web.parser().parse_args([])
    with pytest.raises(SystemExit): web.parser().parse_args(['--data-dir','x','--host','0.0.0.0'])


def test_stop_signals_unwind_and_restore_handlers(tmp_path,monkeypatch):
    import signal
    before={getattr(signal,name):signal.getsignal(getattr(signal,name))
            for name in ('SIGTERM','SIGBREAK') if hasattr(signal,name)}
    observed=[]
    def interrupt(coroutine):
        coroutine.close()
        observed.extend(signal.getsignal(sig) is signal.default_int_handler for sig in before)
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM,None)
    monkeypatch.setattr(web.asyncio,'run',interrupt)
    assert web.main(['--data-dir',str(tmp_path)])==0
    assert observed and all(observed)
    assert all(signal.getsignal(sig)==handler for sig,handler in before.items())


async def test_stopped_synthetic_backup_restores_run_artifact_and_account(tmp_path,adapter_factory):
    import asyncio
    import shutil
    from pyharness import cli
    from pyharness.desktop.app import DesktopApp
    from pyharness.application.accounts import Accounts
    from tests.support import isolated_settings
    root=tmp_path/'data'
    cfg=isolated_settings(root)
    adapter_factory(cfg)
    app=DesktopApp(cli.assemble_ctx(cfg))
    try:
        accounts=Accounts(root)
        user=accounts.provision('backup-user','synthetic-backup-password','backup-team')
        run=await app.service.platform.start_run({'text':'synthetic backup verification'})
        assert (await asyncio.wait_for(app.service._queues[run['session_id']].wait_for(run['task_id']),10)).ok
        artifact=await app.service.platform.upload({'session_id':run['session_id'],'filename':'synthetic.txt','data_base64':'b2s='})
    finally:
        await app.service.shutdown()
    backup=tmp_path/'backup'
    archive=tmp_path/'old-data'
    for path in (root,backup,archive):
        assert path.resolve().is_relative_to(tmp_path.resolve())
    shutil.copytree(root,backup)
    root.rename(archive)
    shutil.copytree(backup,root)
    restored=DesktopApp(cli.assemble_ctx(cfg))
    try:
        assert (await restored.service.platform.run(run['run_id']))['status']=='completed'
        record,raw=await restored.service.platform.artifact_content(artifact['artifact_id'])
        assert raw==b'ok' and record['sha256']==artifact['sha256']
        restored_accounts=Accounts(root)
        token=restored_accounts.login('backup-user','synthetic-backup-password')
        assert restored_accounts.authenticate(token)['user_id']==user['user_id']
        assert not restored.service._engines
    finally:
        await restored.service.shutdown()
