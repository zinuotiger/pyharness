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

if TYPE_CHECKING:  # 仅类型标注:executor 传入的 ToolCall 鸭子契约,运行期不依赖
    pass

logger = logging.getLogger("pyharness.approval")

# ====================================================================== 常量
# 交互裁决通道(外壳装配注入:交互 CLI/Web/ACP);headless/管道/后台 job 无通道
CHANNELS: tuple[str, ...] = ("cli", "web", "acp", "desktop")

# 三结果字面量(request 返回值/终态;超时=denied 安全默认但结果字面量仍为 timeout)
VERDICTS: tuple[str, ...] = ("granted", "denied", "timeout")
TERMINAL_STATES: frozenset = frozenset(VERDICTS)

# 审批结果 → 结果事件名(词表 §3.4.5 三结果同构)
_OUTCOME_EVENTS: dict[str, str] = {
    "granted": "approval.granted",
    "denied": "approval.denied",
    "timeout": "approval.timeout",
}

# 人类裁决通道白名单(假冒审批结构防线 S-2 收紧,P2):仅四通道可裁决——通道名
# 本身("desktop")或其 ":" 前缀("cli:alice")视为合法;任意其它自报身份拒
# (旧实现只拒 llm:/tool:/plugin: 黑名单,`by="hacker"` 冒充人类可过)。
_HUMAN_CHANNELS: tuple[str, ...] = ("cli", "web", "acp", "desktop")

# 会话级信任名单容量上限(≤N 条 FIFO 淘汰;N 可经构造参数覆盖)
DEFAULT_TRUST_MAX: int = 100

# 信任留痕事件名(词表外,尽力而为出口,见偏离 5)
_TRUST_SET = "approval.trust_set"
_TRUST_ADDED = "approval.trust_added"
_TRUST_HIT = "approval.trust_hit"
_TRUST_CLEARED = "approval.trust_cleared"


def _danger_of(call: Any) -> str:
    """读工具契约 danger(只有 high 能进审批;critical 已在 guard 层转 reject)。"""
    d = getattr(getattr(call, "defn", None), "danger", None)
    return d if d in ("high", "critical") else "high"


