"""ApprovalLifecycle operations, separated from facade assembly."""
from __future__ import annotations
import asyncio
import inspect
from typing import TYPE_CHECKING, Any, Optional
from pyharness.errors import raise_code
from pyharness.events.vocab import is_registered
from pyharness.core.approval_models import logger, _HUMAN_CHANNELS
if TYPE_CHECKING:  # 仅类型标注:executor 传入的 ToolCall 鸭子契约,运行期不依赖
    pass

class ApprovalLifecycle:
    """ApprovalLifecycle responsibility; state is owned by the composing facade."""

    def detach(self, ctx: Any = None) -> None:
        """摘订阅 + 取消全部等待(未决全置 denied)+ 清信任;幂等,随 ctx.close()。"""
        if self._detached:
            return
        self._detached = True
        self.cancel_all(reason="detach")
        if self._bus is not None:
            try:
                self._bus.unsubscribe_all(self._subscription_owner)
            except Exception as exc:             # noqa: BLE001 摘除失败不扩散
                logger.warning("approval 摘订阅失败: %s", exc)
        self._trust.clear()                      # 会话级信任随会话关闭失效
        self._enabled = False
        self._detached = True
        logger.info("approval detach: 未决全置 denied,订阅已摘(幂等)")

    async def aclose(self):
        self.detach()
        # Registration may be inside append/pause when detach is called.
        async with self._transition_lock:
            self.cancel_all(reason="close")
        while self._tasks:
            tasks = list(self._tasks)
            await asyncio.gather(*(asyncio.shield(t) for t in tasks), return_exceptions=True)
            # gather(done tasks) can finish synchronously on Python 3.13.
            # Do not spin waiting for discard callbacks to get a loop turn.
            self._tasks.difference_update(t for t in tasks if t.done())
        async with self._transition_lock:
            if self._suspended:
                await self._resume_queue_or_record_async(self._suspend_log)
                self._suspended = False
            self._pending.clear()
            self._merge.clear()

    def pending_count(self) -> int:
        """未决请求数(队列暂停/自检用;0 且曾挂起 → queue.resumed 由裁决路径落)。"""
        return len(self._pending)

    def owns_initializing(self, approval_id: int, *, sid: Optional[str] = None) -> bool:
        """Route a published requested event while registration still holds the lock.

        The async decision revalidates pending under that same lock. This is only
        routing evidence, never authorization and never a replay acceptance.
        """
        if self._detached or self._initializing is None:
            return False
        log, before_seq = self._initializing
        if approval_id <= before_seq:
            return False
        if sid is not None and getattr(log, "sid", None) != sid:
            return False
        get = getattr(log, "get", None)
        event = get(approval_id) if callable(get) else None
        return event is not None and event.type == "approval.requested"

    def trust_count(self) -> int:
        """信任名单当前条数(审计/自检只读)。"""
        return len(self._trust)

    @staticmethod
    def _require_human(by: Any) -> None:
        """裁决者身份校验(假冒审批防线 S-2 白名单,P2):仅 cli/web/acp/desktop
        通道名或其 ":" 前缀合法,任意其它自报身份(含 llm:/tool:/plugin:/hacker)→
        APR-503。"""
        if not isinstance(by, str) or not by:
            raise_code("APR-503", why="裁决者身份缺失:by 由框架按通道打,不可省略")
        ok = any(by == c or by.startswith(c + ":") for c in _HUMAN_CHANNELS)
        if not ok:
            raise_code("APR-503", by=by,
                       why="裁决者身份非法:仅 cli/web/acp/desktop 人类通道可裁决"
                           "(LLM/工具/插件及任意自报身份无权冒充人类批准)")

    def _cfg_int(self, path: tuple[str, ...], default: int) -> int:
        """config 只读取数(path 逐层 getattr,缺省回落默认;禁热更键)。"""
        node: Any = self._config
        for key in path:
            node = getattr(node, key, None) if node is not None else None
            if node is None:
                return default
        try:
            return int(node)
        except (TypeError, ValueError):
            return default

    def _own(self, coro):
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(self._task_error)
        return task

    def _spawn(self, coro: Any) -> bool:
        """异步协程排程(fire-and-forget):失败只记日志;任务登记防 GC。"""
        if not inspect.isawaitable(coro):
            coro = coro()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("approval 无运行中事件循环,协程未投递: %s",
                           getattr(coro, "__name__", coro))
            return False
        task = loop.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(self._task_error)
        return True

    @staticmethod
    def _task_error(task: Any) -> None:
        """done 回调:吞异常日志(未预期异常不静默;取消不记)。"""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning("approval 后台任务异常: %s: %s",
                           type(exc).__name__, exc)

    async def _soft_append(self, sess: Any, type_: str, payload: dict) -> None:
        """队列联动事件异步落盘(尽力而为;失败只记日志不阻断审批主路径)。"""
        if sess is None:
            return
        try:
            await sess.append(type_, payload, actor="system", sync=False)
        except Exception as exc:                 # noqa: BLE001 EVT-104(会话关闭)等
            logger.warning("approval %s 落盘失败(尽力而为): %s", type_, exc)

    def _record(self, sess: Any, type_: str, payload: dict) -> None:
        """同步上下文事件落盘(scope._record 同款:async append → create_task 投递)。"""
        if sess is None:
            return
        try:
            r = sess.append(type_, payload, actor="system")
        except Exception as exc:                 # noqa: BLE001 事件失败不阻断
            logger.warning("approval append %s 失败: %s", type_, exc)
            return
        if inspect.isawaitable(r):
            try:
                self._spawn(r)
            except Exception:                    # noqa: BLE001
                logger.warning("approval %s 投递失败(尽力而为)", type_)

    def _emit_trust(self, type_: str, payload: dict) -> None:
        """信任留痕(approval.trust_* 词表外,尽力而为出口;见偏离 5)。

        词表注册后自动生效;未注册时(现 57 词表锁定)只 debug 日志不广播,
        避免每次 EVT-102 error 噪音。
        """
        if not is_registered(type_):
            logger.debug("approval %s 未入事件词表,留痕降级(仅日志): %s",
                         type_, payload)
            return
        bus = self._bus
        if bus is None:
            logger.debug("approval 信任事件未广播(未接线 bus): %s %s", type_, payload)
            return
        from pyharness.bus import schedule_emit
        try:
            schedule_emit(bus, type_, payload)
        except Exception as exc:                 # noqa: BLE001 广播失败尽力而为
            logger.debug("approval %s 广播降级: %s", type_, exc)

