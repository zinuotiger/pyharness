"""Desktop session surface and persistence manager."""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from pyharness.core.session import SessionLog, open_session
from pyharness.errors import raise_code
from pyharness.events import EVENT_TYPES
from pyharness.persistence import (now_ts, session_log_paths,
                                   session_recorder)

log = logging.getLogger("pyharness.desktop.sessions")

_SID_RE = re.compile(r"^s-[A-Za-z0-9._-]{6,64}$")


def validate_session_id(sid: Any) -> str:
    """会话 ID 安全校验:只允许内部生成的安全字符,阻断路径穿越。"""
    value = str(sid or "")
    if not _SID_RE.fullmatch(value):
        raise_code("EVT-100", field="sid", sid=value,
                   advice="sid 必须匹配 s-[A-Za-z0-9._-]{6,64}")
    return value

async def _await(res: Any) -> Any:
    """同步/异步兼容调用(引擎各面注入形状不一,cli/acp 同款先例)。"""
    if inspect.isawaitable(res):
        return await res
    return res


def _cfg_of(ctx: Any) -> Any:
    """配置读取:cli 门面存 settings,测试门面存 config,两键兼容。"""
    return getattr(ctx, "settings", None) or getattr(ctx, "config", None)


def _resolve_secret_value(ref: Any) -> str:
    """shell.web.token 秘密引用解析(env:/file:;明文回落原值)。"""
    s = str(ref or "")
    if s.startswith("env:"):
        return os.environ.get(s[4:], "")
    if s.startswith("file:"):
        p = Path(s[5:]).expanduser()
        try:
            if p.exists():
                lines = p.read_text(encoding="utf-8").splitlines()
                if lines:
                    return lines[0].strip()
        except OSError:
            pass
        return ""
    return s


def _plugin_within(child: Path, root: Path) -> bool:
    """目录包含判定(child 必须在 root 内,防 HTTP 加载任意本地路径)。"""
    try:
        child.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _sessions_dir_of(ctx: Any) -> Optional[Path]:
    """会话日志目录(ctx.storage.sessions_dir 或 settings.storage.sessions_dir)。"""
    storage = getattr(ctx, "storage", None)
    d = getattr(storage, "sessions_dir", None)
    if d is None:
        cfg = _cfg_of(ctx)
        st = getattr(cfg, "storage", None)
        d = getattr(st, "sessions_dir", None)
    return Path(d).expanduser() if d else None


class _SingleLogSurface:
    """形状 (b):ctx.session 即已绑定会话日志 → 单会话投影(其余 sid → EVT-106)。"""

    def __init__(self, log: Any) -> None:
        self._log = log
        self.sid = getattr(log, "sid", "")

    async def create(self) -> str:
        raise_code("EVT-106", hint="ctx.session 为已绑定日志,桌面无法新建会话;"
                   "注入会话门面(create/open_session)后支持多会话")

    async def open_session(self, sid: str) -> Any:
        if sid == self.sid:
            return self._log
        raise_code("EVT-106", session_id=sid,
                   hint=f"ctx.session 仅绑定会话 {self.sid};注入会话门面后支持切换")

    def list(self) -> list[dict]:
        return [_summarize_log(self._log)]


