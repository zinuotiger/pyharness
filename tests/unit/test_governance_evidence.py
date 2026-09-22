"""tests/unit/test_governance_evidence.py — 证据模块单测(S5-1 + S5-2a + S5-2b)。

- **S5-1**:``EvidenceRef`` / ``Evidence`` / ``archive()`` / 事件注册 / payload
  validator / INV-E1(只引用) / INV-E2(引用可解析) / INV-E4(唯一写点)。
- **S5-2a**:``on_event``(只读索引) / ``from_log``(日志重建) /
  ``collect_for_task``(段锚聚合) / INV-E3(索引可再派生,非真源)。
- **S5-2b**:``engine`` 装配(构造 → 订阅 → 注入)· 单实例 · 订阅真送达。
- **不含**:``TraceabilityMatrix``(S7)。
"""
from __future__ import annotations

import ast
import asyncio
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


# ============================== S5-2a:只读索引 + 段锚聚合(D-1(a))
class TestEvidenceIndex:
    """D-1(a):``collect_for_task`` 的 Evidence **只来自 ``evidence.archived`` 事件**;
    索引是**派生缓存**(可由 ``from_log`` 完全重建),**非事实源**。

    日志布局(seq → type):
        1 segment.start(t-3) · 2 decision.issued(D1) · 3 receipt.emitted(R1)
        4 E-dec(decision_id D1) · 5 E-receipt(receipt_id R1) · 6 E-seg(segment t-3)
        7 E-seq(seq s-abc12345:10) · 8 segment.end(t-3) · 9 segment.start(t-9)
       10 tool.call(c1)
    """

    @staticmethod
    def _log() -> _Log:
        log = _Log()
        log.events.append(_Ev(1, "segment.start", {"task_id": "t-3"}))
        log.events.append(_Ev(2, "decision.issued", {"decision_id": "D1"}))
        log.events.append(_Ev(3, "receipt.emitted", {"receipt_id": "R1"}))
        for seq, eid, ref in (
            (4, "E-dec", {"kind": "decision_id", "locator": "D1"}),
            (5, "E-receipt", {"kind": "receipt_id", "locator": "R1"}),
            (6, "E-seg", {"kind": "segment", "locator": "t-3:seg"}),
            (7, "E-seq", {"kind": "seq", "locator": "s-abc12345:10"}),
        ):
            log.events.append(_Ev(seq, "evidence.archived",
                                  {"evidence_id": eid, "claim": "c",
                                   "refs": [ref], "artifact_path": None}))
        log.events.append(_Ev(8, "segment.end",
                              {"task_id": "t-3", "start_seq": 1}))
        log.events.append(_Ev(9, "segment.start", {"task_id": "t-9"}))
        log.events.append(_Ev(10, "tool.call", {"call_id": "c1"}))
        return log

    async def _indexed(self) -> EvidenceCollector:
        col = EvidenceCollector()
        for e in self._log().events:
            await col.on_event(e)
        return col

    # ------------------------------------------------------------ T-1
    async def test_t1_on_event_builds_index(self):
        col = await self._indexed()
        assert col.evidence_count() == 4

    # ------------------------------------------------------------ T-4 / T-5
    async def test_t4_t5_aggregate_by_segment_anchor(self):
        """段锚聚合:四类 ref 均能归入正确 task;跨段不串。"""
        col = await self._indexed()
        t3 = col.collect_for_task("t-3")
        assert [e.evidence_id for e in t3] == ["E-dec", "E-receipt", "E-seg"]
        assert [e.evidence_id for e in col.collect_for_task("t-9")] == ["E-seq"]

    async def test_t6_unknown_or_empty_task_returns_empty(self):
        col = await self._indexed()
        assert col.collect_for_task("nope") == ()
        assert col.collect_for_task("") == ()

    # ------------------------------------------------------------ T-2
    async def test_t2_rebuild_from_log_matches_subscription_state(self):
        """INV-E3 核心:新 collector 仅靠 replay 重建 ⇒ 与订阅态**逐条相等**。"""
        col = await self._indexed()
        rebuilt = await EvidenceCollector.from_log(self._log())
        assert rebuilt.evidence_count() == col.evidence_count()
        for task in ("t-3", "t-9"):
            assert ([e.evidence_id for e in rebuilt.collect_for_task(task)]
                    == [e.evidence_id for e in col.collect_for_task(task)])

    # ------------------------------------------------------------ T-3
    async def test_t3_on_event_is_read_only(self):
        """只读铁证:喂完全部事件后**事件数不变**;且 ``on_event`` 体内无 ``append``。"""
        log = self._log()
        before = len(log.events)
        col = EvidenceCollector()
        for e in log.events:
            await col.on_event(e)
        assert len(log.events) == before              # 零写入
        tree = ast.parse((_ROOT / "pyharness/governance/evidence.py")
                         .read_text(encoding="utf-8"))
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.AsyncFunctionDef) and n.name == "on_event")
        assert not any(isinstance(x, ast.Attribute) and x.attr == "append"
                       for x in ast.walk(fn)), "on_event 不得写事件"

    # ------------------------------------------------------------ T-7
    async def test_t7_exception_safe_on_malformed_events(self):
        """畸形事件不得抛穿,亦不破坏已建索引。"""
        col = await self._indexed()
        good = col.evidence_count()
        for bad in (_Ev(99, "evidence.archived", None),
                    _Ev(100, "evidence.archived", {"evidence_id": "X",
                                                   "refs": ["not-a-dict"]}),
                    _Ev(101, "evidence.archived", {}),
                    _Ev(102, "segment.end", {}),
                    _Ev(103, "unknown.type", {"x": 1}),
                    object()):
            await col.on_event(bad)                   # 不抛
        assert col.evidence_count() == good           # 索引未被破坏

    # ------------------------------------------------------------ 索引非真源
    async def test_index_is_cache_not_truth_source(self):
        """约束 ③/⑤:``archive()`` **不维护索引**;索引只由 ``on_event`` 建(可重建)。"""
        log = _Log(); _seed(log)
        col = EvidenceCollector()
        await col.archive("c", [EvidenceRef("decision_id", "D1")],
                          ctx=_Ctx(log))
        assert col.evidence_count() == 0              # archive 不写索引
        await col.on_event(log.events[-1])            # 由事件面进入索引
        assert col.evidence_count() == 1


