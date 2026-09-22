r"""不变量:文档声明的锚点值必须等于代码默认值（Doc Anchors == Config）—— R20 收口。

**性质**:本仓库的文档漂移是**反复出现**的缺陷类别（N9 计数漂移、R16-2 invariant 计数、
R18-2 docstring 谎称用途、R20 `max_turns` 默认值在 **3 份约束文档**里写成 10 而代码是 30）。
其中 ``docs/CFG.md`` 的"默认值"列是**唯一权威**（CFG §1：L1 代码默认值=唯一权威），
``docs/CONSTRAINTS-*.md`` 是**硬约束**（CLAUDE.md §1 读取优先级）—— 这两处写错会让
实现者照错的规格写代码/写测试。

**本文件把三件事实钉在一起**：``config.DEFAULTS``（代码真值）↔ ``CFG.md`` 表格锚点
（76 行，逐行解析）↔ 约束文档里的数字声明。

**负向**:改动任一锚点的代码默认值而不同步文档（或反之）⇒ 本文件必须 RED。
"""
from __future__ import annotations

import pathlib
import re
from typing import Any, Optional

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CFG = ROOT / "docs" / "CFG.md"
CONSTRAINTS = sorted((ROOT / "docs").glob("CONSTRAINTS-*.md"))

# CFG §3 表格行：| `dotted.key` | int|float|str|bool | `值` | ...
_ROW = re.compile(
    r"^\| `([a-z_][a-z0-9_.]*)` \| (?:int|float|str|bool) \| `([^`]*)` \|", re.M)
# 约束文档里的轮数上限声明（R20 实测漂移 4 处）
_MAX_TURNS_CLAIM = re.compile(r"最大轮数[^。\n|]*?默认\s*(\d+)")


def _resolve(key: str) -> Optional[Any]:
    """按点分路径解析默认值：先查 `config.DEFAULTS`，再查 **`Settings` 模型树**。

    R22：只查 `DEFAULTS` 会**漏掉模型独有的默认**（如 `security.policy.preset` 定义在
    `Settings` 的字段上、不在 `DEFAULTS` 字面量里）⇒ 本闸会对合法引用报假阳。两者
    并查才与"配置键是否存在"这一事实对齐。
    """
    from pyharness.config import DEFAULTS

    cur: Any = DEFAULTS
    ok = True
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            ok = False
            break
        cur = cur[part]
    if ok:
        return cur
    return _resolve_in_model(key)


def _resolve_in_model(key: str) -> Optional[Any]:
    """在 `Settings`（pydantic 模型）的嵌套注解里解析该键；不存在 → None。"""
    from pyharness.config import Settings

    from pydantic import BaseModel

    node: Any = Settings
    for part in key.split("."):
        if not (isinstance(node, type) and issubclass(node, BaseModel)):
            return None
        field = node.model_fields.get(part)
        if field is None:
            return None
        node = field.annotation
    return "<model-default>" if (isinstance(node, type)
                                 and issubclass(node, BaseModel)) else node


def _norm(v: Any) -> str:
    """文档字面量 ↔ 代码值的归一（bool 小写；空串 `""` 与 `` 等价）。"""
    if v is True:
        return "true"
    if v is False:
        return "false"
    s = str(v).strip()
    if s == '""':                       # 文档里空串写作 `""`
        return ""
    return s


def test_cfg_table_defaults_match_config_defaults():
    """`docs/CFG.md` 的"默认值"列（L1 唯一权威）必须逐行等于 `config.DEFAULTS`。"""
    rows = _ROW.findall(CFG.read_text(encoding="utf-8"))
    assert len(rows) >= 50, f"CFG 锚点表解析行数异常（表格被改格式？）:{len(rows)}"

    drift: list[tuple[str, str, str]] = []
    unresolved: list[str] = []
    for key, doc_value in rows:
        code = _resolve(key)
        if code is None:
            unresolved.append(key)
            continue
        if _norm(code) != _norm(doc_value):
            drift.append((key, doc_value, _norm(code)))
    assert drift == [], (
        "CFG.md 默认值列与 config.DEFAULTS 不一致（L1 唯一权威被破坏）:"
        f"{drift}")
    assert unresolved == [], (
        f"CFG 声明的键在 config.DEFAULTS 里不存在（死声明）:{unresolved}")


