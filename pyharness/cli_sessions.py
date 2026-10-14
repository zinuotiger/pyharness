"""Cli sessions: complete migrated implementation."""
from __future__ import annotations
import uuid
from pathlib import Path
from typing import Any, Optional
from pyharness.errors import PyHError

from pyharness.cli_output import ShellCtx, log

class _ExitFlag:
    """外壳退出旗标(commands.cmd_exit 经 ctx.shell.request_exit(0) 副作用表达退出;
    interactive_loop 消费 .exit_code,见 commands.py.md 偏离 6)。"""

    def __init__(self) -> None:
        self.exit_code: Optional[int] = None

    def request_exit(self, code: int = 0) -> None:
        self.exit_code = int(code)

def _sessions_path(cfg: Any) -> Path:
    return Path(cfg.storage.sessions_dir).expanduser()




async def _flush_session(sh: ShellCtx) -> None:
    """收尾落盘 + 资源回收:会话存储攒批全量 flush、引擎外部资源 close、会话门面
    ``shutdown_all``(F011 双速写——真源不可丢;INV-07 会话锁随句柄释放)。

    2026-09-21 R14-13:两步**相互独立**,不得用一个的前置条件短路另一个。此前在
    "``ctx.session`` 不是会话日志(无 ``_persistence``)"时**直接 return**,而 ACP 外壳
    的 ``ctx.session`` 是 ``DesktopSessionManager``(其真源在各 store 里)⇒ **spine.close()
    与 ``shutdown_all()`` 全被跳过**:ACP 退出时既不 flush 也不关 store(61 个非强同步
    事件类型如 ``agent.message``/``tool.result``/``llm.response`` 仍在攒批缓冲里,
    进程退出即丢),与 chat/run 形态(``ctx.session`` 是 SessionLog)行为不一致。
    """
    errors = []
    async def attempt(fn):
        if not callable(fn): return
        try:
            result = fn()
            if hasattr(result, '__await__'): await result
        except BaseException as exc: errors.append(exc)
    if getattr(sh.ctx, '_acp_runtimes', None) or getattr(sh.ctx, '_acp_owns_llm', False):
        from pyharness.acp import close_owned_runtimes
        await attempt(lambda: close_owned_runtimes(sh.ctx))
    await attempt(getattr(getattr(sh.ctx, 'engine_spine', None), 'close', None))
    log_ = getattr(sh.ctx, 'session', None)
    persist = getattr(log_, '_persistence', None)
    if not getattr(getattr(persist, '_fh', None), 'closed', False):
        await attempt(getattr(persist, 'flush', None))
    await attempt(getattr(persist, 'close', None))
    await attempt(getattr(log_, 'shutdown_all', None))
    await attempt(getattr(getattr(sh.ctx, 'llm_runtime', None), 'aclose', None))
    if errors: raise BaseExceptionGroup('CLI cleanup failed', errors)

async def _scan_unhealthy(sessions_dir: Path, *,
                          cfg: Any = None) -> Optional[str]:
    """启动自检(F060):pyharness.repair.auto_scan 装配后取首个不健康会话;模块未
    装配 → 降级跳过(记日志),不阻断启动(见偏离 7)。

    ``cfg``(2026-09-21 R11-2):带上即可启用**索引落后对账**(``auto_scan(db_path=…)``)
    —— 此前该对账因"无人提供 fts_last_seq"而从未生效。

    ``2026-09-21 R14-2``:跳过**正被其他进程持有**的会话。索引是 200ms **攒批**落的
    派生视图,活会话在攒批窗口内必然 `view_last < last` ⇒ 被判 `index_stale`;此前自检
    会把它当"待修"交给 ``_ensure_repair`` ⇒ ``repair_session`` 取锁撞 PERS-202 ⇒
    **上抛** ⇒ 第二个 CLI 启动直接失败(两进程实测复现)。活会话本就不该是候选:它既不是
    损坏,本进程也修不动。
    """
    try:
        from pyharness.repair import auto_scan
    except Exception as exc:                   # noqa: BLE001 repair 模块未装配
        log.info("repair.auto_scan 未装配,跳过启动自检 (%s)", exc)
        return None
    db_path = str(getattr(getattr(cfg, "storage", None), "db_path", "") or "")
    try:
        hits = await auto_scan(sessions_dir, db_path=db_path or None)
    except PyHError:
        raise
    except Exception:                          # noqa: BLE001 自检失败不阻断启动
        log.warning("启动自检异常,跳过", exc_info=True)
        return None
    return _repairable_unhealthy(hits, sessions_dir)

def _repairable_unhealthy(hits: list, sessions_dir: Path) -> Optional[str]:
    """从 auto_scan 结果取首个**可修**的损坏会话;**跳过被其他进程持有的活会话**。

    R14-2 / R14-7:索引是 200ms **攒批**落的派生视图 ⇒ 活会话在攒批窗口内必然
    ``view_last != last`` 而被判 ``index_stale``。这类会话**既不是损坏**、本进程也
    **修不动**(``repair_session`` 取锁即 PERS-202)⇒ 必须跳过;否则调用方要么
    启动中止(启动自检,R14-2),要么整条命令以 PERS-202 失败、**永远够不到真正损坏
    的会话**(``repair``,R14-7 —— 修复前的实测:只有活会话时不报"无损坏会话"而直接
    抛 PERS-202)。

    **唯一候选筛选点**:自检与 repair 子命令都必须走这里。
    """
    from pyharness.persistence import session_lock_held, session_lock_path
    for h in hits:
        if getattr(h, "healthy", False):
            continue
        sid = getattr(h, "sid", None)
        if not sid:
            continue
        if session_lock_held(session_lock_path(sessions_dir / f"{sid}.jsonl")):
            log.info("repair 候选:会话正被其他进程持有,跳过 sid=%s", sid)
            continue
        return sid
    return None

async def _ensure_repair(ctx: Any, sid: str) -> None:
    """repair 前置(幂等):损坏则先 repair 再 open;模块未装配 → 跳过,坏行由回放
    PERS-201 记跳(见偏离 7)。不可修复 → PERS-201 上抛(原文件已备份留存)。"""
    try:
        from pyharness.repair import repair_session
    except Exception as exc:                   # noqa: BLE001 未装配降级
        log.info("repair_session 未装配,跳过会话前置修复 (%s)", exc)
        return
    try:
        await repair_session(ctx, sid, interactive=False)
    except PyHError as e:
        if e.code.startswith("PERS-201"):
            raise                                  # 不可修复:明确报错+备份留存
        log.warning("repair_session sid=%s 异常:%s", sid, e.code)
        raise

# Preserve historical import and pickle identities through the compatibility facade.
_ExitFlag.__module__ = 'pyharness.cli'