# ============================== S5-2b:engine 装配(构造 → 订阅 → 注入)
class TestEvidenceWiring:
    """S5-2b:``EvidenceCollector`` 由 engine 装配、订阅真送达、注入单实例。

    自带 cfg 辅助(不跨测试文件引用),保持 S5-2b 白名单不变。
    """

    @staticmethod
    def _cfg(tmp_path):
        from pyharness.config import load_settings
        cfg = load_settings()
        cfg.storage.root = str(tmp_path)
        cfg.storage.sessions_dir = str(tmp_path / "sessions")
        cfg.storage.workspaces_dir = str(tmp_path / "workspaces")
        cfg.storage.spill_dir = str(tmp_path / "spill")
        cfg.storage.db_path = str(tmp_path / "pyharness.db")
        return cfg

    async def test_spine_wires_evidence_single_instance(self, tmp_path):
        """注入 + 单实例(与 ``ctx.governance`` 同一对象)。"""
        from pyharness.engine import assemble_real_engine
        ctx = await assemble_real_engine(
            self._cfg(tmp_path), sid="s-eng-ev-000003",
            sessions_dir=tmp_path / "sessions", channel="cli")
        gov = ctx.engine_spine.governance
        assert isinstance(gov.evidence, EvidenceCollector)
        assert ctx.governance is gov                    # 单实例挂载

    async def test_subscription_delivers_events_to_index(self, tmp_path):
        """构造→订阅→注入 顺序正确:事件真流入**只读**索引,聚合可用。"""
        from pyharness.engine import assemble_real_engine
        ctx = await assemble_real_engine(
            self._cfg(tmp_path), sid="s-eng-ev-000004",
            sessions_dir=tmp_path / "sessions", channel="cli")
        gov = ctx.engine_spine.governance
        await ctx.session.append("session.created",
                                 {"title": "", "model": "m"}, actor="system")
        await ctx.session.append("segment.start", {"task_id": "t-1"},
                                 actor="system")
        await ctx.session.append("evidence.archived", {
            "evidence_id": "E1", "claim": "c",
            "refs": [{"kind": "segment", "locator": "t-1:seg"}],
            "artifact_path": None}, actor="system")
        await asyncio.sleep(0.05)                       # 总线异步投递
        assert gov.evidence.evidence_count() == 1
        assert [e.evidence_id for e in gov.evidence.collect_for_task("t-1")] \
            == ["E1"]


async def test_unclosed_segment_does_not_swallow_later_tasks_evidence():
    """**R14-10 回归**:崩溃留下的**未闭合段**不得吞掉其后任务的证据。

    进程被杀 → ``_run_task`` 的 ``finally`` 未执行 → 日志里**只有** ``segment.start``
    没有 ``segment.end``。修复前 ``_task_at`` 把未闭合段的右界当作**无穷**、又按插入序
    返回首个命中 ⇒ 该段吞掉其后所有 seq:实测 ``collect_for_task("taskB")`` 返回空,
    而 taskB 的证据被算到 taskA 上(**崩溃重启后治理证据按任务查错**)。

    判据已改为"起点 ≤ seq 的**最近**一段"(段在时间轴上互不重叠,未闭合段的右界因此
    天然止于下一段起点之前)。
    """
    log = _Log()
    log.events.append(_Ev(1, "segment.start", {"task_id": "taskA"}))   # 崩溃现场:无 end
    log.events.append(_Ev(2, "evidence.archived",
                          {"evidence_id": "evA", "claim": "A",
                           "refs": [{"kind": "seq", "locator": "s-abc12345:2"}]}))
    log.events.append(_Ev(10, "segment.start", {"task_id": "taskB"}))
    log.events.append(_Ev(11, "evidence.archived",
                          {"evidence_id": "evB", "claim": "B",
                           "refs": [{"kind": "seq", "locator": "s-abc12345:11"}]}))
    log.events.append(_Ev(12, "segment.end",
                          {"task_id": "taskB", "start_seq": 10}))

    col = await EvidenceCollector.from_log(log)
    assert [e.evidence_id for e in col.collect_for_task("taskA")] == ["evA"], \
        "未闭合段不得越界吞掉后续任务的证据"
    assert [e.evidence_id for e in col.collect_for_task("taskB")] == ["evB"], \
        "后续任务必须看到自己的证据"
