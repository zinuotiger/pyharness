"""tests/unit/test_session_query.py — 会话全文查询 FTS5 单测(specs/session_query.py.md F057)。

覆盖面(spec 类与函数清单逐条 + F063 编辑级联 + F060/rebuild 对账 + ERR 码):
- enter:建表(events_fts + 伴生 fts_rows)、幂等、文件库 WAL/权限 600(尽力而为)、
  开库失败 → PERS-202(回 detached 无半态)
- 事件 → 索引:白名单 6 类文本抽取、瞬时/白名单外/空文本不索引、增量追加、
  ≥64 条自动落库闸、总线订阅接线与 detach 退订
- query:命中字段(seq 锚点/snippet/rank)、session_id/actor/after_ts 过滤、
  limit 钳到 result_limit、空集、超短词/空串 → EVT-100、未装载/已摘 → PERS-202
- 中文检索:2-gram 滑窗与整串补偿、单字拒、拉丁词 ≥3、混合串、MATCH 注入安全
- 超时软降级:5s 闸(测试用小阈值)返回 QueryResult(timed_out=True) 不抛
- rebuild:全量/单会话、INV-03 与增量逐行一致、编辑重放、二次 rebuild 无重复行
  (幂等闸回归)、未知会话空重建
- delete_session:级联删索引行 + 摘回放源
- detach:幂等、flush 未落批(文件库重开可见)、announce 挂载/注册/摘除、
  FtsQueryHandle 只读面(TLB-802 未挂载)

存储一律 :memory:/tmp_path(不碰真实 ~/.pyharness);事件用 SimpleNamespace 双形态
(dict 也通,模块 _field/_payload 双形态支持)。错误码经 PyHError.code 断言。
"""
import sqlite3
import sys
import time
import types
from pathlib import Path

import pytest

from pyharness.bus import EventBus
from pyharness.core.session_query import (
    DEFAULT_DB_NAME,
    FtsQueryHandle,
    Hit,
    QueryResult,
    SessionQueryIndex,
    TOOL_NAME,
    default_db_path,
)
from pyharness.core.tools_registry import ToolRegistry
from pyharness.errors import PyHError

SID = "s-abc12345"          # 本会话(行键测试主体)
SID2 = "s-other99999"       # 他会话(隔离断言)
TS0 = "2026-09-07T00:00:00.000000Z"


# ===================================================================== 替身
def ev(sid: str, seq: int, typ: str, *, actor: str = "user",
       ts: str | None = None, payload: dict | None = None) -> types.SimpleNamespace:
    """事件信封替身(SimpleNamespace 形态;字段与 Envelope 十字段子集一致)。"""
    if ts is None:
        ts = f"2026-09-07T00:00:00.{seq:06d}Z"
    return types.SimpleNamespace(session_id=sid, seq=seq, type=typ, ts=ts,
                                 actor=actor, payload=payload or {})


def msg(sid: str, seq: int, content: str, typ: str = "user.message",
        **kw) -> types.SimpleNamespace:
    """content 载荷的常用事件替身。"""
    return ev(sid, seq, typ, payload={"content": content}, **kw)


def edited(sid: str, seq: int, target_seq: int, new_content: str,
           **kw) -> types.SimpleNamespace:
    """user.message_edited(F063 级联更新 target 行)。"""
    return ev(sid, seq, "user.message_edited",
              payload={"target_seq": target_seq, "new_content": new_content}, **kw)


class FakeStore:
    """rebuild 回放源替身(鸭子协议 replay();SessionStore 形态)。"""

    def __init__(self, events: list) -> None:
        self._events = list(events)

    def replay(self):
        return list(self._events)


def table_rows(idx: SessionQueryIndex, table: str) -> list[tuple]:
    """整表行快照(INV-03 派生比对用;断言前取尽,防跨线程游标)。"""
    with idx._lock:
        return sorted(idx._db.execute(f"SELECT * FROM {table}").fetchall())


def assert_no_dup(idx: SessionQueryIndex, sid: str | None = None) -> None:
    """行键 (session_id, seq) 唯一断言:全库或单会话无重复行。"""
    sql = ("SELECT session_id, seq, COUNT(*) c FROM events_fts"
           " GROUP BY session_id, seq HAVING c > 1")
    args: tuple = ()
    if sid is not None:
        sql = ("SELECT session_id, seq, COUNT(*) c FROM events_fts"
               " WHERE session_id=? GROUP BY session_id, seq HAVING c > 1")
        args = (sid,)
    with idx._lock:
        dup = idx._db.execute(sql, args).fetchall()
    assert dup == [], f"行键 (session_id, seq) 出现重复: {dup}"


