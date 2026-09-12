"""pyharness/bus/event_bus.py — 订阅模型 + 事件总线 (specs/bus.py.md)

EventBus:单进程内唯一通信通道——精确/通配订阅(F002)、三模式分发
(sequential/waterfall/parallel)、per-sender FIFO 背压(emit_ordered,F005,
默认阈值 1000 在途,满=拒新不丢旧,dropped 计数可观测)、强同步事件恒
sequential、事件类型先注册才能 emit(EVT-102,与 pyharness.events.vocab
词表联动,见 _type_registered)。

边界(bus 与 events):总线只中转不落盘——payload 不做 pydantic 校验、不写
会话日志;落盘由日志订阅者(强同步订阅者)负责。错误全经 raise_code
(F019 唯一入口),禁裸 raise str;订阅者异常 EVT-103 单行结构化日志隔离,
不外抛、不中断其他订阅者;CancelledError 不吞,按 F025 取消协议上抛。
"""
from __future__ import annotations

import asyncio
import inspect
import itertools
import logging
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Optional

from pyharness.errors import raise_code, struct_error
from pyharness.events.vocab import SYNC_TYPES, is_registered

if TYPE_CHECKING:  # 仅类型标注用(总线不实例化 schema,只做存在性判定)
    from pydantic import BaseModel

log = logging.getLogger("pyharness.bus")


class _Sentinel:
    """模块级哨兵:repr 自解释,身份比较恒稳。"""

    __slots__ = ("_name",)

    def __init__(self, name: str) -> None:
        self._name = name

    def __repr__(self) -> str:
        return f"<{self._name}>"


# 瀑布流短路哨兵:handler 返回 STOP → 其后订阅不再执行(spec emit waterfall)
STOP = _Sentinel("STOP")
# 私有失败哨兵:handler/谓词异常已被 EVT-103 隔离并返回此哨兵,供统计识别
# (区别于 void handler 正常返回的 None——两者都不得计为 errored/delivered 错位)
_FAILED = _Sentinel("_FAILED")


def _wild_match(pattern: str, type_: str) -> bool:
    """通配匹配辅助:段级 `前缀.*` 精确前缀命中,禁正则(确定性,F002)。

    如 "tool.*" 命中 "tool.call" / "tool.call.extra",不命中 "toolbox.call"。
    """
    if not pattern.endswith(".*"):
        return False
    prefix = pattern[:-2]
    if not prefix:
        return False  # 裸 ".*" 非合法通配(事件名恒有点分段)
    return type_.startswith(prefix + ".")


def _needs_two_args(handler: Callable) -> bool:
    """handler 形如 (type_, payload) → True;仅 (payload) → False。

    无法自省(内置/部分对象)时按单参处理,由调用方保证签名契约。
    """
    try:
        sig = inspect.signature(handler)
    except (TypeError, ValueError):  # 内置函数等无签名
        return False
    n = 0
    for p in sig.parameters.values():
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD):
            n += 1
    return n >= 2


@dataclass
class Subscription:
    """一条订阅记录:精确/通配统一入列,owner 供卸载按属主精确摘除(F004)。

    when 谓词只读过滤(payload 谓词,禁止改写 payload);谓词抛异常 = 该订阅
    跳过并记 EVT-103;seq = 注册序,同命中按序执行。
    """

    pattern: str            # "user.message" 或 "tool.*"
    handler: Callable       # async/同步均可,签名 (type_, payload) 或 (payload)
    owner: str              # 插件/能力 id;unsubscribe_all 按此摘除
    when: Optional[Callable] = None   # payload 谓词,只读(F002)
    wildcard: bool = False
    seq: int = 0            # 注册序(总线内单调递增)


