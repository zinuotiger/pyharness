"""Safe skill registry search/install/rollback primitives."""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Optional
from urllib.parse import unquote, urlparse

import httpx

from pyharness.errors import raise_code

log = logging.getLogger("pyharness.skill_registry")

_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_MAX_REGISTRY_BYTES = 2 * 1024 * 1024
_MAX_ZIP_BYTES = 5 * 1024 * 1024
_MAX_FILES = 200


class SkillInstaller:
    """Download, verify, quarantine and install SKILL.md packages."""

    def __init__(self, skills_dir: Path, *, state_dir: Optional[Path] = None,
                 max_package_bytes: int = _MAX_ZIP_BYTES,
                 max_files: int = _MAX_FILES) -> None:
        self.skills_dir = Path(skills_dir).expanduser()
        self.state_dir = Path(state_dir).expanduser() if state_dir else \
            self.skills_dir.parent / "skill_state"
        self.versions_dir = self.state_dir / "versions"
        self.quarantine_dir = self.state_dir / "quarantine"
        self.max_package_bytes = int(max_package_bytes)
        self.max_files = int(max_files)
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.versions_dir.mkdir(parents=True, exist_ok=True)
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ transport
    async def _read_url(self, url: str, *, max_bytes: int) -> bytes:
        parsed = urlparse(str(url or ""))
        if parsed.scheme == "file":
            path = Path(unquote(parsed.path.lstrip("/"))) if os.name == "nt" else Path(unquote(parsed.path))
            try:
                data = path.read_bytes()
            except OSError as exc:                     # noqa: BLE001
                raise_code("TLB-805", url=url, stage="file",
                           cause=exc, advice="本地 Skill 包无法读取")
            if len(data) > max_bytes:
                raise_code("TLB-805", url=url, stage="size",
                           advice=f"Skill 包超过上限 {max_bytes} bytes")
            return data
        if parsed.scheme not in ("http", "https"):
            raise_code("CFG-601", field="url", url=url,
                       advice="Skill Registry 仅支持 http/https/file URL")
        chunks: list[bytes] = []
        size = 0
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
                async with client.stream("GET", url) as resp:
                    if resp.status_code >= 400:
                        raise_code("TLB-805", url=url, http_status=resp.status_code,
                                   advice="Skill Registry 下载失败")
                    async for chunk in resp.aiter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            raise_code("TLB-805", url=url, stage="size",
                                       advice=f"下载超过上限 {max_bytes} bytes")
                        chunks.append(chunk)
        except httpx.HTTPError as exc:
            raise_code("TLB-805", url=url, stage="transport",
                       cause=exc, advice="Skill Registry 网络失败")
        return b"".join(chunks)

    async def fetch_registry(self, url: str) -> dict:
        raw = await self._read_url(url, max_bytes=_MAX_REGISTRY_BYTES)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:  # noqa: BLE001
            raise_code("TLB-805", url=url, stage="registry",
                       cause=exc, advice="Skill Registry 不是合法 JSON")
        if not isinstance(data, dict) or not isinstance(data.get("skills"), list):
            raise_code("EVT-100", field="registry",
                       advice="Registry 必须包含 skills 数组")
        return data

    async def search(self, query: str, registry_url: str, *, limit: int = 20) -> list[dict]:
        data = await self.fetch_registry(registry_url)
        q = str(query or "").strip().lower()
        rows: list[dict] = []
        for skill in data.get("skills", []):
            if not isinstance(skill, dict):
                continue
            name = str(skill.get("name") or "")
            desc = str(skill.get("description") or "")
            tags = " ".join(str(x) for x in (skill.get("tags") or []))
            hay = f"{name} {desc} {tags}".lower()
            if q and q not in hay:
                continue
            versions = skill.get("versions") or []
            version_rows = [v for v in versions if isinstance(v, dict)
                            and v.get("version")]
            version_ids = [str(v.get("version")) for v in version_rows]
            latest = str(skill.get("latest") or
                         (version_ids[-1] if version_ids else ""))
            latest_entry = next((v for v in reversed(version_rows)
                                 if str(v.get("version")) == latest), {})
            rows.append({"name": name, "description": desc,
                         "tags": list(skill.get("tags") or []),
                         "latest": latest,
                         "versions": version_ids,
                         "sha256": str(latest_entry.get("sha256") or ""),
                         "source": str(latest_entry.get("source") or
                                       latest_entry.get("url") or registry_url)})
        return rows[:max(1, int(limit))]

    # ------------------------------------------------------------ install
    def _entry(self, registry: dict, name: str) -> dict:
        for row in registry.get("skills", []):
            if isinstance(row, dict) and str(row.get("name")) == str(name):
                return row
        raise_code("EVT-101", skill=name, advice="Registry 中不存在该 Skill")

    @staticmethod
    def _version_entry(entry: dict, version: Optional[str]) -> dict:
        versions = [v for v in (entry.get("versions") or []) if isinstance(v, dict)]
        if not versions:
            raise_code("EVT-100", field="versions", advice="Skill 没有可用版本")
        wanted = str(version or entry.get("latest") or versions[-1].get("version"))
        for row in versions:
            if str(row.get("version")) == wanted:
                return row
        raise_code("EVT-101", skill=entry.get("name"), version=wanted,
                   advice="Registry 中不存在该版本")

    @staticmethod
    def _sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _safe_extract(self, raw: bytes, target: Path) -> None:
        try:
            zf = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile as exc:                # noqa: BLE001
            raise_code("TLB-805", stage="unzip", cause=exc,
                       advice="Skill 包不是合法 zip")
        infos = zf.infolist()
        if len(infos) > self.max_files:
            raise_code("TLB-805", stage="unzip",
                       advice=f"Skill 包文件数超过上限 {self.max_files}")
        total = 0
        for info in infos:
            name = info.filename.replace("\\", "/")
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts:
                raise_code("TLB-805", stage="path",
                           advice=f"Skill 包包含非法路径:{name}")
            if info.flag_bits & 0x1:
                raise_code("TLB-805", stage="encrypted",
                           advice="不支持加密 Skill 包")
            total += int(info.file_size)
            if total > self.max_package_bytes:
                raise_code("TLB-805", stage="size",
                           advice=f"解压后大小超过上限 {self.max_package_bytes}")
        target.mkdir(parents=True, exist_ok=True)
        try:
            zf.extractall(target)
        except (OSError, zipfile.BadZipFile) as exc:     # noqa: BLE001
            raise_code("TLB-805", stage="unzip", cause=exc,
                       advice="Skill 包解压失败")
        finally:
            zf.close()

    @staticmethod
    def _find_skill_root(root: Path) -> Path:
        matches = list(root.rglob("SKILL.md"))
        if len(matches) != 1:
            raise_code("EVT-100", field="SKILL.md",
                       advice="Skill 包必须恰好包含一个 SKILL.md")
        return matches[0].parent

    @staticmethod
    def _frontmatter_name(skill_root: Path) -> str:
        from pyharness.core.skill import _parse_frontmatter
        text = (skill_root / "SKILL.md").read_text(encoding="utf-8")
        meta = _parse_frontmatter(text)
        if not meta or not _NAME_RE.fullmatch(str(meta.get("name") or "")):
            raise_code("EVT-100", field="SKILL.md",
                       advice="SKILL.md frontmatter 缺合法 name")
        return str(meta["name"])

    def _replace_active(self, name: str, version_dir: Path, version: str) -> None:
        active = self.skills_dir / name
        backup = self.versions_dir / name / self._current_version(name)
        if active.exists() and not backup.exists():
            try:
                shutil.copytree(active, backup)
            except OSError:                              # noqa: BLE001
                log.warning("active skill backup failed name=%s", name, exc_info=True)
        tmp = self.skills_dir / f".{name}.installing-{os.getpid()}"
        if tmp.exists():
            shutil.rmtree(tmp)
        shutil.copytree(version_dir, tmp)
        (tmp / ".pyharness-version").write_text(version, encoding="utf-8")
        if active.exists():
            shutil.rmtree(active)
        tmp.replace(active)

    def _current_version(self, name: str) -> str:
        marker = self.skills_dir / name / ".pyharness-version"
        try:
            return marker.read_text(encoding="utf-8").strip() or "unknown"
        except OSError:
            return "unknown"

    async def install(self, name: str, *, registry_url: str,
                      version: Optional[str] = None,
                      approved_by: Optional[str] = None) -> dict:
        if not approved_by:
            raise_code("GRD-401", reason="skill-install-approval",
                       advice="Skill 安装必须由人类显式批准")
        if not _NAME_RE.fullmatch(str(name or "")):
            raise_code("EVT-100", field="name", advice="Skill 名称格式非法")
        registry = await self.fetch_registry(registry_url)
        entry = self._entry(registry, name)
        selected = self._version_entry(entry, version)
        package_url = str(selected.get("url") or "")
        expected = str(selected.get("sha256") or "").lower()
        if not package_url or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise_code("EVT-100", skill=name, version=selected.get("version"),
                       advice="版本必须包含 url 与合法 sha256")
        raw = await self._read_url(package_url, max_bytes=self.max_package_bytes)
        actual = self._sha256(raw)
        if actual != expected:
            raise_code("GRD-401", reason="skill-hash-mismatch", skill=name,
                       expected=expected, actual=actual,
                       advice="SHA-256 不匹配，拒绝安装")
        version_id = str(selected.get("version"))
        with tempfile.TemporaryDirectory(prefix="skill-", dir=self.quarantine_dir) as td:
            extract_root = Path(td) / "extract"
            self._safe_extract(raw, extract_root)
            skill_root = self._find_skill_root(extract_root)
            fm_name = self._frontmatter_name(skill_root)
            if fm_name != name:
                raise_code("EVT-100", skill=name, actual=fm_name,
                           advice="Registry name 与 SKILL.md frontmatter 不一致")
            version_dir = self.versions_dir / name / version_id
            if version_dir.exists():
                shutil.rmtree(version_dir)
            version_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(skill_root, version_dir)
            self._replace_active(name, version_dir, version_id)
        return {"ok": True, "name": name, "version": version_id,
                "sha256": actual, "source": package_url,
                "approved_by": approved_by, "path": str(self.skills_dir / name)}

    def rollback(self, name: str, version: str, *, approved_by: Optional[str] = None) -> dict:
        if not approved_by:
            raise_code("GRD-401", reason="skill-rollback-approval",
                       advice="Skill 回滚必须由人类显式批准")
        version_dir = self.versions_dir / name / version
        if not version_dir.is_dir():
            raise_code("EVT-101", skill=name, version=version,
                       advice="目标版本不存在")
        previous = self._current_version(name)
        self._replace_active(name, version_dir, version)
        return {"ok": True, "name": name, "from_version": previous,
                "to_version": version, "approved_by": approved_by}

    def remove(self, name: str, *, approved_by: Optional[str] = None) -> dict:
        if not approved_by:
            raise_code("GRD-401", reason="skill-remove-approval",
                       advice="Skill 卸载必须由人类显式批准")
        active = self.skills_dir / name
        if not active.exists():
            return {"ok": False, "name": name, "removed": False}
        version = self._current_version(name)
        shutil.rmtree(active)
        return {"ok": True, "name": name, "removed": True,
                "version": version, "approved_by": approved_by}

    def versions(self, name: str) -> list[str]:
        root = self.versions_dir / name
        return sorted(p.name for p in root.iterdir() if p.is_dir()) if root.exists() else []


__all__ = ["SkillInstaller"]
