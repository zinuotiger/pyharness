"""R03: synthetic hostile configuration must never enter shared fixtures."""
import socket
from pathlib import Path

import pytest


def test_shared_fixture_ignores_host_configuration(tmp_path, monkeypatch):
    from tests.conftest import e2e_settings
    host = tmp_path / 'synthetic-user'
    host.mkdir()
    config = host / 'config.yaml'
    config.write_text('plugins:\n  enabled: [must-not-load]\n  dir: forbidden-plugins\n'
                      'skills:\n  dir: forbidden-skills\n', encoding='utf-8')
    monkeypatch.setenv('PH_CFG_PATH', str(config))
    monkeypatch.setenv('PH_LLM_MODEL', 'must-not-inherit')
    work = tmp_path / 'isolated'
    cfg = e2e_settings(work)
    assert cfg.plugins.enabled == []
    assert cfg.plugins.mcp_servers == []
    assert cfg.llm.model != 'must-not-inherit'
    for value in (cfg.storage.root, cfg.storage.sessions_dir, cfg.storage.workspaces_dir,
                  cfg.storage.spill_dir, cfg.storage.db_path, cfg.plugins.dir,
                  cfg.skills.dir, cfg.security.credentials.file):
        assert Path(value).is_relative_to(work), value


def test_offline_boundary_blocks_network_without_dns():
    with socket.socket() as sock, pytest.raises(PermissionError, match='non-loopback'):
        sock.connect(('192.0.2.1', 443))


def test_default_process_boundary(tmp_path):
    import subprocess
    import sys
    with pytest.raises(PermissionError, match='controlled_process'):
        subprocess.run([sys.executable, '-c', 'raise SystemExit(0)'], cwd=tmp_path)
