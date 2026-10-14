"""Repair recovery: complete migrated implementation."""
from __future__ import annotations
import inspect
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Optional
from pyharness.errors import PyHError, raise_code
from pyharness.persistence import flush_kwargs_of, open_store

from pyharness.repair_scan import QuarantineEntry, RepairPolicy, SessionHealth, _QUARANTINE_RAW_CAP, _TAIL_SEQ_PROBE, _db_path_of, _iter_bytes, _iter_text, _log_last_seq, _session_content_last, _sessions_dir, log, now_ts

def backup_file(path: Path, *, _clock=now_ts, _sync_directory) -> Path:
    """修复前强制备份:copy2 整文件 → `{sid}.corrupt-{utc_ms}.jsonl`(PRD F060 命名)。

    同 ts 备份已存在(重试/并发)→ 直接返回(幂等,不重复备份);备份权限 600
    (Windows NTFS chmod 尽力而为,失败只记日志不阻断——权限收紧非修复动作)。
    异常:OSError → PERS-202(备份优先于一切修复动作,备份失败 = 修复中止)。
    """
    dst = path.with_name(f"{path.stem}.corrupt-{_clock()}.jsonl")
    if dst.exists():
        return dst
    try:
        shutil.copy2(path, dst)            # 元数据一并保留
    except OSError as e:
        raise_code("PERS-202", op="backup", path=str(path), why=str(e),
                   advice="备份失败,修复中止(备份优先于一切修复动作);查磁盘/权限")
    try:
        os.chmod(dst, 0o600)
    except OSError as e:
        log.warning("repair: 备份文件权限 600 收紧失败(尽力而为) dst=%s why=%s",
                    dst, e)
    log.info("repair: 修复前备份 %s", dst)
    return dst




def locate_holes(health: SessionHealth) -> list[int]:
    """seq 空洞定位(声明合法化,纯函数):holes 已被 compacted/recovered 声明
    覆盖 = 合法(压缩/修复事实)不告警;未声明空洞 → 告警并触发 F031 深查线索
    (不自动补 seq,append 永远 max+1)。scan 已滤声明,此处按 spec 语义再防御一遍。
    """
    if not health.holes:
        return []
    undeclared = []
    for h in health.holes:
        if any(lo <= h <= hi for lo, hi in health.holes_declared):
            continue
        undeclared.append(h)
    if undeclared:
        log.warning("repair: 未声明 seq 空洞 %s → F031 深查线索(不回填,append "
                    "恒 max+1) sid=%s", undeclared, health.sid)
    return undeclared

def _index_handle(ctx: Any) -> tuple[Any, bool]:
    """FTS 派生视图句柄解析(**唯一入口**):返回 (句柄, 是否本函数自查构造)。

    解析序(鸭子类型,逐级兜底):
      ① ``ctx.session_query``(装配注入面;引擎**只注入在 agent ctx** 上);
      ② 兄弟形态 ``ctx.fts`` / ``ctx.engine_spine.fts``(engine 的 spine 自带);
      ③ 由 ``cfg.storage.db_path`` **惰性构造**(repair 只需"库在哪" —— 与 R11-2
         的 `read_max_seq` 同款思路,自有句柄由调用方 detach 收尾)。

    2026-09-21 R14-1 修:此前只认 ①,而 repair 的调用方(CLI 命令 / 桌面)传的是
    **外壳 ctx**(无 `session_query`)⇒ "⑤派生视图整体重建"步骤**不可达** ⇒ repair
    修完仍 `index_stale=True`(不收敛)。同 PIT-19("约定被读 ≠ 被注入")。
    """
    sq = getattr(ctx, "session_query", None)
    if sq is not None and callable(getattr(sq, "rebuild", None)):
        return sq, False
    for holder in (ctx, getattr(ctx, "engine_spine", None)):
        cand = getattr(holder, "fts", None) if holder is not None else None
        if cand is not None and callable(getattr(cand, "rebuild", None)):
            return cand, False
    db_path = _db_path_of(ctx)
    if not db_path:
        return None, False
    try:
        from pyharness.core.session_query import SessionQueryIndex
        return SessionQueryIndex(db_path=db_path), True
    except Exception as e:                           # noqa: BLE001 构造失败=未装配
        log.warning("repair: 构造 FTS 句柄失败(%s):视图重建跳过 sid=?", e)
        return None, False