class DesktopSessionManager:
    """形状 (c):真 persistence 会话门面(desktop 自装配)——多会话磁盘真源管理。

    create:新建 sid → open_store + open_session(session.created seq=1,EVT-106 引导);
    open_session:文件不存在 → EVT-106;存在 → repair 鸭子前置(F060 未装配跳过) →
    重放重建 + 总线落盘订阅(owner=f"persistence:{sid}",cli._attach_log_persistence 同款);
    list:扫描目录 *.jsonl,头行(session.created)取标题,尾行取终态(不整文件重放)。
    shutdown_all:全量 flush + 关句柄 + 摘订阅(优雅停服兜底)。
    """

    def __init__(self, *, dir: Path, bus: Any, config: Any = None,
                 settings: Any = None, tenant_id: Optional[str] = None) -> None:
        self.dir = Path(dir)
        self.bus = bus
        self.config = config or settings
        # GAP-10:本管理器所服务的租户;经 open_session 盖到**每条事件**的信封上
        # (而不是只体现在目录布局里)。恢复时以日志已落值为准(见 core/session.py)。
        self.tenant_id = tenant_id
        self._logs: dict[str, SessionLog] = {}
        self._owners: dict[str, str] = {}
        self._stores: dict[str, Any] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------ 内部
    def _path(self, sid: str) -> Path:
        sid = validate_session_id(sid)
        root = self.dir.resolve()
        path = (root / f"{sid}.jsonl").resolve()
        try:
            path.relative_to(root)
        except ValueError:
            raise_code("EVT-100", field="sid", sid=sid,
                       advice="会话路径越界:拒绝访问 sessions_dir 之外")
        return path

    def _lock_for(self, sid: str) -> asyncio.Lock:
        return self._locks.setdefault(sid, asyncio.Lock())

    def _attach_persistence(self, log_: SessionLog, store: Any, sid: str) -> None:
        """总线 → 存储订阅(事件一入内存即入真源队列;强同步即写即刷 F011)。

        sid 过滤:桌面总线全局共享(多会话同 bus),订阅者必须只落本会话事件
        ——否则后开会话的事件会串写进先开会话文件(实测:两个 session.created
        同文件、回放错乱)。store.append 侧无 sid 校验,过滤责任在订阅者。
        2026-09-21 R8:该订阅**改为委托唯一实现** ``persistence.session_recorder``
        (engine 侧长期缺同一过滤 ⇒ 同一关注点两处实现、漏一处;现单点)。
        """
        if self.bus is None:
            return
        # 属主**含租户**(2026-09-21 R12):会话目录按租户分目录 ⇒ 同名 sid 跨租户合法,
        # 若属主只含 sid,则两租户同属主 → 关闭/删除其一即摘掉**另一租户**的落盘订阅
        # (其会话从此静默不落盘)。同 R8-4(原生壳固定属主)的同类:属主必须含**全部**
        # 隔离维度。
        owner = f"persistence:{getattr(self, 'tenant_id', None) or ''}:{sid}"
        record = session_recorder(store, sid)

        for t in EVENT_TYPES:               # 词表逐精确类型订阅(段通配防重复,见偏离 4)
            self.bus.subscribe(t, record, owner=owner)
        log_._bus = self.bus                # append → 分发 → 落盘闭环
        self._owners[sid] = owner

    # ------------------------------------------------ 门面方法
    async def create(self) -> str:
        """新建会话:sid → store → open_session → 总线落盘订阅 → session.created 首事件。"""
        sid = f"s-{uuid.uuid4().hex[:12]}"  # Envelope.session_id min_length=8
        store = await self._open_store(sid)
        log_ = await open_session(sid, store,
                                  tenant_id=self.tenant_id)   # GAP-10
        self._attach_persistence(log_, store, sid)
        await log_.append("session.created", {"title": "", "model": self._default_model()},
                          actor="system", sync=True)
        self._logs[sid] = log_
        self._stores[sid] = store
        return sid

    def _default_model(self) -> str:
        cfg = self.config
        llm = getattr(cfg, "llm", None)
        model = getattr(llm, "model", None)
        return str(model) if model else "unknown"

    async def open_session(self, sid: str) -> SessionLog:
        """打开既有会话(重放重建 + 落盘订阅);文件不存在 → EVT-106。"""
        sid = validate_session_id(sid)
        async with self._lock_for(sid):
            if sid in self._logs:           # 同柄复用(INV-03 缓存一致防双实例漂移)
                return self._logs[sid]
            if not self._path(sid).exists():
                raise_code("EVT-106", session_id=sid,
                           hint="会话不存在(日志缺失);先 `session list` 确认 sid 或新建会话")
            await self._ensure_repaired(sid)    # 损坏先修再 open(F060;未装配跳过)
            store = await self._open_store(sid)
            log_ = await open_session(sid, store,
                                      tenant_id=self.tenant_id)   # GAP-10
            self._attach_persistence(log_, store, sid)
            self._logs[sid] = log_
            self._stores[sid] = store
            return log_

    # ------------------------------------------------------------ 归档(F055)
    def archive_root(self) -> Path:
        """归档根 = **会话目录的兄弟**下的 ``archive/sessions``。

        由会话目录推导(而非另配路径)⇒ 归档与会话**同域**:默认租户落在
        ``~/.pyharness/archive/sessions``,租户 T 落在 ``…/tenants/T/archive/sessions``
        —— 不会把某租户的会话归档进别的租户区(R14-8 同款"派生视图与真源同域")。
        """
        return self.dir.parent / "archive" / "sessions"

    def _archive_days(self) -> int:
        """保留期(天):`storage.archive_days`(默认 30;**0 = 不归档,立即删**)。"""
        from pyharness.config import DEFAULTS

        default = int(DEFAULTS["storage"]["archive_days"])
        v = getattr(getattr(self.config, "storage", None), "archive_days", None)
        try:
            return int(v) if v is not None else default
        except (TypeError, ValueError):
            return default

    def _prune_archives(self, days: int) -> list[str]:
        """清理超过保留期的归档目录(只读判定 + 递归删除;失败只告警)。"""
        import shutil as _sh

        root = self.archive_root()
        if not root.is_dir():
            return []
        cutoff = time.time() - days * 86400
        gone: list[str] = []
        for entry in sorted(root.iterdir()):
            try:
                if entry.is_dir() and entry.stat().st_mtime < cutoff:
                    _sh.rmtree(entry, ignore_errors=True)
                    gone.append(entry.name)
            except OSError:                            # noqa: BLE001 清理尽力而为
                log.warning("archive prune 跳过 %s", entry, exc_info=True)
        return gone

    async def delete_session(self, sid: str) -> dict:
        """删除会话及轮转/备份文件;先摘订阅并关闭句柄。

        ``storage.archive_days``(R24 接线,默认 30 天;``0`` = 不归档立即删)——
        此前该键**零读取者**且删除**直接 unlink**(L-21:误删不可恢复,而文档承诺
        30 天窗口)。现:>0 时把该会话的**全部相关文件**移入同域归档区
        (``{sid}-{ts}/``),再按保留期清理过期归档。
        """
        sid = validate_session_id(sid)
        async with self._lock_for(sid):
            root = self.dir.resolve()
            candidates = {root / f"{sid}.jsonl"}
            candidates.update(root.glob(f"{sid}.*.jsonl"))
            candidates.update(root.glob(f"{sid}.jsonl.*"))
            if sid not in self._logs and not any(p.is_file() for p in candidates):
                raise_code("EVT-106", session_id=sid, hint="会话不存在,无法删除")
            owner = self._owners.pop(sid, None)
            if owner and self.bus is not None:
                try:
                    self.bus.unsubscribe_all(owner)
                except Exception:                      # noqa: BLE001
                    log.warning("desktop delete unsubscribe %s failed", sid,
                                exc_info=True)
            store = self._stores.pop(sid, None)
            if store is not None:
                await _await(store.flush())
                store.close()
            self._logs.pop(sid, None)
            days = self._archive_days()
            moved: list[str] = []
            removed: list[str] = []
            dest: Optional[Path] = None
            if days > 0:
                dest = self.archive_root() / f"{sid}-{now_ts()}"
                try:
                    dest.mkdir(parents=True, exist_ok=True, mode=0o700)
                except OSError as e:                   # noqa: BLE001 归档不可用 → 拒绝删
                    raise_code("PERS-202", op="archive", path=str(dest), why=str(e),
                               advice="归档目录不可创建;删除**已中止**(不静默永久删)")
            for path in sorted(candidates):
                try:
                    resolved = path.resolve()
                    resolved.relative_to(root)
                    if not resolved.is_file():
                        continue
                    if dest is not None:
                        resolved.replace(dest / resolved.name)   # 移动(可恢复)
                        moved.append(resolved.name)
                    else:
                        resolved.unlink()                # archive_days=0:立即删
                        removed.append(resolved.name)
                except FileNotFoundError:
                    continue
            pruned = self._prune_archives(days) if days > 0 else []
            # 每会话串行锁随会话释放(2026-09-21 R8:此前 _locks 只增不减 ⇒
            # 见过的每个 sid 都留一个 asyncio.Lock;此处补 create→…→release 的最后一环)
            self._locks.pop(sid, None)
            return {"sid": sid, "removed": removed + moved, "count": len(removed) + len(moved),
                    "archived": str(dest) if dest is not None else None,
                    "archive_days": days, "pruned": pruned}

    async def _open_store(self, sid: str) -> Any:
        """open_store 工厂(异步外壳保持调用面统一;PERS-202 上抛)。"""
        from pyharness.persistence import flush_kwargs_of, open_store
        # N1:flush 标量由装配层解析后注入(工厂不读 config)
        return open_store(sid, dir=self.dir, **flush_kwargs_of(self.config))

    async def _ensure_repaired(self, sid: str) -> None:
        """repair 前置(F060;ctx.repair.ensure_repaired 未装配 → 跳过,见偏离 8)。"""
        repair = getattr(self, "_ctx_repair", None)
        fn = getattr(repair, "ensure_repaired", None)
        if callable(fn):
            res = fn(sid)
            if hasattr(res, "__await__"):
                await res

    def list(self) -> list[dict]:
        """会话摘要清单:只读头部若干行 + 尾行 + 行数,不整文件重放。

        排序:按最后活动时间(st_mtime)倒序——最新使用的会话排最上(桌面
        会话列表时间序)。preview = 首条 user.message 内容前 10 字(列表显示
        用;title 为空时它就是会话的"名字")。
        """
        out: list[dict] = []
        if not self.dir.exists():
            return out
        # 唯一枚举点(R14-3):此前用 ``stem.startswith("s-")`` 声称"跳过轮转段/备份",
        # 但 ``{sid}.1.jsonl`` / ``{sid}.corrupt-*`` 的 stem **同样**以 ``s-`` 开头
        # ⇒ 该守卫从未生效,列表混入"备份伪装成的会话"(实测:1 会话 → 列出 3 条)。
        for path in session_log_paths(self.dir):
            sid = path.stem
            summary: dict[str, Any] = {"sid": sid, "title": "", "preview": "",
                                       "finished": False}
            try:
                stat = path.stat()
                summary["updated"] = stat.st_mtime          # 使用时间(最后写入)
                summary["size_bytes"] = stat.st_size
                lines: list[bytes] = []
                count = 0
                last: bytes | None = None
                with open(path, "rb") as f:
                    for raw in f:
                        count += 1
                        if len(lines) < 60:
                            lines.append(raw)
                        last = raw
                summary["lines"] = count
                if lines:
                    head = json.loads(lines[0])
                    if head.get("type") == "session.created":
                        summary["title"] = (head.get("payload") or {}).get("title", "")
                    tail = json.loads(last or lines[-1])
                    summary["finished"] = tail.get("type") == "session.finished"
                    # 首条 user.message 内容前 10 字(会话"名字";只扫头部防 O(n))
                    for ln in lines[:60]:
                        try:
                            e = json.loads(ln)
                        except Exception:                 # noqa: BLE001 坏行跳过
                            continue
                        if e.get("type") == "user.message":
                            c = (e.get("payload") or {}).get("content") or ""
                            summary["preview"] = c.strip()[:10]
                            break
            except Exception as e:          # noqa: BLE001 文件坏/竞态 → 摘要留最小
                # 一行告警(不打整段 traceback):UI 每次轮询会话列表都读此路径,
                # 单个不可读文件若打 traceback 会持续刷屏;只报类型名即可定位。
                log.warning("desktop list 读 %s 失败(摘要降级):%s",
                            path.name, type(e).__name__)
            out.append(summary)
        out.sort(key=lambda s: s.get("updated", 0), reverse=True)  # 时间倒序
        return out

    def logs(self) -> list[SessionLog]:
        """已打开会话日志清单(优雅停服 flush 用)。"""
        return list(self._logs.values())

    async def shutdown_all(self) -> None:
        """收尾:全量 flush + 关句柄 + 摘落盘订阅(幂等;正常路径强同步事件已落盘)。"""
        for sid, store in self._stores.items():
            try:
                await _await(store.flush())
            except Exception:               # noqa: BLE001 收尾刷盘失败不掩盖退出
                log.warning("desktop flush sid=%s 失败", sid, exc_info=True)
            try:
                store.close()
            except Exception:               # noqa: BLE001
                log.warning("desktop close store sid=%s failed", sid,
                            exc_info=True)
        if self.bus is not None:
            for owner in set(self._owners.values()):
                try:
                    self.bus.unsubscribe_all(owner)
                except Exception:           # noqa: BLE001
                    log.warning("desktop unsubscribe owner=%s failed", owner,
                                exc_info=True)
        self._stores.clear()
        self._owners.clear()
        self._logs.clear()