def test_constraint_docs_state_the_real_max_turns_default():
    """约束文档里的"最大轮数默认 N"必须等于 `loop.max_turns` 真值。

    R20 实测：`CONSTRAINTS-02/07/08` 三份文档四处写"默认 10"，而
    `CFG.md §3.1` / `PRD-Core.md §5.2` / `PARAMETER-ANCHOR.md` / `config.py`
    **四处一致为 30**。
    """
    real = _norm(_resolve("loop.max_turns"))
    seen: list[tuple[str, str]] = []
    for f in CONSTRAINTS:
        for m in _MAX_TURNS_CLAIM.finditer(f.read_text(encoding="utf-8")):
            seen.append((f.name, m.group(1)))
    assert seen, "约束文档里找不到轮数上限声明（本闸失去判别力）"
    wrong = [(n, v) for n, v in seen if v != real]
    assert wrong == [], (
        f"约束文档的轮数上限默认值必须是 {real}（CFG §3.1 L1 权威）:{wrong}")


@pytest.mark.parametrize("doc_key,config_key", [
    ("loop.max_turns", "loop.max_turns"),
    ("storage.spill.max_per_file_bytes", "storage.spill.max_per_file_bytes"),
    ("log.jsonl.flush_batch", "log.jsonl.flush_batch"),
    ("log.jsonl.flush_interval_s", "log.jsonl.flush_interval_s"),
    ("storage.jsonl.rotate_bytes", "storage.jsonl.rotate_bytes"),
])
def test_key_anchors_are_resolvable_and_nonempty(doc_key, config_key):
    """关键锚点必须真存在于 DEFAULTS（防"文档写了、代码没有"的死声明）。"""
    assert _resolve(config_key) is not None, f"锚点不存在:{config_key}"


# 配置段前缀（= DEFAULTS 的顶级段）→ 用它把"配置键引用"与"事件名/文件名"区分开
def _config_sections() -> tuple:
    from pyharness.config import DEFAULTS

    return tuple(DEFAULTS.keys())


def test_constraint_docs_reference_only_real_config_keys():
    """约束文档里出现的 `配置段前缀.xxx` 引用必须真能在 DEFAULTS 里解析。

    R21 实测：**10 处失真**跨 4 份文档（`llm.max_turns` / `llm.budget.per_task_cny` /
    `llm.cost.model_prices` / `llm.retry.max_attempts` / `llm.timeout.connect` /
    `llm.timeout.read` / `llm.budget.per_task`）—— CONSTRAINTS-*.md 是 CLAUDE.md §1
    明列的**硬约束**读取源，照它写配置会撞 `CFG-601` 或静默不生效。
    文件名（`config.py` / `config.yaml`）不属键引用，按后缀排除。
    """
    sections = _config_sections()
    pat = re.compile(r"(?<![\w`])((?:%s)(?:\.[a-z0-9_]+)+)"
                     % "|".join(sorted(map(re.escape, sections), key=len, reverse=True)))
    dead: list[tuple[str, int, str]] = []
    for f in CONSTRAINTS:
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            for m in pat.finditer(line):
                key = m.group(1)
                if key.endswith((".py", ".yaml", ".yml", ".md", ".json")):
                    continue                      # 文件名，不是配置键
                if _resolve(key) is None:
                    dead.append((f.name, i, key))
    assert dead == [], (
        "约束文档引用了不存在的配置键（照它实现会撞 CFG-601 / 静默不生效）:"
        f"{dead}")


# ---------------------------------------------------------------- 守卫链顺序锚
def test_security_doc_guard_chain_matches_code_order():
    """`SECURITY.md` 声明的 guard 链**顺序**必须等于 `tools_guard._BUILTIN_IDS`。

    R23 实测：该表原写 `[g-schema, g-danger, g-fs-path, g-credential-read,
    g-net-outbound]+钩子(g-exec/g-overwrite)` —— 那是**修复前**的顺序；而代码把
    `g-danger` **殿后**求值，因为「分级只决定是否要人类审批，不短路形状 guard」：
    danger 提前短路会让 `g-exec`/`g-overwrite` 对 `danger=high` 工具**恒为死代码**
    （shell/cwd/strict 约束与覆写审批在人类批准前从未求值）。照旧文档实现=复现该缺陷。
    """
    from pyharness.core.tools_guard import _BUILTIN_IDS

    text = (ROOT / "docs" / "SECURITY.md").read_text(encoding="utf-8")
    row = next((ln for ln in text.splitlines()
                if "guard 链" in ln and "[" in ln), None)
    assert row is not None, "SECURITY.md 里找不到 guard 链声明行（本闸失去判别力）"
    bracketed = re.search(r"\[([^\]]+)\]", row)
    assert bracketed is not None, f"未解析到方括号链:{row[:80]!r}"
    documented = [x.strip() for x in bracketed.group(1).split(",") if x.strip()]
    assert documented == list(_BUILTIN_IDS), (
        "SECURITY.md 的 guard 链必须与代码逐序一致（顺序是安全语义的一部分）:\n"
        f"  文档: {documented}\n  代码: {list(_BUILTIN_IDS)}")


