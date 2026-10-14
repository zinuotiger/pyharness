"""Query projection: complete migrated implementation."""
from __future__ import annotations
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional
from pyharness.config import DEFAULTS
from pyharness.errors import raise_code


log = logging.getLogger("pyharness.session_query")

_FTS_DEFAULTS: dict = DEFAULTS["storage"]["fts"]

_INDEX_BATCH_MS: int = int(_FTS_DEFAULTS["index_batch_ms"])     # 200ms 攒批

_QUERY_TIMEOUT_S: float = float(_FTS_DEFAULTS["query_timeout_s"])  # 5s 软降级

_RESULT_LIMIT: int = int(_FTS_DEFAULTS["result_limit"])         # ≤20 条

_MIN_CJK: int = 2          # 中文下限:≥2 字(2-gram 语义)(F057 边界)

_MIN_LATIN: int = 3        # 英文/数字下限:≥3 字符(F057 边界)

_FLUSH_BATCH: int = 64     # 攒批条数闸(≥64 条立即落库;spec 伪码常量)

_OWNER: str = "cap:session_fts"          # 订阅属主**前缀**(实例再拼库路径+会话集,见 __init__)

TOOL_NAME: str = "session.fts_query"     # LLM 可见搜索工具名(F064 消费)

_CONTENT_COL: int = 6      # snippet 提取列:7 列布局下 content 的列序号(偏离 1)

DEFAULT_DB_NAME: str = "index.db"

def default_db_path() -> Path:
    """默认索引库路径:storage.root 锚点 ~/.pyharness 下 index.db(可被构造参数覆盖)。"""
    root = str(DEFAULTS["storage"]["root"]).replace("~", str(Path.home()))
    return Path(root).expanduser() / DEFAULT_DB_NAME

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

_INDEX_TYPES: tuple[str, ...] = tuple(_WHITELIST) + ("user.message_edited",)

def _index_item(type_: str, env: Any) -> Optional[tuple]:
    """事件 → 索引项(模块级单源;``SessionQueryIndex._extract`` 与 ``is_indexable`` 共用)。

    返回 None = 不索引;("insert", row) = 新增内容行;("edit", (sid, tseq, newc))
    = 级联修正(F063,不改行键、不新增行)。
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
    text = str(text if text is not None else "").strip()
    if not text:
        return None                                # 空文本不索引(llm.response 空轮)
    sid = _field(env, "session_id")
    seq = _field(env, "seq")
    ts = _field(env, "ts", "")
    actor = _field(env, "actor", "")
    if not sid or not seq:
        return None                                # 缺行键:拒(不产生孤儿行)
    row = (str(sid), int(seq), str(type_), str(ts), str(actor),
           _index_text(str(_payload_field(env, "new_title", "") or "")),
           _index_text(text))
    return ("insert", row)

def is_indexable(type_: str, env: Any) -> bool:
    """该事件是否在 FTS 索引**新增行**(insert):edit 不新增行、None 不索引 → False。

    repair 对账用——使"日志水位"与索引 ``max_seq`` **同口径**(CND-06/08:读写判据单源)。
    """
    item = _index_item(type_, env)
    return item is not None and item[0] == "insert"

_MAX_SEQ_SQL = "SELECT MAX(seq) FROM fts_rows WHERE session_id=?"

def read_max_seq(db_path: Any, session_id: str) -> int:
    """**装配外**只读索引末 seq(F060 落后对账;repair 无 live 索引句柄时用)。

    2026-09-21 R11-2 修:`repair._fts_last_seq_provider` 只认 ``ctx.session_query``
    / ``ctx.fts_last_seq``,而**没有任何生产调用方注入它们**(CLI 的 ``auto_scan``
    连 ctx 都不收)⇒ 索引落后对账**从未真正启用**(``max_seq`` 的旧 docstring 亦自认
    "装配后仍无法启用")。本函数让"只知道 db 路径"的调用方也能读数。

    语义:库/表不存在 → 0(未索引,调用方按 0 判"不算落后");读取失败 → PERS-202
    (不静默吞:对账判据不可得时必须显式)。连接短开短合,只读打开。
    """
    import sqlite3 as _sql
    db_file = Path(str(db_path)).expanduser()
    if not db_file.exists():
        return 0
    try:
        conn = _sql.connect(f"file:{db_file}?mode=ro", uri=True)
    except _sql.Error as e:
        raise_code("PERS-202", op="fts_max_seq_ro", path=str(db_file),
                   hint=f"只读打开索引库失败: {e}")
    try:
        row = conn.execute(_MAX_SEQ_SQL, (str(session_id),)).fetchone()
    except _sql.Error as e:
        if "no such table" in str(e).lower():       # 索引未建表:未索引
            return 0
        raise_code("PERS-202", op="fts_max_seq_ro", path=str(db_file),
                   hint=f"索引末 seq 读数失败: {e}")
    finally:
        conn.close()
    return int(row[0] or 0) if row else 0

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

def _log_warn(msg: str, *args: Any) -> None:
    """统一告警出口(模块 logger;ctx.log 无则回落,行为不变)。"""
    log.warning(msg, *args)

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

# Preserve historical import and pickle identities through the compatibility facade.
Hit.__module__ = 'pyharness.core.session_query'
QueryResult.__module__ = 'pyharness.core.session_query'
FtsQueryHandle.__module__ = 'pyharness.core.session_query'
