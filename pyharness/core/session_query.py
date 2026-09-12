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

log = logging.getLogger("pyharness.session_query")

# ===================================================================== 常量
# CFG.md §3.7 storage.fts 默认值唯一落地(config.DEFAULTS 权威;运行期可经 ctx.config
# 覆盖,见 enter)。PARAMETER-ANCHOR 锁死:index_batch_ms=200 / query_timeout_s=5 /
# result_limit=20。
_FTS_DEFAULTS: dict = DEFAULTS["storage"]["fts"]
_INDEX_BATCH_MS: int = int(_FTS_DEFAULTS["index_batch_ms"])     # 200ms 攒批
_QUERY_TIMEOUT_S: float = float(_FTS_DEFAULTS["query_timeout_s"])  # 5s 软降级
_RESULT_LIMIT: int = int(_FTS_DEFAULTS["result_limit"])         # ≤20 条

_MIN_CJK: int = 2          # 中文下限:≥2 字(2-gram 语义)(F057 边界)
_MIN_LATIN: int = 3        # 英文/数字下限:≥3 字符(F057 边界)
_FLUSH_BATCH: int = 64     # 攒批条数闸(≥64 条立即落库;spec 伪码常量)

_OWNER: str = "cap:session_fts"          # 订阅属主(退订按此摘除)
TOOL_NAME: str = "session.fts_query"     # LLM 可见搜索工具名(F064 消费)
_CONTENT_COL: int = 6      # snippet 提取列:7 列布局下 content 的列序号(偏离 1)

# 默认索引库:~/.pyharness/index.db(spec 数据结构表;与 storage KV 分库,WAL)
DEFAULT_DB_NAME: str = "index.db"


def default_db_path() -> Path:
    """默认索引库路径:storage.root 锚点 ~/.pyharness 下 index.db(可被构造参数覆盖)。"""
    root = str(DEFAULTS["storage"]["root"]).replace("~", str(Path.home()))
    return Path(root).expanduser() / DEFAULT_DB_NAME


# ------------------------------------------------------------- 分词器(纯函数)
# unicode61 把连续中文当一个 token → 索引侧先逐字空格化(每字成词);查询侧按段
# 2-gram 滑窗 + 整串补偿。以下三个正则即全部分词口径,索引与查询严格同源。
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")          # 连续中文段(查询侧滑窗)
_CJK_CHAR = re.compile(r"[\u4e00-\u9fff]")          # 单中文字(索引侧逐字)
_WORD = re.compile(r"[A-Za-z0-9_]{3,}")             # 查询侧拉丁词(≥3 字符)


def _tokenize(text: str) -> list[str]:
    """逐段扫描文本:中文连续段逐字出 token,ASCII 连续段整词出 token,其余丢弃。"""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if _CJK_CHAR.fullmatch(ch):
            j = i + 1
            while j < n and _CJK_CHAR.fullmatch(text[j]):
                j += 1
            out.extend(text[i:j])                    # 逐字成 token(每字一词)
            i = j
        elif ch.isascii() and (ch.isalnum() or ch == "_"):
            j = i + 1
            while j < n and text[j].isascii() and (text[j].isalnum() or text[j] == "_"):
                j += 1
            out.append(text[i:j])                    # ASCII 整词成 token
            i = j
        else:
            i += 1                                   # 标点/空白/其他:丢弃(分隔)
    return out


def _index_text(text: str) -> str:
    """写索引前的文本变换:token 空格拼接(中文字间有空格,词边界即 token 边界)。"""
    return " ".join(_tokenize(text))


def _pretty_snippet(snippet: str) -> str:
    """snippet 美化:回缩中文相邻字之间的索引空格(展示层复原;拉丁词间距保留)。"""
    return re.sub(r"(?<=[\u4e00-\u9fff]) (?=[\u4e00-\u9fff])", "", snippet)


