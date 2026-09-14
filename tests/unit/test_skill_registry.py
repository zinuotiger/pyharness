from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from pyharness.core.skill_registry import SkillInstaller
from pyharness.errors import PyHError


def _skill_zip(path: Path, name: str, text: str, *, member: str = "SKILL.md") -> str:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("demo/SKILL.md" if member == "SKILL.md" else member,
                    f"---\nname: {name}\ndescription: demo\n---\n\n{text}\n")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _registry(path: Path, entries: list[dict]) -> str:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("demo/SKILL.md", "---\nname: demo\ndescription: demo\n---\n\ndemo\n")


@pytest.mark.asyncio
async def test_skill_install_and_rollback(tmp_path):
    pkg1 = tmp_path / "demo-1.zip"
    pkg2 = tmp_path / "demo-2.zip"
    sha1 = _skill_zip(pkg1, "demo", "version one")
    sha2 = _skill_zip(pkg2, "demo", "version two")
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"skills": [{
        "name": "demo", "description": "demo skill", "latest": "2.0.0",
        "versions": [
            {"version": "1.0.0", "url": pkg1.as_uri(), "sha256": sha1},
            {"version": "2.0.0", "url": pkg2.as_uri(), "sha256": sha2},
        ]}]}), encoding="utf-8")

    installer = SkillInstaller(tmp_path / "skills", state_dir=tmp_path / "state")
    found = await installer.search("demo", registry.as_uri())
    assert found[0]["name"] == "demo"
    assert found[0]["sha256"] == sha2
    assert found[0]["source"] == pkg2.as_uri()
    await installer.install("demo", registry_url=registry.as_uri(),
                            version="1.0.0", approved_by="test")
    assert "version one" in (tmp_path / "skills" / "demo" / "SKILL.md").read_text(encoding="utf-8")
    await installer.install("demo", registry_url=registry.as_uri(),
                            version="2.0.0", approved_by="test")
    assert "version two" in (tmp_path / "skills" / "demo" / "SKILL.md").read_text(encoding="utf-8")
    installer.rollback("demo", "1.0.0", approved_by="test")
    assert "version one" in (tmp_path / "skills" / "demo" / "SKILL.md").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_skill_name_rejects_path_traversal(tmp_path):
    """CND-04:外部 name/version 不得作资源路径依据——"."/".."/含分隔符一律拒。

    修复前:`_NAME_RE` 放行 "."/".." → `remove("..")` 会 rmtree 父目录;`versions`/
    `rollback` 无校验可读写逃逸。"""
    skills = tmp_path / "skills"
    installer = SkillInstaller(skills, state_dir=tmp_path / "state")
    sibling = tmp_path / "keep"
    sibling.mkdir()
    (sibling / "x").write_text("keep", encoding="utf-8")

    # 正常路径:合法名放行(空版本列表,不抛)
    assert installer.versions("demo") == []

    for bad in (".", "..", "../keep", "a/b", "a\\b", ""):
        with pytest.raises(PyHError) as e:
            installer.remove(bad, approved_by="test")
        assert e.value.code in ("EVT-100", "POL-FS-2")        # negative:拒
        with pytest.raises(PyHError):
            installer.versions(bad)
        with pytest.raises(PyHError):
            installer.rollback(bad, "1.0.0", approved_by="test")
    # 版本段亦属外部输入:合法名 + 逃逸版本 → 拒
    with pytest.raises(PyHError):
        installer.rollback("demo", "../keep", approved_by="test")
    assert (sibling / "x").read_text(encoding="utf-8") == "keep"   # 无越界删除


@pytest.mark.asyncio
async def test_skill_registry_rejects_hash_mismatch(tmp_path):
    pkg = tmp_path / "demo.zip"
    _skill_zip(pkg, "demo", "payload")
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"skills": [{
        "name": "demo", "latest": "1.0.0",
        "versions": [{"version": "1.0.0", "url": pkg.as_uri(),
                      "sha256": "0" * 64}]}]}), encoding="utf-8")
    installer = SkillInstaller(tmp_path / "skills", state_dir=tmp_path / "state")
    with pytest.raises(PyHError) as exc:
        await installer.install("demo", registry_url=registry.as_uri(),
                                approved_by="test")
    assert exc.value.code == "GRD-401"


@pytest.mark.asyncio
async def test_skill_registry_rejects_zip_path_traversal(tmp_path):
    pkg = tmp_path / "evil.zip"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("../evil.txt", "bad")
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"skills": [{
        "name": "demo", "latest": "1.0.0",
        "versions": [{"version": "1.0.0", "url": pkg.as_uri(),
                      "sha256": hashlib.sha256(pkg.read_bytes()).hexdigest()}]}]}),
        encoding="utf-8")
    installer = SkillInstaller(tmp_path / "skills", state_dir=tmp_path / "state")
    with pytest.raises(PyHError) as exc:
        await installer.install("demo", registry_url=registry.as_uri(),
                                approved_by="test")
    assert exc.value.code == "TLB-805"
    assert not (tmp_path / "evil.txt").exists()
