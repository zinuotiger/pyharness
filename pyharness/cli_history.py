"""Cli history: complete migrated implementation."""
from __future__ import annotations
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from pyharness.errors import raise_code

from pyharness.cli_output import ShellCtx, _json_line
from pyharness.cli_sessions import _sessions_path

def _sessions_dir_ctx(sh: ShellCtx) -> Path:
    cfg = sh.ctx.settings
    return Path(getattr(getattr(sh.ctx, "storage", None), "sessions_dir",
                        _sessions_path(cfg))).expanduser()

class _session_lines:
    """会话**真源**(轮转段 + 主文件)合并为一个逐行文本流(R14-3)。

    用法与 ``with open(p, encoding="utf-8") as fh:`` **同形**,便于既有读取循环直接
    换用;语义按会话:①跨段完整(轮转后旧事件在段里,不读就丢)②段与主文件都归到
    真实 sid(辅助档本身不是会话,见 ``persistence.session_log_paths``)。
    逐段顺序开合,不并发持有句柄。
    """

    def __init__(self, main: Path) -> None:
        from pyharness.persistence import session_data_paths
        self._parts = session_data_paths(main)

    def __enter__(self) -> "_session_lines":
        return self

    def __exit__(self, *_exc: Any) -> bool:
        return False

    def __iter__(self):
        for part in self._parts:
            with open(part, encoding="utf-8", errors="replace") as fh:
                yield from fh

async def _cmd_search(sh: ShellCtx, positional: list[str], flags: dict) -> int:
    """search <q>:FTS 全文检索(派生索引只读,不触真源)。"""
    from pyharness.core.session_query import SessionQueryIndex
    from pyharness.persistence import SessionReader, session_log_paths
    q = " ".join(positional).strip()
    if not q:
        raise_code("EVT-100", advice="search 需要检索词",
                   hint="用法:pyharness search <检索词>")
    sessions_dir = _sessions_dir_ctx(sh)
    db = Path(str(sh.ctx.settings.storage.db_path)).expanduser()
    sources: dict = {}
    # 唯一枚举点(R14-3):**辅助档**(轮转段/修复备份/隔离档)不是独立会话 —— 此前
    # 按 `stem.startswith("s-")` 判定会把它们当会话注册,而备份/段与主文件**同 seq**
    # ⇒ rebuild 撞 fts_rows 主键冲突 ⇒ search 直接 PERS-202 崩(实测)。
    #
    # 回放源用 **SessionReader**(R14-5):``open_store`` 会取 INV-07 **写**锁并持有到
    # 查询结束 ⇒ 一条只读命令把**所有**会话锁住,期间其他进程开会话撞 PERS-202
    # (实测:并发 open 交替 OPEN_OK / BLOCKED)。只读命令不该持有写锁。
    for f in session_log_paths(sessions_dir):
        sources[f.stem] = SessionReader(f.stem, dir=sessions_dir)
    idx = SessionQueryIndex(db_path=db, sources=sources)
    try:
        await idx.enter(ctx=SimpleNamespace(config=sh.ctx.settings))
        # 逐会话重建(R14-4):只动**本次确实读到源**的会话行。此前用全量
        # ``rebuild()``(DELETE 整表再重插),而打不开的会话(如被活进程持锁 ⇒
        # open_store 抛 PERS-202 ⇒ 上面 `continue` 未登记源)**索引行被一并抹掉且不再
        # 重建** —— 实测:被锁会话的索引水位 2 → 0,静默不可搜。sources 为空时更是
        # 直接清空整个索引。
        # 残余(如实标注):日志被**手工**移除的会话,其陈旧索引行不再被顺带清除
        # (正规删除走 ``delete_session`` 级联)。刻意不"按目录反推清理"——索引库是
        # **全局**的而会话目录按租户分,反推会误删他租户的行。
        for sid in sources:
            await idx.rebuild(session_id=sid)
        res = await idx.query(q)
        hits = [{"session_id": h.session_id, "seq": h.seq, "type": h.type,
                 "ts": h.ts, "snippet": h.snippet, "rank": h.rank}
                for h in res.hits]
    finally:
        try:
            await idx.detach()
        except Exception:                            # noqa: BLE001
            pass
    if flags.get("json"):
        _json_line({"query": q, "timed_out": res.timed_out, "hits": hits})
        return 0
    if not hits:
        print("无结果")
        return 0
    for h in hits:
        print(f"[{h['session_id']}#{h['seq']} {h['type']}] {h['snippet']}")
    return 0