# ================================================================= 事件白名单
# 索引事件类型 → 文本抽取器(数据结构表 _WHITELIST;user.message_edited 走修正分支,
# 见 _extract)。llm.response 仅在 content 非空时入索引(工具调用轮 content="" 不索引)。
def _payload(env: Any) -> dict:
    """信封/裸 dict 双形态取 payload(总线会话事件载荷为 Envelope;dict 兜底)。"""
    if env is None:
        return {}
    if isinstance(env, dict):
        p = env.get("payload")
        return p if isinstance(p, dict) else {}
    p = getattr(env, "payload", None)
    return p if isinstance(p, dict) else {}


def _field(env: Any, name: str, default: Any = None) -> Any:
    """信封/裸 dict 双形态取顶层字段(session_id/seq/type/ts/actor)。"""
    if env is None:
        return default
    if isinstance(env, dict):
        return env.get(name, default)
    return getattr(env, name, default)


def _payload_field(env: Any, name: str, default: Any = "") -> Any:
    """payload 内字段抽取(dict 形态容错;缺省空串——空文本行不入索引)。"""
    return _payload(env).get(name, default)


def _extract_content(env: Any) -> str:
    return str(_payload_field(env, "content") or "")


def _extract_summary(env: Any) -> str:
    return str(_payload_field(env, "summary") or "")


def _extract_new_title(env: Any) -> str:
    return str(_payload_field(env, "new_title") or "")


_WHITELIST: dict[str, Any] = {
    "user.message": _extract_content,
    "agent.message": _extract_content,
    "llm.response": _extract_content,          # content 非空才索引(抽取后判空)
    "tool.result": _extract_summary,
    "session.renamed": _extract_new_title,
    "context.compacted": _extract_summary,
}
# 订阅类型全量:白名单 6 类 + user.message_edited(F063 修正 target 行)
_INDEX_TYPES: tuple[str, ...] = tuple(_WHITELIST) + ("user.message_edited",)


# ================================================================= 结果模型
@dataclass(frozen=True)
class Hit:
    """命中条目(spec 数据结构表):seq = JSONL 行号锚,下游回原会话取全事件。"""

    session_id: str
    seq: int
    type: str
    ts: str
    snippet: str
    rank: float


@dataclass(frozen=True)
class QueryResult:
    """query 返回值:hits 命中列表;timed_out=True = 5s 超时软降级(不抛不挂)。"""

    hits: list[Hit] = field(default_factory=list)
    timed_out: bool = False


# ============================================================ 模块工具(读配置)
def _cfg_holder(ctx: Any) -> Any:
    """取配置持有者:spec 伪码经 ctx.cfg;本项目装配(Ctx)为 ctx.config(双形态都收)。"""
    if ctx is None:
        return None
    for name in ("cfg", "config"):
        holder = getattr(ctx, name, None)
        if holder is not None:
            return holder
    return None


def _cfg(holder: Any, dotted: str, default: Any = None) -> Any:
    """点分键取值,双形态兼容:dict(点分整键或逐层嵌套)与属性链(pydantic Settings)。"""
    if holder is None:
        return default
    parts = dotted.split(".")
    if isinstance(holder, dict):
        if dotted in holder:
            return holder[dotted]
        cur: Any = holder
        for p in parts:
            if not isinstance(cur, dict) or p not in cur:
                return default
            cur = cur[p]
        return cur
    cur = holder
    for p in parts:
        cur = getattr(cur, p, None)
        if cur is None:
            return default
    return cur


def _log_warn(msg: str, *args: Any) -> None:
    """统一告警出口(模块 logger;ctx.log 无则回落,行为不变)。"""
    log.warning(msg, *args)


# ============================================================ FTS 查询工具句柄
class FtsQueryHandle:
    """session.fts_query 工具的 Provider 实现(executor 契约 handle(args, ctx))。

    只读面:把 LLM 参数转发到 ctx.session.fts 上挂载的 SessionQueryIndex.query;
    未挂载(能力未 announce)→ TLB-802(不静默,防幻觉工具名)。
    """

    name: str = TOOL_NAME

    async def handle(self, args: dict, ctx: Any) -> dict:
        """executor 关3 Provider 调用面:经 ctx.session.fts 执行查询。"""
        sess = getattr(ctx, "session", None)
        index = getattr(sess, "fts", None) if sess is not None else None
        if index is None or not callable(getattr(index, "query", None)):
            raise_code("TLB-802", tool=TOOL_NAME,
                       advice="会话 FTS 索引未挂载(ctx.session.fts);先 announce 能力")
        result = await index.query(args.get("q", ""),
                                   limit=args.get("limit", _RESULT_LIMIT),
                                   session_id=args.get("session_id"),
                                   actor=args.get("actor"),
                                   after_ts=args.get("after_ts"))
        return {"hits": [h.__dict__ for h in result.hits],
                "timed_out": result.timed_out}


