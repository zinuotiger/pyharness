"""技能系统内核单测 — 契约:core/skill.py + tool_skill.py + engine 装配(F073/#31)。

覆盖:frontmatter 解析(name/description/剥正文)、扫描计数、list 排序、load
正文、SKL-901 未知名、render_catalog 目录段、engine 装配(仓库 skills/ 目录
真实两技能可见 + skill.load/list 注册 + ctx.skills 绑定)、词表 skill.used 注册。
"""
from __future__ import annotations

import asyncio
import re

import pytest

from pyharness.config import load_settings
from pyharness.core.skill import SkillManager
from pyharness.events.vocab import EVENT_TYPES


def _write_skill(base, name: str, description: str, body: str) -> None:
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}",
        encoding="utf-8")


def test_scan_parse_and_load(tmp_path):
    _write_skill(tmp_path, "code-review", "用 22 维框架审查代码",
                 "# 审查\n1. 先读文件\n2. 逐维过")
    _write_skill(tmp_path, "alpha", "第一个技能", "body-a")
    # 坏 frontmatter 文件应被跳过不炸
    bad = tmp_path / "broken" / "SKILL.md"
    bad.parent.mkdir()
    bad.write_text("没有 frontmatter 的普通 md", encoding="utf-8")

    mgr = SkillManager([tmp_path])
    assert mgr.scan() == 2
    names = [m["name"] for m in mgr.list()]
    assert names == ["alpha", "code-review"], "list 按名排序"

    data = mgr.load("code-review")
    assert data["description"] == "用 22 维框架审查代码"
    assert data["body"].startswith("# 审查"), "正文已剥 frontmatter"
    assert "name: code-review" not in data["body"]


def test_load_unknown_raises_skl901(tmp_path):
    _write_skill(tmp_path, "ok", "d", "b")
    mgr = SkillManager([tmp_path])
    with pytest.raises(Exception) as ei:
        mgr.load("nope")
    assert "SKL-901" in str(ei.value)


def test_render_catalog_and_empty(tmp_path):
    mgr = SkillManager([tmp_path])          # 空目录
    assert mgr.render_catalog() == ""
    _write_skill(tmp_path, "x", "技能 X 一句话", "b")
    mgr.scan()
    txt = mgr.render_catalog()
    assert "x" in txt and "技能 X 一句话" in txt and "skill.load" in txt


def test_skill_used_event_registered():
    assert "skill.used" in EVENT_TYPES, "词表须含 skill.used(F073)"


def test_engine_assembles_skills_and_tools(tmp_path):
    """真实引擎装配:仓库 skills/ 两技能进索引;skill.load/list 注册;ctx 绑定。"""
    from pyharness.engine import build_spine

    cfg = load_settings()
    cfg.storage.sessions_dir = str(tmp_path / "sessions")
    cfg.storage.db_path = str(tmp_path / "ph.db")
    spine = asyncio.run(build_spine(
        cfg, sid="s-skill-test",
        sessions_dir=tmp_path / "sessions",
        bus=None))
    try:
        mgr = getattr(spine, "skills", None)
        assert mgr is not None and mgr.scan() >= 2, \
            "仓库 skills/ 至少 interview-pitch + demo-script 两技能"
        names = {m["name"] for m in mgr.list()}
        assert {"interview-pitch", "demo-script"}.issubset(names)
        cat = mgr.render_catalog()
        assert "interview-pitch" in cat
        # 工具可见面:skill.load/list strict 下恒可见(SELF_DOMAINS skill 域)
        vis = {((s or {}).get("function") or {}).get("name")
               for s in spine.tools.schemas_for(spine.scope)}
        assert {"skill.load", "skill.list"}.issubset(vis), \
            f"skill 工具应可见;实际缺 {set(vis)}"
    finally:
        asyncio.run(spine.registry.close()) if hasattr(
            spine.registry, "close") else None