def _norm_value(v: Any) -> Any:
    """canonical_json 值归一(键排序/类型归一:bool→int、整值 float→int、嵌套递归)。"""
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, dict):
        return {str(k): _norm_value(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_norm_value(x) for x in v]
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def canonical_json(args: Any) -> str:
    """参数规范化序列化(键排序、值类型归一;合并与信任共用的指纹原料)。"""
    return json.dumps(_norm_value(args), sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False)


# ---------------------------------------------------------------- 数据结构
@dataclass
class ApprovalRequest:
    """等待者记录(规格数据结构表)。

    approval_id = 请求事件 seq(防重放唯一键);合并等待者(60s 批内后到者)无事件,
    approval_id=None,挂到 lead 的批等同一裁决(batch 级联)。
    """

    tool: str = ""
    call_id: str = ""
    args_summary: str = ""
    danger: str = "high"
    ttl_ms: int = 120_000
    channel: Optional[str] = None          # cli/web/acp
    by: Optional[str] = None               # 裁决人(裁决时由框架打)
    state: str = "pending"                 # pending/granted/denied/timeout 一次性迁移
    approval_id: Optional[int] = None      # = 请求事件 seq(lead 才有)
    fingerprint: str = ""                  # 规范化指纹(合并批 + 信任共键)
    binding: str = ""                      # 执行侧绑定指纹(executor 传入;授权↔执行一致性)
    session_id: str = ""                   # 请求所属会话(事件落点路由)
    log: Any = None                        # 会话日志(事件落点;经 ctx 注入)
    waiter: Any = None                     # asyncio Future(裁决/超时/取消先到者解决)
    timer: Any = None                      # TTL asyncio.TimerHandle(仅 lead)
    batch: list = field(default_factory=list)  # 所属 60s 批(含自身;lead 恒 batch[0])
    batch_mono: float = 0.0                # 批创建单调时钟(60s 窗口判定)
    decision_inflight: Optional[str] = None  # 单次决定的预留，早于任何 await
    completion: Any = None                 # provider 拥有的完成任务，API/关闭共同等待

    @property
    def is_terminal(self) -> bool:
        """终态判定(终态后不再接受裁决,一次性消费)。"""
        return self.state in TERMINAL_STATES


@dataclass
class TrustEntry:
    """会话级信任记录(指纹 → 条目;FIFO 淘汰;不随 fork 继承)。"""

    tool: str = ""
    fingerprint: str = ""
    by: str = ""                           # 首次 granted 的裁决人
    added_seq: int = 0                     # 记录时 lead 的 approval_id(FIFO 序)
    hits: int = 0                          # 命中计数(审计)
    session_id: str = ""


# ================================================================== 服务类
class ApprovalProvider:
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
                    call, args_summary, ch, ttl, sid, sess, fp, binding, registered)
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

    async def _register(self, call, args_summary, ch, ttl, sid, sess, fp, binding, registered):
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
        trace: dict = {"channel": ch}
        if getattr(call, "call_id", None):
            trace["call_id"] = call.call_id
        if getattr(call, "parent_seq", None):
            trace["parent_seq"] = call.parent_seq
        stats = getattr(sess, "stats", None)
        self._initializing = (sess, stats()["seq"]) if callable(stats) else None
        try:
            env = await sess.append("approval.requested", payload,
                                    actor="tool", sync=True, trace=trace)
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
    def approve(self, approval_id: int, *, by: str) -> None:
        """人类批准入口(CLI/Web/ACP;外壳层唯一裁决通道,LLM 无权调用)。

        by 由框架按通道上下文打(cli:<user>/web:<会话>/acp:<client_id>);身份
        非法(llm:/tool:/plugin: 前缀)或 id 未知/已消费 → APR-503(防重放)。
        结果事件强同步落盘后经总线分发 → on_verdict 唤醒等待者。
        """
        self._spawn_outcome("granted", approval_id, by)

    async def approve_async(self, approval_id: int, *, by: str) -> None:
        """等待强同步裁决、必要队列恢复及等待者可继续后才返回。"""
        await self._decide_async("granted", approval_id, by)

    def deny(self, approval_id: int, *, by: str) -> None:
        """人类拒绝入口(同 approve:落 approval.denied)。"""
        self._spawn_outcome("denied", approval_id, by)

    async def deny_async(self, approval_id: int, *, by: str) -> None:
        """等待拒绝生效及本审批持有的队列暂停解除后才返回。"""
        await self._decide_async("denied", approval_id, by)

    async def user_choice(self, options: list[str],
                          *, prompt: str = "选择(输入序号/文字): ") -> str:
        """plan 单步失败三选一裁决通道(F046):仅交互 cli 通道可用。

        headless/desktop 无键盘通道 → APR-501(安全默认由 plan 层中止);
        输入接受序号或命中选项文字,词表外一律按“中止”安全默认。
        """
        if self._headless or self._channel != "cli":
            raise_code("APR-501", reason="no-user-choice",
                       hint="无用户裁决通道(ctx.approval.user_choice 未接线)",
                       advice="headless/桌面无键盘三选一;单步失败直接中止")
        for i, opt in enumerate(options, 1):
            print(f"  {i}. {opt}", file=sys.stderr)
        try:
            ans = (await asyncio.to_thread(input, prompt)).strip()
        except (EOFError, KeyboardInterrupt):
            return "中止"
        if ans.isdigit() and 1 <= int(ans) <= len(options):
            return options[int(ans) - 1]
        hit = next((o for o in options if o in ans), None)
        return hit or "中止"

    def _spawn_outcome(self, verdict: str, approval_id: int, by: str) -> None:
        self._require_human(by)
        if approval_id not in self._pending and self.owns_initializing(approval_id):
            # CLI is a synchronous command sender. Its valid early command must
            # join registration too; unknown/replayed IDs still fail below.
            self._own(self._decide_async(verdict, approval_id, by))
            return
        req, sess, type_ = self._prepare_outcome(verdict, approval_id, by)
        req.completion = self._own(self._emit_outcome(req, sess, type_, by, actor="user"))

    async def _decide_async(self, verdict: str, approval_id: int, by: str) -> None:
        # A request event may already be visible while registration is awaiting
        # persistence. Join registration before looking up its ID.
        async with self._transition_lock:
            req, sess, type_ = self._prepare_outcome(verdict, approval_id, by)
            req.completion = self._own(self._emit_outcome(req, sess, type_, by, actor="user"))
        await asyncio.shield(req.completion)

    def _prepare_outcome(self, verdict: str, approval_id: int, by: str) -> tuple:
        """同步完成身份与 pending 校验,保证无效裁决立即抛错而不是后台吞掉。"""
        self._require_human(by)
        if self._detached:
            raise_code("APR-503", why="审批服务已关闭")
        req = self._pending.get(int(approval_id))
        if req is None or req.state != "pending":
            raise_code("APR-503", approval_id=approval_id,
                       why="未知或已裁决的 approval_id;同一审批至多一个结果(防重放)")
        if req.decision_inflight is not None:
            raise_code("APR-503", approval_id=approval_id,
                       why=f"审批正在处理({req.decision_inflight}),拒绝重复裁决")
        sess = req.log or self._session
        if sess is None:
            raise_code("CYC-999", module="approval",
                       hint="请求无会话日志落点,裁决无法强同步落盘")
        type_ = _OUTCOME_EVENTS[verdict]
        req.decision_inflight = verdict
        return req, sess, type_

    async def _emit_outcome(self, req: ApprovalRequest, sess: Any, type_: str,
                            by: str, *, actor: str, emit: bool = True) -> None:
        """Only completion owner: durable verdict, queue acknowledgement, waiter."""
        async with self._transition_lock:
            verdict = req.decision_inflight
            if req.is_terminal:
                return
            resume_attempted = False
            try:
                if emit:
                    await self._emit_verdict(sess, type_,
                        {"approval_id": req.approval_id, "by": by, "ttl_ms": req.ttl_ms},
                        actor=actor)
                resume_attempted = True
                await self._release_if_last(req)
            except BaseException:
                # No granted waiter/slot on any incomplete commit. Retain the
                # pending record if its queue transition still needs recovery.
                released = False
                if not resume_attempted:
                    try:
                        await self._release_if_last(req)
                        released = True
                    except Exception:
                        logger.warning("approval recovery failed; preserving commit error",
                                       exc_info=True)
                self._settle(req, "denied", by="system", remove=released)
                raise
            if (verdict == "granted" and not self._detached
                    and self._enabled and req.channel in CHANNELS):
                req.by = by
                self._remember(req)
            self._settle(req, verdict, by)

    async def _release_if_last(self, req: ApprovalRequest) -> None:
        if self._suspended and not any(w is not req for w in self._pending.values()):
            await self._resume_queue_or_record_async(req.log)
            self._suspended = False

    async def _emit_verdict(self, sess: Any, type_: str, payload: dict, *,
                            actor: str) -> Any:
        return await sess.append(type_, payload, actor=actor, sync=True,
                                 trace={"kind": "approval.verdict"})

    async def on_verdict(self, type_: str, payload: Any) -> None:
        """Legacy external verdict delivery; locally owned events are observations.

        Never await our own completion from a SessionLog subscriber: append must
        finish dispatch and strong flush before completion can proceed.
        """
        p = payload.payload if hasattr(payload, "payload") else (payload or {})
        aid = p.get("approval_id") if isinstance(p, dict) else None
        req = self._pending.get(aid) if isinstance(aid, int) else None
        if req is None or req.state != "pending" or self._cross_session(payload, req):
            await self._note_replay(aid)
            return
        if req.decision_inflight is not None:
            return
        verdict = type_.rsplit(".", 1)[-1] if type_ else ""
        if verdict not in VERDICTS or self._detached:
            return
        by = p.get("by") or ("system" if verdict == "timeout" else "")
        req.decision_inflight = verdict
        req.completion = self._own(self._emit_outcome(
            req, req.log, type_, by, actor="system", emit=False))
        await asyncio.shield(req.completion)

    def _cross_session(self, payload: Any, req: ApprovalRequest) -> bool:
        """信封会话与本请求会话不符 → 异会话串扰过滤(偏离 6;无信封视为同会话)。"""
        if not hasattr(payload, "session_id"):
            return False
        return bool(req.session_id) and payload.session_id != req.session_id

    async def _note_replay(self, aid: Optional[int]) -> None:
        """重放/未知裁决留痕:system.error(APR-503),不重复执行。"""
        logger.warning("APR-503 未知或已处理的裁决 id=%s,已忽略(防重放)", aid)
        sess = self._session
        if sess is None:
            return
        try:
            await sess.append("system.error",
                              {"code": "APR-503",
                               "hint": f"未知或已处理的裁决 id={aid},已忽略(防重放)"},
                              actor="system")
        except Exception as exc:                 # noqa: BLE001 尽力而为
            logger.warning("approval system.error 落盘失败: %s", exc)

    # ================================================== 终态迁移(一次性消费)
    def _settle(self, req: ApprovalRequest, verdict: str, by: str, *,
                remove: bool = True) -> None:
        """终态迁移单点:批内全部 pending 成员迁移 + 取消 TTL + 解决各自 waiter。

        幂等:成员已终态跳过(防重复解决);lead 从 _pending 摘除后,同 id 再收
        裁决即 APR-503(重放闸)。合并等待者各自 waiter 在此级联解决(每个等待者
        各自配对裁决结果,granted 后各自独立重入 guard 链)。
        """
        lead_ref = req.approval_id                   # approval identity(仅 lead 有)
        for w in list(req.batch):
            if w.state != "pending":
                continue
            w.state = verdict                    # 一次性迁移,终态不再接受裁决
            w.by = by
            w.decision_inflight = None
            self._cancel_timer(w)
            if verdict == "granted" and w.call_id:
                # 授权↔执行绑定:executor 重入 approval 决策时按 call_id 取此校验
                self._grant_slots[w.call_id] = w.binding
            if w.call_id and lead_ref is not None:
                # S4-P1-2:绑定 call_id → approval identity(= approval.requested 的
                # seq)。merged waiter 自身无 approval_id,按批共享 lead 的 identity;
                # 不新造 uuid,identity 恒来自真实请求事件。
                self._ref_slots[w.call_id] = lead_ref
            if not w.waiter.done():
                w.waiter.set_result(verdict)     # 唤醒 request() 等待者
        if remove and req.approval_id is not None and req.approval_id in self._pending:
            self._pending.pop(req.approval_id, None)

    # ================================================== TTL(超时=denied 安全默认)
    def _start_ttl(self, req: ApprovalRequest) -> None:
        """起 TTL 定时器(仅 lead);到点无人裁决 → _on_timeout(与外部迟到裁决竞争)。"""
        if req.ttl_ms <= 0 or req.is_terminal:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("approval 无运行中事件循环,TTL 定时器未启动 id=%s",
                           req.approval_id)
            return
        req.timer = loop.call_later(req.ttl_ms / 1000.0, self._ttl_tick, req)

    def _ttl_tick(self, req: ApprovalRequest) -> None:
        """定时器到点回调:转异步 _on_timeout(仍 pending 才落 timeout 事件)。"""
        req.timer = None
        if req.state != "pending":
            return
        coro = self._on_timeout(req)
        try:
            self._spawn(coro)
        except Exception as exc:                 # noqa: BLE001 定时器路径不扩散
            logger.warning("approval TTL 触发失败 id=%s: %s", req.approval_id, exc)

    async def _on_timeout(self, req: ApprovalRequest) -> None:
        if req.state != "pending" or req.decision_inflight is not None:
            return
        req.decision_inflight = "timeout"
        req.completion = self._own(self._emit_outcome(
            req, req.log, "approval.timeout", "system", actor="system"))
        await asyncio.shield(req.completion)

    async def _wait_any(self, req: ApprovalRequest) -> str:
        try:
            return await asyncio.shield(req.waiter)
        except asyncio.CancelledError:
            try:
                await self._cancel_request(req)
            except Exception:
                logger.warning("approval cancel cleanup failed", exc_info=True)
            raise

    def _cancel_request_start(self, req):
        lead = req.batch[0]
        if lead.state == "pending" and lead.decision_inflight is None:
            lead.decision_inflight = "denied"
            lead.completion = self._own(self._emit_outcome(
                lead, lead.log, "approval.denied", "system", actor="system", emit=False))
        return lead.completion

    async def _cancel_request(self, req):
        completion = self._cancel_request_start(req)
        if completion is not None:
            await asyncio.shield(completion)

    def cancel_all(self, reason: str = "detach") -> None:
        """Reserve denial synchronously; request/aclose join its full completion."""
        for req in list(self._pending.values()):
            self._cancel_request_start(req)

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
    def set_default_channel(self, value: Any) -> None:
        """装配期覆写 provider 缺省通道(N5):与构造期**同一归一化**,避免直写
        ``_channel`` 绕过契约(``"acp:<id>"`` 必须归一为 ``"acp"``)。"""
        self._channel = default_channel_of(value)

    def _ensure_channel(self, ctx: Any) -> Optional[str]:
        """通道判定(**唯一实现 = ``core.channel`` 契约**;N5)。

        返回:交互通道的**归一化名**(``"acp:<id>"`` → ``"acp"``,保留 ACP 身份);
        显式 headless → ``None``(``APR-501`` 入口,既有语义不变)。
        抛:``MISSING`` / ``INVALID`` / ``UNKNOWN`` ⇒ ``APR-503`` fail-closed。

        **与治理层 ``GovernanceContext.principal_of`` 同契约**(逐状态一致性由
        ``tests/invariants`` 的真值表断言钉死)。

        **修复点(N5-b/N6)**:
        - 判据由"精确名 ``ch in CHANNELS``"改为**前缀归一** ⇒ ``"acp:<id>"`` 不再
          被误判为 headless;
        - **删除 ``self._channel`` 回退** ⇒ 硬编码的 ``"desktop"`` 不再劫持
          "ctx 未声明通道"的情形(那会让审批层以为有 desktop 人类通道,而治理层
          对同一输入 fail-closed —— 同一状态两个相反判定)。

        headless 标志(构造期或 ctx 注入)优先且压过通道名(既有语义,不变)。
        """
        headless = self._headless_of(ctx)
        if headless:
            return None
        st = require_channel(ctx, module="approval", field="channel")
        return None if st.is_headless else st.name

    def _headless_of(self, ctx: Any) -> bool:
        """headless 解析:ctx 显式注入优先,回落构造缺省(agent.py 同款两级解析)。"""
        try:
            v = getattr(ctx, "headless", None)
        except Exception:                        # noqa: BLE001 防御第三方 getattr
            v = None
        if v is not None:
            return bool(v)
        return self._headless

    def _fingerprint(self, tool: str, args: Any) -> str:
        """规范化指纹 = sha1(tool + canonical_json(args))(合并与信任共用的唯一键)。"""
        canon = canonical_json(args or {})
        return hashlib.sha1(f"{tool}\n{canon}".encode("utf-8")).hexdigest()

    def grant_binding(self, call_id: str) -> Optional[str]:
        """最近一次 granted(含信任命中)为该 call_id 记录的绑定指纹(None = 无)。

        executor 重入 guard 得到 approval 决策时读取:与当前调用参数指纹不一致
        即"批准针对异参数",拒绝执行(GRD-403)。
        """
        return self._grant_slots.get(call_id)

    def approval_ref_of(self, call_id: str) -> Optional[int]:
        """该 call_id 关联的 **approval identity**(S4-P1-2,只读)。

        值 = 该次 ``approval.requested`` 的 seq(60s 合并批内共享 lead 的 identity)。
        结算后仍可查(与 ``grant_binding`` 同型生命周期)。``None`` = 该 call_id
        **从未产生过** approval 请求(普通 allow/reject 路径,或信任名单命中路径
        ——后者不落 ``approval.requested``,故无 identity,属正确语义)。

        治理层经执行侧把该值填入审批后重新授权决策(D2)的 ``approval_ref``;
        不新造 identity、不用 decision_id/call_id 代替。
        """
        return self._ref_slots.get(call_id)

    @staticmethod
    def _sid_of(ctx: Any, sess: Any) -> str:
        """会话 id 解析:会话日志 sid 优先,回落 ctx 显式。"""
        sid = getattr(sess, "sid", "") or ""
        if not sid:
            sid = getattr(ctx, "session_id", "") or getattr(ctx, "sid", "") or ""
        return sid

    @staticmethod
    def _log_of(ctx: Any) -> Any:
        """会话日志解析:ctx.session(装配注入)优先,None = 未接线。"""
        return getattr(ctx, "session", None)

    # ================================================== 等待者工厂
    def _new_waiter(self, fp: str, call: Any, args_summary: str, ch: str,
                    ttl: int, *, sid: str, sess: Any,
                    binding: Optional[str] = None) -> ApprovalRequest:
        """构造等待者(waiter Future 由运行中事件循环创建;批先含自身)。"""
        req = ApprovalRequest(
            tool=call.name,
            call_id=getattr(call, "call_id", "") or "",
            args_summary=args_summary,
            danger=_danger_of(call),
            ttl_ms=ttl,
            channel=ch,
            fingerprint=fp,
            binding=binding or fp,               # 未绑定 → 回落规范化指纹
            session_id=sid,
            log=sess,
        )
        req.waiter = asyncio.get_running_loop().create_future()
        req.batch = [req]
        return req

    @staticmethod
    def _cancel_timer(req: ApprovalRequest) -> None:
        """取消 TTL 定时器(幂等)。"""
        if req.timer is not None:
            try:
                req.timer.cancel()
            except Exception:                    # noqa: BLE001 已触发/已取消
                pass
            req.timer = None

    # ================================================== 会话级信任名单
    def enable_trust(self, on: bool, *, by: str) -> None:
        """信任名单开关(默认关;仅交互会话可开;headless 调用直接拒绝 APR-501)。"""
        self._require_human(by)
        if on and (self._headless_of(None) or self._channel not in CHANNELS):
            raise_code("APR-501", op="enable_trust",
                       hint="headless/无交互通道:信任名单仅交互 cli/web/acp 可开,"
                            "headless 永不生效")
        self._enabled = bool(on)
        self._emit_trust(_TRUST_SET, {"on": self._enabled, "by": by})
        logger.info("approval 信任名单 %s(by=%s)",
                    "启用" if self._enabled else "关闭", by)

    def clear_trust(self, tool: Optional[str] = None) -> int:
        """清除信任(全会话或单工具;用户可手动撤销)→ 返回清除条数。"""
        if tool is None:
            count = len(self._trust)
            self._trust.clear()
        else:
            victims = [fp for fp, e in self._trust.items() if e.tool == tool]
            count = len(victims)
            for fp in victims:
                self._trust.pop(fp, None)
        if count:
            self._emit_trust(_TRUST_CLEARED, {"tool": tool, "count": count})
        return count

    def _trust_hit(self, fp: str, sid: str, ch: str) -> bool:
        """信任命中:开关开 + 交互通道 + 指纹在表(同会话);命中计数 + 留痕。"""
        if not self._enabled or ch not in CHANNELS:
            return False
        entry = self._trust.get(fp)
        if entry is None:
            return False
        if sid and entry.session_id and entry.session_id != sid:
            return False                         # 会话级:他会话信任不生效
        entry.hits += 1
        self._emit_trust(_TRUST_HIT, {"tool": entry.tool, "hits": entry.hits})
        return True                              # 命中 ≠ 跳过 guard(executor 重入)

    def _remember(self, req: ApprovalRequest) -> None:
        """granted 后写信任表(≤N 条 FIFO 淘汰)+ 留痕;critical 绝不在名单。"""
        if req.danger == "critical":
            return                               # critical 类绝不在名单
        entry = TrustEntry(tool=req.tool, fingerprint=req.fingerprint,
                           by=req.by or "system", added_seq=req.approval_id or 0,
                           session_id=req.session_id)
        self._trust[req.fingerprint] = entry
        while len(self._trust) > self._trust_max:  # FIFO 淘汰(按加入序)
            oldest = min(self._trust.values(), key=lambda e: e.added_seq)
            self._trust.pop(oldest.fingerprint, None)
        self._emit_trust(_TRUST_ADDED, {"tool": entry.tool, "by": entry.by,
                                        "seq": entry.added_seq})

    # ================================================== 会话关闭/detach
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

    # ================================================== 查询与公共只读
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

    # ================================================== 内部辅助
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