async def _cmd_session(sh: ShellCtx, positional: list[str], flags: dict) -> int:
    """session list / session show <sid>:只读会话清单与回放展示。

    ``2026-09-21 R14-6``:``show`` 改用**只读回放源** ``SessionReader``(与 ``search`` 同款)
    —— 此前借 ``open_store`` 读,而它会取 INV-07 **写**锁 ⇒ **会话正被使用时**
    ``session show`` 自身撞 PERS-202(实测:无锁 rc=0 / 有锁 rc≠0),即"看不了正在跑的
    会话"。只读命令不该持写锁。
    """
    from pyharness.core.session import open_session
    from pyharness.persistence import SessionReader
    sessions_dir = _sessions_dir_ctx(sh)
    act = (positional or ["list"])[0]
    if act == "list":
        from pyharness.desktop.sessions import DesktopSessionManager
        mgr = DesktopSessionManager(dir=sessions_dir, bus=sh.ctx.bus,
                                    config=sh.ctx.settings)
        rows = mgr.list()
        if flags.get("json"):
            _json_line({"sessions": [dict(r) for r in rows]})
            return 0
        if not rows:
            print("0 个会话")
            return 0
        for r in rows:
            name = (r.get("title") or r.get("preview") or "新会话")
            state = "已终态" if r.get("finished") else "活跃"
            print(f"{r['sid']}  {name}  {r.get('lines', 0)} 事件  {state}")
        return 0
    if act == "show":
        sid = positional[1] if len(positional) > 1 else flags.get("session")
        if not sid:
            raise_code("EVT-100", advice="session show 需要 sid",
                       hint="用法:pyharness session show <sid>")
        from pyharness.desktop.sessions import validate_session_id
        sid = validate_session_id(str(sid))
        if not (sessions_dir / f"{sid}.jsonl").exists():
            raise_code("EVT-106", session_id=sid,
                       hint="会话不存在(先 session list 确认 sid)")
        # 只读回放源(R14-6):不取会话写锁 ⇒ 会话正被他进程使用时也能查看
        log_ = await open_session(sid, SessionReader(sid, dir=sessions_dir))
        evs = list(log_.events_after(0))
        msgs = log_.derive_messages()
        if flags.get("json"):
            _json_line({"sid": sid, "messages": msgs,
                        "events": [e.model_dump(exclude_none=True)
                                   for e in evs]})
            return 0
        print(f"会话 {sid} 事件 {len(evs)} 条:")
        for m in msgs:
            role = m.get("role", "?")
            content = str(m.get("content") or "").replace("\n", " ")[:200]
            if not content and m.get("tool_calls"):
                content = "工具调用"
            print(f"  [{role}] {content}")
        return 0
    raise_code("EVT-100", advice=f"未知 session 子命令:{act}",
               hint="可用:session list | session show <sid>")

async def _cmd_fork(sh: ShellCtx, positional: list[str], flags: dict) -> int:
    """fork <sid>:物理复制事件流到新会话 + 父会话落 fork.created 声明。"""
    from pyharness.core.session import open_session
    from pyharness.persistence import flush_kwargs_of, open_store
    sessions_dir = _sessions_dir_ctx(sh)
    if not positional:
        raise_code("EVT-100", advice="fork 需要源会话 sid",
                   hint="用法:pyharness fork <sid>")
    from pyharness.desktop.sessions import validate_session_id
    sid = validate_session_id(str(positional[0]))
    if not (sessions_dir / f"{sid}.jsonl").exists():
        raise_code("EVT-106", session_id=sid, hint="源会话不存在")
    src = open_store(sid, dir=sessions_dir,
                     **flush_kwargs_of(sh.ctx.settings))
    try:
        events = list(src.replay())
    finally:
        src.close()
    if not events:
        raise_code("EVT-106", session_id=sid, hint="源会话为空(缺 session.created)")
    new_sid = f"s-fork-{uuid.uuid4().hex[:8]}"
    tgt = open_store(new_sid, dir=sessions_dir,
                     **flush_kwargs_of(sh.ctx.settings))
    try:
        for e in events:
            if e.type == "session.finished":
                continue
            await tgt.append(e.model_copy(update={"session_id": new_sid}),
                             sync=True)
        await tgt.flush()
    finally:
        tgt.close()
    store2 = open_store(sid, dir=sessions_dir,
                        **flush_kwargs_of(sh.ctx.settings))
    log_ = await open_session(sid, store2)
    try:
        base_seq = max((e.seq for e in events), default=0)
        await log_.append("fork.created",
                          {"new_session_id": new_sid, "base_seq": base_seq,
                           "reason": "cli fork"},
                          actor="system", sync=True)
    finally:
        store2.close()
    if flags.get("json"):
        _json_line({"source_sid": sid, "new_session_id": new_sid,
                    "base_seq": base_seq, "events": len(events)})
    else:
        print(f"已分叉 {sid} → {new_sid}({len(events)} 条事件,base_seq={base_seq})")
    return 0

# Preserve historical import and pickle identities through the compatibility facade.
_session_lines.__module__ = 'pyharness.cli'