# 默认内存索引夹具:无总线 → 不起 200ms 定时器;统一 detach 防任务泄漏
@pytest.fixture
async def idx() -> SessionQueryIndex:
    ix = SessionQueryIndex(db_path=":memory:", batch_ms=60000)
    await ix.enter(ctx=None)
    yield ix
    await ix.detach()


# ===================================================================== enter
class TestEnter:
    async def test_enter_creates_tables_and_idempotent(self):
        """建表成功(events_fts + 伴生 fts_rows)+ enter 幂等。"""
        ix = SessionQueryIndex(db_path=":memory:")
        await ix.enter(ctx=None)
        try:
            assert ix._db is not None
            tables = {r[0] for r in ix._db.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','virtual')")}
            assert "events_fts" in tables and "fts_rows" in tables
            # events_fts 7 列(含 actor UNINDEXED 冗余,偏离 1)
            cols = [r[1] for r in ix._db.execute("PRAGMA table_info(events_fts)")]
            assert cols == ["session_id", "seq", "type", "ts", "actor",
                            "title", "content"]
            # 伴生表行键主键(幂等闸)
            pk = ix._db.execute(
                "SELECT sql FROM sqlite_master WHERE name='fts_rows'").fetchone()[0]
            assert "PRIMARY KEY" in pk
            await ix.enter(ctx=None)                  # 幂等:二次 enter 直接返回
            assert ix._db is not None
        finally:
            await ix.detach()

    async def test_enter_file_db_wal(self, tmp_path: Path):
        """文件库:WAL 模式 + 权限 600(posix 断言;Windows ACL 尽力而为)。"""
        p = tmp_path / "idx" / "index.db"
        ix = SessionQueryIndex(db_path=p)
        try:
            await ix.enter(ctx=None)
            assert p.exists()
            mode = ix._db.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode == "wal"
            if not sys.platform.startswith("win"):
                assert (p.stat().st_mode & 0o777) == 0o600
        finally:
            await ix.detach()

    async def test_enter_failure_pers_202(self, tmp_path: Path):
        """开库失败(路径是目录)→ PERS-202;回 detached 不留半态(_db is None)。"""
        d = tmp_path / "not_a_db"
        d.mkdir()
        ix = SessionQueryIndex(db_path=d)             # 指向目录 → 无法打开
        with pytest.raises(PyHError) as ei:
            await ix.enter(ctx=None)
        assert ei.value.code == "PERS-202"
        assert ix._db is None                         # 无半态残留

    def test_default_db_path_anchor(self):
        """默认索引库路径锚点:storage.root(~/.pyharness) 下 index.db。"""
        assert DEFAULT_DB_NAME == "index.db"
        assert str(default_db_path()).endswith(
            f"{Path.home() / '.pyharness' / 'index.db'}")


# ================================================================= 事件 → 索引
class TestIndexing:
    async def test_index_user_and_agent_content(self, idx):
        """白名单 user.message/agent.message content 入索引,命中带完整字段。"""
        await idx.on_event("user.message", msg(SID, 1, "帮我整理桌面文件夹并备份到网盘"))
        await idx.on_event("agent.message", msg(SID, 2, "好的,已完成备份。", typ="agent.message"))
        n = await idx.flush()
        assert n == 2
        r = await idx.query("备份")
        assert not r.timed_out
        assert {h.seq for h in r.hits} == {1, 2}
        h = r.hits[0]
        assert isinstance(h, Hit)
        assert h.session_id == SID and isinstance(h.seq, int)
        assert h.type in ("user.message", "agent.message")
        assert h.ts.startswith("2026-09-07T") and h.ts.endswith("Z")
        assert "备份" in h.snippet                  # 片段带命中词
        assert isinstance(h.rank, float)

    async def test_whitelist_other_types(self, idx):
        """tool.result(summ)、session.renamed(new_title)、context.compacted(summ) 入索引。"""
        await idx.on_event("tool.result", ev(SID, 1, "tool.result",
                                             payload={"summary": "扫描到 128 个文件备份完成"}))
        await idx.on_event("session.renamed", ev(SID, 2, "session.renamed",
                                                 payload={"new_title": "网盘备份项目"}))
        await idx.on_event("context.compacted", ev(SID, 3, "context.compacted",
                                                   payload={"summary": "前文已压缩:备份策略已定"}))
        await idx.flush()
        assert {h.seq for h in (await idx.query("备份")).hits} == {1, 2, 3}
        # 标题列命中(搜索词出现在 new_title 而非 content)
        assert {h.seq for h in (await idx.query("网盘")).hits} == {2}

    async def test_transient_and_empty_not_indexed(self, idx):
        """瞬时事件(llm.chunk)/白名单外(guard.*)/空文本(llm.response 空轮)不索引。"""
        await idx.on_event("llm.chunk", msg(SID, 1, "增量令牌", typ="llm.chunk"))
        await idx.on_event("guard.user_input", msg(SID, 2, "拦截词", typ="guard.user_input"))
        await idx.on_event("llm.response", msg(SID, 3, "", typ="llm.response"))
        await idx.on_event("user.message", msg(SID, 4, "   "))     # 空白内容
        assert await idx.flush() == 0
        assert idx._pending == [] and idx._pending_edits == []
        assert (await idx.query("增量")).hits == []
        assert (await idx.query("备份")).hits == []

    async def test_rowkey_missing_skipped(self, idx):
        """缺 session_id/seq(无行键)→ 拒插,不产生孤儿行。"""
        bad = types.SimpleNamespace(seq=None, type="user.message", ts=TS0,
                                    actor="user", payload={"content": "备份正文"})
        await idx.on_event("user.message", bad)
        assert await idx.flush() == 0

    async def test_incremental_append(self, idx):
        """增量:新事件 flush 后立即可查;旧命中保留。"""
        await idx.on_event("user.message", msg(SID, 1, "第一轮备份"))
        await idx.flush()
        assert {h.seq for h in (await idx.query("备份")).hits} == {1}
        await idx.on_event("user.message", msg(SID, 2, "第二轮也备份了"))
        await idx.flush()
        assert {h.seq for h in (await idx.query("备份")).hits} == {1, 2}
        # flush 空缓冲 → 0(幂等)
        assert await idx.flush() == 0

    async def test_autoflush_at_64(self, idx):
        """攒批条数闸:≥64 条自动落库,缓冲余量 <64。"""
        for i in range(1, 71):
            await idx.on_event("user.message", msg(SID, i, f"备份批次{i:03d}"))
        assert len(idx._pending) == 70 - 64           # 第 64 条触发一次自动 flush
        # 已落库的可查(拉丁词 "064" 只出现在批次 64,避开 2-gram 全召回);
        # 未落库的(最后 6 条)不可查
        assert {h.seq for h in (await idx.query("064")).hits} == {64}
        assert (await idx.query("070")).hits == []
        await idx.flush()                             # 余量落库后全量可查
        assert {h.seq for h in (await idx.query("070")).hits} == {70}
        assert len(table_rows(idx, "events_fts")) == 70
        assert_no_dup(idx)

    async def test_bus_subscription_and_detach_unsub(self):
        """总线接线:emit 持久事件 → on_event 入缓冲;detach 按 owner 退订。"""
        bus = EventBus()
        ctx = types.SimpleNamespace(bus=bus, config=None, cfg=None)
        ix = SessionQueryIndex(db_path=":memory:", batch_ms=60000)
        await ix.enter(ctx)
        try:
            assert ix._bus is bus
            payload = {"type": "user.message", "session_id": SID, "seq": 1,
                       "ts": TS0, "actor": "user",
                       "payload": {"content": "总线送达的备份指令"}}
            await bus.emit("user.message", payload)
            assert len(ix._pending) == 1              # 订阅回调已攒批
            await ix.flush()
            assert {h.seq for h in (await ix.query("备份")).hits} == {1}
        finally:
            await ix.detach()
        assert ix._bus is None
        # detach 后总线无残留订阅(cap:session_fts 全部摘除)
        subs = [s for lst in bus._by_type.values() for s in lst] + list(bus._wild)
        assert all(s.owner != "cap:session_fts" for s in subs)


# ===================================================================== query
class TestQuery:
    async def test_filters_session_actor_after_ts(self, idx):
        """过滤:session_id / actor / after_ts(ISO8601 UTC 字典序)。"""
        await idx.on_event("user.message", msg(SID, 1, "在 A 会话备份"))
        await idx.on_event("agent.message", msg(SID, 2, "在 A 会话也备份", typ="agent.message"))
        await idx.on_event("user.message", msg(SID2, 1, "在 B 会话备份"))
        await idx.on_event("user.message", msg(SID, 3, "A 会话第三条备份",
                                              actor="agent"))
        await idx.flush()
        # session_id 过滤(seq2 agent.message 同属 SID 且含"备份")
        assert {h.seq for h in (await idx.query("备份", session_id=SID)).hits} == {1, 2, 3}
        # actor 过滤(actor 冗余列,偏离 1 的功能落点)
        assert {h.seq for h in (await idx.query("备份", session_id=SID,
                                                actor="agent")).hits} == {3}
        # after_ts 过滤:≥ 给定时间戳(seq2/3 的时间戳不早于截止)
        ts_mid = "2026-09-07T00:00:00.000002Z"
        got = (await idx.query("备份", session_id=SID, after_ts=ts_mid)).hits
        assert {h.seq for h in got} == {2, 3}
        # 过滤组合无命中 → 空
        assert (await idx.query("备份", session_id=SID2, actor="agent")).hits == []

    async def test_limit_clamped_to_result_limit(self):
        """limit 钳制:≤ result_limit;越界钳到 result_limit。"""
        ix = SessionQueryIndex(db_path=":memory:", result_limit=3)
        await ix.enter(ctx=None)
        try:
            for i in range(1, 8):
                await ix.on_event("user.message", msg(SID, i, f"备份文件第{i}号"))
            await ix.flush()
            r = await ix.query("备份", limit=100)     # 钳到 result_limit=3
            assert len(r.hits) == 3
            r2 = await ix.query("备份", limit=2)      # 未超限按请求
            assert len(r2.hits) == 2
        finally:
            await ix.detach()

    async def test_query_no_match_empty(self, idx):
        """无命中 → 空结果,timed_out=False(不抛)。"""
        await idx.on_event("user.message", msg(SID, 1, "整理桌面"))
        await idx.flush()
        r = await idx.query("量子计算机")
        assert isinstance(r, QueryResult)
        assert r.hits == [] and r.timed_out is False

    async def test_query_short_terms_evt100(self, idx):
        """词长低于下限(F057 边界:中文<2 字、英文<3 字符)→ EVT-100。"""
        for bad in ("备", "ab", "  ", "", "!!!", None, 123):
            with pytest.raises(PyHError) as ei:
                await idx.query(bad)
            assert ei.value.code == "EVT-100", f"q={bad!r} 应拒 EVT-100"

    async def test_query_not_entered_or_detached_pers_202(self):
        """未装载(enter 前)与已摘(detach 后)查询 → PERS-202。"""
        ix = SessionQueryIndex(db_path=":memory:")
        with pytest.raises(PyHError) as ei:
            await ix.query("备份")
        assert ei.value.code == "PERS-202"
        await ix.enter(ctx=None)
        await ix.on_event("user.message", msg(SID, 1, "备份一次"))
        await ix.flush()
        await ix.detach()
        with pytest.raises(PyHError) as ei:
            await ix.query("备份")
        assert ei.value.code == "PERS-202"

    async def test_query_rank_ordered(self, idx):
        """按 FTS5 rank 排序:整串短语命中(高相关)排前,部分 2-gram 命中殿后。"""
        await idx.on_event("user.message", msg(SID, 1, "把桌面所有文件备份到网盘"))
        await idx.on_event("user.message", msg(SID, 2, "整理桌面文件夹并执行备份"))
        await idx.on_event("user.message", msg(SID, 3, "今天天气不错"))
        await idx.flush()
        r = await idx.query("整理桌面文件夹并执行备份")
        seqs = [h.seq for h in r.hits]
        assert seqs[0] == 2                          # 整串命中最前(rank 最优)
        assert 1 in seqs and 3 not in seqs           # 2-gram OR 召回 seq1;无关行不召回

    async def test_query_match_expr_no_injection(self, idx):
        """畸形查询串(引号/运算符)经自研 tokenizer 清洗,不破坏 MATCH 语法(偏离 4)。"""
        await idx.on_event("user.message", msg(SID, 1, "备份文件已生成"))
        await idx.flush()
        # 引号/注释符/星号都不会整体嵌入 MATCH(OperationalError 兜底为双保险)
        r = await idx.query('备份" OR 1=1 --')
        assert not r.timed_out
        assert {h.seq for h in r.hits} == {1}
        r2 = await idx.query('("NEAR(备份*)')
        assert not r2.timed_out                      # 不抛(空集或命中皆可)


# ================================================================= 中文检索
class TestChineseSearch:
    async def test_bigram_hit_adjacent_chars(self, idx):
        """2-gram 命中:查询词是原文连续字串的子串才命中。"""
        await idx.on_event("user.message", msg(SID, 1, "把备份文件放网盘"))
        await idx.on_event("user.message", msg(SID, 2, "备份执行完毕"))
        await idx.flush()
        assert {h.seq for h in (await idx.query("备份")).hits} == {1, 2}
        # 顺序敏感:反向字序不命中(2-gram 短语 = 相邻 token 序列)
        assert (await idx.query("份备")).hits == []

    async def test_bigram_sliding_window_query(self, idx):
        """多字查询:滑窗 2-gram OR + 整串短语补偿收紧(搜索两端行为一致)。"""
        await idx.on_event("user.message", msg(SID, 1, "整理文件夹这事交给你"))
        await idx.on_event("user.message", msg(SID, 2, "先整理再备份文件夹"))
        await idx.flush()
        r = await idx.query("整理文件夹")
        assert {h.seq for h in r.hits} == {1, 2}
        # 整串命中者 snippet 含连续整串
        top = r.hits[0]
        assert "整理文件夹" in top.snippet.replace("[", "").replace("]", "")

    async def test_single_cjk_char_rejected(self, idx):
        """单中文字 → 词库空 → EVT-100(上层拒,不静默空集)。"""
        await idx.on_event("user.message", msg(SID, 1, "备份开始"))
        await idx.flush()
        with pytest.raises(PyHError) as ei:
            await idx.query("备")
        assert ei.value.code == "EVT-100"

    async def test_latin_word_ge3(self, idx):
        """英文/数字词 ≥3 字符命中;2 字符词拒。"""
        await idx.on_event("user.message", msg(SID, 1, "任务 backup 已完成"))
        await idx.on_event("user.message", msg(SID, 2, "任务 xx 未开始"))
        await idx.flush()
        assert {h.seq for h in (await idx.query("backup")).hits} == {1}
        # "xx" 与中文混合:仅中文 2-gram 在,英文 2 字符被丢弃
        assert {h.seq for h in (await idx.query("xx 未开始")).hits} == {2}

    async def test_mixed_cjk_latin_query(self, idx):
        """中英混合查询:中文 2-gram 与拉丁词 OR 合并。"""
        await idx.on_event("user.message", msg(SID, 1, "把 backup 文件上传网盘"))
        await idx.flush()
        r = await idx.query("backup 网盘")
        assert {h.seq for h in r.hits} == {1}

    def test_tokenizer_pure_functions(self, idx):
        """分词器纯函数边界(GWT-F057-03):2-gram 滑窗/拉丁 ≥3/混合拆解。"""
        assert idx._cjk_terms("整理文件夹") == ["整理", "理文", "文件", "件夹"]
        assert idx._cjk_terms("备份") == ["备份"]
        assert idx._cjk_terms("a备b") == []           # 单字不产 2-gram
        assert idx._cjk_terms("") == []
        assert idx._latin_terms("backup 备份") == ["backup"]
        assert idx._latin_terms("ab") == []
        assert idx._latin_terms("v1.2.3") == []       # 点分隔,无 ≥3 连续词段
        assert idx._latin_terms("backup ver123 完成") == ["backup", "ver123"]

    def test_match_expr_shapes(self, idx):
        """MATCH 表达式:2-gram 转相邻单字短语;整串 ≥4 补短语;无注入原文。"""
        expr = idx._match_expr("备份")
        assert '"备 份"' in expr
        expr2 = idx._match_expr("整理文件夹")
        assert '"整 理"' in expr2 and '"文 件"' in expr2
        # 整串补偿短语(去重保序;token 不含引号字符)
        assert '"整 理 文 件 夹"' in expr2
        # 引号/星号只来自我们自己的 token,不进原文串(防注入,偏离 4)
        assert "1=1" not in idx._match_expr('备份" OR 1=1 --')


# ================================================================= 编辑级联 F063
class TestEdited:
    async def test_edited_updates_target_row(self, idx):
        """user.message_edited → 索引行换新版(派生视图更新,真源不动):新词命中旧词消失。"""
        await idx.on_event("user.message", msg(SID, 1, "请把桌面文件备份到网盘"))
        await idx.flush()
        assert {h.seq for h in (await idx.query("桌面")).hits} == {1}
        await idx.on_event("user.message_edited", edited(SID, 2, 1, "请把照片同步到私有云"))
        await idx.flush()
        assert (await idx.query("桌面")).hits == []   # 旧内容不可再搜
        hits = (await idx.query("照片")).hits
        assert len(hits) == 1 and hits[0].seq == 1    # target 行仍是原 seq 锚点
        assert_no_dup(idx)

    async def test_edited_malformed_skipped(self, idx):
        """缺 target_seq/new_content 的编辑事件 → 不攒批不炸。"""
        await idx.on_event("user.message", msg(SID, 1, "原版备份指令"))
        await idx.flush()
        await idx.on_event("user.message_edited", edited(SID, 2, 0, ""))  # 空新版
        await idx.on_event("user.message_edited",
                           ev(SID, 3, "user.message_edited",
                              payload={"new_content": "无 target"}))
        assert idx._pending_edits == []              # 两条均被拒
        assert {h.seq for h in (await idx.query("备份")).hits} == {1}


# ================================================================= 重建 F060
class TestRebuild:
    def _source_events(self) -> list:
        """标准真源回放集:白名单多类型 + 一条编辑(F063 在 rebuild 中也要兑现)。"""
        return [
            msg(SID, 1, "把桌面文件夹备份到网盘"),
            msg(SID, 2, "第二轮整理完成", typ="agent.message"),
            ev(SID, 3, "tool.result", actor="tool",
               payload={"summary": "备份脚本执行成功"}),
            ev(SID, 4, "session.renamed", actor="system",
               payload={"new_title": "全量备份计划"}),
            msg(SID, 5, "", typ="llm.response"),     # 空文本:不索引
            edited(SID, 6, 1, "改把照片同步到私有云"),
        ]

    async def test_rebuild_full_matches_incremental(self, idx):
        """INV-03 派生比对:全量 rebuild 与增量订阅产生逐行一致的表。"""
        events = self._source_events()
        # 增量侧:逐条 on_event 模拟订阅流
        for e in events:
            await idx.on_event(e.type, e)
        await idx.flush()
        inc_rows = table_rows(idx, "events_fts")
        inc_gate = table_rows(idx, "fts_rows")
        assert len(inc_rows) == 4                     # 5 条索引 + 编辑更新 target(无新行)
        # rebuild 侧:同一真源重放
        idx.attach_source(SID, FakeStore(events))
        cnt = await idx.rebuild()                     # 全量
        assert cnt == 4
        assert table_rows(idx, "events_fts") == inc_rows
        assert table_rows(idx, "fts_rows") == inc_gate
        # 命中集一致:新版词可搜、旧版词不可搜(编辑在 rebuild 后仍生效,F063)
        assert {h.seq for h in (await idx.query("照片")).hits} == {1}
        assert (await idx.query("桌面")).hits == []
        assert {h.seq for h in (await idx.query("全量计划")).hits} == {4}  # 标题唯一词
        assert_no_dup(idx)

    async def test_rebuild_single_session_scope(self, idx):
        """单会话 rebuild:只清/只重放目标会话,他会话索引不动。"""
        for e in (msg(SID, 1, "A 会话说备份"), msg(SID2, 1, "B 会话说整理"),
                  msg(SID2, 2, "B 会话也说备份")):
            await idx.on_event(e.type, e)
        await idx.flush()
        idx.attach_source(SID, FakeStore([msg(SID, 1, "A 会话新版备份指令")]))
        cnt = await idx.rebuild(session_id=SID)
        assert cnt == 1
        a = (await idx.query("备份", session_id=SID)).hits
        assert len(a) == 1 and "新版" in a[0].snippet     # A 会话已按真源重放
        assert {h.seq for h in (await idx.query("备份", session_id=SID2)).hits} == {2}
        assert_no_dup(idx, SID)

    async def test_rebuild_unknown_session_noop(self, idx):
        """未知会话 rebuild:无源可重放 → 0 行,不动既有索引。"""
        await idx.on_event("user.message", msg(SID, 1, "保留行备份"))
        await idx.flush()
        assert await idx.rebuild(session_id="s-nosuch000") == 0
        assert {h.seq for h in (await idx.query("备份")).hits} == {1}

    async def test_rebuild_then_incremental_no_dup(self, idx):
        """幂等闸回归:rebuild 后迟到增量 flush 同 (sid, seq) 不再落重复行。"""
        events = [msg(SID, i, f"备份批次{i}") for i in (1, 2, 3)]
        idx.attach_source(SID, FakeStore(events))
        assert await idx.rebuild(session_id=SID) == 3
        assert len(table_rows(idx, "events_fts")) == 3
        assert len(table_rows(idx, "fts_rows")) == 3  # 伴生闸随 rebuild 重填(偏离 6 修复)
        # 模拟增量迟到重投:同事件再次 on_event + flush
        for e in events:
            await idx.on_event(e.type, e)
        await idx.flush()
        assert len(table_rows(idx, "events_fts")) == 3
        assert_no_dup(idx)
        # 二次 rebuild 幂等:行数稳定
        assert await idx.rebuild(session_id=SID) == 3
        assert len(table_rows(idx, "events_fts")) == 3
        assert_no_dup(idx)

    async def test_rebuild_not_entered_pers_202(self):
        """未装载 rebuild → PERS-202。"""
        ix = SessionQueryIndex(db_path=":memory:")
        with pytest.raises(PyHError) as ei:
            await ix.rebuild()
        assert ei.value.code == "PERS-202"

    async def test_replay_source_invalid_pers_201(self, idx):
        """回放源不支持 replay()/events_after(0) → PERS-201。"""
        idx.attach_source(SID, object())              # 无鸭子协议
        with pytest.raises(PyHError) as ei:
            await idx.rebuild(session_id=SID)
        assert ei.value.code == "PERS-201"


# ================================================================= 级联删除
class TestDeleteSession:
    async def test_delete_session_cascades(self, idx):
        """删会话级联删索引行:该会话不可再搜,他会话保留,源同步摘除。"""
        await idx.on_event("user.message", msg(SID, 1, "A 会话备份"))
        await idx.on_event("user.message", msg(SID2, 1, "B 会话备份"))
        await idx.flush()
        idx.attach_source(SID, FakeStore([msg(SID, 1, "A 会话备份")]))
        await idx.delete_session(SID)
        assert (await idx.query("备份", session_id=SID)).hits == []
        assert {h.seq for h in (await idx.query("备份", session_id=SID2)).hits} == {1}
        # 源已摘:全量 rebuild 不会复活已删会话
        await idx.rebuild()
        assert (await idx.query("备份", session_id=SID)).hits == []
        assert_no_dup(idx)

    async def test_delete_session_not_entered_pers_202(self):
        """未装载 delete_session → PERS-202。"""
        ix = SessionQueryIndex(db_path=":memory:")
        with pytest.raises(PyHError) as ei:
            await ix.delete_session(SID)
        assert ei.value.code == "PERS-202"


# ================================================================= 超时软降级
class TestTimeoutDegrade:
    async def test_query_timeout_soft_degrade(self, idx, monkeypatch):
        """>query_timeout_s → 软降级:返回 timed_out=True 空集,不抛不挂。"""
        idx.query_timeout_s = 0.05

        def slow(sql, args):                          # 模拟慢查询(线程内 sleep)
            time.sleep(0.5)
            return []

        monkeypatch.setattr(idx, "_execute_query", slow)
        t0 = time.monotonic()
        r = await idx.query("备份")
        assert r.timed_out is True and r.hits == []   # 5s 语义以测试小阈值为证
        assert time.monotonic() - t0 < 0.3            # 未被慢查询拖住

    async def test_query_operational_error_returns_empty(self, idx, monkeypatch):
        """MATCH 语法错(OperationalError)→ 空集不抛(检索降级优于报错)。

        真实路径:畸形表达式让 SQLite 抛 OperationalError,_execute_query 内
        捕获返回空集(引号转义/分词清洗已挡掉绝大多数畸形,此为双保险)。
        """

        def bad_expr(q):
            return '"unterminated'                   # FTS 侧缺闭合引号 → 语法错

        monkeypatch.setattr(idx, "_match_expr", bad_expr)
        r = await idx.query("备份")
        assert r.hits == [] and r.timed_out is False

    async def test_query_db_error_pers_202(self, idx, monkeypatch):
        """库通道故障(非超时,如磁盘 I/O)→ PERS-202。"""

        def boom(sql, args):
            raise sqlite3.DatabaseError("disk I/O error")

        monkeypatch.setattr(idx, "_execute_query", boom)
        with pytest.raises(PyHError) as ei:
            await idx.query("备份")
        assert ei.value.code == "PERS-202"


# ================================================================= 生命周期
class TestLifecycle:
    async def test_detach_idempotent_flush_pers_202(self):
        """detach 幂等;摘后 flush/query 均 PERS-202;可再次 enter。"""
        ix = SessionQueryIndex(db_path=":memory:")
        await ix.enter(ctx=None)
        await ix.detach()
        assert ix._db is None
        await ix.detach()                             # 二次摘除安全
        with pytest.raises(PyHError) as ei:
            await ix.flush()
        assert ei.value.code == "PERS-202"

    async def test_detach_flushes_pending_to_file(self, tmp_path: Path):
        """detach 前清空攒批(文件库):重开后未手动 flush 的事件可查。"""
        p = tmp_path / "idx.db"
        ix = SessionQueryIndex(db_path=p)
        await ix.enter(ctx=None)
        await ix.on_event("user.message", msg(SID, 1, "detach 前最后一批备份"))
        await ix.detach()                             # ① flush 未落批(不手动 flush)
        ix2 = SessionQueryIndex(db_path=p)            # ② 重开同一文件库
        await ix2.enter(ctx=None)
        try:
            assert {h.seq for h in (await ix2.query("备份")).hits} == {1}
        finally:
            await ix2.detach()


# ================================================================= announce/工具面
class TestAnnounce:
    def _announce_ctx(self, ix: SessionQueryIndex) -> types.SimpleNamespace:
        return types.SimpleNamespace(session=types.SimpleNamespace(),
                                     tools=ToolRegistry(), registry=None,
                                     locator=None)

    async def test_announce_none_noop(self):
        """ctx=None → announce 直返(装配面缺失不阻断,偏离 5)。"""
        ix = SessionQueryIndex(db_path=":memory:")
        await ix.enter(ctx=None)
        try:
            await ix.announce(ctx=None)
            assert ix._mounted is False
        finally:
            await ix.detach()

    async def test_announce_registers_tool_and_mounts(self):
        """announce:注册 session.fts_query + 挂 ctx.session.fts;detach 逆序摘除。"""
        ix = SessionQueryIndex(db_path=":memory:")
        await ix.enter(ctx=None)
        await ix.on_event("user.message", msg(SID, 1, "把备份记录归档"))
        await ix.flush()
        ctx = self._announce_ctx(ix)
        try:
            await ix.announce(ctx)
            # 工具已注册(Definition 可查)
            defn = ctx.tools.lookup(TOOL_NAME)
            assert defn.danger == "none"
            # 能力已挂载
            assert ctx.session.fts is ix
            # 二次 announce:查重跳过注册,无副作用
            await ix.announce(ctx)
            assert ctx.session.fts is ix
            # Provider 只读面可执行
            handle = FtsQueryHandle()
            out = await handle.handle({"q": "备份"}, ctx)
            assert out["timed_out"] is False
            assert {h["seq"] for h in out["hits"]} == {1}
            assert "session_id" in out["hits"][0] and "snippet" in out["hits"][0]
        finally:
            await ix.detach(ctx)
        assert not hasattr(ctx.session, "fts")        # 挂载已摘
        with pytest.raises(PyHError) as ei:           # 工具已注销
            ctx.tools.lookup(TOOL_NAME)
        assert ei.value.code == "TLB-802"

    async def test_handle_unmounted_tlb_802(self):
        """能力未挂载时 Provider 拒执行(TLB-802,防幻觉工具名)。"""
        ctx = types.SimpleNamespace(session=types.SimpleNamespace())
        handle = FtsQueryHandle()
        with pytest.raises(PyHError) as ei:
            await handle.handle({"q": "备份"}, ctx)
        assert ei.value.code == "TLB-802"
