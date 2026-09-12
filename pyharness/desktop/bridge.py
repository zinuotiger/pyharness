"""pywebview JavaScript bridge."""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
import uuid
from typing import Any

from pyharness.errors import PyHError

from .constants import BRIDGE_TIMEOUT_S
from .projection import _jsonable

log = logging.getLogger("pyharness.desktop.bridge")

class DesktopBridge:
    """pywebview js_api:webview 线程方法 → 引擎事件循环跨线程投递(run_coroutine_threadsafe)。

    暴露 window.submit(text)/approve(aid,decision)/replay_frame(sid,from_seq)/
    list_sessions();方法薄壳无业务,返回 JSON 字符串(错误 = code 体 ADR-011)。桥无特权:
    裁决仍过 guard/预算引擎链(单调性高于人类意志)。_pending_calls 登记在途调用供审计。
    """

    def __init__(self, app: DesktopApp) -> None:
        self.app = app
        self._pending_calls: dict[str, float] = {}      # call_id → 发起时刻(在途登记)

    def _run(self, coro: Any) -> str:
        """跨线程投递并阻塞取结果(≤60s);PyHError → code 体;超时 → BUSY(不悬挂)。"""
        app = self.app
        loop = app.loop
        if loop is None or not loop.is_running():
            if inspect.iscoroutine(coro):
                coro.close()                # 未投递协程显式关闭,防 'never awaited' 告警
            return json.dumps({"code": "BUSY",
                               "advice": "引擎服务未就绪(loop 未运行),稍后再试"},
                              ensure_ascii=False)
        call_id = uuid.uuid4().hex[:8]
        self._pending_calls[call_id] = time.monotonic()
        fut = None
        try:
            fut = asyncio.run_coroutine_threadsafe(coro, loop)
            data = fut.result(timeout=BRIDGE_TIMEOUT_S)
            return json.dumps(_jsonable(data), ensure_ascii=False)
        except PyHError as e:                           # 引擎错:远端只回 code+advice
            advice = (e.ctx.get("advice") or e.ctx.get("hint") or e.spec.advice)
            return json.dumps({"code": e.code, "advice": str(advice)},
                              ensure_ascii=False)
        except (asyncio.TimeoutError, TimeoutError):    # 引擎忙:本地错误体(提示稍后再试)
            if fut is not None:
                fut.cancel()                            # 不留下继续消费/重复执行的协程
            return json.dumps({"code": "BUSY", "advice": "引擎繁忙,稍后再试"},
                              ensure_ascii=False)
        except Exception as exc:                        # noqa: BLE001 未预期兜底
            log.error("bridge 调用未预期异常", exc_info=True)
            return json.dumps({"code": "CYC-999",
                               "advice": f"{type(exc).__name__}: 引擎内部错误"},
                              ensure_ascii=False)
        finally:
            self._pending_calls.pop(call_id, None)

    # ---------------- js_api 方法(薄壳,无业务;前端 window.<name> 直调)
    def submit(self, sid: str, text: str) -> str:
        """前端回车/发送按钮 → create_message(门面写,无特权)。"""
        return self._run(self.app.create_message(str(sid), {"text": str(text)}))

    def approve(self, aid: Any, decision: str) -> str:
        """审批弹窗按钮(approve/deny;桥无特权:裁决仍过引擎链)。"""
        try:
            aid_int = int(aid)
        except (TypeError, ValueError):
            return json.dumps({"code": "EVT-100", "advice": "aid 须为数字"},
                              ensure_ascii=False)
        return self._run(self.app.decide_approval(aid_int, {"decision": str(decision)}))

    def replay_frame(self, sid: str, from_seq: Any) -> str:
        """轨迹面板逐帧回放取帧(session_timeline 增量续拉)。"""
        try:
            fseq = int(from_seq)
        except (TypeError, ValueError):
            fseq = 0
        return self._run(self.app.session_timeline(str(sid), after_seq=fseq))

    def list_sessions(self) -> str:
        """会话列表(前端启动拉取)。"""
        return self._run(self.app.list_sessions())

    def on_close(self) -> None:
        """窗口关闭事件:优雅停服入口(与 window.events.closing 双保险,幂等)。"""
        self.app.request_shutdown()