def _summarize_log(log_: Any) -> dict:
    """单会话日志摘要(形状 b 投影;stats 鸭子读取)。"""
    stats_fn = getattr(log_, "stats", None)
    stats = stats_fn() if callable(stats_fn) else {}
    return {"sid": getattr(log_, "sid", ""),
            "title": "", "finished": bool(stats.get("closed", False)),
            "seq": stats.get("seq", 0), "event_count": stats.get("event_count", 0)}


def _surface_of(ctx: Any, *, bus: Any, config: Any = None,
                sessions_dir: Optional[Path] = None,
                tenant_id: Optional[str] = None) -> Any:
    """会话面解析(偏离 1):门面 → 委托;绑定日志 → 单会话;None → 自装配管理器。"""
    surface = getattr(ctx, "session", None)
    if surface is None:
        if sessions_dir is None:
            raise_code("EVT-106", hint="会话未装配(ctx.session=None)且无持久化目录;"
                       "注入会话门面或先配置 storage.sessions_dir")
        mgr = DesktopSessionManager(dir=sessions_dir, bus=bus, config=config,
                                    tenant_id=tenant_id)
        try:
            ctx.session = mgr               # 惰性回写:引擎门面后续可复用(acp 同款)
        except Exception:                   # noqa: BLE001 只读 ctx:仅本地持有
            log.debug("desktop surface write-back skipped", exc_info=True)
        return mgr
    if callable(getattr(surface, "open_session", None)):
        return surface                      # (a) 门面(create/open_session/list)
    if getattr(surface, "sid", None):
        return _SingleLogSurface(surface)   # (b) 已绑定会话日志
    raise_code("EVT-106", hint="会话门面形状非法(无 open_session 亦无 sid);"
               "注入 create/open_session 门面")
