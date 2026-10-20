"""pyharness/core/skill.py — 技能系统内核(F073/#31 Phase 1,本地版)。

Hermes 同款机制(薄版):技能库根(skills/)下每个技能一个文件夹,内含 SKILL.md
(frontmatter: name/description + 正文)。引擎装配时 scan() 建索引 → 目录
(名字+一句话)注入 system prompt;agent 判断任务匹配 → 调 skill.load 工具 →
正文读入上下文(长正文截断,references 惰性加载属 Phase 2)。

事件:skill.load 由 tool_skill 落 skill.used(审计);正文不进事件日志(防刷)。
信任模型:本地技能=与插件同级信任(仓库内文本);网络技能源(Phase 2)需 hash 校验。
"""
from __future__ import annotations

import logging
import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Optional

from pyharness.errors import raise_code

log = logging.getLogger("pyharness.skill")

FRONT_RE = re.compile(r"^---\s*\n(.*?)^---\s*$", re.S | re.M)
_NAME_RE = re.compile(r"^\s*name:\s*([A-Za-z0-9._-]+)\s*$", re.M)
_DESC_RE = re.compile(r"^\s*description:\s*(.+?)\s*$", re.M)
_BODY_CAP = 6000          # 单次装载正文上限(超出截断,防上下文爆炸)


def _parse_frontmatter(text: str) -> Optional[dict]:
    """取 SKILL.md 头 frontmatter → {name, description};无/坏 → None。"""
    m = FRONT_RE.search(text)
    if not m:
        return None
    head = m.group(1)
    nm = _NAME_RE.search(head)
    if not nm:
        return None
    desc = _DESC_RE.search(head)
    return {"name": nm.group(1),
            "description": (desc.group(1).strip().strip('"\'')
                            if desc else "")}


class SkillManager:
    """技能库索引/装载器:scan() → list() 目录 / load(name) 正文 / render_catalog()。

    纯只读文本操作;会话事件(skill.used)由工具面经 session.append 落盘。
    """

    def __init__(self, roots, *, session: Any = None, bus: Any = None) -> None:
        self.roots: list[Path] = [Path(r) for r in roots]
        self._session = session
        self._bus = bus
        self._index: dict[str, dict] = {}
        self._source_hashes: dict[str, str] = {}
        self._frozen = False
        self._loaded_views: dict[str, dict] = {}
        self.scan()

    # ------------------------------------------------------------ 索引
    def scan(self) -> int:
        """重建索引(启动/热重载调用);格式坏或重复名的技能跳过并记日志。"""
        if self._frozen:
            return len(self._index)
        from pyharness.core.skill_registry import _safe_under
        from pyharness.errors import PyHError
        found: dict[str, dict] = {}
        hashes: dict[str, str] = {}
        for root in self.roots:
            if not root.exists():
                continue
            for md in sorted(root.glob("*/SKILL.md")):
                try:
                    target = _safe_under(root, md.parent.name, 'SKILL.md')
                    raw = target.read_bytes()
                    if _safe_under(root, md.parent.name, 'SKILL.md') != target:
                        continue
                    # Preserve the original read_text universal-newline view;
                    # retain the physical bytes separately for source identity.
                    text = raw.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n')
                except (OSError, UnicodeError, PyHError) as e:
                    log.warning("skill read failed %s: %s", md, e)
                    continue
                meta = _parse_frontmatter(text)
                if not meta:
                    log.warning("skill skip(坏 frontmatter): %s", md)
                    continue
                name = meta["name"]
                if name in found:
                    log.warning("skill 重名跳过: %s (%s)", name, md)
                    continue
                found[name] = {
                    "name": name,
                    "description": meta["description"],
                    "dir": str(md.parent),
                    "body": self._strip_front(text),
                }
                hashes[name] = hashlib.sha256(raw).hexdigest()
        self._index = found
        self._source_hashes = hashes
        return len(found)

    def frozen_copy(self, names=None):
        """Private loaded index copy; explicit classic scans remain available."""
        clone = SkillManager([], session=self._session, bus=self._bus)
        chosen = list(self._index) if names is None else list(names)
        for name in chosen:
            self.load(name)  # Original unknown-name contract.
        clone._index = {name: copy.deepcopy(self._index[name]) for name in chosen}
        clone._source_hashes = {name: self._source_hashes[name] for name in chosen}
        clone._loaded_views = {name: copy.deepcopy(self._loaded_views[name])
                               for name in chosen if name in self._loaded_views}
        clone._frozen = True
        return clone

    @classmethod
    def from_loaded_snapshot(cls, snapshot):
        """Restore a server-owned bounded load view, without rereading its path."""
        clone = cls([])
        name = snapshot['name']
        view = {k: snapshot[k] for k in ('name', 'description', 'dir', 'body')}
        clone._index[name] = copy.deepcopy(view)
        clone._source_hashes[name] = snapshot['source_sha256']
        clone._loaded_views[name] = copy.deepcopy(view)
        clone._frozen = True
        if clone.snapshot(name)['loaded_sha256'] != snapshot['loaded_sha256']:
            raise_code('SKL-901', name=name, advice='固定技能视图校验失败')
        return clone

    def snapshot(self, name: str) -> dict:
        """Describe exact loaded text separately from complete source identity."""
        loaded = self.load(name)
        raw = json.dumps({k: loaded[k] for k in ('name', 'description', 'body')},
                         ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
        return {**loaded, 'source_sha256': self._source_hashes[name],
                'loaded_sha256': hashlib.sha256(raw).hexdigest(),
                'truncated': len(self._index[name]['body']) > _BODY_CAP,
                'frozen': self._frozen}

    @staticmethod
    def _strip_front(text: str) -> str:
        m = FRONT_RE.match(text)
        body = text[m.end():] if m else text
        return body.strip()

    # ------------------------------------------------------------ 读面
    def list(self) -> list[dict]:
        """技能目录(名字+一句话),按名排序;目录注入/工具面共用。"""
        return [{"name": m["name"], "description": m["description"]}
                for m in sorted(self._index.values(), key=lambda x: x["name"])]

    def get(self, name: str) -> Optional[dict]:
        return self._index.get(name)

    def load(self, name: str) -> dict:
        """装载技能全文;未知名 → SKL-901(advice 指向 skill.list)。"""
        if name in self._loaded_views:
            return copy.deepcopy(self._loaded_views[name])
        m = self._index.get(name)
        if m is None:
            raise_code("SKL-901", name=name,
                       advice="技能未找到;先 skill.list 看可用技能清单")
        body = m["body"]
        if len(body) > _BODY_CAP:
            body = body[:_BODY_CAP] + "\n…(正文超长截断)"
        return {"name": m["name"], "description": m["description"],
                "dir": m["dir"], "body": body}

    def render_catalog(self) -> str:
        """目录段文本(system prompt 注入;空技能库返回空串零开销)。"""
        rows = [f"- {m['name']}: {m['description']}"
                for m in self.list()]
        if not rows:
            return ""
        return ("可用技能目录(任务匹配某技能时,调 skill.load 装载后按技能执行):\n"
                + "\n".join(rows))


__all__ = ["SkillManager"]
