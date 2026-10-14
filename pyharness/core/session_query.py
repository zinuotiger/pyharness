"""pyharness/core/session_query.py — 会话全文查询 FTS5 核心(specs/session_query.py.md;F057)

功能编号:F057(会话全文查询 FTS5 核心)· 联动 F009/F011(FTS 索引 = JSONL 的**派生
副本**,原则 1)/F042(session.renamed 标题入索引)/F063(user.message_edited 级联更新
索引)/F060(repair 索引对账:落后即整体重建)/F058(compaction 不折叠 FTS——索引独立
于压缩)/F064(CLI `search` 子命令)。权威口径:specs/session_query.py.md(编码契约)、
PRD-Core §5.6 F057、EVENT-SCHEMA §4.2(派生视图纪律:FTS 是日志投影,可整体丢弃
重建)、CFG.md §3.7(storage.fts.index_batch_ms=200/query_timeout_s=5/result_limit=20)、
ERR.md §2.3(PERS-2xx)、SECURITY.md §8.3(索引同权限 600,不进版本库)。

一句话:给全会话历史建 **SQLite FTS5 派生索引**,实现"上次让它整理过某文件夹"式
检索——索引**只做可整体重建的派生副本,永不写回、更不替代 JSONL 真源**(原则 1:
任何一行索引可删了重放日志再建);订阅 append 事件异步攒批(200ms)增量索引,关键词/
短语查询返回命中(带 **seq** 锚点,下游按 seq 回原会话 JSONL 取全事件);中文分词弱
用"每字成词索引 + 查询端二元切分与连续子串补偿"(unicode61 局限如实标注);查询超时
5s 软降级返回空+告警;索引损坏/落后由 rebuild() 整体重建。

实现要点(逐条兑现 spec):
1. events_fts 为 FTS5 虚表,每行从 JSONL 事件派生,行键 (session_id, seq) 唯一;
   本模块对 JSONL 只读(rebuild 经注入 replay 源读),禁止任何"索引写回日志/日志补
   索引"双写路径(INV-01);索引可随时 DELETE 全表重建,JSONL 一行不改。
2. on_event 订阅持久事件 → 白名单抽取文本(user.message.content / agent.message.content /
   llm.response.content / tool.result.summary / session.renamed.new_title /
   user.message_edited.new_content(更新 target 行)/ context.compacted.summary);
   攒批:≥200ms(index_batch_ms,定时器)或 ≥64 条批量落库;瞬时事件(llm.chunk)永不索引。
3. query(q, limit=20, session_id?, actor?, after_ts?) —— 中文 2-gram + 英文 ≥3 字符词;
   词长低于下限 → EVT-100 拒;整串短语补偿;按 FTS5 rank 排序截断 ≤limit;**超时软降级**
   返回 {"hits": [], "timed_out": True} + WARNING,不抛不挂(检索属 UX 场景)。
4. 每条 Hit 带 (session_id, seq, type, ts, snippet, rank) —— seq 即 JSONL 行号锚;
   CLI/UI 拿到 Hit 后经 session.events_between(seq, seq) 回原会话取全事件。
5. rebuild(session_id=None) 全量/单会话重建:清派生行 → 重放 JSONL → 逐事件重插
   (与 on_event 同一白名单/抽取逻辑,INV-03);崩溃后半索引/repair 对账落后时调用。
6. detach 幂等摘除;delete_session 级联删该会话索引行;索引物理位置 ~/.pyharness/index.db
   (WAL,权限 600,与 storage KV 分库;SQLite 串行写由进程内锁保证)。

中文分词与 unicode61 局限(如实标注):
- SQLite 默认 unicode61 分词器把连续中文当**一个 token**(只按标点/空白切分),对
  "搜 2 字词"完全不适用——实测 SQLite 3.50 下对原文直查 '备份'/'备份*' 均 0 命中。
- 本实现采用**索引侧每字成词**:写索引前把中文连续串逐字以空格隔开(每字一个 token,
  英文/数字连续段保持整词 token,其余字符丢弃),FTS5 即可表达"相邻字"短语。
- **查询端二元切分**:查询串中文段滑窗产 2-gram,每个 2-gram 转成相邻单字短语
  ("备份" → `"备 份"`,quoted phrase = 相邻 token 序列),OR 合并保证召回;
  **连续子串补偿**:整串(≥4 字/字符)再按同规则切成 token 短语 OR 上,收紧命中精度。
- 局限:索引/摘要片段展示为字间带空格文本(查询结果 snippet 已做字间空格回缩美化);
  同音/繁体不联想;2 字以上连续子串命中依赖"查询串含于原文连续字串",跨词序不命中。

偏离说明(契约 = specs/session_query.py.md;本仓库既有实现约束下的取舍,均列理由):
1. **actor 冗余列 + 7 列行**:spec 建表 6 列但注"actor 过滤需冗余 actor 列"且 query
   伪码带 actor=? —— 二者矛盾;取 spec 注的意图:建表含 actor UNINDEXED,行按 7 列
   插入,snippet 取 content 列(索引 6,伪码的 5 是 6 列布局下的旧序号)。
2. **行键唯一性靠伴生表而非 INSERT OR IGNORE**:FTS5 无唯一约束,实测同 (session_id,
   seq) 二次 INSERT OR IGNORE 会落两行——spec 伪码幂等语义无法靠 OR IGNORE 兑现;
   故加伴生表 fts_rows(session_id, seq PRIMARY KEY) 在**同一事务**内做幂等闸
   (INSERT OR IGNORE 进 fts_rows 成功才写 events_fts),对外行为与 spec 一致。
3. **rebuild 对 user.message_edited 走共享抽取路径**:spec rebuild 伪码只按 _WHITELIST
   重插,会丢编辑(重放原 user.message 旧文,编辑事件被跳过);本实现与 on_event 共用
   _extract(),重放遇 edited 同样 UPDATE target 行——保证 INV-03"重建与增量逐行一致"
   及 F063"编辑后 rebuild 仍搜得到新版"。
4. **查询串不整体嵌入 MATCH**:spec 伪码把原始 q 直接引号嵌入(含引号即语法错,靠
   OperationalError 兜空);本实现短语补偿串先经自研 tokenizer 清洗再引号化,含引号
   查询不会破坏 MATCH 语法(OperationalError 兜底保留为双保险)。
5. **无 repo 装配面时 announce 降级**:spec announce 依赖 bus.tools.register_tool /
   locator.mount —— 本仓库阶段无 F064 消费方与全局工具装配,announce 在 ctx 带
   tools/registry 时注册工具并挂 ctx.session.fts,否则仅记 info(装配面缺失不阻断);
   注册入口单独提供 register()(spill 同款形态)供后续装配。
6. **全文/单会话 rebuild 数据源注入**:spec 伪码直接调 persistence.replay;本实现经
   attach_source(sid, store) 注入可回放源(鸭子类型:store.replay() 或
   SessionLog.events_after(0) 均可),单测不依赖真实用户目录。

依赖方向(INV-08):本文件 → errors(raise_code)、config(DEFAULTS 只读)、events(类型
仅作字段访问);不反向写 session/persistence。外部:sqlite3(stdlib,FTS5 ≥3.9,Python
3.11 自带)、asyncio(攒批定时/超时闸)、threading(单连接串行写)。无 LLM 依赖。
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional, Union

from pyharness.config import DEFAULTS
from pyharness.errors import PyHError, raise_code


# ===================================================================== 常量
# CFG.md §3.7 storage.fts 默认值唯一落地(config.DEFAULTS 权威;运行期可经 ctx.config
# 覆盖,见 enter)。PARAMETER-ANCHOR 锁死:index_batch_ms=200 / query_timeout_s=5 /
# result_limit=20。


# 默认索引库:~/.pyharness/index.db(spec 数据结构表;与 storage KV 分库,WAL)


# ------------------------------------------------------------- 分词器(纯函数)
# unicode61 把连续中文当一个 token → 索引侧先逐字空格化(每字成词);查询侧按段
# 2-gram 滑窗 + 整串补偿。以下三个正则即全部分词口径,索引与查询严格同源。


# ================================================================= 事件白名单
# 索引事件类型 → 文本抽取器(数据结构表 _WHITELIST;user.message_edited 走修正分支,
# 见 _extract)。llm.response 仅在 content 非空时入索引(工具调用轮 content="" 不索引)。


# 订阅类型全量:白名单 6 类 + user.message_edited(F063 修正 target 行)


# 索引末 seq 的**唯一判据 SQL**(装配内 ``max_seq`` 与装配外 ``read_max_seq`` 共用,
# 避免 CND-06"读写判据多源"导致的漂移)


# ================================================================= 结果模型


# ============================================================ 模块工具(读配置)
from pyharness.core.config_access import _cfg_holder


from pyharness.core.config_access import _cfg


# ============================================================ FTS 查询工具句柄


# ============================================================ 主类
from pyharness.core.query_projection import (log, _FTS_DEFAULTS, _INDEX_BATCH_MS, _QUERY_TIMEOUT_S, _RESULT_LIMIT, _MIN_CJK, _MIN_LATIN, _FLUSH_BATCH, _OWNER, TOOL_NAME, _CONTENT_COL, DEFAULT_DB_NAME, default_db_path, _CJK_RUN, _CJK_CHAR, _WORD, _tokenize, _index_text, _pretty_snippet, _payload, _field, _payload_field, _extract_content, _extract_summary, _extract_new_title, _WHITELIST, _INDEX_TYPES, _index_item, is_indexable, _MAX_SEQ_SQL, read_max_seq, Hit, QueryResult, _log_warn, FtsQueryHandle, _replay_store)

from pyharness.core.query_lifecycle import QueryLifecycle
from pyharness.core.query_search import QuerySearch
from pyharness.core.query_maintenance import QueryMaintenance

class SessionQueryIndex(QueryLifecycle, QuerySearch, QueryMaintenance):
    """会话全文查询 FTS5 派生索引(F057)。

    派生纪律(原则 1):events_fts 每一行都从 JSONL 事件派生,行键 (session_id, seq)
    唯一;对 JSONL 只读,禁止"索引写回日志/日志补索引"双写路径;索引可随时 DELETE
    全表重建(rebuild),JSONL 一行不改——FTS 是日志投影,可整体丢弃重建(EVENT-SCHEMA
    §4.2)。物理:SQLite WAL 单库(默认 ~/.pyharness/index.db,权限 600),进程内锁
    串行化写与查询线程(单连接 check_same_thread=False + threading.Lock)。

    订阅 append 事件异步攒批(≥64 条或 index_batch_ms 定时器)→ 批量 INSERT;
    user.message_edited 级联更新 target 行(派生视图允许更新,真源不动);查询 5s
    超时软降级返回 QueryResult(timed_out=True),不抛不挂;rebuild/delete_session
    提供整体重建与级联删除。

    实例字段:db 连接 _db、攒批缓冲 _pending/_pending_edits、订阅/调度器引用
    _bus/_owner/_sched_task、注入的回放源 _sources(sid → replayable 鸭子对象)。
    """

    def __init__(self, db_path: Optional[Union[str, Path]] = None, *,
                 sources: Optional[dict] = None,
                 batch_ms: Optional[int] = None,
                 query_timeout_s: Optional[float] = None,
                 result_limit: Optional[int] = None) -> None:
        """构造(未装载):db_path 缺省 ~/.pyharness/index.db;测试请显式传内存/tmp。

        阈值优先级:构造显式参数 > ctx.config(storage.fts.*,enter 时读) > DEFAULTS。
        sources: 会话回放源注册表 {session_id: replayable}——replayable 需提供
        replay()(SessionStore 形态)或 events_after(0)(SessionLog 形态),供
        rebuild() 读 JSONL 真源(派生服从真源)。
        """
        self._db_path: Path = (Path(db_path).expanduser()
                               if db_path is not None else default_db_path())
        self._sources: dict = dict(sources or {})
        # 阈值(F057 边界;显式参数优先于 ctx.config 覆盖,见 enter)
        self._explicit: dict[str, bool] = {
            "index_batch_ms": batch_ms is not None,
            "query_timeout_s": query_timeout_s is not None,
            "result_limit": result_limit is not None,
        }
        self.index_batch_ms: int = _INDEX_BATCH_MS if batch_ms is None else int(batch_ms)
        self.query_timeout_s: float = (_QUERY_TIMEOUT_S if query_timeout_s is None
                                       else float(query_timeout_s))
        self.result_limit: int = _RESULT_LIMIT if result_limit is None else int(result_limit)
        # 运行态
        self._db: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()              # 单连接串行写+查询线程互斥
        self._pending: list[tuple] = []            # 攒批缓冲(行)
        self._pending_edits: list[tuple] = []      # 攒批缓冲(编辑修正)
        self._bus: Any = None                      # 事件总线(enter 时接线)
        # 订阅属主(退订按此摘除)。**必须含全部隔离维度(R31-1-F)**:总线在多租户
        # 桌面下是**共享**的,而本索引是**每会话**一个、库按租户分域(R14-8)。
        # 属主若只用一个模块常量,任一实例 `detach()` 的 `unsubscribe_all(owner)`
        # 会把**其他租户/会话**的订阅一并摘掉(实测:2 租户共 14 条订阅被一次摘光)
        # ⇒ 其他租户的检索**静默变陈旧**。故属主 = 常量前缀 + 库路径(含租户)+ 会话集。
        _sids = ",".join(sorted(str(k) for k in self._sources))
        self._owner: str = f"{_OWNER}:{self._db_path}:{_sids}"
        self._sched_task: Optional[asyncio.Task] = None  # 200ms 定时落库任务
        # announce 痕迹(detach 逆序摘除用)
        self._announce_ctx: Any = None
        self._mounted: bool = False
        self._tool_registered: bool = False

    # ------------------------------------------------------------ 装载/摘除


    # ------------------------------------------------------------ 攒批调度

    # ------------------------------------------------------------ 事件 → 索引
    async def on_event(self, type_: str, env: Any = None) -> None:
        """白名单事件的订阅回调(spec 伪码):抽取入 _pending,攒满 64 条立即 flush。

        user.message_edited 走"修正 target 行"(派生视图允许更新,真源不动,F063);
        白名单外事件(含 guard.*/瞬时)不索引;flush 落库失败按 spec 记 PERS-202
        上抛(总线侧 EVT-103 隔离,不中断主流程,索引稍后 rebuild 自愈)。
        """
        item = self._extract(type_, env)
        if item is None:
            return                                     # 白名单外不索引
        if item[0] == "edit":
            self._pending_edits.append(item[1])
        else:
            self._pending.append(item[1])
        if len(self._pending) + len(self._pending_edits) >= _FLUSH_BATCH:
            await self.flush()                         # 条数闸立即刷

    def _extract(self, type_: str, env: Any) -> Optional[tuple]:
        """事件 → 攒批项(共享抽取核心;on_event/rebuild 同一口径,INV-03)。

        委托模块级 ``_index_item``(单源,避免第二份口径漂移)。
        """
        return _index_item(type_, env)

    # ------------------------------------------------------------ 批量落库
    async def flush(self) -> int:
        """把 _pending 批量落库(幂等,行键 (sid, seq) 唯一)+ _pending_edits 更新。

        先取走缓冲再写(失败回填:拒新不丢旧);INSERT OR IGNORE 进伴生表 fts_rows
        成功才写 events_fts(偏离 2:幂等语义落地);同一事务提交(WAL);返回新写入
        行数。SQLite 写失败 → PERS-202 + 回填缓冲(repair/rebuild 可自愈,不中断
        主流程)。进程内锁与查询线程互斥(单连接串行写)。
        """
        if self._db is None:
            raise_code("PERS-202", op="fts_flush",
                       hint="索引未装载(enter 未执行或已 detach)",
                       advice="先 enter/重新装载索引")
        rows, edits = self._pending, self._pending_edits
        self._pending, self._pending_edits = [], []    # 先取走(失败可回填)
        if not rows and not edits:
            return 0
        n = 0
        try:
            with self._lock:
                with self._db:                         # WAL 事务(原子提交)
                    for r in rows:
                        cur = self._db.execute(
                            "INSERT OR IGNORE INTO fts_rows(session_id, seq)"
                            " VALUES(?,?)", (r[0], r[1]))
                        if cur.rowcount:               # 行键首见才写 FTS 行
                            self._db.execute(
                                "INSERT INTO events_fts VALUES(?,?,?,?,?,?,?)", r)
                            n += 1
                    for sid, tseq, newc in edits:      # 派生视图更新(真源不动)
                        self._db.execute(
                            "UPDATE events_fts SET content=? "
                            "WHERE session_id=? AND seq=?",
                            (_index_text(str(newc)), sid, tseq))
        except sqlite3.Error as e:
            self._pending = rows + self._pending        # 回填:拒新不丢旧
            self._pending_edits = edits + self._pending_edits
            raise_code("PERS-202", op="fts_flush", n=len(rows),
                       hint=f"FTS 索引落库失败: {e}",
                       advice="运行 repair(F060) 重建索引;缓冲已回填不丢")
        except BaseException:                           # 取消等:同样回填再上抛
            self._pending = rows + self._pending
            self._pending_edits = edits + self._pending_edits
            raise
        return n

    # ------------------------------------------------------------ 对账读数
    def max_seq(self, session_id: str) -> int:
        """该会话在索引里已落库的最大 seq(派生视图末位,F060 落后对账用)。

        只读;索引未装载 → 0(调用方据 0 判定"未索引",不算落后);与攒批 flush
        共用连接锁,避免读数与写批交错。判据 SQL 与 ``read_max_seq`` **同源**
        (``_MAX_SEQ_SQL``),保证"装配内读数"与"装配外读数"口径一致(CND-06)。
        """
        db = self._db
        if db is None:
            return 0
        with self._lock:
            try:
                row = db.execute(_MAX_SEQ_SQL, (str(session_id),)).fetchone()
            except sqlite3.Error as e:
                raise_code("PERS-202", op="fts_max_seq",
                           hint=f"索引末 seq 读数失败: {e}")
        return int(row[0] or 0) if row else 0

    # ------------------------------------------------------------ 全文查询


    # ------------------------------------------------------------ 分词器(纯函数)


    # ------------------------------------------------------------ 重建/级联删


__all__ = [
    "Hit", "QueryResult", "SessionQueryIndex", "FtsQueryHandle",
    "_WHITELIST", "_MIN_CJK", "_MIN_LATIN", "_FLUSH_BATCH",
    "_INDEX_BATCH_MS", "_QUERY_TIMEOUT_S", "_RESULT_LIMIT",
    "TOOL_NAME", "default_db_path", "is_indexable", "read_max_seq",
]
