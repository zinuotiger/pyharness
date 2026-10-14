"""Real Windows junction containment, including Python 3.11's missing API."""
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from pyharness.core.tools_guard import g_fs_path_check


@pytest.mark.skipif(os.name != "nt", reason="real Windows junction acceptance")
@pytest.mark.controlled_process
@pytest.mark.parametrize("force_legacy_api", [False, True])
@pytest.mark.parametrize("child", [False, True])
async def test_junction_and_descendant_escape_are_rejected(tmp_path, monkeypatch, force_legacy_api, child):
    if force_legacy_api:
        monkeypatch.delattr(os.path, "isjunction", raising=False)
    workspace, outside = tmp_path / "workspace", tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    marker = outside / "marker.txt"
    marker.write_text("synthetic untouched content", encoding="utf-8")
    link = workspace / "junction"
    subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(link), str(outside)],
                   cwd=tmp_path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=5, check=True)
    try:
        target = link / marker.name if child else link
        assert Path(os.path.realpath(target)).is_relative_to(outside)
        scope = SimpleNamespace(policy=SimpleNamespace(workspace_root=str(workspace), read_extra_dirs=[]))
        call = SimpleNamespace(name="fs.read_file", safe_args={"path": str(target)})
        assert await g_fs_path_check(call, scope) == ("reject", "POL-FS-3")
        assert marker.read_text(encoding="utf-8") == "synthetic untouched content"
    finally:
        if os.path.lexists(link):
            os.rmdir(link)  # Remove only this test's junction, never its target.
