"""不变量:LLM 出网必须只有唯一汇点（Single Egress Chokepoint）—— R16 收口。

**性质**:`LLMClient._egress_guard` 把**会话级预算硬闸**下沉到唯一汇点
`_chat_any`，并在 docstring 的 negative space 里声明：

> ``OpenAICompatAdapter.chat`` / ``.chat_stream``:适配器层…已核验**全库无生产代码
> 直接调用**（CI 静态闸扫此），唯一路径 = 本类 ``_chat_any`` ⇒ 已被本闸覆盖。

**R16-1**:该"CI 静态闸"**此前并不存在**（全库无任何测试扫描此事）⇒ 出网"唯一汇点"
这一**安全性质**没有自动防线：任何一处新写的 ``adapter.chat(...)`` 都会**静默绕过
会话预算**，且不会让任何用例变红。本文件即补上该闸。

**负向**:在 ``pyharness/`` 任意非白名单模块里引用具体适配器类 ⇒ 本文件必须 RED。
"""
from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
PKG = ROOT / "pyharness"

# 具体适配器（带 chat/chat_stream 出网面）允许出现的位置：
#   定义处 —— core/llm.py；注册处 —— engine.py（构造并 register_adapter）
_ADAPTER_CLASS = "OpenAICompatAdapter"
_ADAPTER_ALLOWED = {"pyharness/core/llm.py", "pyharness/engine.py"}


def _py_files() -> list[pathlib.Path]:
    return sorted(p for p in PKG.rglob("*.py") if "__pycache__" not in p.parts)


def test_concrete_adapter_referenced_only_at_definition_and_registration():
    """`OpenAICompatAdapter` 只允许出现在定义处与注册处（出网唯一汇点防线）。"""
    refs = sorted(f.relative_to(ROOT).as_posix() for f in _py_files()
                  if _ADAPTER_CLASS in f.read_text(encoding="utf-8"))
    assert set(refs) <= _ADAPTER_ALLOWED, (
        "具体适配器类只应在 llm.py（定义）与 engine.py（注册）出现；"
        "其他模块直接持有它意味着可以**绕过 _chat_any 的预算闸**出网:"
        f"{sorted(set(refs) - _ADAPTER_ALLOWED)}")


def test_adapter_invocation_is_single_dynamic_dispatch_point():
    """`core/llm.py` 内对适配器实例的调用**只允许一处**（`_chat_any` 的动态派发）。

    适配器经 `getattr(inst, method)(...)` 单一动态派发调用；副本一旦出现，就多出
    一条**不经 `_egress_guard`** 的出网路径。
    """
    src = (PKG / "core" / "llm.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    dispatch: list[int] = []
    for node in ast.walk(tree):
        # 形态:getattr(<inst>, <method>)(...)
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Call)
                and isinstance(node.func.func, ast.Name)
                and node.func.func.id == "getattr"):
            dispatch.append(node.lineno)
    assert len(dispatch) == 1, (
        "对适配器实例的动态派发必须唯一（_chat_any）；"
        f"发现 {len(dispatch)} 处:行号 {dispatch}")


def test_every_public_exit_routes_through_chat_any():
    """五个公开出口（chat/chat_stream/mini/summarize/json_chat）都经 `_chat_any`。

    判据:这些方法的函数体里必须出现对 `self._chat_any` 的调用。
    """
    src = (PKG / "core" / "llm.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == "LLMClient")
    exits = {"chat", "chat_stream", "mini", "summarize", "json_chat"}
    found: dict[str, bool] = {}
    for fn in cls.body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if fn.name not in exits:
            continue
        found[fn.name] = any(
            isinstance(c, ast.Call)
            and isinstance(c.func, ast.Attribute)
            and c.func.attr == "_chat_any"
            for c in ast.walk(fn)
        )
    assert set(found) == exits, f"未找到全部公开出口定义:{sorted(exits - set(found))}"
    assert all(found.values()), (
        "以下公开出口未走 _chat_any ⇒ 可绕过会话预算闸:"
        f"{sorted(k for k, v in found.items() if not v)}")
