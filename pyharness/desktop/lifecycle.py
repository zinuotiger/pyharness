"""DesktopLifecycle operations, separated from facade assembly."""
from __future__ import annotations
from typing import TYPE_CHECKING
import asyncio
import threading
from .sessions import _await
from pyharness.desktop.host_policy import log
if TYPE_CHECKING:  # 仅注解引用(app↔bridge 环形依赖,运行时不需要)
    pass

class DesktopLifecycle:
    """DesktopLifecycle responsibility; state is owned by the composing facade."""

    def shutdown_gracefully(self) -> None:
        """窗口关闭 = 优雅停服(F065 边界;幂等:双击关闭只走一次)。

        序:stopping 置位 → 停事件 fan-out → hub 关闭(SSE 客户端收 close)→ 兜底 flush
        (强同步事件已随 append 落盘)→ server.should_exit=True 停 uvicorn → webview.destroy。
        幂等判据用 _graceful_done(伪码 `if stopping.is_set(): return` 会在 closing 事件
        先置位时让首次 graceful 空转——伪码缺陷,以行为契约修正,见模块 docstring)。
        """
        if self._graceful_done:
            return
        self._graceful_done = True
        self.stopping.set()
        loop = self.loop
        if loop is not None and loop.is_running():
            try:
                fut = asyncio.run_coroutine_threadsafe(self._shutdown_async(), loop)
                fut.result(timeout=15)
            except Exception:                       # noqa: BLE001
                log.warning("desktop async shutdown failed", exc_info=True)
        else:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(self._shutdown_async())
            else:
                # 已处于另一个事件循环(通常仅启动失败路径):用短线程隔离执行。
                errors: list[BaseException] = []

                def _worker() -> None:
                    try:
                        asyncio.run(self._shutdown_async())
                    except BaseException as exc:    # noqa: BLE001
                        errors.append(exc)

                t = threading.Thread(target=_worker, name="desktop-shutdown", daemon=True)
                t.start()
                t.join(timeout=15)
                if errors:
                    log.warning("desktop shutdown worker failed", exc_info=errors[0])
        self._destroy_webview()

    async def _shutdown_async(self) -> None:
        """在 uvicorn 事件循环内完成 fan-out 摘除、刷盘和服务器退出信号。"""
        bus = getattr(self.ctx, "bus", None)
        if bus is not None and self._sub:
            try:
                bus.unsubscribe_all("desktop")      # 停事件 fan-out
            except Exception:                       # noqa: BLE001
                log.warning("desktop shutdown unsubscribe failed", exc_info=True)
        self._sub = None
        self.hub.close_all()                        # SSE 客户端收 close,前端可重开
        errors = []
        try: await self._flush_persistence_async()
        except BaseException as exc: errors.append(exc)
        for service in self._service_registry.all():
            try:
                await service.shutdown()
            except BaseException as exc:
                errors.append(exc)
        if self.server is not None:
            try:
                self.server.should_exit = True      # uvicorn 线程自然退出
            except Exception:                       # noqa: BLE001
                log.warning("desktop server stop signal failed", exc_info=True)
        if errors: raise BaseExceptionGroup('desktop cleanup failed', errors)

    async def _flush_persistence_async(self) -> None:
        """异步收尾:等待 flush 完成后才允许调用方关闭 store。"""
        persist = getattr(self.ctx, "persistence", None)
        if persist is not None:
            flush_sync = getattr(persist, "flush_sync", None)
            fn = flush_sync if callable(flush_sync) else getattr(persist, "flush", None)
            if fn is not None:
                try:
                    await _await(fn())
                except Exception:                       # noqa: BLE001 兜底失败不阻断退出
                    log.warning("desktop 收尾 flush 失败", exc_info=True)
        # This helper flushes only. ApplicationService owns writer shutdown.
        surface = getattr(self, '_surface', None)
        for store in list(getattr(surface, '_stores', {}).values()):
            await _await(store.flush())

    @staticmethod
    def _destroy_webview() -> None:
        """webview.destroy 兜底(已销毁则忽略,本地异常不跨边界阻止进程退出)。"""
        try:
            from .launcher import _load_webview
            wv = _load_webview()
            if wv is not None:
                wv.destroy()
        except Exception:                           # noqa: BLE001
            log.debug("webview destroy skipped", exc_info=True)

