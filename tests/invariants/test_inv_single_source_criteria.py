r"""不变量:横切判据必须只有一份实现（Single-Source Criteria）—— R14 收口。

**性质**:R14 轮修掉的三类缺陷都源于"同一横切判据在多处各自手写"，且其中若干处以
"看起来对、实际恒真/漏项"的形式失效。收口动作（判据连同**枚举点**抽成唯一函数）
必须**静态锁死**，否则第 7 份副本会再次出现：

1. **会话枚举**:`stem.startswith("s-")` 作为"是否会话"的判据只有一份
   （`persistence.session_log_paths`）。它**对辅助档恒为真**（`{sid}.1.jsonl` /
   `{sid}.corrupt-*` 的 stem 同样以 `s-` 开头）⇒ 任何别处再用它就是同款漏判据
   （R14-3 实测：search 崩 PERS-202 / 桌面列表混入备份 / 用量重复计）。
2. **空洞合法化**:`seq-holes:[…]` 的解析**正则**只有一份
   （`events/envelope.py::declared_ranges`）。四处消费方全部委托（R14-9）。
3. **修复入口**:生产代码不得调用旧面 `SessionStore.repair()`
   （备份名 `.jsonl.bak-` 与 F060 的 `.corrupt-` 不同、生产零调用者；见 L-27）。
   R14-15 起写通道恢复由 `flush()` 承担，不再依赖它。

**负向**:任一处重新内联上述判据 ⇒ 本文件必须 RED。
"""
from __future__ import annotations

import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PKG = ROOT / "pyharness"

_SESSION_GUARD = "s-"          # "是否会话"的命名前缀判据
_SEQHOLES_TOKEN = "^seq-holes:"  # 空洞声明的解析正则起点


def _py_files() -> list[pathlib.Path]:
    return sorted(p for p in PKG.rglob("*.py") if "__pycache__" not in p.parts)


# ------------------------------------------------------- ① 会话枚举判据唯一
def test_session_guard_lives_in_exactly_one_place():
    """`stem.startswith("s-")` 只允许出现在 `persistence.session_log_paths`。"""
    hits: list[str] = []
    for f in _py_files():
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "startswith"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value == _SESSION_GUARD):
                hits.append(f"{f.relative_to(ROOT).as_posix()}:{node.lineno}")
    assert len(hits) == 1 and hits[0].startswith("pyharness/persistence.py:"), (
        '`startswith("s-")` 只应是 persistence.session_log_paths 的**唯一**判据；'
        f"其他位置复现即为同款漏判据(R14-3):{hits}")


def test_aux_predicate_has_exactly_one_implementation():
    """辅助档判据的**正则表**只有一份（`persistence.AUX_SESSION_PATTERNS`）。"""
    owners = [f.relative_to(ROOT).as_posix() for f in _py_files()
              if "AUX_SESSION_PATTERNS" in f.read_text(encoding="utf-8")
              and "def is_aux_session_file" in f.read_text(encoding="utf-8")]
    assert owners == ["pyharness/persistence.py"], owners


# ------------------------------------------------------- ② 空洞判据唯一
def test_seq_holes_regex_lives_in_exactly_one_place():
    """`seq-holes:[…]` 的**解析正则**只允许存在于 `events/envelope.py`。"""
    owners = [f.relative_to(ROOT).as_posix() for f in _py_files()
              if _SEQHOLES_TOKEN in f.read_text(encoding="utf-8")]
    assert owners == ["pyharness/events/envelope.py"], (
        "声明解析正则必须单源；消费者应委托 events.declared_ranges(R14-9):"
        f"{owners}")


@pytest.mark.parametrize("kind,payload", [
    ("context.compacted", {"ranges": [[3, 5]], "summary": "s"}),
    ("session.recovered", {"lost": [7]}),
    ("session.recovered", {"fixed": ["tail-truncated", "seq-holes:[9, 10]"]}),
    ("user.message", {"content": "无关事件"}),
])
def test_declared_ranges_consumers_all_delegate(kind, payload):
    """三个消费方对同一事件的判定必须**逐字相同**（判据单源的运行时证据）。"""
    from pyharness.events import Envelope, declared_ranges
    from pyharness.governance.audit import AuditSystem
    from pyharness.repair import _declared_ranges

    env = Envelope(seq=1, ts="2026-09-07T06:00:00.000000Z", type=kind,
                   session_id="s-invss-0001", actor="system", payload=payload)
    expected = declared_ranges(env)
    assert _declared_ranges(env) == expected, "repair 必须委托唯一判据"
    assert AuditSystem._declared_holes([env]) == expected, \
        "治理对账必须委托唯一判据"


# ------------------------------------------------------- ③ 修复入口唯一
def test_production_never_calls_legacy_store_repair():
    """生产代码不得调用 `store.repair()`（旧面,备份名与 F060 不同,见 L-27）。

    R14-15 起写通道恢复由成功 `flush()` 承担,不再需要该旧面。
    """
    offenders: list[str] = []
    for f in _py_files():
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "repair"):
                offenders.append(f"{f.relative_to(ROOT)}:{node.lineno}")
    assert offenders == [], (
        "生产路径不得调用旧面 SessionStore.repair()(备份名/F060 入口不一致);"
        f"需要修复请用 pyharness.repair.repair_session:{offenders}")
