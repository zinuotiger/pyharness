"""QueryLifecycle operations, separated from facade assembly."""
from __future__ import annotations
import asyncio
import os
import sqlite3
from typing import Any, Optional
from pyharness.errors import PyHError, raise_code
from pyharness.core.config_access import _cfg_holder
from pyharness.core.config_access import _cfg
from pyharness.core.query_projection import log, TOOL_NAME, _INDEX_TYPES, _log_warn, FtsQueryHandle

class QueryLifecycle:
    """QueryLifecycle responsibility; state is owned by the composing facade."""

    async def enter(self, ctx: Any = None) -> None:
        """开库建表 + 挂订阅(spec 伪码;幂等:已装载直接返回)。

        - 开 SQLite(WAL;文件库父目录 mkdir 0o700,库文件 chmod 0o600 尽力而为);
        - CREATE VIRTUAL TABLE IF NOT EXISTS events_fts(7 列,actor 为 UNINDEXED
          冗余列,偏离 1)+ 伴生唯一表 fts_rows(行键幂等闸,偏离 2);
        - 阈值经 ctx.config/cfg 的 storage.fts.* 覆盖(构造显式参数优先);
        - ctx.bus 接线:按 _INDEX_TYPES 逐类型订阅 → on_event(owner=**本实例属主**),
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
                has = getattr(registry, "has", None)
                already = has(TOOL_NAME) if callable(has) else False
                lookup = getattr(registry, "lookup", None)
                if not callable(has) and callable(lookup):
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

