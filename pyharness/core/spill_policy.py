"""Spill policy: complete migrated implementation."""
from __future__ import annotations
import logging
from pathlib import Path
from typing import Any, Optional
from pyharness.core.config_access import _cfg_holder
from pyharness.core.config_access import _cfg


log = logging.getLogger("pyharness.spill")

SUMMARY_PREVIEW: int = 500          # 摘要/引用 preview = 头 500 字符(固定)

DEFAULT_MAX_PER_FILE: int = 10_485_760   # 默认 10MB(storage.spill.max_per_file_bytes)

DEFAULT_MAX_PER_SESSION_MB: int = 100

DEFAULT_LIMIT: int = 200            # read 默认行数(1-1000)

MAX_READ_LIMIT: int = 1000          # read 单次上限行(schema 同步锁死)

MODE_PRIVATE: int = 0o600           # 私有文件权限

_MODE_PRIVATE_DIR: int = 0o700      # 目录需 owner execute 才能遍历，group/other 仍为零

TOOL_NAME: str = "storage.spill"    # LLM 可见读工具名(模块职责 7:只读面)

COUNTER_KEY: str = "spill_read_chars"  # 已读量计数键(防循环烧预算,F039 边界)

SPILL_PATTERN: str = "spill-*.txt"  # spill 文件 glob(清理/审计用)

FILE_PREFIX: str = "spill-"         # 文件名前缀(事件可 grep)

_REFUSED_ADVICE: str = "spill 引用越界,已拒绝读取;只允许本会话私有区"

_TOO_BIG_ADVICE: str = "输出超过 10MB spill 上限;上游截断或分块"

_WRITE_FAIL_ADVICE: str = "查 spill 目录权限(700)、文件权限(600)与磁盘空间(F039)"

def _spill_root(ctx: Any) -> Optional[Path]:
    """spill 根目录(expanduser 后 Path);配置缺失 → None(调用方定错误码)。"""
    raw = _cfg(_cfg_holder(ctx), "storage.spill_dir", None)
    if raw is None or not str(raw).strip():
        return None
    return Path(str(raw)).expanduser()

def _session_id(ctx: Any) -> Optional[str]:
    """会话 id 获取:ctx.session_id 直挂 → ctx.session.{session_id|sid}。"""
    sid = getattr(ctx, "session_id", None)
    if sid:
        return str(sid)
    sess = getattr(ctx, "session", None)
    if sess is not None:
        for attr in ("session_id", "sid"):
            v = getattr(sess, attr, None)
            if v:
                return str(v)
    return None

def _max_per_file(ctx: Any) -> int:
    """单文件上限:storage.spill.max_per_file_bytes(默认 10485760,CFG §3.6)。"""
    v = _cfg(_cfg_holder(ctx), "storage.spill.max_per_file_bytes",
             DEFAULT_MAX_PER_FILE)
    try:
        return int(v)
    except (TypeError, ValueError):
        return DEFAULT_MAX_PER_FILE

def _max_per_session_bytes(ctx: Any) -> int:
    """会话 spill 区**总量**上限(字节):`storage.spill.max_per_session_mb`(默认 100MB)。

    R24 接线:该键此前**零读取者**(L-20 死配置)⇒ 文档承诺的"会话总量 100MB 闸"在
    实现里不存在,只有**单文件** 10MB 闸 ⇒ 同一会话可反复 spill 撑爆磁盘。
    """
    v = _cfg(_cfg_holder(ctx), "storage.spill.max_per_session_mb",
             DEFAULT_MAX_PER_SESSION_MB)
    try:
        mb = int(v)
    except (TypeError, ValueError):
        mb = DEFAULT_MAX_PER_SESSION_MB
    return max(0, mb) * 1024 * 1024

def _session_dir_bytes(d: Any) -> int:
    """会话 spill 目录内现有字节数(只读;不存在 → 0,不可读项跳过不放大)。"""
    total = 0
    try:
        entries = list(d.iterdir())
    except OSError:
        return 0
    for p in entries:
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total

def _redact(ctx: Any, text: str) -> str:
    """出口脱敏(INV-09):优先 ctx.redact(F016 单口);否则回落 config.redact。

    ctx.redact 为装配层注入的脱敏单口(已含 log.redact_enabled 开关语义);
    回落路径尊重 log.redact_enabled(默认 true),关则原文直写(配置层承诺的
    出口策略,见 SECURITY §6)。
    """
    fn = getattr(ctx, "redact", None)
    if callable(fn):
        try:
            return fn(text)
        except Exception:                       # noqa: BLE001 脱敏自身失败不吞事实
            log.warning("spill ctx.redact 异常,回落 config.redact")
    holder = _cfg_holder(ctx)
    if _cfg(holder, "log.redact_enabled", True) is False:
        return text                             # 显式关闭全出口脱敏
    try:
        from pyharness.config import redact as _module_redact
        return _module_redact(text)
    except Exception:                           # noqa: BLE001 兜底:原文直写
        log.warning("spill 脱敏不可用,原文落盘(检查 config.redact)")
        return text

def _bump_counter(ctx: Any, key: str, delta: int) -> None:
    """已读量入计数器(防循环烧预算):支持 dict / bump(key, n) / 属性三形态。

    ctx.counters 未接线时静默跳过(log debug)——读数不受影响,仅防循环闸
    少一个数据源(预算/轮数闸另有兜底)。
    """
    c = getattr(ctx, "counters", None)
    if c is None:
        return
    if isinstance(c, dict):                     # dict 形态(单测/装配可直挂)
        c[key] = c.get(key, 0) + delta
        return
    bump = getattr(c, "bump", None)
    if callable(bump):
        try:
            bump(key, delta)
            return
        except Exception:                       # noqa: BLE001 降级到属性形态
            pass
    try:                                        # 属性形态(UsageCounters 风格)
        setattr(c, key, int(getattr(c, key, 0)) + delta)
    except Exception:                           # noqa: BLE001 均不可用:忽略
        log.debug("spill 计数器形态不支持 bump,key=%s", key)

def _log_warn(ctx: Any, msg: str, *args: Any) -> None:
    """告警出口:ctx.log.warning 优先,缺失回落模块 logger(偏离 8)。"""
    clog = getattr(ctx, "log", None)
    if clog is not None and callable(getattr(clog, "warning", None)):
        try:
            clog.warning(msg, *args)
            return
        except Exception:                       # noqa: BLE001
            pass
    log.warning(msg, *args)

async def _maybe_await(r: Any) -> Any:
    """异步上下文里的 await-if-awaitable(executor._spill_put 同款处理)。"""
    import inspect
    if inspect.isawaitable(r):
        return await r
    return r