class EventBus:
    """事件总线(specs/bus.py.md EventBus 全量契约)。

    构造不失败;backpressure_limit 默认 1000 在途(F005,与 PARAMETER-ANCHOR
    锁定一致;可配上限通常来自 config plugins.backpressure_limit,由调用方
    读取后传入)。
    """

    # 总线内部瞬时广播类型(仅内存、无 payload 模型——EVENT-SCHEMA §8.1
    # 落盘矩阵:registry.updated 仅总线,不进 57 词表 EVENT_TYPES)。
    # 预置入 _types 使 Registry 的 F003 留痕 emit 不被自身 EVT-102 闸拦截。
    _INTERNAL_TYPES: tuple[str, ...] = ("registry.updated",)

    def __init__(self, backpressure_limit: int = 1000):
        self._by_type: dict[str, list[Subscription]] = {}   # 精确索引
        self._wild: list[Subscription] = []                 # 通配索引(tool.*)
        # 事件 schema 注册表(EVT-102 判定):register_type 写入 + 预置内部瞬时
        # 广播;其余 57 词表类型经 events.vocab 联动放行(见 _type_registered)
        self._types: dict[str, Optional[type]] = {t: None for t in self._INTERNAL_TYPES}
        self._queues: dict[str, deque] = {}                 # per-sender FIFO
        self.dropped: dict[str, int] = {}                   # 背压丢弃计数(可观测)
        self._inflight: set[str] = set()                    # 投递中 owner(F004 busy 判定)
        self.backpressure_limit = backpressure_limit        # F005 阈值(public)
        self._sub_seq = itertools.count()                   # 订阅注册序发号器
        self._draining: set[str] = set()                    # 正在排空队列的 sender
        self._qlocks: dict[str, asyncio.Lock] = {}          # per-sender 队列锁

    @property
    def _backpressure_limit(self) -> int:
        """背压阈值读别名(INV 骨架按私有名断言;真源为 backpressure_limit)。"""
        return self.backpressure_limit

    # ------------------------------------------------------------ 类型注册
    def register_type(self, type_: str, model: type) -> None:
        """预注册事件类型 schema;同类型二次注册 → EVT-102(只增不改 §3.8)。

        model 供未来 payload 强校验引用;总线只做存在性判定、不实例化校验
        (中转不落盘),故不强制 BaseModel 子类。
        """
        if type_ in self._types:
            raise_code("EVT-102", type_=type_, detail="类型重复注册,事件类型只增不改")
        self._types[type_] = model

    def _type_registered(self, type_: str) -> bool:
        """类型已注册判定:总线本地 _types ∪ events.vocab 词表联动。

        词表 57 核心类型(payload 模型随 events 包导入即注册)开箱即发;
        插件/能力命名空间类型须先 register_type(或经 events.register_event_type)。
        """
        return type_ in self._types or is_registered(type_)

    # ------------------------------------------------------------ 订阅管理
    def subscribe(self, pattern: str, handler: Callable, *,
                  owner: str = "anonymous", when: Optional[Callable] = None
                  ) -> Subscription:
        """注册订阅:精确名或 `*.` 通配(F002)。

        精确类型未注册允许先订阅(emit 侧仍按 EVT-102 拒);语法校验失败在
        调用侧自查(spec 异常表:handler 非 Callable 由调用方负责)。
        """
        sub = Subscription(pattern=pattern, handler=handler, owner=owner,
                           when=when, wildcard=pattern.endswith(".*"),
                           seq=next(self._sub_seq))
        if sub.wildcard:
            self._wild.append(sub)
        else:
            self._by_type.setdefault(pattern, []).append(sub)
        return sub

    def unsubscribe_all(self, owner: str) -> int:
        """按属主精确摘除全部订阅(精确+通配);返回实际摘除条数,幂等。"""
        removed = 0

        def _drop(lst: list[Subscription]) -> list[Subscription]:
            nonlocal removed
            keep: list[Subscription] = []
            for s in lst:
                if s.owner == owner:
                    removed += 1
                else:
                    keep.append(s)
            return keep

        self._by_type = {t: _drop(ss) for t, ss in self._by_type.items()}
        self._by_type = {t: ss for t, ss in self._by_type.items() if ss}
        self._wild = _drop(self._wild)
        return removed

    def _match(self, type_: str) -> list[Subscription]:
        """匹配:精确先、通配后,各按注册序(spec emit 顺序契约)。"""
        subs = list(self._by_type.get(type_, ()))
        for w in self._wild:
            if _wild_match(w.pattern, type_):
                subs.append(w)
        return subs

    # ------------------------------------------------------------ 分发入口
    def emit(self, type_: str, payload: dict, mode: str = "sequential"):
        """总线入口:类型校验(EVT-102)→ 返回分发协程(await 后得统计 dict)。

        偏离点(契约语义不变):类型先验在创建协程前同步完成——未注册类型
        立即抛 EVT-102(fail-fast),避免"调用了 emit 却不执行"的隐性失败;
        调用方 await 返回值即获得 spec 中 await emit(...) 的 dict 统计。
        """
        if not self._type_registered(type_):
            raise_code("EVT-102", type_=type_,
                       hint="事件类型未注册:先 register_type,或确认类型已在 events 词表注册")
        return self._dispatch(type_, payload, mode)

    async def _dispatch(self, type_: str, payload: dict,
                        mode: str = "sequential") -> dict:
        """三模式分发主体(注册序逐条/前值后入/只读投影 gather)。"""
        if type_ in SYNC_TYPES:             # 强同步事件恒 sequential(§3.7)
            mode = "sequential"
        subs = self._match(type_)
        stats: dict = {"delivered": 0, "errored": 0}
        if mode == "sequential":
            for s in subs:
                if s.when and not self._safe_when(s, payload):
                    continue
                r = await self._run(s, type_, payload)
                if r is _FAILED:
                    stats["errored"] += 1
                else:
                    stats["delivered"] += 1
        elif mode == "waterfall":           # 前返回值 = 后入参;STOP 短路
            acc = payload
            for s in subs:
                if s.when and not self._safe_when(s, acc):
                    continue
                r = await self._run(s, type_, acc)
                if r is _FAILED:
                    stats["errored"] += 1
                    continue
                stats["delivered"] += 1      # 成功执行即计(含 void/STOP 件)
                if r is STOP:
                    break
                if r is not None:
                    acc = r
            return {"result": acc, **stats}
        else:                               # parallel:只读投影 gather
            todo = [s for s in subs
                    if not (s.when and not self._safe_when(s, payload))]
            if todo:
                results = await asyncio.gather(
                    *(self._run(s, type_, payload) for s in todo),
                    return_exceptions=True)
                for r in results:
                    if r is _FAILED or isinstance(r, Exception):
                        stats["errored"] += 1
                    else:
                        stats["delivered"] += 1
        return stats

    def emit_sync(self, type_: str, payload: dict) -> dict:
        """同步投递口:仅供总线内部瞬时事件(如 registry.updated)由同步调用方
        (Registry.register/unregister 等)使用,保证 F003 留痕确定可见。

        订阅者须为同步函数(registry.updated 仅内存,订阅者即同步刷新器);
        异步订阅者:有事件循环时 create_task 调度,无循环时跳过并记日志。
        订阅者异常一律 EVT-103 隔离,不外抛。
        """
        if not self._type_registered(type_):
            raise_code("EVT-102", type_=type_, hint="emit_sync 仅限已注册(含总线内部瞬时)类型")
        stats: dict = {"delivered": 0, "errored": 0}
        for s in self._match(type_):
            if s.when and not self._safe_when(s, payload):
                continue
            self._inflight.add(s.owner)
            try:
                args: tuple = (type_, payload) if _needs_two_args(s.handler) else (payload,)
                fn = s.handler(*args)
                if inspect.isawaitable(fn):
                    try:
                        loop = asyncio.get_running_loop()
                    except RuntimeError:
                        log.warning("emit_sync 无事件循环,跳过异步订阅者 owner=%s pattern=%s",
                                    s.owner, s.pattern)
                        continue
                    task = loop.create_task(self._consume_task(fn, s, type_))
                    task.add_done_callback(lambda t: self._task_error(t, s, type_))
                stats["delivered"] += 1
            except Exception as exc:        # 订阅者崩溃 → EVT-103 隔离
                log.error("%s", struct_error("EVT-103", type_=type_, owner=s.owner,
                                             pattern=s.pattern,
                                             exc=f"{type(exc).__name__}: {exc}"))
                stats["errored"] += 1
            finally:
                self._inflight.discard(s.owner)
        return stats

    async def _consume_task(self, aw: Any, sub: Subscription, type_: str) -> None:
        """异步订阅者协程体(await 结果,异常由 _task_error 统一收口)。"""
        try:
            await aw
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("%s", struct_error("EVT-103", type_=type_, owner=sub.owner,
                                         pattern=sub.pattern,
                                         exc=f"{type(exc).__name__}: {exc}"))

    @staticmethod
    def _task_error(task: asyncio.Task, sub: Subscription, type_: str) -> None:
        """create_task 回调:补记未 await 到的异常(取消不记)。"""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.error("%s", struct_error("EVT-103", type_=type_, owner=sub.owner,
                                         pattern=sub.pattern,
                                         exc=f"{type(exc).__name__}: {exc}"))

    # ------------------------------------------------------------ 背压通道
    async def emit_ordered(self, sender: str, type_: str, payload: dict) -> dict:
        """同 sender 事件严格 FIFO 串行(F005);队列满=拒新不丢旧。

        在途 ≥ backpressure_limit → 丢弃计数 +1 并广播 bus.backpressure 瞬时
        事件(可观测,禁静默),返回 {"delivered":0,"errored":0,"dropped":True};
        队内旧件按序投递完。首个入队者兼任 drainer 逐件顺序分发;其余调用
        方入队即返回 {"queued":True}(事件由 drainer 保证 FIFO 送达)。
        """
        if not self._type_registered(type_):    # 前置类型校验:拒投脏件,队列不动
            raise_code("EVT-102", type_=type_, hint="emit_ordered 前置类型校验:先 register_type")
        lock = self._qlocks.setdefault(sender, asyncio.Lock())
        drop: Optional[int] = None
        drainer = False
        async with lock:
            q = self._queues.setdefault(sender, deque())
            if len(q) >= self.backpressure_limit:   # 满 = 拒新(不丢旧)
                self.dropped[sender] = self.dropped.get(sender, 0) + 1
                drop = self.dropped[sender]
            else:
                q.append((type_, payload))          # 入队 = 已承诺,绝不丢
                if sender not in self._draining:
                    self._draining.add(sender)
                    drainer = True
        if drop is not None:
            await self.emit("bus.backpressure",     # 背压瞬时事件(可观测)
                            {"sender": sender, "dropped": drop})
            return {"delivered": 0, "errored": 0, "dropped": True}
        if not drainer:
            return {"delivered": 0, "errored": 0, "queued": True}
        total: dict = {"delivered": 0, "errored": 0}
        try:
            while True:
                async with lock:                    # 退出判定与清标记同锁原子
                    if not self._queues[sender]:
                        self._draining.discard(sender)
                        return total
                    t, p = self._queues[sender].popleft()
                st = await self.emit(t, p, mode="sequential")  # 未完成不取下一件
                total["delivered"] += st["delivered"]
                total["errored"] += st["errored"]
        finally:                                    # 异常兜底:防队列悬挂
            self._draining.discard(sender)

    # ------------------------------------------------------------ 执行器
    def _safe_when(self, sub: Subscription, payload: dict) -> bool:
        """when 谓词安全求值:truthy 才投递;异常 → EVT-103 记日志并跳过(不扩散)。"""
        try:
            return bool(sub.when(payload))          # 谓词只读,禁止改写 payload
        except Exception as exc:
            log.error("%s", struct_error("EVT-103", owner=sub.owner, pattern=sub.pattern,
                                         exc=f"{type(exc).__name__}: {exc}"))
            return False

    async def _run(self, sub: Subscription, type_: str, payload: dict):
        """单订阅执行器:async 直接 await;同步 handler 经 to_thread 执行
        (防阻塞事件循环);异常 → EVT-103 单行结构化日志后返回 _FAILED;
        CancelledError 不吞,继续传播(F025)。"""
        self._inflight.add(sub.owner)               # F004:投递中不可 deactivate
        try:
            if asyncio.iscoroutinefunction(sub.handler):
                fn = (sub.handler(type_, payload) if _needs_two_args(sub.handler)
                      else sub.handler(payload))
                return await fn
            # 同步 handler:线程内调用,避免阻塞事件循环(spec to_thread)
            args: tuple = (type_, payload) if _needs_two_args(sub.handler) else (payload,)
            return await asyncio.to_thread(sub.handler, *args)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                    # 吞错不扩散,errored 可观测
            log.error("%s", struct_error("EVT-103", type_=type_, owner=sub.owner,
                                         pattern=sub.pattern,
                                         exc=f"{type(exc).__name__}: {exc}"))
            return _FAILED
        finally:
            self._inflight.discard(sub.owner)


