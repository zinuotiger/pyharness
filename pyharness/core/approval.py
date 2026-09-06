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
   四字段(57 类型已锁定,实测超字段 append → EVT-100)→ channel/call_id 改经
   Envelope.trace 携带(信封 trace 为自由 dict 不触发载荷校验,tools_guard 偏离 1
   同款先例);risk 恒 "high"(guard 层只放行 high 进审批,critical 已转 reject)。
2. headless 分支以"异常表 + ERR.md + DIS-SEAM §6.2 G2"为准:request() 抛 APR-501
   (raise_code,零事件零等待),不用伪码 `return self._deny_no_channel(call)` 返回
   denied——伪码与异常表(APR-501 PyHError 行)冲突,异常表为规范面;且 approval.denied
   无对应 approval.requested 即孤儿裁决事件(EVENT-SCHEMA §3.4.5 锚点语义非法),
   不落孤儿事件。headless 拒绝审计面由 guard 层 guard.rejected(GRD-401,policy_ref=
   APR-501)负责(tools_guard 偏离 3 同口径)。_deny_no_channel 保留为内部实现,
   只负责 raise(签名标注 -> str 但恒不返回,见 spec 函数速览表同名列)。
3. 裁决唤醒单点 = on_verdict(总线订阅):approve/deny/_on_timeout 只负责强同步落结果
   事件,唤醒经总线分发到达 on_verdict 完成(一次性消费);总线未装配(纯内存
   SessionLog/单测)时 _emit_verdict 回落为直接 await on_verdict,保证不悬挂
   (装配态走总线同路径,语义不变)。
4. queue.suspended/resumed 已入 57 词表(可落盘)→ 经注入 session 尽力而为落盘,
   reason 取 spec 伪码字面量 "approval"(EVENT-SCHEMA §3.5.3 示例值 approval-pending
   仅为示例,冲突以伪码为准);重复挂起合并(首请挂起、末决恢复)。落盘失败只记日志
   不阻断审批主路径(与 scope._record 同款尽力而为)。
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
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from pyharness.errors import raise_code
from pyharness.events.vocab import is_registered  # 词表注册判定(trust_* 未入 57 词表,见偏离 5)

if TYPE_CHECKING:  # 仅类型标注:executor 传入的 ToolCall 鸭子契约,运行期不依赖
    from pyharness.core.tools_guard import ToolCall

logger = logging.getLogger("pyharness.approval")

# ====================================================================== 常量
# 交互裁决通道(外壳装配注入:交互 CLI/Web/ACP);headless/管道/后台 job 无通道
CHANNELS: tuple[str, ...] = ("cli", "web", "acp")

# 三结果字面量(request 返回值/终态;超时=denied 安全默认但结果字面量仍为 timeout)
VERDICTS: tuple[str, ...] = ("granted", "denied", "timeout")
TERMINAL_STATES: frozenset = frozenset(VERDICTS)

# 审批结果 → 结果事件名(词表 §3.4.5 三结果同构)
_OUTCOME_EVENTS: dict[str, str] = {
    "granted": "approval.granted",
    "denied": "approval.denied",
    "timeout": "approval.timeout",
}