# ============================================================ 主类
class SessionQueryIndex:
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
        self._owner: str = _OWNER                  # 订阅属主(退订按此摘除)
        self._sched_task: Optional[asyncio.Task] = None  # 200ms 定时落库任务
        # announce 痕迹(detach 逆序摘除用)
        self._announce_ctx: Any = None
        self._mounted: bool = False
        self._tool_registered: bool = False

    # ------------------------------------------------------------ 装载/摘除
    async def enter(self, ctx: Any = None) -> None:
        """开库建表 + 挂订阅(spec 伪码;幂等:已装载直接返回)。

        - 开 SQLite(WAL;文件库父目录 mkdir 0o700,库文件 chmod 0o600 尽力而为);
        - CREATE VIRTUAL TABLE IF NOT EXISTS events_fts(7 列,actor 为 UNINDEXED
          冗余列,偏离 1)+ 伴生唯一表 fts_rows(行键幂等闸,偏离 2);
        - 阈值经 ctx.config/cfg 的 storage.fts.* 覆盖(构造显式参数优先);
        - ctx.bus 接线:按 _INDEX_TYPES 逐类型订阅 → on_event(owner=cap:session_fts),
          并启动 index_batch_ms 攒批定时器;
        - 失败(磁盘/权限/旧 SQLite 无 FTS5)→ PERS-202,回 detached 不留半态。
        """
        if self._db is not None:
            return                                 # 幂等
        holder = _cfg_holder(ctx)
        if not self._explicit["index_batch_ms"]:
            self.index_batch_ms = int(_cfg(holder, "storage.fts.index_batch_ms",
                                           self.index_batch_ms))
        if not self._explicit["query_timeout_s"]:
            self.query_timeout_s = float(_cfg(holder, "storage.fts.query_timeout_s",
                                              self.query_timeout_s))
        if not self._explicit["result_limit"]:
            self.result_limit = int(_cfg(holder, "storage.fts.result_limit",
                                         self.result_limit))
        db: Optional[sqlite3.Connection] = None
        try:
            if str(self._db_path) != ":memory:":
                self._db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            db = sqlite3.connect(str(self._db_path), check_same_thread=False)
            db.execute("PRAGMA journal_mode=WAL")      # :memory: 返回 memory 无副作用
            db.execute("PRAGMA busy_timeout=3000")
            db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5("
                       "session_id UNINDEXED, seq UNINDEXED, type UNINDEXED, "
                       "ts UNINDEXED, actor UNINDEXED, title, content)")
            # 伴生唯一表:行键 (session_id, seq) 幂等闸(FTS5 无唯一约束,偏离 2)
            db.execute("CREATE TABLE IF NOT EXISTS fts_rows("
                       "session_id TEXT NOT NULL, seq INTEGER NOT NULL, "
                       "PRIMARY KEY (session_id, seq)) WITHOUT ROWID")
            db.commit()
            if str(self._db_path) != ":memory:":
                try:
                    os.chmod(self._db_path, 0o600)     # 权限 600(SECURITY §8.3)
                except OSError:
                    pass          # Windows ACL 尽力而为(chmod 主防线在文件库位置)
        except (sqlite3.Error, OSError) as e:            # 磁盘/权限/旧 SQLite 无 FTS5
            if db is not None:
                db.close()
            raise_code("PERS-202", op="fts_enter", path=str(self._db_path),
                       hint=f"FTS 索引开库/建表失败: {e}",
                       advice="修存储或换带 FTS5 的 SQLite;重试 enter")
        self._db = db
        # 总线订阅(持久事件流;瞬时事件 llm.chunk 不在 _INDEX_TYPES,天然不过滤)
        bus = getattr(ctx, "bus", None) if ctx is not None else None
        subscribe = getattr(bus, "subscribe", None) if bus is not None else None
        if callable(subscribe):
            for t in _INDEX_TYPES:
                subscribe(t, self.on_event, owner=self._owner)
            self._bus = bus
            try:                                       # 200ms 攒批定时器兜底
                self._sched_task = asyncio.get_running_loop().create_task(
                    self._scheduler())
            except RuntimeError:                       # 无事件循环(同步上下文)
                self._sched_task = None
        log.info("session fts entered db=%s batch_ms=%d timeout_s=%s limit=%d",
                 self._db_path, self.index_batch_ms, self.query_timeout_s,
                 self.result_limit)

    async def announce(self, ctx: Any = None) -> None:
        """对外宣布(F049 同类五步):注册 LLM 可见工具 session.fts_query + 挂 ctx.session.fts。

        失败(重名/保留名 TLB-801)回滚回 detached 无残留。仓库当前阶段无 F064 消费
        方与全局装配面:ctx 缺 tools/registry 时仅记 info 不阻断(偏离 5)。
        """
        if ctx is None:
            return
        self._announce_ctx = ctx
        try:
            registry = None
            for attr in ("tool_registry", "tools", "registry"):
                cand = getattr(ctx, attr, None)
                if cand is not None and callable(getattr(cand, "register_tool", None)):
                    registry = cand
                    break
            if registry is not None:
                # 查重:lookup 命中 = 已注册;抛 TLB-802 = 未注册(静默跳过登记)
                already = False
                lookup = getattr(registry, "lookup", None)
                if callable(lookup):
                    try:
                        lookup(TOOL_NAME)
                        already = True
                    except PyHError as e:
                        if e.code != "TLB-802":
                            raise                     # 其他注册表故障如实上抛
                if not already:
                    registry.register_tool(self._tool_definition(),
                                           provider=FtsQueryHandle())
                    self._tool_registered = True
            if not self._mounted:                      # 幂等:已挂载不重挂(防二次
                self._mounted = self._mount(ctx)       # announce 把 _mounted 清零)
            log.info("session fts announced tool=%s mounted=%s", TOOL_NAME,
                     self._mounted)
        except BaseException:
            self._rollback_announce()                  # 半载回滚:无脏注册表/挂载
            raise

    def _tool_definition(self) -> Any:
        """search 工具 Definition(spec announce defn 实契约;owner=spine 先例)。"""
        from pyharness.core.tools_registry import ToolDefinition
        return ToolDefinition(
            name=TOOL_NAME,
            danger="none",
            description="全历史全文搜索(关键词/短语),返回带 seq 的命中与摘要片段",
            schema={"type": "object",
                    "properties": {"q": {"type": "string"},
                                   "limit": {"type": "integer", "minimum": 1,
                                             "maximum": 100},
                                   "session_id": {"type": "string"},
                                   "actor": {"type": "string"},
                                   "after_ts": {"type": "string"}},
                    "required": ["q"],
                    "additionalProperties": False},
            approval="never",
            timeout_s=15,                              # 查询自带 5s 软降级,外层闸放宽
            owner="spine",
            ctx_path="session.fts",
            version="1.0.0",
        )

    def _mount(self, ctx: Any) -> bool:
        """挂载定位器 ctx.session.fts:有 locator.mount 用 locator;否则直挂属性。"""
        locator = getattr(ctx, "locator", None)
        mount = getattr(locator, "mount", None)
        if callable(mount):
            try:
                mount("ctx.session.fts", self)
                return True
            except Exception as e:                     # noqa: BLE001
                _log_warn("fts announce locator.mount 失败: %s", e)
                return False
        sess = getattr(ctx, "session", None)
        if sess is not None and not hasattr(sess, "fts"):
            try:
                setattr(sess, "fts", self)             # 阶段 fallback(直挂属性)
                return True
            except Exception as e:                     # noqa: BLE001
                _log_warn("fts announce 挂载 ctx.session.fts 失败: %s", e)
        return False

    def _rollback_announce(self) -> None:
        """announce 失败回滚:摘工具 + 摘挂载(尽力而为,不掩盖主异常)。"""
        try:
            if self._tool_registered:
                self._unregister_tool(self._announce_ctx)
        except Exception as e:                         # noqa: BLE001
            _log_warn("fts announce 回滚摘工具失败: %s", e)
        try:
            if self._mounted:
                self._unmount(self._announce_ctx)
        except Exception as e:                         # noqa: BLE001
            _log_warn("fts announce 回滚摘挂载失败: %s", e)
        self._mounted = False
        self._tool_registered = False

    async def detach(self, ctx: Any = None) -> None:
        """摘除(spec 伪码):停定时器 → flush 未落批 → 退订 → 摘工具/挂载 → 关库。

        幂等(已摘直接返回);逐步骤容错,失败记日志继续摘,不抛(半卸 > 僵尸)。
        """
        if self._db is None:
            return                                     # 幂等
        # ① 停 200ms 定时器(取消 + 等待退出;flush 中断已由回填纪律兜底)
        task = self._sched_task
        self._sched_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        # ② 清空攒批(失败仅告警,继续摘)
        try:
            await self.flush()
        except PyHError as e:
            _log_warn("fts detach flush 失败 code=%s(缓冲已回填,rebuild 可自愈)", e.code)
        # ③ 摘订阅(owner 精确)
        if self._bus is not None:
            unsub = getattr(self._bus, "unsubscribe_all", None)
            if callable(unsub):
                try:
                    unsub(self._owner)
                except Exception as e:                 # noqa: BLE001
                    _log_warn("fts detach 退订失败: %s", e)
            self._bus = None
        # ④ 摘工具/挂载(announce 痕迹)
        c = ctx if ctx is not None else self._announce_ctx
        if c is not None:
            try:
                if self._tool_registered:
                    self._unregister_tool(c)
                if self._mounted:
                    self._unmount(c)
            except Exception as e:                     # noqa: BLE001
                _log_warn("fts detach 摘能力失败: %s", e)
        self._mounted = False
        self._tool_registered = False
        self._announce_ctx = None
        # ⑤ 关库置 None(幂等)
        try:
            self._db.close()
        except Exception as e:                         # noqa: BLE001
            _log_warn("fts detach 关库失败: %s", e)
        self._db = None
        log.info("session fts detached")

    def _unregister_tool(self, ctx: Any) -> None:
        """摘已注册工具(TLB-802 = 已摘,静默幂等)。"""
        for attr in ("tools", "registry"):
            cand = getattr(ctx, attr, None)
            unreg = getattr(cand, "unregister_definition", None)
            if not callable(unreg):
                unreg = getattr(cand, "unregister", None)
            if callable(unreg):
                try:
                    unreg(TOOL_NAME)
                    return
                except PyHError as e:
                    if e.code != "TLB-802":
                        _log_warn("fts 摘工具失败 code=%s", e.code)
                    return
                except Exception as e:                 # noqa: BLE001
                    _log_warn("fts 摘工具异常: %s", e)
                    return

    def _unmount(self, ctx: Any) -> None:
        """摘挂载:locator.unmount 优先;否则摘直挂属性(被替换则不误摘)。"""
        locator = getattr(ctx, "locator", None)
        unmount = getattr(locator, "unmount", None)
        if callable(unmount):
            try:
                unmount("ctx.session.fts")
                return
            except Exception as e:                     # noqa: BLE001
                _log_warn("fts unmount 失败: %s", e)
                return
        sess = getattr(ctx, "session", None)
        if sess is not None and getattr(sess, "fts", None) is self:
            try:
                delattr(sess, "fts")
            except Exception as e:                     # noqa: BLE001
                _log_warn("fts 摘直挂属性失败: %s", e)

    # ------------------------------------------------------------ 攒批调度
    async def _scheduler(self) -> None:
        """200ms 定时落库(index_batch_ms;spec 攒批兜底):周期 flush,失败告警续跑。"""
        while True:
            await asyncio.sleep(self.index_batch_ms / 1000.0)
            try:
                await self.flush()
            except PyHError as e:
                _log_warn("fts 定时落库失败 code=%s(下次重试;rebuild 可自愈)", e.code)
            except Exception:                          # noqa: BLE001
                log.exception("fts 定时落库异常(下次重试)")

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

        返回 None = 不索引;("insert", row) / ("edit", (sid, target_seq, new_content))。
        row = (session_id, seq, type, ts, actor, title, content)(7 列,偏离 1)。
        """
        if type_ == "user.message_edited":             # F063:级联更新 target 行
            sid = _field(env, "session_id")
            tseq = _payload_field(env, "target_seq")
            newc = _payload_field(env, "new_content")
            if not sid or not tseq or not newc:
                return None
            return ("edit", (sid, int(tseq), str(newc)))
        fn = _WHITELIST.get(type_)
        if fn is None:
            return None                                # 白名单外(含 guard.*/瞬时)
        text = fn(env)
        if text is None:
            text = ""
        text = str(text).strip()
        if not text:
            return None                                # 空文本不索引(llm.response 空轮)
        sid = _field(env, "session_id")
        seq = _field(env, "seq")
        ts = _field(env, "ts", "")
        actor = _field(env, "actor", "")
        if not sid or not seq:
            return None                                # 缺行键:拒(不产生孤儿行)
        # 写索引前中文逐字空格化(每字一词,unicode61 局限的索引侧补偿)——title 与
        # content 同变换,保证查询侧 2-gram 短语/整串短语能与索引 token 对齐。
        row = (str(sid), int(seq), str(type_), str(ts), str(actor),
               _index_text(str(_payload_field(env, "new_title", "") or "")),
               _index_text(text))
        return ("insert", row)

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
        共用连接锁,避免读数与写批交错。修复:此前 SessionQueryIndex 未暴露该
        读数面,导致 repair.scan_session 的 index_stale 检测在装配后仍无法启用。
        """
        db = self._db
        if db is None:
            return 0
        with self._lock:
            try:
                row = db.execute(
                    "SELECT MAX(seq) FROM fts_rows WHERE session_id=?",
                    (str(session_id),)).fetchone()
            except sqlite3.Error as e:
                raise_code("PERS-202", op="fts_max_seq",
                           hint=f"索引末 seq 读数失败: {e}")
        return int(row[0] or 0) if row else 0

    # ------------------------------------------------------------ 全文查询
    async def query(self, q: str, *, limit: int = 20,
                    session_id: Optional[str] = None,
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

    # ------------------------------------------------------------ 分词器(纯函数)
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

    # ------------------------------------------------------------ 重建/级联删
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
        except sqlite3.Error as e:
            raise_code("PERS-202", op="fts_delete_session", session=session_id,
                       hint=f"FTS 级联删除失败: {e}",
                       advice="重试;索引为派生副本可整库重建兜底")
        log.info("session fts delete_session %s", session_id)


def _replay_store(store: Any) -> Iterator[Any]:
    """回放源鸭子协议:replay()(SessionStore)或 events_after(0)(SessionLog)。"""
    replay = getattr(store, "replay", None)
    if callable(replay):
        yield from replay()
        return
    events_after = getattr(store, "events_after", None)
    if callable(events_after):
        yield from events_after(0)
        return
    raise_code("PERS-201", op="fts_replay",
               hint=f"回放源不支持 replay()/events_after(0): {type(store).__name__}",
               advice="注入 SessionStore/SessionLog 形态的回放源")


__all__ = [
    "Hit", "QueryResult", "SessionQueryIndex", "FtsQueryHandle",
    "_WHITELIST", "_MIN_CJK", "_MIN_LATIN", "_FLUSH_BATCH",
    "_INDEX_BATCH_MS", "_QUERY_TIMEOUT_S", "_RESULT_LIMIT",
    "TOOL_NAME", "default_db_path",
]
