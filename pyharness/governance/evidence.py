"""pyharness/governance/evidence.py — 证据工件与归档入口(S5-1)。

一句话职责:把「支持某个 claim 的**引用集合** + 脱敏摘要」升格为一等对象,并以
``evidence.archived`` 事件留痕。**核心纪律(INV-G4 / INV-E1):只存引用,绝不复制
事件内容** —— 事件日志仍是唯一真源,Evidence 只是**索引**。

依赖方向(ADR-018:308,强制):本模块只 import 标准库、``pyharness.errors`` 与本包;
**禁止** ``pyharness.core.*`` / ``engine`` / ``bus`` / ``persistence``。会话经
``ctx.session``/构造注入**鸭子类型**只读使用(与 ``receipt.py`` 同型)。

**边界**:

- **S5-1**:数据契约 + ``archive()``(唯一写点)。
- **S5-2a**:``on_event()``(**只读**索引) + ``from_log()``(日志重建) +
  ``collect_for_task()``(段锚聚合)——**索引只是派生缓存,可由事件日志完全重建**
  (``from_log``),**不是事实源**(INV-E3);``on_event`` 体内**零 ``append``**。
  订阅面 5 类:``evidence.archived`` / ``segment.start`` / ``segment.end`` /
  ``decision.issued`` / ``receipt.emitted``;**不订阅** ``tool.result``(属工具执行
  因果关系,由 S5-3 Audit 承担)。
- **S5-2b**:``engine`` 装配 + 订阅 + 注入 ``GovernanceContext.evidence``。
- **S7**:``TraceabilityMatrix``。

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
        # ---- 派生缓存(S5-2a):**可由事件日志完全重建**,非事实源(INV-E3) ----
        # ① 证据索引:evidence_id → (事件 seq, Evidence)
        self._evidence: dict[str, tuple[int, Evidence]] = {}
        # ② 段索引:task_id → [start_seq, end_seq](end 未闭合时为 None)
        self._segments: dict[str, list[Optional[int]]] = {}
        # ③ 锚 seq:decision_id / receipt_id → 其事件 seq(用于把引用解析到段)
        self._anchor_seq: dict[str, int] = {}

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

    # ------------------------------------------------------------ 只读索引
    async def on_event(self, env: Any) -> None:
        """**只读**消费一条事件,更新派生索引(S5-2a)。

        约束:① 只消费不产生——**本方法体内零 ``append``**;② 索引仅是缓存,
        可由日志 replay 完全重建(``from_log``);③ 未知类型静默忽略;④ 畸形
        事件不得抛穿(总线的 EVT-103 隔离之外,**此处亦不抛**)。

        订阅面(5 类):``evidence.archived``(证据索引) · ``segment.start`` /
        ``segment.end``(段范围) · ``decision.issued`` / ``receipt.emitted``
        (锚 → seq,用于把引用解析到段)。
        **不订阅** ``tool.result``(属工具执行因果关系,由 S5-3 Audit 承担)。
        """
        try:
            t = getattr(env, "type", None)
            p = getattr(env, "payload", None) or {}
            seq = int(getattr(env, "seq", 0) or 0)
            if t == EVENT_EVIDENCE_ARCHIVED:
                ev = self._evidence_of(p, seq)
                if ev is not None:
                    self._evidence[ev.evidence_id] = (seq, ev)
            elif t == "segment.start":
                task = str(p.get("task_id") or "")
                if task:
                    self._segments.setdefault(task, [seq, None])[0] = seq
            elif t == "segment.end":
                task = str(p.get("task_id") or "")
                if task:
                    st = int(p.get("start_seq") or 0) or seq
                    self._segments.setdefault(task, [st, None])
                    self._segments[task][0] = st
                    self._segments[task][1] = seq      # 闭区间右端 = end 事件 seq
            elif t == "decision.issued":
                did = str(p.get("decision_id") or "")
                if did:
                    self._anchor_seq[f"decision_id:{did}"] = seq
            elif t == "receipt.emitted":
                rid = str(p.get("receipt_id") or "")
                if rid:
                    self._anchor_seq[f"receipt_id:{rid}"] = seq
        except Exception:                            # noqa: BLE001 只读索引:绝不抛穿
            return

    @classmethod
    async def from_log(cls, session: Any, **kw: Any) -> "EvidenceCollector":
        """**由事件日志重建** collector(INV-E3:索引可完全再派生)。"""
        col = cls(session=session, **kw)
        for env in _read_events(session):
            await col.on_event(env)
        return col

    @staticmethod
    def _evidence_of(p: Any, seq: int) -> Optional[Evidence]:
        """由 ``evidence.archived`` 载荷重建 ``Evidence``(只读;载荷非法 → None)。"""
        if not isinstance(p, dict):
            return None
        try:
            refs = _coerce_refs(p.get("refs") or ())
        except Exception:                            # noqa: BLE001 载荷非法:跳过
            return None
        eid = str(p.get("evidence_id") or "")
        if not eid:
            return None
        return Evidence(evidence_id=eid, claim=str(p.get("claim") or ""),
                        refs=refs, summary="",
                        artifact_path=p.get("artifact_path"), ts="")

    def _task_at(self, seq: int) -> Optional[str]:
        """该事件 seq 落在哪个段内(闭区间;未闭合段**不得**向右无限延伸)。

        段在时间轴上互不重叠且按 seq 递增开合,故判据 = **起点 ≤ seq 的最近一段**
        (未闭合段的右界由此天然止于下一段起点之前)。

        2026-09-21 R14-10:此前把未闭合段(崩溃现场:进程被杀 → ``_run_task`` 的
        ``finally`` 未执行 → 无 ``segment.end``)的右界当作**无穷**,又按插入序遍历
        ``_segments`` ⇒ 该段**吞掉其后所有任务的证据**(实测:未闭合的 taskA 之后,
        taskB 的证据被算到 taskA 上,``collect_for_task("taskB")`` 返回空)。属**恢复
        一致性**缺陷:崩溃重启后证据按任务查错。
        """
        best_task: Optional[str] = None
        best_start = -1
        for task, (s, e) in self._segments.items():
            if s is None:
                continue
            s = int(s)
            if s > seq or (e is not None and seq > int(e)):
                continue
            if s > best_start:                       # 起点最近者胜(段不重叠)
                best_task, best_start = task, s
        return best_task

    def _tasks_of(self, ev: Evidence) -> set[str]:
        """该证据归属的 task 集合(按引用解析;无法解析的引用被忽略)。"""
        out: set[str] = set()
        for r in ev.refs:
            if r.kind == "segment":
                task = r.locator.rsplit(":", 1)[0]
                if task:
                    out.add(task)
                continue
            if r.kind == "seq":
                tail = r.locator.rsplit(":", 1)[-1]
                try:
                    t = self._task_at(int(tail))
                except ValueError:
                    t = None
            else:                                    # decision_id / receipt_id
                anchor = self._anchor_seq.get(f"{r.kind}:{r.locator}")
                t = self._task_at(anchor) if anchor else None
            if t:
                out.add(t)
        return out

    def collect_for_task(self, task_id: str) -> tuple[Evidence, ...]:
        """按**段锚**聚合该任务的证据(S5-2a)。

        来源**只有** ``evidence.archived`` 事件(D-1(a));返回按事件 seq 升序的
        不可变元组;未知/空 ``task_id`` 或无线索 ⇒ ``()``(**不抛**)。
        """
        if not task_id:
            return ()
        hits = [(seq, ev) for seq, ev in self._evidence.values()
                if task_id in self._tasks_of(ev)]
        hits.sort(key=lambda it: it[0])
        return tuple(ev for _seq, ev in hits)

    def evidence_count(self) -> int:
        """当前索引内的证据条数(只读;供测试/自检)。"""
        return len(self._evidence)


__all__ = [
    "REF_KINDS", "EVENT_EVIDENCE_ARCHIVED", "EvidenceRef", "Evidence",
    "EvidenceCollector",
]