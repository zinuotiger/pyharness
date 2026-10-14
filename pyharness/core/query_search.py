"""QuerySearch operations, separated from facade assembly."""
from __future__ import annotations
import asyncio
import sqlite3
from typing import Any, Optional
from pyharness.errors import raise_code
from pyharness.core.query_projection import _CONTENT_COL, _CJK_RUN, _WORD, _tokenize, _pretty_snippet, Hit, QueryResult, _log_warn

class QuerySearch:
    """QuerySearch responsibility; state is owned by the composing facade."""

    async def query(self, q: str, *, limit: int = 20,
                    session_id: Optional[str] = None,
                    session_ids: Optional[set[str]] = None,
                    actor: Optional[str] = None,
                    after_ts: Optional[str] = None) -> QueryResult:
        """全文查询(spec 伪码,F057 验收直译)。

        解析(中文 2-gram + 英文 ≥3 字符词)→ 拼 MATCH(词间 OR + 整串短语补偿)→
        SQLite 查询带 snippet() → 按 rank 取 ≤min(limit, result_limit) 条 Hit;
        词长低于下限 → EVT-100;5s 超时**软降级**返回 {hits: [], timed_out: True}
        不抛不挂(WARNING 留痕);库通道故障(非超时)→ PERS-202。
        """
        if self._db is None:
            raise_code("PERS-202", op="fts_query",
                       hint="索引未装载(enter 未执行或已 detach)",
                       advice="先 enter/重新装载索引")
        if not isinstance(q, str) or not q.strip():
            raise_code("EVT-100", hint=f"查询词非法: {q!r}",
                       advice="中文≥2 字、英文≥3 字符;或直接给整句短语")
        terms = self._cjk_terms(q) + self._latin_terms(q)
        if not terms:                                  # 中文<2 且英文<3 → 拒
            raise_code("EVT-100", hint=f"查询词过短: {q!r}",
                       advice="中文≥2 字、英文≥3 字符;或直接给整句短语")
        expr = self._match_expr(q)
        sql = ("SELECT session_id, seq, type, ts, "
               f"snippet(events_fts, {_CONTENT_COL}, '[', ']', '…', 24), rank "
               "FROM events_fts WHERE events_fts MATCH ? AND seq>0")
        args: list = [expr]
        if session_id:
            sql += " AND session_id=?"
            args.append(session_id)
        if session_ids is not None:
            # An explicit empty scope must never fall back to the whole index.
            if not session_ids:
                return QueryResult(hits=[], timed_out=False)
            sql += " AND session_id IN (" + ",".join("?" for _ in session_ids) + ")"
            args.extend(sorted(session_ids))
        if actor:
            sql += " AND actor=?"
            args.append(actor)
        if after_ts:
            sql += " AND ts>=?"                       # ISO8601 UTC 字典序可比
            args.append(after_ts)
        cap = self._clamp_limit(limit)
        sql += f" ORDER BY rank LIMIT {cap}"
        try:
            hits = await asyncio.wait_for(
                asyncio.to_thread(self._execute_query, sql, args),
                timeout=self.query_timeout_s)
            return QueryResult(hits=hits, timed_out=False)
        except asyncio.TimeoutError:                   # 超时软降级:不抛不挂
            _log_warn("fts query timeout q=%r(>%ss,软降级返回空)", q,
                      self.query_timeout_s)
            return QueryResult(hits=[], timed_out=True)
        except sqlite3.Error as e:                     # 库通道故障(非超时)
            raise_code("PERS-202", op="fts_query", hint=f"FTS 查询失败: {e}",
                       advice="运行 repair(F060) 重建索引")

    def _clamp_limit(self, limit: Any) -> int:
        """limit 钳制:1 ≤ limit ≤ result_limit(超限钳到 config result_limit)。"""
        try:
            lim = int(limit)
        except (TypeError, ValueError):
            lim = self.result_limit
        return max(1, min(lim, self.result_limit))

    def _execute_query(self, sql: str, args: list) -> list[Hit]:
        """同步查询执行(线程内;锁内取尽结果防跨线程游标)。

        MATCH 语法错(极端畸形串)→ 空集(检索场景降级优于报错);结果 snippet 做
        中文相邻字空格回缩美化(偏离说明:索引侧每字成词的展示补偿)。
        """
        try:
            with self._lock:
                rows = self._db.execute(sql, args).fetchall()
            return [Hit(session_id=r[0], seq=r[1], type=r[2], ts=r[3],
                        snippet=_pretty_snippet(r[4]), rank=float(r[5]))
                    for r in rows]
        except sqlite3.OperationalError:
            return []                                  # MATCH 语法错 → 空集
        except sqlite3.Error:
            raise

    def _cjk_terms(self, q: str) -> list[str]:
        """中文段 2-gram 滑窗(每段 ≥2 字才产词;单字段自然为空,上层 EVT-100)。

        例:"整理文件夹" → [整理, 理文, 文件, 件夹](spec 伪码原样;含"夹?"整串
        短语由 _match_expr 连续子串补偿兜住)。
        """
        out: list[str] = []
        for seg in _CJK_RUN.findall(q):
            for i in range(len(seg) - 1):
                out.append(seg[i:i + 2])
        return out

    def _latin_terms(self, q: str) -> list[str]:
        """拉丁/数字词抽取(≥3 字符才入词;英文下限 F057 边界)。"""
        return _WORD.findall(q)

    def _match_expr(self, q: str) -> str:
        """MATCH 表达式:2-gram 相邻单字短语 OR 拉丁词短语 + 整串连续子串补偿。

        每个 2-gram 转 quoted phrase("备份" → `"备 份"`,相邻 token 序列)——unicode61
        把中文连续串当单 token,索引侧已逐字空格化,故短语即"原文相邻字"语义;
        len(q) ≥ 4 时把整串按同一 tokenizer 切成短语 OR 上(补偿 2-gram 割裂,
        spec "连续子串补偿":补召回保精度)。token 只来自中文单字/ASCII 词,天然
        无引号注入;去重保序。
        """
        parts: list[str] = []
        seen: set[str] = set()
        for g in self._cjk_terms(q):                   # "备份" → "备 份"
            ph = f'"{g[0]} {g[1]}"'
            if ph not in seen:
                seen.add(ph)
                parts.append(ph)
        for w in self._latin_terms(q):
            ph = f'"{w}"'
            if ph not in seen:
                seen.add(ph)
                parts.append(ph)
        if len(q) >= 4:                                # 整串短语补偿(≥4 字符)
            toks = _tokenize(q)
            if toks:
                ph = '"' + " ".join(toks) + '"'
                if ph not in seen:
                    parts.append(ph)
        return " OR ".join(parts)