# ---------------------------------------------------------------- 强同步集锚
def test_sync_types_count_in_docs_matches_code():
    """文档声明的强同步事件数/清单必须与 `events.vocab.SYNC_TYPES` 一致。

    R25 实测：`EVENT-SCHEMA §1.2` 写「强同步**三类**」、`§8.1` 落盘矩阵只列 **8** 型，
    而代码 `SYNC_TYPES` 为 **14** 型（治理/编排型事件后续追加）⇒ 照旧规格实现会漏
    强同步（崩溃时丢的正是"声明/裁决/终态"这类**语义崩坏**面）。现已两处同步为 14。
    """
    from pyharness.events import SYNC_TYPES

    schemas = (ROOT / "docs" / "EVENT-SCHEMA.md").read_text(encoding="utf-8")
    constraints = (ROOT / "docs" / "CONSTRAINTS-04-SessionLog.md").read_text(
        encoding="utf-8")
    for name, text in (("EVENT-SCHEMA.md", schemas),
                       ("CONSTRAINTS-04-SessionLog.md", constraints)):
        assert f"{len(SYNC_TYPES)} 型" in text, (
            f"{name} 未写明强同步集为 {len(SYNC_TYPES)} 型（与 SYNC_TYPES 同步）")
    # §8.1 落盘矩阵的强同步行必须点名全部 4 个"非原始三类"的治理型事件
    for t in ("decision.issued", "receipt.emitted", "policy.updated"):
        assert t in schemas, f"§8.1 落盘矩阵漏了强同步事件 {t}"


# ------------------------------------------------ R29/R30:规格与文档的数字锚
def test_no_stale_sync_count_wording_anywhere_in_docs():
    """文档全域不得再出现**未加限定的**「强同步三类」措辞（R25/R29）。

    该措辞在 `EVENT-SCHEMA`/`CONSTRAINTS-04` 与 **8 份 spec** 里都曾是**过期的**规模
    描述（真实为 `SYNC_TYPES`＝14 型）。现统一为「原始三类族」（明确列举三者时）或
    「强同步事件(`SYNC_TYPES`)」（未列举时）。
    """
    offenders: list[str] = []
    for f in sorted(ROOT.joinpath("docs").rglob("*.md")):
        if "archive" in f.parts or "baseline" in f.parts:
            continue
        if "强同步三类" in f.read_text(encoding="utf-8"):
            offenders.append(f.relative_to(ROOT).as_posix())
    assert offenders == [], f"仍有过期措辞「强同步三类」:{offenders}"


def test_event_and_envelope_counts_in_docs_match_code():
    """LIMITATIONS §1 声明的事件/载荷/信封规模必须与代码一致（R30）。

    R30 实测（此前手工核对过，现固化为闸）：`EVENT_TYPES` 77 · payload 模型 77 ·
    `SYNC_TYPES` 14 · `TRANSIENT_TYPES` 3 · 信封字段 10。
    """
    from pyharness.events import (EVENT_TYPES, SYNC_TYPES, TRANSIENT_TYPES,
                                  Envelope, payload_model_for)

    models = sum(1 for t in EVENT_TYPES if payload_model_for(t) is not None)
    text = (ROOT / "LIMITATIONS.md").read_text(encoding="utf-8")
    for label, value in (("`EVENT_TYPES`", len(EVENT_TYPES)),
                         ("payload 模型", models),
                         ("`SYNC_TYPES`", len(SYNC_TYPES)),
                         ("`TRANSIENT_TYPES`", len(TRANSIENT_TYPES)),
                         ("信封字段", len(Envelope.model_fields))):
        assert f"{label} **{value}**" in text or f"{label} {value}" in text, (
            f"LIMITATIONS §1 未按实测值声明 {label}={value}")
