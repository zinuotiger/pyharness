"""pyharness/core/approval.py — 人类审批服务:F015 审批通道核心 (specs/approval.py.md 契约;阶段 1 模块)

功能编号:F015(人类审批核心)· 联动 F014(guard 重入)/F043(队列暂停)/F064(headless)/
F066(ACP approve);经 ctx.approval 注入,裁决按 approval_id=请求事件 seq 配对。

权威口径:specs/approval.py.md(编码契约)、EVENT-SCHEMA §3.4(approval.* 事件字段级)、
PARAMETER-ANCHOR(审批 TTL=120s,超时=denied 安全默认)、ERR.md §2.6(APR-5xx);
冲突以 PRD-Core §5.2 F015 / §6.7 为准。

职责一句话:danger≥high 调用的人类裁决服务——请求摘要化 → 人类裁决
(granted/denied/timeout=denied 安全默认,TTL 默认 120s)→ 事件强同步留痕;
granted 不等于放行(executor 重入 guard 链);60s 同工具同参合并防轰炸;
会话级信任名单(默认关、仅交互、headless 永不生效);APR-501/502/503 错误域。

核心语义:
1. 裁决流程:request(executor 关 2.5 唯一调用方)→ 强同步 approval.requested
   (approval_id=事件 seq,args_summary 摘要化)→ 等人类裁决(approve/deny 入口)/
   TTL 超时 → 结果事件强同步 → 返回 granted/denied/timeout;executor 对非 granted
   一律不执行(本模块无执行权)。
2. 安全默认:headless/无通道 → APR-501 直接拒(零事件零等待);TTL 到点无人 → timeout
   + approval.timeout(by=system);取消/会话关闭 → cancel_all 全置 denied(无悬挂 Future)。
3. 60s 合并防轰炸(R13):同工具同参数(规范化指纹)窗口内合并为一条 approval.requested,
   每个等待者各自配对裁决结果(granted 后各自独立重入 guard 链)。
4. 防重放(APR-503):approval_id 一次性消费;同一 id 二次裁决 → system.error/抛错忽略,
   不重复执行。
5. 事件强同步:approval.requested/granted/denied/timeout 全 sync=True 落盘(崩溃不丢);
   by 由框架按通道打(cli:<user>/web:<会话>/acp:<client_id>),LLM/工具/插件无权自报
   (假冒审批结构防线 S-2)。
6. 队列联动(F043):首请挂起(queue.suspended),末决恢复(queue.resumed),重复挂起合并。

依赖方向(单向,禁止反向):errors.raise_code(APR-5xx)← session(注入 log,强同步
append)← config(TTL/合并窗只读)← bus(裁决事件订阅 + queue 联动广播)。不依赖
guard 链/executor(重入由 executor 编排,本模块不 import)。

偏离说明(相对 spec 伪码;契约=spec,以下为与既有实现冲突处的取舍,均列理由):
1. approval.requested 载荷不含 channel/call_id:events/payload.py 的
   ApprovalRequestedPayload 为 extra="forbid" 且只有 tool/args_summary/ttl_ms/risk
   四字段(74 类型已锁定,实测超字段 append → EVT-100)→ channel/call_id 改经
   Envelope.trace 携带(信封 trace 为自由 dict 不触发载荷校验,tools_guard 偏离 1
   同款先例);risk 恒 "high"(guard 层只放行 high 进审批,critical 已转 reject)。
2. headless 分支以"异常表 + ERR.md + DIS-SEAM §6.2 G2"为准:request() 抛 APR-501
   (raise_code,零事件零等待),不用伪码 `return self._deny_no_channel(call)` 返回
   denied——伪码与异常表(APR-501 PyHError 行)冲突,异常表为规范面;且 approval.denied
   无对应 approval.requested 即孤儿裁决事件(EVENT-SCHEMA §3.4.5 锚点语义非法),
   不落孤儿事件。headless 拒绝审计面由 guard 层 guard.rejected(GRD-401,policy_ref=
   APR-501)负责(tools_guard 偏离 3 同口径)。_deny_no_channel 保留为内部实现,
   只负责 raise(签名标注 -> str 但恒不返回,见 spec 函数速览表同名列)。
3. 裁决完成单点 = provider 拥有的 completion task:强同步裁决事件 → 必要队列
   恢复确认 → pending 移除与 waiter 完成。总线消费者仅观察本地裁决，不拥有
   状态迁移。approve_async/deny_async 等待整个完成边界;同步入口仅提交命令。
4. queue.suspended/resumed 已入 57 词表(可落盘)→ 经注入 session 落盘,
   reason 取 spec 伪码字面量 "approval"(EVENT-SCHEMA §3.5.3 示例值 approval-pending
   仅为示例,冲突以伪码为准);重复挂起合并(首请挂起、末决恢复)。队列转换失败
   必须上抛并拒绝工具执行，不能以仅写事件降级为成功。
5. approval.trust_*(trust_set/trust_added/trust_hit/trust_cleared)不在 57 词表
   (实测 session.append → EVT-102;词表扩展属 events 模块后续阶段门)→ 信任留痕经
   bus/日志尽力而为(scope.py 偏离 4 / tools_guard 偏离 1 同款先例),测试只断言
   信任行为(跳过再次询问/清除计数)不断言事件落盘。
6. "全会话共享同一对象"的多会话并发装配(跨会话同 seq 碰撞路由)属阶段 3 装配层
   职责:本模块按"一会话一装配"使用,请求在创建时记录自属 session log 与 sid,
   approve/deny/timeout 事件恒落请求所属会话;测试全为单会话。on_verdict 收到
   异会话信封(Envelope.session_id 与本请求 sid 不符)时按未知裁决忽略(APR-503)。
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from pyharness.core.channel import (default_channel_of, require_channel)
from pyharness.errors import raise_code
from pyharness.events.vocab import is_registered  # 词表注册判定(trust_* 未入 77 词表,见偏离 5)

from pyharness.core.approval_models import (logger, CHANNELS, VERDICTS, TERMINAL_STATES, _OUTCOME_EVENTS, _HUMAN_CHANNELS, DEFAULT_TRUST_MAX, _TRUST_SET, _TRUST_ADDED, _TRUST_HIT, _TRUST_CLEARED, _danger_of, _norm_value, canonical_json, ApprovalRequest, TrustEntry)

if TYPE_CHECKING:  # 仅类型标注:executor 传入的 ToolCall 鸭子契约,运行期不依赖
    pass


# ====================================================================== 常量
# 交互裁决通道(外壳装配注入:交互 CLI/Web/ACP);headless/管道/后台 job 无通道

# 三结果字面量(request 返回值/终态;超时=denied 安全默认但结果字面量仍为 timeout)

# 审批结果 → 结果事件名(词表 §3.4.5 三结果同构)

# 人类裁决通道白名单(假冒审批结构防线 S-2 收紧,P2):仅四通道可裁决——通道名
# 本身("desktop")或其 ":" 前缀("cli:alice")视为合法;任意其它自报身份拒
# (旧实现只拒 llm:/tool:/plugin: 黑名单,`by="hacker"` 冒充人类可过)。

# 会话级信任名单容量上限(≤N 条 FIFO 淘汰;N 可经构造参数覆盖)

# 信任留痕事件名(词表外,尽力而为出口,见偏离 5)


# ---------------------------------------------------------------- 数据结构


# ================================================================== 服务类
from pyharness.core.approval_decisions import ApprovalDecisions
from pyharness.core.approval_binding import ApprovalBinding
from pyharness.core.approval_lifecycle import ApprovalLifecycle

class ApprovalProvider(ApprovalDecisions, ApprovalBinding, ApprovalLifecycle):
    """人类审批服务(脊柱子系统;DIS-SEAM §6.2 seam B)。

    构造期注入装配(偏离 3/6 同款注入式先例):session = 默认会话日志(事件落点),
    bus = EventBus(裁决事件订阅 + queue 联动),config = Settings(TTL/合并窗只读);
    channel/headless = 装配上下文缺省通道(外壳可在 ctx 上注入显式 channel/headless,
    request 时优先)。全会话共享同对象时按请求记录路由会话(见偏离 6)。

    状态:_pending(approval_id→lead)、_merge((sid,指纹)→批,60s 窗口)、_trust(指纹→
    信任记录)、_ttl_ms(默认 120_000)、_merge_window_ms(60_000)、_enabled(信任
    名单开关,默认 False,仅交互可开)、_suspended(队列挂起水位,重复挂起合并)。
    """

    def __init__(self, *, session: Any = None, bus: Any = None,
                 config: Any = None, channel: Optional[str] = None,
                 headless: bool = False, trust_max: int = DEFAULT_TRUST_MAX,
                 queue_getter: Any = None) -> None:
        self._session: Any = session            # 默认会话日志(可 None;未接线事件降级)
        self._bus: Any = bus                    # EventBus(可 None = 纯内存模式)
        self._config: Any = config              # Settings(只读 TTL/合并窗)
        # 队列联动(R12-3):**惰性取值**而非构造期绑定 —— provider 常先于队列构造
        # (装配序不定),取值函数保证"用的时候才找队列"。给定时,本 provider 只**驱动**
        # `queue.pause/resume`(它俩是 queue.suspended/resumed 事件的唯一写者),
        # 不再自行 append,避免双写。
        self._queue_getter: Any = queue_getter
        self._queue_driven: bool = False        # 本次挂起是否由队列驱动(决定谁来恢复)
        self._paused_queue: Any = None          # 恢复实际暂停过的队列，禁止重新取错对象
        self._transition_lock = asyncio.Lock()  # 仅本 provider 的登记/队列转换边界
        self._initializing: Any = None          # 当前 append 的日志与起始 seq，仅供路由
        self._channel: Optional[str] = default_channel_of(channel)
        """本 provider 服务的**交互外壳**归一化名(N5)。

        ``"acp:<id>"`` → ``"acp"``;**非法/未知/None** → ``None``。

        **纪律(N5 修复点)**:这是**provider 级缺省**,用于 ``user_choice`` 与信任
        名单判定,**不得**作为"ctx 未声明通道"时的回退源 —— 那正是修复前的缺陷
        (缺失通道被硬编码 ``"desktop"`` 劫持成人类通道,与治理层 fail-closed 矛盾)。
        ctx 的声明是唯一权威;缺失即 fail-closed(见 ``_ensure_channel``)。
        """
        self._headless: bool = bool(headless)   # 缺省 headless 标志(装配上下文)
        self._pending: dict[int, ApprovalRequest] = {}
        self._merge: dict[tuple[str, str], list[ApprovalRequest]] = {}
        self._trust: dict[str, TrustEntry] = {}
        self._trust_max: int = int(trust_max or DEFAULT_TRUST_MAX)
        self._enabled: bool = False             # 信任名单默认关(SECURITY §5.4)
        self._suspended: bool = False           # 队列挂起水位(F043;重复挂起合并)
        self._suspend_log: Any = None           # 挂起事件落点(恢复事件同落点)
        self._detached: bool = False            # detach 幂等标记
        self._subscription_owner = f'approval:{id(self)}'
        self._tasks: set = set()                # fire-and-forget 任务登记(防 GC)
        self._reviewer = None  # trusted assembly only; never provided by tool arguments
        self._grant_slots: dict[str, str] = {}  # call_id → granted 绑定指纹(executor
        # 重入校验用;call_id 每次调用唯一,单 slot 无生命周期问题)
        self._ref_slots: dict[str, int] = {}    # call_id → approval identity(S4-P1-2:
        # = 该次 approval.requested 的 seq(batch lead);结算后仍可查,供治理层把
        # D2.approval_ref 关联到真实 approval lifecycle——不新造 identity)
        # 裁决事件订阅:approval.granted/denied/timeout → on_verdict(DIS-SEAM §6.2
        # Definition.subscriptions 同款);owner="approval" 供 detach 摘除。
        if self._bus is not None:
            for t in ("approval.granted", "approval.denied", "approval.timeout"):
                try:
                    self._bus.subscribe(t, self.on_verdict, owner=self._subscription_owner)
                except Exception as exc:         # noqa: BLE001 订阅失败不阻断构造
                    logger.warning("approval 订阅 %s 失败: %s", t, exc)
        # TTL / 合并窗配置编译(config 键面 security.approval.ttl_ms / merge_window_s)
        self._ttl_ms: int = self._cfg_int(
            ("security", "approval", "ttl_ms"), 120_000)          # 120s 超时=denied
        self._merge_window_ms: int = self._cfg_int(
            ("security", "approval", "merge_window_s"), 60) * 1000  # 60s 窗口

    # ======================================================== 审批主入口
    async def request(self, call: Any, args_summary: str, ctx: Any, *,
                      ttl_ms: Optional[int] = None,
                      binding: Optional[str] = None) -> str:
        """审批主入口(F015;executor 关 2.5 唯一调用方)→ granted/denied/timeout。

        binding:executor 传入的"授权↔执行"绑定指纹(本轮参数序列化),granted
        时按 call_id 记录,供 executor 重入 guard 得到 approval 决策时校验一致
        (批准针对同参数才放行);信任命中路径同样记录。None = 未绑定(旧调用方)。

        流程:通道检查(headless → APR-501 直接拒)→ 信任名单命中(仅交互+开)返回
        granted → 60s 合并或新建批(强同步 approval.requested,approval_id=事件 seq)
        → 等裁决/超时 → 返回结果字面量。denied/timeout 由 executor 解读为不执行,
        本模块无执行权;granted 后 executor 仍重入 guard 链(单调性高于人类意志)。
        """
        ch = self._ensure_channel(ctx)
        if ch is None:
            return self._deny_no_channel(call)   # 恒 raise APR-501(见偏离 2)
        sess = self._log_of(ctx) or self._session
        if sess is None:
            raise_code("CYC-999", module="approval",
                       hint="会话日志未接线(ctx.session=None),审批请求被拒")
        sid = self._sid_of(ctx, sess)
        args = getattr(call, "args", None)
        if not isinstance(args, dict):
            args = getattr(call, "raw_args", {}) or {}
        fp = self._fingerprint(call.name, args)
        ttl = self._ttl_ms if ttl_ms is None else int(ttl_ms)
        # 信任名单命中:本会话内"同工具同参已批准" → 跳过再次询问(仅交互+开)
        if self._trust_hit(fp, sid, ch):
            if binding:                             # 信任命中也记录绑定(重入校验面)
                cid = getattr(call, "call_id", "") or ""
                if cid:
                    self._grant_slots[cid] = binding
            return "granted"                     # executor 仍重入 guard 链
        req = None
        registered = []
        try:
            async with self._transition_lock:
                if self._detached:
                    raise_code("APR-503", why="审批服务已关闭")
                req = await self._register(
                    call, args_summary, ch, ttl, sid, sess, fp, binding, registered,
                    getattr(ctx, 'task_id', None))
            runtime = getattr(ctx, 'platform_runtime', None)
            if self._reviewer is not None and runtime is not None and req.approval_id is not None:
                import copy
                review = {'tool': call.name, 'args': copy.deepcopy(args), 'call_id': req.call_id,
                    'context': [e.payload.get('content','') for e in sess.events_after(0) if e.type=='user.message'][-1:],
                    'scope': {'mode': runtime.definition.sandbox_profile.mode,
                        'allowed_tools': list(runtime.definition.allowed_tools),
                        'session_id': sid, 'task_id': str(getattr(ctx,'task_id','') or ''),
                        'network': runtime.definition.sandbox_profile.network}}
                self._own(self._review_request(req, review))
            return await self._wait_any(req)
        except BaseException:
            # Registration can fail/cancel while append or pause is suspended.
            # Only clean up a request this invocation actually registered.
            if req is None and registered:
                req = registered[0]
            if req is not None:
                try:
                    await self._cancel_request(req)
                except Exception:
                    logger.warning("approval cleanup failed; preserving primary error", exc_info=True)
            raise

    async def _register(self, call, args_summary, ch, ttl, sid, sess, fp, binding, registered, task_id=None):
        """Called with the provider transition lock, through pause acknowledgement."""
        # 60s 合并防轰炸(R13):同指纹且批未终态且在窗口内 → 挂批,不新增请求事件。
        # **键含 sid**(F-28):provider 按 spine 共享,子会话与父会话的指纹可相同,
        # 若只按指纹合并,子请求会挂进父批并被父的裁决唤醒(跨会话授权污染)。
        # 与 _trust 的 `_trust_hit(fp, sid, ch)` 口径对齐——信任名单本就按会话判。
        batch = self._merge.get((sid, fp))
        if (batch and not batch[0].is_terminal
                and time.monotonic() - batch[0].batch_mono
                < self._merge_window_ms / 1000.0):
            req = self._new_waiter(fp, call, args_summary, ch, ttl, sid=sid,
                                   sess=sess, binding=binding)
            batch.append(req)                    # 挂到既有批,等同一裁决
            req.batch = batch
            registered.append(req)
            return req
        # 开新批:强同步 approval.requested(approval_id = append 返回 seq)
        payload = {"tool": call.name, "args_summary": args_summary,
                   "ttl_ms": ttl, "risk": "high"}
        # Exact execution input is presented without the usual summary truncation.
        # Secret redaction remains mandatory; reviewer receives a separate copy.
        runtime = getattr(getattr(self, '_platform_runtime', None), 'definition', None)
        if runtime is not None:
            from pyharness.config import redact
            raw = json.dumps(getattr(call,'args',None) or getattr(call,'raw_args',{}) or {}, ensure_ascii=False)
            shown = redact(raw)
            payload['details'] = {'arguments': shown, 'redacted': shown != raw,
                'execution': runtime.sandbox_profile.mode,
                'reason': '工具操作需要确认；批准后仍检查原有工具、文件与账户权限'}
            arguments=getattr(call,'args',None) or getattr(call,'raw_args',{}) or {}
            payload['details']['files']=[redact(str(arguments['path']))] if 'path' in arguments else []
            if call.name in ('workspace.export_artifact','workspace.apply_patch'):
                item=await self._platform_runtime.platform.artifact(arguments['artifact_id'])
                payload['details']['files']=[redact(item['filename'])]
                payload['details']['execution']='账户产物存储（服务端）'
        trace: dict = {"channel": ch}
        if getattr(call, "call_id", None):
            trace["call_id"] = call.call_id
        if getattr(call, "parent_seq", None):
            trace["parent_seq"] = call.parent_seq
        stats = getattr(sess, "stats", None)
        self._initializing = (sess, stats()["seq"]) if callable(stats) else None
        try:
            env = await sess.append("approval.requested", payload,
                                    actor="tool", sync=True, trace=trace,
                                    **({'task_id': str(task_id)} if task_id else {}))
        finally:
            self._initializing = None
        req = self._new_waiter(fp, call, args_summary, ch, ttl, sid=sid,
                               sess=sess, binding=binding)
        req.approval_id = env.seq                # approval_id=请求事件 seq
        req.batch_mono = time.monotonic()
        # F-28 一级保护:approval_id 以**事件 seq** 为键,而 provider 跨会话共享、
        # 子会话 seq 自 1 重数 ⇒ 父子可能同键。**禁止跨会话覆盖**(覆盖会让 A 的
        # 裁决去解决 B 的请求)。同会话重入仍按原语义放行。
        # 已知副作用:此守卫位于 approval.requested 之后,拒绝时该事件已落盘而
        # 无裁决留痕(工具侧会以 APR-503 记 tool.error 形成可追溯的失败记录)。
        # 结构性修法(复合键 (sid, approval_id))见 F1-X5/X6,本阶段不做。
        _prior = self._pending.get(env.seq)
        if _prior is not None and getattr(_prior, "session_id", "") != sid:
            raise_code("APR-503", approval_id=env.seq,
                       why="approval_id 跨会话碰撞:同 seq 已被另一会话占用",
                       advice="禁止跨会话覆盖 pending(fail-closed);"
                              "复合键设计见 F1-X5/X6")
        self._pending[env.seq] = req
        self._merge[(sid, fp)] = req.batch
        registered.append(req)

        if self._detached:
            self._settle(req, "denied", by="system")
            return req
        self._start_ttl(req)                     # TTL 包含等待 pause 确认的时间
        if not self._suspended:
            self._suspended = True
            self._suspend_log = sess
            await self._pause_queue_or_record(sess)
        return req

    def _deny_no_channel(self, call: Any) -> str:
        """无通道直接拒(APR-501;零事件零等待,见偏离 2;恒 raise 不返回)。"""
        raise_code("APR-501", tool=call.name,
                   call_id=getattr(call, "call_id", ""),
                   hint="headless/无交互通道:审批不可用,直接拒"
                        "(接通道或不用 high 工具;不发 approval.requested,无等待)")

    # ================================================== 人类裁决入口(外壳)


    # ================================================== 终态迁移(一次性消费)

    # ================================================== TTL(超时=denied 安全默认)


    # ------------------------------------------------- 队列联动(R12-3)
    def _queue(self) -> Any:
        """惰性取本会话队列;取值异常上抛，禁止伪装成未装配。"""
        getter = self._queue_getter
        if getter is None:
            return None
        return getter() if callable(getter) else getter

    async def _pause_queue_or_record(self, sess: Any) -> None:
        q = self._queue()
        if q is not None:
            # Record ownership before awaiting: failed/cancelled pause may have
            # already changed the real queue, so cleanup must reach that queue.
            self._paused_queue = q
            self._queue_driven = True
            await q.pause("approval", by="system")
        else:
            await sess.append("queue.suspended", {"reason": "approval"}, actor="system")

    async def _resume_queue_or_record_async(self, sess: Any) -> None:
        if self._queue_driven:
            await self._paused_queue.resume("approval", by="system")
            self._queue_driven = False
            self._paused_queue = None
        else:
            await (self._suspend_log or sess).append(
                "queue.resumed", {"reason": "approval"}, actor="system")

    # ================================================== 通道与指纹


    # ================================================== 等待者工厂


    # ================================================== 会话级信任名单


    # ================================================== 会话关闭/detach


    # ================================================== 查询与公共只读


    # ================================================== 内部辅助


# ---------------------------------------------------------------- 别名/工厂
# DIS-SEAM §6.2 seam B Provider 名;装配层经 ctx.approval 注入本对象
ApprovalService = ApprovalProvider


def create_approval(*, session: Any = None, bus: Any = None, config: Any = None,
                    channel: Optional[str] = None, headless: bool = False,
                    trust_max: int = DEFAULT_TRUST_MAX) -> ApprovalProvider:
    """装配工厂:构造审批服务并完成裁决事件订阅(bus 注入时)。"""
    return ApprovalProvider(session=session, bus=bus, config=config,
                            channel=channel, headless=headless,
                            trust_max=trust_max)


__all__ = [
    "ApprovalProvider", "ApprovalService", "ApprovalRequest", "TrustEntry",
    "create_approval", "canonical_json", "CHANNELS", "VERDICTS",
]
