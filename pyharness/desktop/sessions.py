"""Desktop session surface and persistence manager."""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any, Optional

from pyharness.core.session import SessionLog, open_session
from pyharness.errors import raise_code
from pyharness.events import EVENT_TYPES

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
                 settings: Any = None) -> None:
        self.dir = Path(dir)
        self.bus = bus
        self.config = config or settings
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
        """总线 → 存储订阅(事件一入内存即入真源队列;强同步三类即写即刷 F011)。

        sid 过滤:桌面总线全局共享(多会话同 bus),订阅者必须只落本会话事件
        ——否则后开会话的事件会串写进先开会话文件(实测:两个 session.created
        同文件、回放错乱)。store.append 侧无 sid 校验,过滤责任在订阅者。
        """
        if self.bus is None:
            return
        owner = f"persistence:{sid}"

        async def _record(type_: str, payload: Any) -> None:
            if getattr(payload, "session_id", None) != sid:
                return                       # 跨会话事件:不落本文件(多会话隔离)
            await store.append(payload, sync=type_ in (
                "user.message", "guard.rejected", "approval.requested",
                "approval.granted", "approval.denied", "approval.timeout",
                "session.finished", "session.recovered", "segment.start",
                "fork.created", "context.compacted"))

        for t in EVENT_TYPES:               # 词表逐精确类型订阅(段通配防重复,见偏离 4)
            self.bus.subscribe(t, _record, owner=owner)
        log_._bus = self.bus                # append → 分发 → 落盘闭环
        self._owners[sid] = owner

    # ------------------------------------------------ 门面方法
    async def create(self) -> str:
        """新建会话:sid → store → open_session → 总线落盘订阅 → session.created 首事件。"""
        sid = f"s-{uuid.uuid4().hex[:12]}"  # Envelope.session_id min_length=8
        store = await self._open_store(sid)
        log_ = await open_session(sid, store)      # 空日志回放(文件刚建)
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
            log_ = await open_session(sid, store)      # 重放重建(坏行 PERS-201 记跳隔离)
            self._attach_persistence(log_, store, sid)
            self._logs[sid] = log_
            self._stores[sid] = store
            return log_

    async def delete_session(self, sid: str) -> dict:
        """永久删除会话及轮转/备份文件;先摘订阅并关闭句柄。"""
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
            removed: list[str] = []
            for path in sorted(candidates):
                try:
                    resolved = path.resolve()
                    resolved.relative_to(root)
                    if resolved.is_file():
                        resolved.unlink()
                        removed.append(resolved.name)
                except FileNotFoundError:
                    continue
            return {"sid": sid, "removed": removed, "count": len(removed)}

    async def _open_store(self, sid: str) -> Any:
        """open_store 工厂(异步外壳保持调用面统一;PERS-202 上抛)。"""
        from pyharness.persistence import open_store
        return open_store(sid, dir=self.dir)

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
        for path in sorted(self.dir.glob("*.jsonl")):
            sid = path.stem
            if not sid.startswith("s-"):        # 轮转段 .N.jsonl / 备份非会话文件跳过
                continue
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
            except Exception:               # noqa: BLE001 文件坏/竞态 → 摘要留最小
                log.warning("desktop list 读 %s 失败(摘要降级)", path.name, exc_info=True)
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
                sessions_dir: Optional[Path] = None) -> Any:
    """会话面解析(偏离 1):门面 → 委托;绑定日志 → 单会话;None → 自装配管理器。"""
    surface = getattr(ctx, "session", None)
    if surface is None:
        if sessions_dir is None:
            raise_code("EVT-106", hint="会话未装配(ctx.session=None)且无持久化目录;"
                       "注入会话门面或先配置 storage.sessions_dir")
        mgr = DesktopSessionManager(dir=sessions_dir, bus=bus, config=config)
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
