"""外壳 ↔ 服务契约漂移检测(**无需 GUI**)。

GAP-3 的 headless 边界:PySide6 是可选依赖,CI/无 GUI 环境下原生壳无法实例化。
但"外壳调用了 ApplicationService 上不存在的方法"是一类**真实且常见**的缺陷,
它可以用 **AST 静态分析**在任何环境下检出——不需要启动 Qt。

本模块回答两个问题(纯静态,零运行时依赖):

1. ``controller.py`` / ``desktop/app.py`` 里每一次 ``self.service.X(...)`` 调用,
   ``X`` 在 ``ApplicationService`` 上真的存在且可调用吗?
2. ``main_window.py`` 里每一次 ``self.controller.X(...)`` 调用,``X`` 在
   ``NativeController`` 上真的定义了吗?

顺带钉死 GAP-7/GAP-8 的读面不会在任一侧被悄悄摘掉。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from pyharness.application import ApplicationService

ROOT = Path(__file__).resolve().parents[2]


def _service_calls(path: Path) -> set[str]:
    """收集 ``self.service.<name>(...)`` 形式的属性名。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not isinstance(f, ast.Attribute):
            continue
        owner = f.value
        if (isinstance(owner, ast.Attribute)
                and isinstance(owner.value, ast.Name)
                and owner.value.id == "self"
                and owner.attr == "service"):
            out.add(f.attr)
    return out


def _controller_calls(path: Path) -> set[str]:
    """收集 ``self.controller.<name>(...)`` 形式的属性名。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not isinstance(f, ast.Attribute):
            continue
        owner = f.value
        if (isinstance(owner, ast.Attribute)
                and isinstance(owner.value, ast.Name)
                and owner.value.id == "self"
                and owner.attr == "controller"):
            out.add(f.attr)
    return out


def _method_names(path: Path, class_name: str) -> set[str]:
    """收集某类内定义的方法/属性名(含 async)。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {n.name for n in node.body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    return set()


NATIVE_CONTROLLER = ROOT / "pyharness/desktop_native/controller.py"
NATIVE_WINDOW = ROOT / "pyharness/desktop_native/main_window.py"
WEB_APP = ROOT / "pyharness/desktop/app.py"


@pytest.mark.parametrize("path", [NATIVE_CONTROLLER, WEB_APP])
def test_every_service_call_exists_on_application_service(path: Path):
    """外壳里 ``self.service.X()`` 的每个 X 都必须在 ApplicationService 上可调用。"""
    calls = _service_calls(path)
    assert calls, f"{path.name} 未检出任何 service 调用(解析失效?)"
    missing = sorted(n for n in calls
                     if not callable(getattr(ApplicationService, n, None)))
    assert not missing, f"{path.name} 调用了 ApplicationService 上不存在的成员: {missing}"


def test_native_main_window_only_calls_existing_controller_methods():
    """原生窗口里 ``self.controller.X()`` 的每个 X 都必须在 NativeController 上定义。"""
    calls = _controller_calls(NATIVE_WINDOW)
    assert calls, "main_window 未检出任何 controller 调用(解析失效?)"
    defined = _method_names(NATIVE_CONTROLLER, "NativeController")
    assert defined, "未解析到 NativeController 方法表(AST 失效?)"
    missing = sorted(calls - defined)
    assert not missing, f"main_window 调用了 NativeController 上不存在的方法: {missing}"


def test_governance_read_surfaces_present_on_both_shells():
    """GAP-7/GAP-8 的读面必须两侧都在:服务方法存在 + 外壳确实调用它。"""
    for name in ("governance_audit", "governance_evidence"):
        assert callable(getattr(ApplicationService, name, None)), \
            f"ApplicationService 缺 {name}"
    # 原生壳确实调用(不是"定义了但没人用")
    assert "governance_audit" in _service_calls(NATIVE_CONTROLLER)
    assert "governance_evidence" in _service_calls(NATIVE_CONTROLLER)
    # Web 壳经路由层转发
    assert "governance_audit" in _service_calls(WEB_APP)


def test_native_controller_exposes_governance_read_surfaces():
    """NativeController 自己也要暴露这两个读面(方法表静态可查,无需 PySide6)。"""
    defined = _method_names(NATIVE_CONTROLLER, "NativeController")
    assert {"governance_audit", "governance_evidence"} <= defined
