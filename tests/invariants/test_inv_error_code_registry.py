r"""不变量:错误码纪律 —— `raise_code` 的码必须已登记（Error-Code Registry）—— R19 收口。

**性质**:``raise_code(code, ...)`` 是**全系统唯一抛出入口**，但它对**未登记码**的行为是
**静默改写为 ``CYC-999``**（原码只留在 ``ctx["unknown_code"]``）。因此：

- 用 ``raise_code("某个没登记的码")`` ⇒ 调用方/LLM/HTTP 看到的是"未知内部错误"，
  **预期的语义码从未到达**（CONSTRAINTS-05：错误只回 code+advice，**禁现场造码**）；
- 更糟的是**同一语义条件在不同路径报不同码**（实测 R19：同一"会话忙"在 ACP 报
  ``BUSY``、在服务层报 ``CYC-999``；路径穿越在 ``tool_fs`` 报 ``GRD-401/POL-FS-2``、
  在 ``skill_registry`` 报 ``CYC-999``）。

**两类码要分清**：

| 类别 | 归属 | 用法 |
|---|---|---|
| **主域码**（``EVT-``/``PERS-``/``GRD-``/``TLB-``/``LLM-`` …） | `errors.py::ERRORS` | ``raise_code`` |
| **功能特性码 / 命名状态字面量**（``QUE-001``/``JOB-001``/``PTY-001``/``ATT-001``/``EDT-001``/``TO-301``/``BUSY``） | **ERR.md §2.11**（**故意不进** `ERRORS`） | **直接构造** ``PyHError(code)`` 保字面码 |

**负向**:任何 ``raise_code(<字面量>)`` 用到未登记码 ⇒ 本文件必须 RED。
"""
from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
PKG = ROOT / "pyharness"


def _py_files() -> list[pathlib.Path]:
    return sorted(p for p in PKG.rglob("*.py") if "__pycache__" not in p.parts)


def _raise_code_literals() -> list[tuple[str, int, str]]:
    """收集所有 `raise_code("<字面量>", ...)` 调用点。"""
    out: list[tuple[str, int, str]] = []
    for f in _py_files():
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            fn = n.func
            name = (fn.attr if isinstance(fn, ast.Attribute)
                    else fn.id if isinstance(fn, ast.Name) else None)
            if name != "raise_code" or not n.args:
                continue
            a0 = n.args[0]
            if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                out.append((f.relative_to(ROOT).as_posix(), n.lineno, a0.value))
    return out


# --------------------------------------------------------- ① 静态:码必须已登记
def test_every_literal_raise_code_is_registered():
    """所有 ``raise_code("<字面量>")`` 的码都必须在 `ERRORS` 中登记。"""
    from pyharness.errors import ERRORS

    unregistered = [(f, ln, c) for f, ln, c in _raise_code_literals()
                    if c not in ERRORS]
    assert unregistered == [], (
        "以下 raise_code 用了未登记码 ⇒ 会被**静默改写为 CYC-999**、预期语义码丢失；"
        "若属功能特性码/命名状态字面量(ERR.md §2.11)请改为直接构造 PyHError:"
        f"{unregistered}")


# --------------------------------------------------------- ② 行为:改写语义
def test_unregistered_code_is_rewritten_to_cyc999():
    """钉住 `raise_code` 的改写语义（本防线存在的理由）。"""
    import pytest

    from pyharness.errors import PyHError, raise_code

    with pytest.raises(PyHError) as e:
        raise_code("NOT-A-REAL-CODE", note="probe")
    assert e.value.code == "CYC-999"
    assert e.value.ctx.get("unknown_code") == "NOT-A-REAL-CODE"


# --------------------------------------------------------- ③ 约定:直接构造保字面码
def test_feature_codes_are_constructed_directly_and_keep_literal_code():
    """功能特性码必须**直接构造**并保住字面码（不许经 raise_code）。"""
    import pytest

    from pyharness.core.attachment import reject_attachment
    from pyharness.errors import PyHError

    with pytest.raises(PyHError) as e:
        reject_attachment("mime-not-allowed", name="x.png")
    assert e.value.code == "ATT-001", (
        "ATT-001 是 ERR.md §2.11 功能特性码(errors.py 故意不登记)，"
        "必须直接构造以保住字面码")
