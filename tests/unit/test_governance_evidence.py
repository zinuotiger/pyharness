"""tests/unit/test_governance_evidence.py — S5-1 证据契约与归档入口单测。

范围(S5-1):``EvidenceRef`` / ``Evidence`` / ``archive()`` / 事件注册 / payload
validator / INV-E1(只引用) / INV-E2(引用可解析) / INV-E4(唯一写点)。
**不含** ``on_event`` / ``collect_for_task``(S5-2)与 ``TraceabilityMatrix``(S7)。
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from pyharness.errors import PyHError
from pyharness.events import vocab as V
from pyharness.governance.evidence import (EVENT_EVIDENCE_ARCHIVED, REF_KINDS,
                                           Evidence, EvidenceCollector,
                                           EvidenceRef)

_ROOT = pathlib.Path(__file__).resolve().parents[2]


class _Ev:
    def __init__(self, seq, type_, payload, trace=None, ts="T"):
        self.seq, self.type, self.payload = seq, type_, payload
        self.trace, self.ts = trace, ts


class _Log:
    """会话日志替身:``append`` + ``events_after``(与 SessionLog 同型)。"""

    def __init__(self):
        self.events: list[_Ev] = []

    async def append(self, type_, payload, *, actor, sync=False, trace=None):
        e = _Ev(len(self.events) + 1, type_, dict(payload), trace)
        self.events.append(e)
        return e

    def events_after(self, after: int = 0):
        return [e for e in self.events if e.seq > after]


class _Ctx:
    def __init__(self, log): self.session = log


def _seed(log: _Log) -> None:
    """铺一条最小真源:decision.issued + receipt.emitted + segment.start。"""
    log.events.append(_Ev(1, "decision.issued",
                          {"decision_id": "D1", "verdict": "allow"}))
    log.events.append(_Ev(2, "receipt.emitted",
                          {"receipt_id": "R1", "decision_id": "D1"}))
    log.events.append(_Ev(3, "segment.start", {"task_id": "t-3"}))


# ---------------------------------------------------------------- 契约一致性
def test_ref_kinds_match_evidence_module():
    """payload 的本地 kind 常量必须与 ``governance.evidence.REF_KINDS`` 一致。

    （events 不得反向 import governance ⇒ 本地声明 + 一致性测试守卫,沿用
    S3-1 HUMAN_CHANNELS 的手法。）
    """
    from pyharness.events.payload import _EVIDENCE_REF_KINDS
    assert _EVIDENCE_REF_KINDS == REF_KINDS


def test_event_registered_but_not_sync():
    """INV-E3:``evidence.archived`` 已注册、**非瞬态**、**不入 SYNC**(普通攒批)。"""
    assert V.is_registered(EVENT_EVIDENCE_ARCHIVED) is True
    assert EVENT_EVIDENCE_ARCHIVED not in V.SYNC_TYPES
    assert V.is_transient(EVENT_EVIDENCE_ARCHIVED) is False


def test_dependency_boundary():
    """治理层依赖边界:evidence.py 无 core/persistence/bus/engine import。"""
    tree = ast.parse((_ROOT / "pyharness/governance/evidence.py")
                     .read_text(encoding="utf-8"))
    mods = {n.module or "" for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom)}
    mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
             for a in n.names}
    for m in mods:
        assert not m.startswith(("pyharness.core", "pyharness.persistence",
                                 "pyharness.bus", "pyharness.engine")), m


# ---------------------------------------------------------------- payload
def test_payload_four_fields_and_validator():
    """payload 恰 4 字段;``refs`` 内层严格性由 validator 补回。"""
    m = V.payload_model_for(EVENT_EVIDENCE_ARCHIVED)
    assert set(m.model_fields) == {"evidence_id", "claim", "refs",
                                   "artifact_path"}
    ok = m(evidence_id="e1", claim="c", artifact_path=None,
           refs=[{"kind": "decision_id", "locator": "D1"}])
    assert ok.refs[0]["kind"] == "decision_id"
    # 内层键集必须恰为两键
    for bad in ([{"kind": "decision_id"}],
                [{"kind": "decision_id", "locator": "D1", "extra": 1}],
                [{"kind": "bogus", "locator": "x"}],
                [{"kind": "seq", "locator": ""}],
                ["not-a-dict"]):
        with pytest.raises(Exception):
            m(evidence_id="e1", claim="c", refs=bad)


def test_payload_rejects_unknown_top_field():
    m = V.payload_model_for(EVENT_EVIDENCE_ARCHIVED)
    with pytest.raises(Exception):
        m(evidence_id="e1", claim="c", refs=[], bogus=1)


# ---------------------------------------------------------------- archive
async def test_inv_e1_archive_stores_reference_not_fact():
    """INV-E1:归档事件**只含引用**,不含任何事件内容副本。"""
    log = _Log(); _seed(log)
    col = EvidenceCollector(id_factory=lambda: "ev1", clock=lambda: "TS")
    ev = await col.archive("工具调用已被拒绝",
                           [EvidenceRef("decision_id", "D1")],
                           ctx=_Ctx(log), summary="摘要(脱敏)")
    assert isinstance(ev, Evidence) and ev.evidence_id == "ev1" and ev.ts == "TS"
    p = log.events[-1].payload
    assert set(p) == {"evidence_id", "claim", "refs", "artifact_path"}
    assert p["refs"] == [{"kind": "decision_id", "locator": "D1"}]
    # 不含被引用事件的内容(verdict 等)
    assert "verdict" not in p and "tool" not in p


@pytest.mark.parametrize("kind,locator,ok", [
    ("seq", "s-abc12345:3", True),
    ("seq", "s-abc12345:99", False),          # 不存在
    ("decision_id", "D1", True),
    ("decision_id", "NOPE", False),
    ("receipt_id", "R1", True),
    ("receipt_id", "NOPE", False),
    ("segment", "t-3:seg", True),
    ("segment", "t-99:seg", False),
])
async def test_inv_e2_locator_must_resolve(kind, locator, ok):
    """INV-E2:locator 必须指向真实存在的真源锚;不可解析 → CYC-999 fail-closed。"""
    log = _Log(); _seed(log)
    col = EvidenceCollector()
    if ok:
        ev = await col.archive("c", [EvidenceRef(kind, locator)],
                               ctx=_Ctx(log))
        assert ev is not None
    else:
        with pytest.raises(PyHError) as ei:
            await col.archive("c", [EvidenceRef(kind, locator)], ctx=_Ctx(log))
        assert ei.value.code == "CYC-999"


async def test_ref_kind_and_locator_validated():
    log = _Log(); _seed(log)
    col = EvidenceCollector()
    with pytest.raises(PyHError):
        await col.archive("c", [EvidenceRef("bogus", "x")], ctx=_Ctx(log))
    with pytest.raises(PyHError):
        await col.archive("c", [EvidenceRef("decision_id", "")], ctx=_Ctx(log))
    with pytest.raises(PyHError):
        await col.archive("", [EvidenceRef("decision_id", "D1")], ctx=_Ctx(log))
    with pytest.raises(PyHError):
        await col.archive("c", ["not-a-ref"], ctx=_Ctx(log))


async def test_test_kind_is_external_and_not_validated():
    """``kind="test"`` 是外部引用:不做运行时存在性校验。"""
    log = _Log()
    col = EvidenceCollector()
    ev = await col.archive("c", [EvidenceRef("test", "tests/x.py::test_y")],
                           ctx=_Ctx(log))
    assert ev is not None


async def test_inv_e4_archive_is_only_writer():
    """INV-E4:``archive`` 是该模块**唯一写点**——每次调用恰写一条事件。"""
    log = _Log(); _seed(log)
    col = EvidenceCollector()
    before = len(log.events)
    for _ in range(3):
        await col.archive("c", [EvidenceRef("decision_id", "D1")],
                          ctx=_Ctx(log))
    assert len(log.events) - before == 3       # 每次恰一条


async def test_archive_degrades_without_session():
    """无会话(纯内存/单测)⇒ 降级返回 None,不发射、不抛。"""
    col = EvidenceCollector()
    assert await col.archive("c", [EvidenceRef("decision_id", "D1")]) is None


async def test_archive_accepts_dict_refs_and_dedupes_nothing():
    """接受 ``{kind, locator}`` 字典形态;refs 原序保留。"""
    log = _Log(); _seed(log)
    col = EvidenceCollector()
    ev = await col.archive("c", [{"kind": "decision_id", "locator": "D1"},
                                 {"kind": "receipt_id", "locator": "R1"}],
                           ctx=_Ctx(log))
    assert ev is not None
    assert [r.kind for r in ev.refs] == ["decision_id", "receipt_id"]
    assert log.events[-1].payload["refs"] == [
        {"kind": "decision_id", "locator": "D1"},
        {"kind": "receipt_id", "locator": "R1"}]