async def rebuild_derived_views(ctx: Any, sid: str) -> bool:
    """派生视图整体重建(原则 1):FTS 与 storage KV 全部丢弃重建(重放,非增量修补)。

    句柄经 ``_index_handle`` 解析(装配注入面 → 兄弟形态 → cfg 惰性构造;R14-1);
    视图未装配 → 日志降级返回 False(派生可弃,装配后自动生效);重建后对账(视图
    暴露 max_seq(sid) 时):日志末 seq != 视图末 seq → PERS-201(视图落后不静默)。
    索引写失败 → PERS-202(原日志不受影响,索引=派生可再建)。
    """
    sq, owned = _index_handle(ctx)
    if owned and sq is not None:                 # 自建句柄须先装载(R14-1)
        try:
            await sq.enter(ctx)
        except Exception as e:                   # noqa: BLE001 装载失败=视图不可用
            log.warning("repair: 自建 FTS 句柄装载失败(%s):视图重建跳过 sid=%s",
                        type(e).__name__, sid)
            sq, owned = None, False
    rebuild = getattr(sq, "rebuild", None) if sq is not None else None
    storage = getattr(ctx, "storage", None)
    kv = None
    if storage is not None:
        cand = getattr(storage, "kv_rebuild", None)
        kv = cand if callable(cand) else None
    if rebuild is None and kv is None:
        log.info("repair: 派生视图(FTS/storage)未装配,整体重建跳过 sid=%s", sid)
        return False
    store = None
    view_last: Optional[int] = None
    try:
        if rebuild is not None:              # FTS 重放源:修复后的主文件
            store = open_store(
                sid, dir=_sessions_dir(ctx),
                **flush_kwargs_of(getattr(ctx, "config", None)
                                  or getattr(ctx, "settings", None)))
            attach = getattr(sq, "attach_source", None)
            if callable(attach):
                attach(sid, store)
            try:
                await rebuild(session_id=sid)
            except OSError as e:
                raise_code("PERS-202", op="views-rebuild", sid=sid, why=str(e),
                           advice="索引为派生副本可再建;真源不受影响,重跑 repair")
            log.info("repair: FTS 派生视图重建完成 sid=%s", sid)
        if kv is not None:                   # storage KV 派生缓存重建(鸭子面)
            try:
                kv(sid)
            except OSError as e:
                raise_code("PERS-202", op="kv_rebuild", sid=sid, why=str(e),
                           advice="KV 派生缓存可再建;真源不受影响")
        # 对账读数**必须在句柄有效期内**取(R14-1 二阶):自建句柄在 finally 里
        # detach 关库(``_db=None``),此后 ``max_seq`` 恒返 0 ⇒ 与日志水位不等 ⇒
        # **假 PERS-201**(修复自身引出的缺陷:重建明明成功却被判不一致)。
        mx = getattr(sq, "max_seq", None) if sq is not None else None
        if callable(mx):
            try:
                view_last = mx(sid)
            except Exception:                # noqa: BLE001 — 提供者故障降级
                log.warning("repair: 视图 max_seq(%s) 查询失败,对账跳过", sid)
    finally:
        if store is not None:
            try:
                store.close()                # 只读回放源句柄收尾
            except Exception:                # noqa: BLE001 — 清理不阻断
                log.debug("repair: FTS 回放源 close 失败", exc_info=True)
        if owned and sq is not None:         # 本函数自建的句柄:摘除并关库(R14-1)
            try:
                det = getattr(sq, "detach", None)
                if callable(det):
                    r = det(None)
                    if inspect.isawaitable(r):
                        await r
            except Exception:                # noqa: BLE001 — 清理不阻断
                log.debug("repair: 自建 FTS 句柄 detach 失败", exc_info=True)
    # 对账判定:日志末 seq == 视图末 seq(读数已在句柄有效期内取得;不一致 → PERS-201)
    if view_last is not None:
        # 对账口径:与索引 max_seq 同口径 = **可索引事件**(内容型 insert)末 seq——
        # 排除 session.recovered 声明(不索引)与一切非内容事件(created/finished/
        # llm.request/guard.* 等,不产索引行);否则尾部非内容即误抛 PERS-201。
        # 水位是**会话级**的(R14-1 二阶):重建源 replay 合并读轮转段 ⇒ 对账也须跨段
        # 取 max(与 scan_session 单源),否则主文件被截空时假 PERS-201。
        log_last = _session_content_last(_sessions_dir(ctx) / f"{sid}.jsonl")
        if log_last != view_last:
            raise_code("PERS-201", op="view-reconcile", sid=sid,
                       log_last=log_last, view_last=view_last,
                       advice="索引对账不一致(视图落后不静默);重跑 repair "
                              "或手动重建派生视图")
    return True

