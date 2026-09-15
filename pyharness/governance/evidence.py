"""pyharness/governance/evidence.py — 证据工件与归档入口(S5-1)。

一句话职责:把「支持某个 claim 的**引用集合** + 脱敏摘要」升格为一等对象,并以
``evidence.archived`` 事件留痕。**核心纪律(INV-G4 / INV-E1):只存引用,绝不复制
事件内容** —— 事件日志仍是唯一真源,Evidence 只是**索引**。

依赖方向(ADR-018:308,强制):本模块只 import 标准库、``pyharness.errors`` 与本包;
**禁止** ``pyharness.core.*`` / ``engine`` / ``bus`` / ``persistence``。会话经
``ctx.session``/构造注入**鸭子类型**只读使用(与 ``receipt.py`` 同型)。

**本步(S5-1)边界**:只含数据契约 + ``archive()``(唯一写点)。
``on_event``(只读订阅)与 ``collect_for_task``(段锚聚合)属 **S5-2**;
``TraceabilityMatrix`` 属 **S7**(冻结计划 §5.2)——**本步均不实现、不声明**。

INV-E2(引用可解析):``archive()`` 在发射前校验每条 ``EvidenceRef.locator`` 指向
**真实存在**的真源锚(seq / decision_id / receipt_id / segment);不可解析则
``CYC-999`` fail-closed。``kind="test"`` 是**外部**引用(测试路径),不做运行时校验。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional
from uuid import uuid4

from pyharness.errors import raise_code
from pyharness.governance.receipt import _read_events

# 引用种类(冻结设计 §3.5)
REF_KINDS: tuple[str, ...] = ("seq", "decision_id", "receipt_id", "segment", "test")
# 需要**运行时存在性校验**的种类("test" 是外部引用,不校验)
_RESOLVABLE_KINDS: tuple[str, ...] = ("seq", "decision_id", "receipt_id", "segment")

EVENT_EVIDENCE_ARCHIVED = "evidence.archived"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class EvidenceRef:
    """指向**真源**的引用(不复制内容)。

    locator 约定:

    - ``seq``          → ``"<session_id>:<seq>"``(Envelope.seq)
    - ``decision_id``  → ``"<decision_id>"``(decision.issued 主键)
    - ``receipt_id``   → ``"<receipt_id>"``(receipt.emitted 主键)
    - ``segment``      → ``"<task_id>:seg"``(segment.start 的 task_id)
    - ``test``         → ``"tests/…::test_x"``(**外部**引用,不做运行时校验)
    """

    kind: str
    locator: str


@dataclass(frozen=True)
class Evidence:
    """★ 证据工件:支持某个 ``claim`` 的**引用集合** + 脱敏摘要(INV-G4)。

    ``artifact_path`` 仅为**可选外部附件**引用——**不承载治理事实**、不得作为
    任何治理判定的输入(见模块纪律)。
    """

    evidence_id: str
    claim: str
    refs: tuple[EvidenceRef, ...]
    summary: str
    artifact_path: Optional[str]
    ts: str

    def to_payload(self) -> dict:
        """持久化形态(冻结 4 字段;``refs`` 内层仍为 ``{kind, locator}``)。"""
        return {"evidence_id": self.evidence_id, "claim": self.claim,
                "refs": [{"kind": r.kind, "locator": r.locator}
                         for r in self.refs],
                "artifact_path": self.artifact_path}


def _coerce_refs(refs: Iterable[Any]) -> tuple[EvidenceRef, ...]:
    """规范化引用集合:接受 ``EvidenceRef`` 或 ``{"kind","locator"}`` 字典。"""
    out: list[EvidenceRef] = []
    for r in refs or ():
        if isinstance(r, EvidenceRef):
            out.append(r)
        elif isinstance(r, dict) and set(r) == {"kind", "locator"}:
            out.append(EvidenceRef(kind=str(r["kind"]),
                                   locator=str(r["locator"])))
        else:
            raise_code("CYC-999", module="governance.evidence", field="refs",
                       why="引用必须是 EvidenceRef 或 {kind, locator} 字典",
                       got=type(r).__name__)
    return tuple(out)


class EvidenceCollector:
    """证据归档入口(S5-1)。

    当前只提供 **``archive()``(唯一写点)**;``on_event`` / ``collect_for_task``
    属 S5-2,``TraceabilityMatrix`` 属 S7。
    """

    def __init__(self, *, session: Any = None,
                 id_factory: Optional[Callable[[], str]] = None,
                 clock: Optional[Callable[[], str]] = None) -> None:
        self._session = session
        self._id_factory = id_factory or (lambda: uuid4().hex)
        self._clock = clock or _utc_now

    # ------------------------------------------------------------ 校验
    @staticmethod
    def _resolve(session: Any, ref: EvidenceRef) -> bool:
        """INV-E2:该引用能否指向**真实存在**的真源锚(只读,不写)。"""
        if ref.kind == "test":
            return True                              # 外部引用:不做运行时校验
        events = _read_events(session)
        if ref.kind == "seq":
            tail = ref.locator.rsplit(":", 1)[-1]
            try:
                want = int(tail)
            except ValueError:
                return False
            return any(int(getattr(e, "seq", -1)) == want for e in events)
        if ref.kind == "decision_id":
            return any(getattr(e, "type", None) == "decision.issued"
                       and str((getattr(e, "payload", None) or {})
                               .get("decision_id") or "") == ref.locator
                       for e in events)
        if ref.kind == "receipt_id":
            return any(getattr(e, "type", None) == "receipt.emitted"
                       and str((getattr(e, "payload", None) or {})
                               .get("receipt_id") or "") == ref.locator
                       for e in events)
        if ref.kind == "segment":
            task = ref.locator.rsplit(":", 1)[0]
            return any(getattr(e, "type", None) == "segment.start"
                       and str((getattr(e, "payload", None) or {})
                               .get("task_id") or "") == task
                       for e in events)
        return False

    # ------------------------------------------------------------ 归档
    async def archive(self, claim: str, refs: Iterable[Any], *,
                      ctx: Any = None, summary: str = "",
                      artifact_path: Optional[str] = None) -> Optional[Evidence]:
        """归档一条证据 → ``append("evidence.archived")``(**本模块唯一写点**)。

        - ``ctx`` 省略时用构造期注入的 ``session``;
        - 无会话(纯内存/单测)⇒ 返回 ``None``(降级,不发射);
        - 引用不可解析(INV-E2)⇒ ``CYC-999`` **fail-closed**;
        - 事件为**普通攒批**(非强同步)——丢失可由既有事件**再派生**(INV-E3)。
        """
        if not isinstance(claim, str) or not claim:
            raise_code("CYC-999", module="governance.evidence", field="claim",
                       why="claim 必须为非空字符串")
        norm = _coerce_refs(refs)
        for r in norm:
            if r.kind not in REF_KINDS:
                raise_code("CYC-999", module="governance.evidence",
                           field="refs.kind", got=str(r.kind),
                           why=f"引用种类必须是 {REF_KINDS} 之一")
            if not isinstance(r.locator, str) or not r.locator:
                raise_code("CYC-999", module="governance.evidence",
                           field="refs.locator", why="locator 必须为非空字符串")
        sess = getattr(ctx, "session", None) if ctx is not None else self._session
        if sess is None:
            return None                              # 未接线:降级不发射
        for r in norm:
            if r.kind in _RESOLVABLE_KINDS and not self._resolve(sess, r):
                raise_code("CYC-999", module="governance.evidence",
                           field="refs.locator", kind=r.kind,
                           locator=r.locator,
                           why="引用不可解析(INV-E2):locator 未指向真实存在的真源锚")
        evidence_id = self._id_factory()
        ts = self._clock()
        payload = {"evidence_id": evidence_id, "claim": claim,
                   "refs": [{"kind": r.kind, "locator": r.locator}
                            for r in norm],
                   "artifact_path": artifact_path}
        r = sess.append(EVENT_EVIDENCE_ARCHIVED, payload, actor="system",
                        trace=None)
        if hasattr(r, "__await__"):
            r = await r
        return Evidence(evidence_id=evidence_id, claim=claim, refs=norm,
                        summary=summary, artifact_path=artifact_path, ts=ts)


__all__ = [
    "REF_KINDS", "EVENT_EVIDENCE_ARCHIVED", "EvidenceRef", "Evidence",
    "EvidenceCollector",
]
