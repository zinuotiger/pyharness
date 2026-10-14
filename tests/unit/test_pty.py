"""The prior PID-only PTY path is explicitly constrained, never silently run."""
import pytest
from pyharness.core import pty
from pyharness.errors import PyHError


def test_pty_execution_requires_owned_tree(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(pty._WinPtySession, 'start', lambda self: calls.append('win'))
    monkeypatch.setattr(pty._PosixPty, 'start', lambda self: calls.append('posix'))
    assert pty.pty_supported() is False
    with pytest.raises(PyHError) as error:
        pty.run_in_pty('synthetic', cwd=tmp_path)
    assert error.value.code == 'TLB-807'
    assert calls == []