# 非法裁决者身份前缀(假冒审批结构防线 S-2:仅人类通道可裁决)
_FAKE_BY_PREFIXES: tuple[str, ...] = ("llm:", "tool:", "plugin:")

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
    session_id: str = ""                   # 请求所属会话(事件落点路由)
    log: Any = None                        # 会话日志(事件落点;经 ctx 注入)
    waiter: Any = None                     # asyncio Future(裁决/超时/取消先到者解决)
    timer: Any = None                      # TTL asyncio.TimerHandle(仅 lead)
    batch: list = field(default_factory=list)  # 所属 60s 批(含自身;lead 恒 batch[0])
    batch_mono: float = 0.0                # 批创建单调时钟(60s 窗口判定)

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

    状态:_pending(approval_id→lead)、_merge(指纹→批,60s 窗口)、_trust(指纹→
    信任记录)、_ttl_ms(默认 120_000)、_merge_window_ms(60_000)、_enabled(信任
    名单开关,默认 False,仅交互可开)、_suspended(队列挂起水位,重复挂起合并)。
    """

    def __init__(self, *, session: Any = None, bus: Any = None,
                 config: Any = None, channel: Optional[str] = None,
                 headless: bool = False, trust_max: int = DEFAULT_TRUST_MAX) -> None:
        self._session: Any = session            # 默认会话日志(可 None;未接线事件降级)
        self._bus: Any = bus                    # EventBus(可 None = 纯内存模式)
        self._config: Any = config              # Settings(只读 TTL/合并窗)
        self._channel: Optional[str] = channel  # 缺省通道(交互 cli/web/acp;None=无)
        self._headless: bool = bool(headless)   # 缺省 headless 标志(装配上下文)
        self._pending: dict[int, ApprovalRequest] = {}
        self._merge: dict[str, list[ApprovalRequest]] = {}
        self._trust: dict[str, TrustEntry] = {}
        self._trust_max: int = int(trust_max or DEFAULT_TRUST_MAX)
        self._enabled: bool = False             # 信任名单默认关(SECURITY §5.4)
        self._suspended: bool = False           # 队列挂起水位(F043;重复挂起合并)
        self._suspend_log: Any = None           # 挂起事件落点(恢复事件同落点)
        self._detached: bool = False            # detach 幂等标记
        self._tasks: set = set()                # fire-and-forget 任务登记(防 GC)
        # 裁决事件订阅:approval.granted/denied/timeout → on_verdict(DIS-SEAM §6.2
        # Definition.subscriptions 同款);owner="approval" 供 detach 摘除。
        if self._bus is not None:
            for t in ("approval.granted", "approval.denied", "approval.timeout"):
                try:
                    self._bus.subscribe(t, self.on_verdict, owner="approval")
                except Exception as exc:         # noqa: BLE001 订阅失败不阻断构造
                    logger.warning("approval 订阅 %s 失败: %s", t, exc)
        # TTL / 合并窗配置编译(config 键面 security.approval.ttl_ms / merge_window_s)
        self._ttl_ms: int = self._cfg_int(
            ("security", "approval", "ttl_ms"), 120_000)          # 120s 超时=denied
        self._merge_window_ms: int = self._cfg_int(
            ("security", "approval", "merge_window_s"), 60) * 1000  # 60s 窗口

    # ======================================================== 审批主入口
    async def request(self, call: Any, args_summary: str, ctx: Any, *,
                      ttl_ms: Optional[int] = None) -> str:
        """审批主入口(F015;executor 关 2.5 唯一调用方)→ granted/denied/timeout。

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
            return "granted"                     # executor 仍重入 guard 链
        # 60s 合并防轰炸(R13):同指纹且批未终态且在窗口内 → 挂批,不新增请求事件
        batch = self._merge.get(fp)
        if (batch and not batch[0].is_terminal
                and time.monotonic() - batch[0].batch_mono
                < self._merge_window_ms / 1000.0):
            req = self._new_waiter(fp, call, args_summary, ch, ttl, sid=sid, sess=sess)
            batch.append(req)                    # 挂到既有批,等同一裁决
            return await self._wait_any(req)
        # 开新批:强同步 approval.requested(approval_id = append 返回 seq)
        payload = {"tool": call.name, "args_summary": args_summary,
                   "ttl_ms": ttl, "risk": "high"}
        trace: dict = {"channel": ch}
        if getattr(call, "call_id", None):
            trace["call_id"] = call.call_id
        if getattr(call, "parent_seq", None):
            trace["parent_seq"] = call.parent_seq
        env = await sess.append("approval.requested", payload,
                                actor="tool", sync=True, trace=trace)
        req = self._new_waiter(fp, call, args_summary, ch, ttl, sid=sid, sess=sess)
        req.approval_id = env.seq                # approval_id=请求事件 seq
        req.batch_mono = time.monotonic()
        self._pending[env.seq] = req
        self._merge[fp] = req.batch
        self._start_ttl(req)                     # TTL 定时器:到点无人 → timeout
        try:
            if not self._suspended:              # 首请挂起(F043;重复挂起合并)
                self._suspended = True
                self._suspend_log = sess
                await self._soft_append(sess, "queue.suspended", {"reason": "approval"})
            return await self._wait_any(req)
        except asyncio.CancelledError:
            # F025/APR-502:取消可能落在 soft_append 与等待之间的任意 await 点
            # (不只在 _wait_any 内)——此时未决批尚未被处理,置 denied 后按取消
            # 协议重抛,保证无悬挂 Future(与 _wait_any 内处理幂等,重复进入无害)。
            if req.state == "pending":
                self._settle(req, "denied", by="system")
                self._maybe_resume_soft()
            raise

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

    def deny(self, approval_id: int, *, by: str) -> None:
        """人类拒绝入口(同 approve:落 approval.denied)。"""
        self._spawn_outcome("denied", approval_id, by)

    def _spawn_outcome(self, verdict: str, approval_id: int, by: str) -> None:
        """裁决落地公共路径:身份校验 → 判 id(未知/已消费 → APR-503)→ 异步强同步落事件。"""
        self._require_human(by)
        req = self._pending.get(int(approval_id))
        if req is None or req.state != "pending":
            raise_code("APR-503", approval_id=approval_id,
                       why="未知或已裁决的 approval_id;同一审批至多一个结果(防重放)")
        sess = req.log or self._session
        if sess is None:
            raise_code("CYC-999", module="approval",
                       hint="请求无会话日志落点,裁决无法强同步落盘")
        type_ = _OUTCOME_EVENTS[verdict]
        self._spawn(self._emit_outcome(req, sess, type_, by, actor="user"))

    async def _emit_outcome(self, req: ApprovalRequest, sess: Any, type_: str,
                            by: str, *, actor: str) -> None:
        """强同步落结果事件(approval.granted/denied/timeout,by 框架打)+ 唤醒。"""
        payload = {"approval_id": req.approval_id, "by": by,
                   "ttl_ms": req.ttl_ms}
        await self._emit_verdict(sess, type_, payload, actor=actor)

    async def _emit_verdict(self, sess: Any, type_: str, payload: dict, *,
                            actor: str) -> Any:
        """强同步落盘(成功才返回;PERS-202 上抛);总线未装配时直接消费(偏离 3)。"""
        env = await sess.append(type_, payload, actor=actor, sync=True,
                                trace={"kind": "approval.verdict"})
        # 唤醒判定:总线装配且与会话同总线 → 由总线分发触达 on_verdict(订阅者);
        # 否则(纯内存会话/异总线)直接消费,保证等待者不悬挂(偏离 3)。
        if self._bus is None or getattr(sess, "_bus", None) is not self._bus:
            await self.on_verdict(type_, env)
        return env

    # ================================================== 裁决事件订阅(总线驱动)
    async def on_verdict(self, type_: str, payload: Any) -> None:
        """裁决事件订阅(DIS-SEAM §6.2 subscriptions):按 approval_id 解决等待者。

        一次性消费:未知/已终态 id 再收裁决 → system.error(APR-503)忽略,不重复
        执行(防重放);timeout 由本服务 TTL 定时器先落(定时器先到者胜,迟到外部
        裁决即重放)。批内合并等待者随 lead 级联解决(各自配对同一裁决)。
        """
        p = payload.payload if hasattr(payload, "payload") else (payload or {})
        aid = p.get("approval_id") if isinstance(p, dict) else None
        req = self._pending.get(aid) if isinstance(aid, int) else None
        if req is None or req.state != "pending":
            await self._note_replay(aid)         # 未知/已消费:APR-503 事件化
            return
        if self._cross_session(payload, req):    # 异会话信封:按未知裁决忽略(偏离 6)
            await self._note_replay(aid)
            return
        verdict = type_.rsplit(".", 1)[-1] if type_ else ""
        if verdict not in VERDICTS:
            return
        by = p.get("by") or ("system" if verdict == "timeout" else "")
        sess = req.log or self._session
        # 信任记录先于一切 await/唤醒(同步块内完成,保证 granted 返回时已就绪)
        if (verdict == "granted" and self._enabled
                and req.approval_id is not None
                and req.channel in CHANNELS):
            self._remember(req)                  # 会话级信任:granted 后记录
        self._settle(req, verdict, by)           # 终态迁移 + 批内级联 + 唤醒
        if self._suspended and not self._pending:  # 末决清空 → 恢复队列(F043)
            self._suspended = False
            await self._soft_append(self._suspend_log or sess,
                                    "queue.resumed", {"reason": "approval"})

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
    def _settle(self, req: ApprovalRequest, verdict: str, by: str) -> None:
        """终态迁移单点:批内全部 pending 成员迁移 + 取消 TTL + 解决各自 waiter。

        幂等:成员已终态跳过(防重复解决);lead 从 _pending 摘除后,同 id 再收
        裁决即 APR-503(重放闸)。合并等待者各自 waiter 在此级联解决(每个等待者
        各自配对裁决结果,granted 后各自独立重入 guard 链)。
        """
        for w in list(req.batch):
            if w.state != "pending":
                continue
            w.state = verdict                    # 一次性迁移,终态不再接受裁决
            w.by = by
            self._cancel_timer(w)
            if not w.waiter.done():
                w.waiter.set_result(verdict)     # 唤醒 request() 等待者
        if req.approval_id is not None and req.approval_id in self._pending:
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
        """超时路径:强同步落 approval.timeout(by=system)→ on_verdict 消费。

        与外部迟到裁决竞争:本事件先落即 TTL 优先;若已被裁决解决(state != pending)
        则放弃(不落孤儿 timeout 事件)。
        """
        if req.state != "pending":
            return
        sess = req.log or self._session
        if sess is None:
            return
        await self._emit_verdict(sess, "approval.timeout",
                                 {"approval_id": req.approval_id, "by": "system",
                                  "ttl_ms": req.ttl_ms},
                                 actor="system")

    # ================================================== 等待与取消(APR-502)
    async def _wait_any(self, req: ApprovalRequest) -> str:
        """等 waiter(裁决/超时/取消任一先到者解决)。

        可被取消(F025):取消 → 本批全置 denied(APR-502 语义,不留悬挂 Future)
        后按取消协议重抛 CancelledError;detach/会话关闭批量路径走 cancel_all。
        """
        try:
            return await req.waiter
        except asyncio.CancelledError:
            if req.state == "pending":           # 取消即 denied(APR-502)
                self._settle(req, "denied", by="system")
                self._maybe_resume_soft()
            raise

    def cancel_all(self, reason: str = "detach") -> None:
        """未决请求全置 denied(APR-502)+ 恢复队列;detach/会话关闭调用,无悬挂。"""
        for batch in list(self._merge.values()):
            for w in list(batch):                # 批内级联幂等,扫一遍即可全覆盖
                if w.state == "pending":
                    self._settle(w, "denied", by="system")
        self._merge.clear()                      # 批表清空(全部终态)
        self._maybe_resume_soft()

    def _maybe_resume_soft(self) -> None:
        """同步上下文恢复队列(取消/批量否认路径;尽力而为落 queue.resumed)。"""
        if self._suspended and not self._pending:
            self._suspended = False
            self._record(self._suspend_log, "queue.resumed", {"reason": "approval"})

    # ================================================== 通道与指纹
    def _ensure_channel(self, ctx: Any) -> Optional[str]:
        """通道判定:交互 cli/web/acp 返回通道名;headless/无 → None(APR-501 入口)。

        优先 ctx 显式注入(ctx.channel/ctx.headless,外壳装配);回落构造缺省
        (self._channel/self._headless)。headless 标志压过通道名(永不生效)。
        """
        headless = self._headless_of(ctx)
        if headless:
            return None
        ch = getattr(ctx, "channel", None)
        if isinstance(ch, str) and ch in CHANNELS:
            return ch
        dc = self._channel
        return dc if isinstance(dc, str) and dc in CHANNELS else None

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
                    ttl: int, *, sid: str, sess: Any) -> ApprovalRequest:
        """构造等待者(waiter Future 由运行中事件循环创建;批先含自身)。"""
        req = ApprovalRequest(
            tool=call.name,
            call_id=getattr(call, "call_id", "") or "",
            args_summary=args_summary,
            danger=_danger_of(call),
            ttl_ms=ttl,
            channel=ch,
            fingerprint=fp,
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
        self.cancel_all(reason="detach")
        if self._bus is not None:
            try:
                self._bus.unsubscribe_all("approval")
            except Exception as exc:             # noqa: BLE001 摘除失败不扩散
                logger.warning("approval 摘订阅失败: %s", exc)
        self._trust.clear()                      # 会话级信任随会话关闭失效
        self._enabled = False
        self._detached = True
        logger.info("approval detach: 未决全置 denied,订阅已摘(幂等)")

    # ================================================== 查询与公共只读
    def pending_count(self) -> int:
        """未决请求数(队列暂停/自检用;0 且曾挂起 → queue.resumed 由裁决路径落)。"""
        return len(self._pending)

    def trust_count(self) -> int:
        """信任名单当前条数(审计/自检只读)。"""
        return len(self._trust)

    # ================================================== 内部辅助
    @staticmethod
    def _require_human(by: Any) -> None:
        """裁决者身份校验(假冒审批防线 S-2):by 缺失或 llm:/tool:/plugin: → APR-503。"""
        if not isinstance(by, str) or not by:
            raise_code("APR-503", why="裁决者身份缺失:by 由框架按通道打,不可省略")
        if by.startswith(_FAKE_BY_PREFIXES):
            raise_code("APR-503", by=by,
                       why="裁决者身份非法:仅 cli/web/acp 人类通道可裁决"
                           "(LLM/工具/插件无权自报'我是人类批准的')")

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

    def _spawn(self, coro: Any) -> None:
        """异步协程排程(fire-and-forget):失败只记日志;任务登记防 GC。"""
        if not inspect.isawaitable(coro):
            coro = coro()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("approval 无运行中事件循环,协程未投递: %s",
                           getattr(coro, "__name__", coro))
            return
        task = loop.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(self._task_error)

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
        try:
            r = bus.emit(type_, payload)
            if inspect.isawaitable(r):
                try:
                    asyncio.get_running_loop().create_task(r)
                except RuntimeError:             # 无事件循环:日志降级
                    logger.warning("approval 无运行中事件循环,%s 未投递", type_)
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
