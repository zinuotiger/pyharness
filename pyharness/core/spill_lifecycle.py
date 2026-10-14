"""Spill lifecycle: complete migrated implementation."""
from __future__ import annotations
from typing import Any, Optional
from pyharness.errors import PyHError, raise_code

from pyharness.core.spill_policy import SPILL_PATTERN, TOOL_NAME, _log_warn, _maybe_await, _session_id, _spill_root, log

async def detach(ctx: Any) -> None:
    """幂等摘除(DIS-SEAM §6.1 ⑤):摘读工具/摘定位器/摘订阅,文件不删。

    会话关闭(ctx.close)时调用;**文件保留**——留给 F060 归档/repair 在会话
    结束后仍可读的窗口,删除统一由 purge_session 在 session.finished 后执行。
    摘除失败仅本地日志告警,不阻断会话关闭(文件系统无副作用残留)。
    """
    storage = getattr(ctx, "storage", None)
    # ---- 1) 幂等闸:locator 自报未挂载即已摘;无 locator 面按挂载痕迹判
    if storage is not None:
        gate = getattr(storage, "_unmounted", None)
        if callable(gate):
            try:
                if await _maybe_await(gate("spill")):
                    return                        # 已摘除(幂等早退)
            except Exception:                     # noqa: BLE001 locator 异常:
                pass                              # 继续尽力摘除,不阻断
        else:
            services = getattr(storage, "services", None) or {}
            if not (hasattr(storage, "spill") or "spill" in services):
                return                            # 无 spill 挂载痕迹 → 已摘
    # ---- 2) 摘 LLM 可见读工具(TLB-802 = 已摘,静默幂等)
    tools = getattr(ctx, "tools", None)
    if tools is not None:
        unreg = getattr(tools, "unregister", None)
        if not callable(unreg):
            unreg = getattr(tools, "unregister_definition", None)
        if callable(unreg):
            try:
                await _maybe_await(unreg(TOOL_NAME))
            except PyHError as e:
                if e.code != "TLB-802":           # 已摘(重入/双摘)→ 静默
                    _log_warn(ctx, "spill detach unregister 失败 code=%s", e.code)
            except Exception as e:                # noqa: BLE001
                _log_warn(ctx, "spill detach unregister 异常: %s", e)
    # ---- 3) 摘定位器(ctx.storage.spill;真实 locator 走 unmount)
    if storage is not None:
        unmount = getattr(storage, "unmount", None)
        if callable(unmount):
            try:
                await _maybe_await(unmount("spill"))
            except Exception as e:                # noqa: BLE001
                _log_warn(ctx, "spill detach unmount 失败: %s", e)
        elif hasattr(storage, "spill"):           # 无 locator API:直接摘属性
            try:
                delattr(storage, "spill")
            except Exception as e:                # noqa: BLE001
                _log_warn(ctx, "spill detach 摘除属性失败: %s", e)
    # ---- 4) 摘 session.finished 清理订阅(按会话属主)
    _unsubscribe_purge(ctx)
    log.info("spill detached(文件保留,F060 读窗口)")

def _unsubscribe_purge(ctx: Any) -> None:
    """按会话属主摘除 purge 订阅(owner=f"spill:{sid}";幂等)。"""
    sid = _session_id(ctx)
    if not sid:
        return
    bus = getattr(ctx, "bus", None)
    drop = getattr(bus, "unsubscribe_all", None)
    if callable(drop):
        try:
            drop(f"spill:{sid}")
        except Exception as e:                    # noqa: BLE001
            _log_warn(ctx, "spill purge 订阅摘除失败: %s", e)

async def purge_session(session_id: str, ctx: Any = None) -> None:
    """清理策略主体(F039 随会话生命周期/I-3):删除该会话 spill 目录全部文件。

    先 containment 校验(只删 spill 根下本会话子目录,逃逸即 PERS-222 拒);
    目录不存在幂等;删除失败记日志+告警不阻断(F060 repair 兜底残留)。
    订阅 session.finished 后由本函数收口"spill 随会话生命周期清理"。
    """
    root = _spill_root(ctx)
    if root is None:
        raise_code("PERS-222", session=session_id,
                   advice="spill 根未配置,无法校验清理目标,已拒绝")
    root = root.resolve()
    target = (root / session_id).resolve()       # 归一后比对
    try:
        target.relative_to(root)                 # containment 单点(Windows
    except ValueError:                           # 大小写不敏感,偏离 4)
        raise_code("PERS-222", session=session_id,
                   advice="清理目标逃逸 spill 根,已拒绝")
    if target == root:                           # 目标即根(空/`.` session)→ 拒
        raise_code("PERS-222", session=session_id,
                   advice="清理目标逃逸 spill 根,已拒绝")
    try:
        if target.exists():
            for f in target.glob(SPILL_PATTERN):  # 逐文件删(可留审计计数)
                f.unlink(missing_ok=True)
            for f in target.glob(f"{SPILL_PATTERN}.tmp"):   # 残留 tmp(偏离 5)
                f.unlink(missing_ok=True)
            target.rmdir()                        # 空目录收口
        log.info("spill purge done session=%s dir=%s", session_id, target)
    except OSError as e:                          # 残留不致命(F060 兜底)
        _log_warn(ctx, "spill purge failed session=%s: %s", session_id, e)

def subscribe_purge(ctx: Any, session_id: Optional[str] = None) -> Optional[str]:
    """订阅本会话 session.finished → purge_session(返回 owner 标记,可摘除)。

    装配于 Provider.enter(生命周期 enter→announce→detach);bus 未接线/无
    subscribe 面 → 返回 None(清理留待装配层兜底,文件由 F060 repair 覆盖)。
    """
    sid = session_id or _session_id(ctx)
    if not sid:
        return None
    bus = getattr(ctx, "bus", None)
    subscribe = getattr(bus, "subscribe", None)
    if not callable(subscribe):
        return None
    owner = f"spill:{sid}"

    async def _on_finished(type_: str, payload: Any) -> None:  # noqa: ARG001
        """session.finished 回调:仅清本会话(总线跨会话共享,按 payload 过滤)。

        声明 async:总线 _dispatch 对协程函数 handler 会 await(同步函数返回
        协程则不会被等待,产生 never-awaited 告警)。
        """
        p_sid = getattr(payload, "session_id", None)
        if p_sid is None and isinstance(payload, dict):
            p_sid = payload.get("session_id")
        if p_sid is not None and str(p_sid) != sid:
            return                                # 其他会话的 finished:不动
        await purge_session(sid, ctx)             # 删除本会话 spill 目录

    try:
        bus.subscribe("session.finished", _on_finished, owner=owner)
    except Exception as e:                        # noqa: BLE001 订阅失败不致命
        _log_warn(ctx, "spill purge 订阅失败: %s", e)
        return None
    return owner
