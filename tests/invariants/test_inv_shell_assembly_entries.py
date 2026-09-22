r"""不变量:各外壳的引擎装配入口必须与文档一致（Shell Assembly Entries）—— R18 收口。

**性质**:本仓库有**三条**装配路径，各自服务不同调用方：

| 入口 | 调用方 | 形态 |
|---|---|---|
| ``engine.attach_engine_to_ctx`` | **CLI / ACP** | 把真实引擎接进轻量门面 ctx |
| ``engine.build_runner_components`` | **Desktop / 服务层** | per-session 懒装配（复用 manager 已 open 的 log_/bus/store） |
| ``engine.assemble_real_engine`` | **仅 scripts/（demo / probe / e2e）** | 一键真实引擎，``pyharness/`` 内**零调用者** |

**R18-2**:``assemble_real_engine`` 的 docstring 曾称 "desktop create_message 用"，
而 desktop 实际走 ``build_runner_components``（F-08 已登记"死装配"，但 docstring
一直未更正）⇒ 读者会以为生产走的是那条路径。本文件把"谁走哪条"钉死，防再漂移。

**负向**:生产模块改走 demo 入口、或某个外壳丢了它自己的装配调用 ⇒ 本文件必须 RED。
"""
from __future__ import annotations

import ast
import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[2]
PKG = ROOT / "pyharness"

# 外壳 → 其装配入口符号必须出现的文件（生产代码）
_SHELL_ENTRY = {
    "pyharness/cli.py": "attach_engine_to_ctx",
    "pyharness/acp.py": "attach_engine_to_ctx",
    "pyharness/application/service.py": "build_runner_components",
    "pyharness/desktop/app.py": "build_runner_components",
}
_DEMO_ENTRY = "assemble_real_engine"


def _py_files() -> list[pathlib.Path]:
    return sorted(p for p in PKG.rglob("*.py") if "__pycache__" not in p.parts)


def _call_lines(path: pathlib.Path, name: str) -> list[int]:
    """该文件里对 `name(...)` 的调用行号（AST，排除定义行）。"""
    src = path.read_text(encoding="utf-8")
    out: list[int] = []
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == name):
            out.append(node.lineno)
        elif (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name) and node.func.id == name):
            out.append(node.lineno)
    return out


def test_demo_entry_is_not_used_by_production():
    """`assemble_real_engine` 是**演示/探针**入口：生产模块不得调用它。"""
    offenders: dict[str, list[int]] = {}
    for f in _py_files():
        if f.name == "engine.py":            # 定义处
            continue
        lines = _call_lines(f, _DEMO_ENTRY)
        if lines:
            offenders[f.relative_to(ROOT).as_posix()] = lines
    assert offenders == {}, (
        "生产代码不得走 demo 装配入口 assemble_real_engine"
        "（生产外壳分别走 attach_engine_to_ctx / build_runner_components）:"
        f"{offenders}")


def test_each_shell_uses_its_declared_entry():
    """每个外壳都必须出现**自己那一条**装配入口调用（丢了即该壳装配断链）。"""
    missing: list[str] = []
    for rel, entry in _SHELL_ENTRY.items():
        f = ROOT / rel
        assert f.exists(), f"外壳文件缺失:{rel}"
        if not _call_lines(f, entry):
            missing.append(f"{rel} 未调用 {entry}")
    assert missing == [], f"外壳装配入口缺失:{missing}"


def test_demo_entry_docstring_does_not_claim_production_use():
    """`assemble_real_engine` 的**摘要行**不得声称被生产外壳使用（R18-2）。

    只查摘要行：正文里可以（也应当）**引用**旧的错误声明作为更正记录。
    """
    from pyharness.engine import assemble_real_engine

    doc = (assemble_real_engine.__doc__ or "").strip()
    summary = doc.splitlines()[0] if doc else ""
    for claim in ("desktop create_message 用", "生产外壳走本函数", "desktop 用"):
        assert claim not in summary, f"摘要行仍含不实用法声明:{claim!r} → {summary!r}"
    assert "演示" in summary or "探针" in summary or "scripts" in summary, (
        f"摘要行必须如实说明它只服务 scripts/（demo/probe/e2e）:{summary!r}")
    assert "零调用者" in doc or "脚本" in doc or "demo" in doc.lower(), (
        "docstring 正文必须交代它在 pyharness/ 内零调用者")