_PENDING_EMITS: set[asyncio.Task] = set()
"""已排程的 emit 协程引用:防 task 被 GC,回调自动摘除。"""


async def _await_dispatch(coro: Any) -> dict:
    """排程协程消费:await 真正分发,异常兜底记录(不静默丢事件)。"""
    try:
        return await coro
    except asyncio.CancelledError:
        raise
    except Exception:                                # noqa: BLE001 分发兜底
        log.exception("scheduled event dispatch failed")
        return {"delivered": 0, "errored": 1}


def schedule_emit(bus: EventBus, type_: str, payload: dict, *,
                  mode: str = "sequential") -> Optional[asyncio.Task]:
    """同步调用方安全调度 emit:类型校验同步抛 EVT-102,分发协程排入运行中循环。

    emit() 返回协程,裸调用会丢弃;本助手保留 task 引用并统一收口异常,供
    config/approval/scope/guard 等 fire-and-forget 出口使用。无运行中事件循环
    时关闭协程并记警告(事件不投递但不泄漏协程)。
    """
    result = (bus.emit(type_, payload, mode=mode)
              if mode != "sequential" else bus.emit(type_, payload))
    if not asyncio.iscoroutine(result):
        return result
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        log.warning("schedule_emit: 无运行中事件循环,%s 未投递", type_)
        coro.close()
        return None
    task = loop.create_task(_await_dispatch(result))
    _PENDING_EMITS.add(task)
    task.add_done_callback(_PENDING_EMITS.discard)
    return task


__all__ = ["EventBus", "Subscription", "STOP", "schedule_emit"]
