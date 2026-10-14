"""QueryMaintenance operations, separated from facade assembly."""
from __future__ import annotations
import sqlite3
from typing import Any, Iterator, Optional
from pyharness.errors import raise_code
from pyharness.core.query_projection import log, _index_text, _field, _replay_store

class QueryMaintenance:
    """QueryMaintenance responsibility; state is owned by the composing facade."""

    def attach_source(self, session_id: str, store: Any) -> None:
        """登记会话回放源(rebuild 读 JSONL 真源用;派生服从真源)。"""
        self._sources[str(session_id)] = store

    def _iter_replay(self, session_id: Optional[str]) -> Iterator[Any]:
        """按作用域产出待重放事件:单会话取其源,全量遍历所有源(seq 升序)。"""
        if session_id is not None:
            store = self._sources.get(str(session_id))
            if store is None:
                return                                  # 未知会话:空(无源可重放)
            yield from _replay_store(store)
            return
        for store in list(self._sources.values()):
            yield from _replay_store(store)

    async def rebuild(self, *, session_id: Optional[str] = None) -> int:
        """索引整体重建(spec 伪码;F060/repair 对账权威入口):清派生行 → 重放重插。

        作用域:session_id 指定 = 单会话(先 DELETE 该会话索引行);None = 全量
        (DELETE 全表——派生可弃,JSONL 一行不改)。重放逐事件走与 on_event 同一
        抽取逻辑(含 user.message_edited 修正,偏离 3),整批单事务提交(读侧见
        旧态或新态,无中间态);返回重建插入行数。回放遇源异常/落库失败 →
        PERS-202(坏行隔离纪律由注入 replay 源承担,PERS-201 域)。
        """
        if self._db is None:
            raise_code("PERS-202", op="fts_rebuild",
                       hint="索引未装载(enter 未执行或已 detach)",
                       advice="先 enter/重新装载索引")
        await self.flush()                             # 先清缓冲防新旧混写
        rows: list[tuple] = []
        edits: list[tuple] = []
        for env in self._iter_replay(session_id):
            item = self._extract(_field(env, "type"), env)
            if item is None:
                continue
            if item[0] == "edit":
                edits.append(item[1])
            else:
                rows.append(item[1])
        try:
            with self._lock:
                with self._db:                         # DELETE+重插同事务(原子)
                    if session_id:
                        self._db.execute(
                            "DELETE FROM events_fts WHERE session_id=?", (session_id,))
                        self._db.execute(
                            "DELETE FROM fts_rows WHERE session_id=?", (session_id,))
                    else:
                        self._db.execute("DELETE FROM events_fts")
                        self._db.execute("DELETE FROM fts_rows")
                    # 幂等闸同事务重填(偏离 6 修复:rebuild 只清不填会让后续增量
                    # flush 绕过闸产生重复行,行键唯一语义被破坏)
                    for r in rows:
                        self._db.execute(
                            "INSERT INTO fts_rows(session_id, seq) VALUES(?,?)",
                            (r[0], r[1]))
                    self._db.executemany(
                        "INSERT INTO events_fts VALUES(?,?,?,?,?,?,?)", rows)
                    for sid, tseq, newc in edits:
                        self._db.execute(
                            "UPDATE events_fts SET content=? "
                            "WHERE session_id=? AND seq=?",
                            (_index_text(str(newc)), sid, tseq))
        except sqlite3.Error as e:
            raise_code("PERS-202", op="fts_rebuild", session=session_id,
                       hint=f"FTS 重建落库失败: {e}",
                       advice="修存储后重试 rebuild;索引为派生副本可整体重建")
        log.info("session fts rebuilt session=%s rows=%d edits=%d",
                 session_id or "*", len(rows), len(edits))
        return len(rows)

    async def reconcile_sources(self, sources, *, authorized_ids=None):
        """Incrementally project an explicitly authorized set; prune removed access.

        Watermarks are derived metadata. A crash before watermark commit replays
        idempotently, and recreation of the database rebuilds from sequence zero.
        Caller serializes reconcile/query within its tenant's application lock.
        """
        db = self._db
        if db is None:
            raise_code('PERS-202', op='fts_reconcile')
        db.execute('CREATE TABLE IF NOT EXISTS source_progress (sid TEXT PRIMARY KEY, seq INTEGER NOT NULL)')
        previous = dict(db.execute('SELECT sid,seq FROM source_progress'))
        known = set(previous) | {r[0] for r in db.execute('SELECT DISTINCT session_id FROM fts_rows')}
        authorized = set(sources) if authorized_ids is None else set(authorized_ids)
        if not set(sources) <= authorized:
            raise_code('EVT-100', hint='Index sources exceed authorized scope')
        for sid in known - authorized:
            await self.delete_session(sid)
            db.execute('DELETE FROM source_progress WHERE sid=?',(sid,))
        scanned = 0
        self._sources = dict(sources)
        for sid, source in sources.items():
            last = previous.get(sid, 0)
            current = source.stats()['seq']
            if current < last:
                await self.delete_session(sid)
                self._sources[sid] = source
                last = 0
            for event in source.events_after(last):
                await self.on_event(event.type, event)
                scanned += 1
            await self.flush()
            db.execute('INSERT OR REPLACE INTO source_progress VALUES (?,?)',(sid,current))
            db.commit()
        return scanned

    async def delete_session(self, session_id: str) -> None:
        """会话删除级联删索引(PRD F057 边界;JSONL 归档/删除由存储层负责)。

        只清本模块派生行(events_fts + 幂等闸 fts_rows);同步摘除回放源(防全量
        rebuild 复活已删会话);失败 → PERS-202(索引可整库重建兜底)。
        """
        if self._db is None:
            raise_code("PERS-202", op="fts_delete_session",
                       hint="索引未装载(enter 未执行或已 detach)",
                       advice="先 enter/重新装载索引")
        self._sources.pop(str(session_id), None)
        try:
            with self._lock:
                with self._db:
                    self._db.execute("DELETE FROM events_fts WHERE session_id=?",
                                     (session_id,))
                    self._db.execute("DELETE FROM fts_rows WHERE session_id=?",
                                     (session_id,))
                    self._db.execute('CREATE TABLE IF NOT EXISTS source_progress (sid TEXT PRIMARY KEY, seq INTEGER NOT NULL)')
                    self._db.execute('DELETE FROM source_progress WHERE sid=?', (session_id,))
        except sqlite3.Error as e:
            raise_code("PERS-202", op="fts_delete_session", session=session_id,
                       hint=f"FTS 级联删除失败: {e}",
                       advice="重试;索引为派生副本可整库重建兜底")
        log.info("session fts delete_session %s", session_id)