async def declare_recovered(ctx: Any, sid: str, fixed: list[str],
                            lost: Optional[list[int]] = None,
                            kept: Optional[list[QuarantineEntry]] = None,
                            backup: Optional[Path] = None) -> Optional[int]:
    """recovered 事件声明(修复是事件不是抹除):actor=system 强同步 append 到目标
    会话日志;声明是修复的审计落点与"空洞合法化"依据(persistence._declared_by_
    compaction 消费 fixed seq-holes/lost,后续 replay 不再告警)。

    载荷按注册词表 SessionRecoveredPayload(fixed 非空/backup/lost,偏离 1):
    fixed 为空(无修复动作,含"用户保留原地")→ 不追加(幂等,注册载荷 min_length=1);
    目标会话已终态(finished)/空文件未 created → append 语义 EVT-104/106 不允许,
    声明跳过(日志 + 报告 recovered_seq=None;审计由备份 + RepairReport 承担);
    其余 PyHError(PERS-202 落盘失败)→ 上抛(文件已修,声明可下次 repair 补,幂等
    保证不重复动作)。返回 recovered 事件 seq。
    """
    if not fixed:                            # 无修复动作:不声明(偏离 3)
        return None
    payload: dict[str, Any] = {"fixed": fixed}
    if backup is not None:
        payload["backup"] = str(backup)
    if lost:
        payload["lost"] = [int(x) for x in lost]
    from pyharness.core.session import open_session   # 惰性:避免装配期硬依赖
    store = open_store(  # 修复先于 open(CLI 序)→ 自开
        sid, dir=_sessions_dir(ctx),
        **flush_kwargs_of(getattr(ctx, "config", None)
                          or getattr(ctx, "settings", None)))
    primary = None
    try:
        log_ = await open_session(sid, store)
        log_._bus = _DirectBus(store)
        env = await log_.append("session.recovered", payload,
                                actor="system", sync=True)
    except PyHError as e:
        primary = e
        if e.code in ("EVT-104", "EVT-106"):  # 终态/未 created:声明不适用
            primary = None  # suppressed contract result must not suppress cleanup errors
            log.warning("repair: recovered 声明跳过(session=%s %s,审计以备份+"
                        "报告为准) fixed=%s", sid, e.code, fixed)
            return None
        raise                                  # PERS-202 等:上抛(声明可下次补)
    except BaseException as exc:
        primary = exc
        raise
    finally:
        try: store.close()
        except BaseException as exc:
            if primary is None: raise
            primary.add_note(f"repair close failed: {type(exc).__name__}")
    log.info("repair: session.recovered 声明 sid=%s seq=%d fixed=%s lost=%s",
             sid, env.seq, fixed, lost or [])
    return env.seq

class _DirectBus:
    """SessionLog 直连落盘总线:等价 cli._attach_log_persistence 的 bus→store 订阅
    (INV-08 无回边:事件经注入 store.append 物理落盘,不自造信封)。仅服务本模块
    recovered 声明的单次落盘,不依赖全局装配。"""

    def __init__(self, store: Any) -> None:
        self._store = store

    async def emit(self, type_: str, payload: Any, mode: str = "sequential"
                   ) -> None:
        env = payload                        # SessionLog._dispatch 以 Envelope 为载荷
        await self._store.append(env, sync=True)

def _dropped_seqs(path: Path, offset: Optional[int]) -> list[int]:
    """尾部半行含未完成事件候选 seq(0/1 个):信封序列化 seq 字段最先落,半行内
    常可探测;探测不到则取"最后完整 seq + 1"(append 恒 max+1 分配的事实口径)。
    offset 为 None/文件空/剩余为空 → []。
    """
    if offset is None:
        return []
    try:
        data = path.read_bytes()[offset:]
    except OSError:
        return []
    if not data.strip():
        return []
    probe = data[:_TAIL_SEQ_PROBE]
    m = re.search(rb'"seq"\s*:\s*(\d+)', probe)
    if m:
        return [int(m.group(1))]
    last = _log_last_seq(path)               # 整文件半行且无 seq 可探:无完整事件
    return [last + 1] if last else []

# Preserve historical import and pickle identities through the compatibility facade.
_DirectBus.__module__ = 'pyharness.repair'
