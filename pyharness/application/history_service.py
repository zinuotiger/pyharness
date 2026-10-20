"""HistoryService operations, separated from facade assembly."""
from __future__ import annotations
from typing import TYPE_CHECKING
from dataclasses import asdict
from typing import Any
from pyharness.application.message_projection import _with_message_origin
from pyharness.application.session_access import _session_module
if TYPE_CHECKING:
    pass
from pyharness.core.tenant_settings import register_session_tenant
from pyharness.errors import raise_code
from pyharness.events import call_id_of
from pyharness.core.lifecycle import admitted

class HistoryService:
    """HistoryService responsibility; state is owned by the composing facade."""

    @admitted
    async def list_sessions(self) -> dict:
        lst = self.surface_mgr().list()
        if callable(lst) or hasattr(lst, "__await__"):
            lst = await _session_module()._await(lst)

        def _title_of(sid: str) -> str:
            log_ = self._logs.get(str(sid))
            if log_ is None:
                return ""
            cache = self.__dict__.setdefault('_list_titles', {})
            seq = log_.stats()['seq']
            if sid in cache and cache[sid][0] == seq:
                return cache[sid][1]
            title = ""
            for ev in log_.events_after(0):
                if ev.type == "session.created":
                    title = ev.payload.get("title") or title
                elif ev.type == "session.renamed":
                    title = ev.payload.get("new_title") or title
            cache[sid] = (seq, str(title))
            return str(title)

        sessions: list[dict] = []
        for item in (lst or []):
            row = item if isinstance(item, dict) else {"sid": str(item)}
            sid = str(row.get("sid") or item)
            from pyharness.application.product_execution_sessions import is_product_session
            if is_product_session(sid):
                from pyharness.errors import PyHError
                try:await self.require_session(sid)
                except PyHError:continue
            register_session_tenant(sid, self.tenant_id)
            if not row.get("title"):
                row["title"] = _title_of(sid)
            sessions.append(row)
        authorized = {row['sid'] for row in sessions}
        for sid in set(self.__dict__.get('_list_titles', {}))-authorized:
            self._list_titles.pop(sid, None)
        return {"sessions": sessions, "count": len(sessions)}

    @admitted
    async def session_messages(self, sid: str, after_seq: int = 0) -> dict:
        log_ = await self.require_session(sid)
        msgs = log_.derive_messages()
        stats_fn = getattr(log_, "stats", None)
        to_seq = int((stats_fn().get("seq") or 0)) if callable(stats_fn) else 0
        projected = _with_message_origin(log_, msgs)
        records = {r['artifact_id']:r for r in (await self.platform.artifacts())['artifacts']
                   if r['session_id'] == sid}
        for row in projected:
            row['attachments'] = [{k:records[i][k] for k in ('artifact_id','filename','media_type','size','preview_type')}
                                  for i in row.pop('artifact_ids', []) if i in records]
        return {"sid": sid, "after_seq": int(after_seq), "to_seq": to_seq,
                "messages": projected}

    @admitted
    async def session_timeline(self, sid: str, after_seq: int = 0,
                               kinds: str = "") -> dict:
        log_ = await self.require_session(sid)
        if isinstance(after_seq, bool) or not isinstance(after_seq, int):
            raise_code("EVT-100", field="after_seq", value=after_seq,
                       advice="after_seq 须 int(seq 游标)")
        from pyharness.application.event_projection import derive_timeline
        nodes = derive_timeline(
            log_.events_after(after_seq=max(0, int(after_seq))),
            kinds=str(kinds or ""))
        return {"sid": sid, "base_seq": max(0, int(after_seq)),
                "nodes": [asdict(n) for n in nodes]}

    @admitted
    async def event_detail(self, sid: str, seq: int) -> dict:
        log_ = await self.require_session(sid)
        if isinstance(seq, bool) or not isinstance(seq, int):
            raise_code("EVT-100", field="seq", value=seq, advice="seq 须 int")
        env = log_.get(int(seq))
        if env is None:
            raise_code("EVT-100", sid=sid, seq=seq,
                       advice="seq 不存在(空洞/坏行隔离/越界)")
        from pyharness.application.event_projection import envelope_dict
        return {"event": envelope_dict(env)}

    @admitted
    async def telemetry_report(self, sid: str) -> dict:
        """旧遥测面:按事件类型计数聚合(与治理因果无关,保留兼容)。"""
        log_ = await self.require_session(sid)
        from pyharness.core import telemetry as _tel
        try:
            return {"sid": str(sid), "ok": True,
                    "audit": _tel.session_audit(log_)}
        except Exception as e:                         # noqa: BLE001
            return {"sid": str(sid), "ok": False,
                    "error": f"{type(e).__name__}:{e}"[:200]}

    @admitted
    async def governance_audit(self, sid: str, *, decision_id: str = "",
                               since_seq: int = 0, reconcile: bool = False,
                               limit: int = 100) -> dict:
        """治理审计查询面:把 ``governance.audit.AuditSystem`` 接成**产品入口**。

        GAP-7 修复点:此前 ``AuditSystem`` 已构造并注入 ``GovernanceContext``,但
        ``causal_chain`` / ``denied_report`` / ``reconcile`` / ``legacy_session_audit``
        在 ``pyharness/`` 内**零调用点** ⇒ 治理数据在盘上、查询面不可达,桌面
        "审计"页只能用旧遥测(类型计数,无决策因果)顶上。

        读侧纪律(**replay-only**):每条结论都由**重放会话日志**现算得出,不读
        任何内存缓存、不订阅事件 ⇒ 重启后同一日志得同一结果(可复现)。

        返回:``decisions``(最近 N 条 decision.issued,倒序)·
        ``denied``(被拒清单,含 ``executed`` 证据位:该 call_id 是否有配对
        tool.result)· ``findings``(``reconcile=True`` 时的三类一致性对账)·
        ``chain``(给定 ``decision_id`` 的因果链还原)。
        """
        log_, spine = await self.public_spine_for(sid)
        audit = getattr(getattr(spine, "governance", None), "audit", None)
        if audit is None:
            raise_code("CYC-999", module="application.service",
                       field="governance.audit",
                       why="治理审计面未接线(AuditSystem 未注入 GovernanceContext)")
        from pyharness.governance import verify_receipt
        events = list(log_.events_after(0))
        decisions = []
        tenant = getattr(log_, "tenant_id", None)
        for e in events:
            if e.type != "decision.issued":
                continue
            p = getattr(e, "payload", None) or {}
            decisions.append({
                "decision_id": str(p.get("decision_id") or ""),
                "verdict": str(p.get("verdict") or ""),
                "tool": str(p.get("tool") or ""),
                "guard_ids": list(p.get("guard_ids") or []),
                "policy_refs": list(p.get("policy_refs") or []),
                "principal_kind": p.get("principal_kind"),
                "principal_id": p.get("principal_id"),
                "principal_channel": p.get("principal_channel"),
                "approval_ref": p.get("approval_ref"),
                "supersedes": p.get("supersedes"),
                "seq": getattr(e, "seq", None),
                "call_id": call_id_of(e),
                # 租户归属(GAP-10):取自**事件信封**(框架侧落盘的事实),
                # 不是从服务实例的 self.tenant_id 反推 —— 后者是"当前请求"的租户,
                # 前者是"该事件属于谁"的事实。
                "tenant_id": getattr(e, "tenant_id", None),
            })
        n = max(1, int(limit or 100))
        # 凭证核验面(离线可验):README 曾自陈 ``verify_receipt`` "未接 CLI/HTTP
        # /工具外壳"—— 库级可达而产品不可达。此处接上:**从事件日志重建**全部凭证
        # (``rebuild_from_log``,INV-G2/R2:不依赖任何内存缓存,重启后同样成立),
        # 逐条重算哈希(篡改任一受保护字段即 False),再校验 prev_hash 链单调。
        from pyharness.governance import verify_chain
        from pyharness.governance.receipt import rebuild_from_log
        all_rec = rebuild_from_log(log_)
        receipts: dict = {
            "count": len(all_rec),
            "verified": sum(1 for r in all_rec if verify_receipt(r)),
            "chain_valid": verify_chain(all_rec) if all_rec else True,
            "items": [{"receipt_id": r.receipt_id,
                       "decision_id": r.decision_id, "kind": r.kind,
                       "verified": verify_receipt(r),
                       "approval_ref": r.approval_ref,
                       "content_hash": r.content_hash,
                       "prev_hash": r.prev_hash} for r in all_rec],
        }
        return {
            "sid": str(sid), "ok": True,
            "tenant_id": tenant,
            "decisions": decisions[-n:][::-1],
            "denied": audit.denied_report(since_seq=int(since_seq or 0)),
            "findings": (await audit.reconcile()) if reconcile else [],
            "chain": audit.causal_chain(str(decision_id)) if decision_id else None,
            "receipts": receipts,
        }

    @admitted
    async def governance_evidence(self, sid: str, *, task_id: str = "") -> dict:
        """证据查询面:按任务段聚合的治理证据工件。

        GAP-8 修复点:此前 ``EvidenceCollector`` 只有订阅者、没有生产者
        (``archive()`` 零调用) ⇒ ``evidence.archived`` 永不产生、索引恒空、
        ``collect_for_task()`` 恒返回 ``()``。现由 ``engine.archive_task_evidence``
        在任务段结束后按冻结规则产出。

        **INV-E3 可核验**:回答**不取自订阅态内存索引**,而是由 ``from_log`` 从
        append-only 日志**现场重建**——故重启后同一日志得同一答案。``indexed`` 与
        ``rebuilt`` 两数并列展示,二者不一致即说明派生缓存与真源脱节(可观测)。
        """
        log_, spine = await self.public_spine_for(sid)
        coll = getattr(getattr(spine, "governance", None), "evidence", None)
        if coll is None:
            raise_code("CYC-999", module="application.service",
                       field="governance.evidence",
                       why="证据面未接线(EvidenceCollector 未注入 GovernanceContext)")
        from pyharness.governance import EvidenceCollector
        rebuilt = await EvidenceCollector.from_log(log_)     # INV-E3:由日志重建
        out: dict = {
            "sid": str(sid), "ok": True, "task_id": str(task_id),
            "indexed": coll.evidence_count(),
            "rebuilt": rebuilt.evidence_count(),
            "artifacts": [
                {"evidence_id": str((getattr(e, "payload", None) or {})
                                    .get("evidence_id") or ""),
                 "claim": str((getattr(e, "payload", None) or {}).get("claim") or ""),
                 "refs": list((getattr(e, "payload", None) or {}).get("refs") or []),
                 "seq": getattr(e, "seq", None)}
                for e in log_.events_after(0)
                if getattr(e, "type", None) == "evidence.archived"],
        }
        if task_id:
            out["for_task"] = [
                {"evidence_id": ev.evidence_id, "claim": ev.claim,
                 "refs": [{"kind": r.kind, "locator": r.locator}
                          for r in ev.refs]}
                for ev in rebuilt.collect_for_task(str(task_id))]
        return out

    @staticmethod
    def _last_env_of(log_: Any, type_: str):
        for ev in reversed(list(log_.events_after(0))):
            if ev.type == type_:
                return ev
        return None

    @admitted
    async def resend_text(self, log_: Any, sid: str, text: str) -> dict:
        self._ensure_open()
        text = str(text or "").strip()
        if not text:
            raise_code("EVT-100", advice="重发内容为空")
        env = await log_.append("user.message", {"content": text},
                                actor="user", origin=self.channel, sync=True)
        queue = await self.queue_for(sid, log_)
        task_id = await queue.submit(text, meta={"channel": self.channel,
                                                 "session_id": sid})
        return {"task_id": task_id, "user_seq": env.seq}

    @admitted
    async def edit_message(self, sid: str, seq: int, text: str,
                           *, resend: bool = False) -> dict:
        text = str(text or "").strip()
        log_ = await self.require_session(sid)
        from pyharness.core.message_edit import edit_user_message
        env = await edit_user_message(log_, int(seq), text)
        out: dict = {"ok": True, "edited_seq": env.seq, "target_seq": int(seq)}
        if resend:
            out["resend"] = await self.resend_text(log_, sid, text)
        return out

    @admitted
    async def resend_message(self, sid: str, seq: int) -> dict:
        log_ = await self.require_session(sid)
        env = log_.get(int(seq))
        if env is None or env.type != "user.message":
            raise_code("EVT-101", seq=int(seq), hint="目标不是 user.message,无法重发")
        return await self.resend_text(log_, sid, env.payload.get("content", ""))

    @admitted
    async def feedback(self, sid: str, seq: int, kind: str,
                       note: str = "") -> dict:
        kind = str(kind or "")
        if kind not in ("up", "down", "flag"):
            raise_code("EVT-100", field="kind", kind=kind,
                       advice="kind 取值 up/down/flag")
        log_ = await self.require_session(sid)
        env = log_.get(int(seq))
        if env is None or env.type != "agent.message":
            raise_code("EVT-101", seq=int(seq), hint="反馈目标须是 agent.message")
        await log_.append("user.feedback",
                          {"target_seq": int(seq), "kind": kind,
                           "note": str(note or "")[:500] or None},
                          actor="user", sync=True)
        return {"ok": True, "target_seq": int(seq), "kind": kind}

    @admitted
    async def edit_last_user(self, sid: str, text: str,
                             *, resend: bool = False) -> dict:
        log_ = await self.require_session(sid)
        env = self._last_env_of(log_, "user.message")
        if env is None:
            raise_code("EVT-101", hint="会话里还没有可编辑的用户消息")
        return await self.edit_message(sid, env.seq, text, resend=resend)

    @admitted
    async def resend_last_user(self, sid: str) -> dict:
        log_ = await self.require_session(sid)
        env = self._last_env_of(log_, "user.message")
        if env is None:
            raise_code("EVT-101", hint="会话里还没有可重发的用户消息")
        return await self.resend_message(sid, env.seq)

    @admitted
    async def feedback_last_agent(self, sid: str, kind: str,
                                  note: str = "") -> dict:
        log_ = await self.require_session(sid)
        env = self._last_env_of(log_, "agent.message")
        if env is None:
            raise_code("EVT-101", hint="会话里还没有 agent 回复可反馈")
        return await self.feedback(sid, env.seq, kind, note)

