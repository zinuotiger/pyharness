"""pyharness/core/compaction.py — 上下文压缩 (specs/compaction.py.md 契约;F058 核心)

接近窗口上限时把**早期完整任务段**折叠为摘要。物理日志仍只追加(INV-01 铁律):
折叠区间原事件一行不删不改,只追加一条强同步 `context.compacted`
(ranges/summary/tokens_before/tokens_after) 声明,折叠标记由 session 在吸收
声明事件时同步入 `_folded` 索引——"遮蔽"与"声明"是同一动作,任何时刻可按
ranges 展开日志核对原文(压缩但不撒谎)。

模块职责(对应 spec §模块职责):
  1. 触发判定 should_compact:派生历史 ≥75% 窗口 **且** 距上次压缩新增 ≥10 轮
     (双条件缺一不触发,防窗口抖动频繁压缩;参数锁死见 PARAMETER-ANCHOR)。
  2. 候选选择 candidates:最近 12 轮(保留锚)以前的**完整任务段**(segment.start/
     segment.end 配对齐全、end_seq ≤ 保留锚),整段不切半(F044);含未完成
     plan/goal/todo、待审批 approval、guard.rejected、已折叠区间的段整体剔除。
  3. 压缩执行 compact/_apply:逐段独立 LLM 摘要(预算 400 token,失败重试 1 次
     仍败 → _degrade_summary 规则降级,不阻塞对话)→ 强同步追加 context.compacted
     (声明不落盘则回放误报空洞,EVENT-SCHEMA §8.1)。
  4. run_if_needed:请求前门面(agent-loop 消费;非空闲边界不压不炸)。
  5. kv_prefix_plan:两次压缩之间派生历史头部前缀稳定,供 llm 层标注 cache 命中区。

依赖(单向只读):errors.raise_code(EVT-100/PERS-202 透传/LLM-399);core.session
SessionLog(仅公开只读 API);core.system_prompt.est_tokens 为窗口截断估算器(本
模块自带 estimate_tokens,中文 1 token/字口径,仅供相对比较与声明留痕)。

偏离说明(契约=spec,以下为落地取舍,均列理由):
  1. session 缺失方法面:spec 伪码消费 session.mark_folded/segments_before/
     folded_overlaps/last_event_of/events_until/all_events,但阶段 1 锁定的
     SessionLog 未提供这些公开方法,且 test_session 以 `public <= allowed`
     钉死方法面(追加方法会破坏既有 989 测试)→ 本模块以公开只读 API
     (events_after/events_between/stats/derive_history)本地实现等价逻辑:
     - "mark_folded" = session.append(context.compacted) 时 _absorb 自动注册
       _folded 索引(遮蔽标记与声明事件同源、可由日志重建,符合 INV-01/03),
       故无需也不应先行写内存;
     - 任务段切片(segments_before)由本模块扫描 segment.start/end 配对重建;
     - 折叠区间索引(folded_overlaps)读取 session.stats()["folded_ranges"]。
  2. tokens_after 口径:本仓库 reducer(session._fold_history)对折叠区间原文仍
     投影(测试断言"折叠前原文仍可派生——压缩但不撒谎"),spec 伪码
     "mark_folded 后 estimate_tokens(derive_history())" 无法减量 → tokens_after
     以**掩码投影口径**计算:before − Σ(折叠区间原文投影 token) + Σ(摘要消息
     token),即"区间以摘要代"后的派生规模;纯会计函数,不建第二份历史。
  3. BUSY 未登记码:raise_code 会把未登记码改写为 CYC-999(语义不符),按
     agent_loop/scope 同款先例直接构造 PyHError("BUSY")(busy 判定:agent.state/
     loop.state 非空闲即忙)。
  4. 摘要 LLM 出口:ctx.llm.summarize(prompt, budget=...) 为注入点(spec F012 出口);
     未装配 → 抛 LLM-399(走降级,不裸退)。摘要失败 = PyHError(含 LLM-3xx
     透传);重试 1 次仍败由 compact() 捕获转规则降级。
  5. 空降级文本兜底:ContextCompactedPayload.summary 要求 min_length=1,整段无可
     保留决策文本时以诚实占位串入摘要(明确声明"无内容保留",degraded=True),
     不伪造内容(压缩但不撒谎)。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional, Union

from pyharness.errors import PyHError, raise_code
from pyharness.events import Envelope

log = logging.getLogger("pyharness.compaction")

# ------------------------------------------------------------------ 常量
# F058 数字约束(PARAMETER-ANCHOR 锁死;读 config loop.compact 可调)
DEFAULT_KEEP_RECENT_ROUNDS: int = 12     # 保留最近 12 轮原文
DEFAULT_WINDOW_RATIO: float = 0.75       # 触发①:派生 ≥75% 窗口
DEFAULT_MIN_NEW_ROUNDS: int = 10         # 触发②:距上次压缩新增 ≥10 轮
DEFAULT_SUMMARY_BUDGET: int = 400        # 每段摘要 token 预算
DEFAULT_WINDOW_TOKENS: int = 65536       # 窗口兜底(scope.window_tokens 数据源优先)

# 降级摘要保留的决策类事件类型(其余类型——tool.* 原文/guard.*/approval.* 等
# 审计细节——一律丢弃;SECURITY.md §3.1:guard 拒绝与不可折叠内容不进摘要)
_DEGRADE_KEEP: frozenset = frozenset({
    "user.message", "agent.message", "session.renamed",
    "plan.proposed", "plan.approved", "plan.rejected",
    "plan.done", "plan.aborted", "goal.updated", "goal.completed",
    "todo.updated", "context.compacted",
})
_DEGRADE_MAX_CHARS: int = 600           # 降级摘要截断保底(spec)

# ASCII 词切分(token 估算用;与 session._estimate_tokens 同口径,保证差值可减)
_ASCII_WORD = re.compile(r"[A-Za-z0-9_./\\@:+-]+")
_CJK_RANGE = range(0x4E00, 0x9FFF + 1)
_MSG_OVERHEAD = 4                        # role/content 结构开销


# ================================================================= 数据结构
# (spec 数据结构表:字段与默认值逐项对齐)
@dataclass(frozen=True)
class FoldCandidate:
    """折叠候选:一个完整任务段(整段不切半)。"""

    task_id: str
    range: tuple[int, int]          # 段 [start_seq, end_seq] 闭区间
    start_ts: str = ""
    n_events: int = 0


@dataclass(frozen=True)
class SegmentSummary:
    """单段摘要产物(range + 摘要文本 + 降级标记 + token 数)。"""

    range: tuple[int, int]
    summary: str                    # ≤~800 字(spec;摘要预算 400 token 约束)
    degraded: bool = False          # True = 规则降级产物(非 LLM 摘要)
    tokens: int = 0


@dataclass(frozen=True)
class CompactReport:
    """压缩报告:fold 明细 + 前后 token + 保留轮数 + 触发原因。"""

    folds: list[SegmentSummary] = field(default_factory=list)
    tokens_before: int = 0
    tokens_after: int = 0
    kept_recent_rounds: int = DEFAULT_KEEP_RECENT_ROUNDS
    reason: str = "window:0 degraded"
    noop: bool = False


@dataclass(frozen=True)
class PrefixPlan:
    """KV 缓存前缀规划:稳定前缀终点 = 最新 compacted 之后(其后只有尾部追加)。"""

    stable_upto_seq: int = 0        # 稳定前缀终点 seq(0 = 无可复用锚)
    prefix_tokens: int = 0
    cacheable: bool = False
    blocks: list = field(default_factory=list)   # 历次折叠块 [[lo, hi], ...]


# ================================================================= 工具函数
def _count_text_tokens(text: str) -> int:
    """纯文本 token 粗估(确定性;与 session._estimate_tokens 同口径):
    中文逐字 1 token,西文词按 4 字符 ≈ 1 token。仅供相对比较与声明留痕。"""
    cjk = sum(1 for ch in text if ord(ch) in _CJK_RANGE)
    words = sum(max(1, (len(w) + 3) // 4) for w in _ASCII_WORD.findall(text))
    return cjk + words


def estimate_tokens(value: Union[str, dict, list, tuple, None]) -> int:
    """token 估算入口:str = 文本;dict = 消息(取 content,含结构开销);
    list/tuple = 逐元素累加(消息列表每条 +_MSG_OVERHEAD)。纯确定性,无 IO。"""
    if value is None:
        return 0
    if isinstance(value, str):
        return _count_text_tokens(value) + _MSG_OVERHEAD
    if isinstance(value, dict):
        content = value.get("content", "") if value.get("role") else value
        if isinstance(content, (list, tuple)):
            import json
            content = json.dumps(list(content), ensure_ascii=False)
        return _count_text_tokens(str(content)) + _MSG_OVERHEAD
    return sum(estimate_tokens(item) for item in value)


def _payload_text(env: Envelope) -> str:
    """事件载荷 → 摘要/降级可用的正文文本(取 content/标题/描述等决策字段)。"""
    p = env.payload
    return (p.get("content") or p.get("new_title") or p.get("desc")
            or p.get("summary") or "")


def _projection(events: list[Envelope]) -> list[dict]:
    """区间事件 → 派生消息投影(会计/前缀规划用;语义 = session reducer 对区间的
    映射,EVENT-SCHEMA §4.2:user→user、llm.response(有 content)→assistant、
    空 content response + tool.result → tool 配对、context.compacted → system
    摘要消息)。纯函数,不建历史,只用于 token 差值口径与前缀估算。

    偏离:user.message_edited 的跨区间改写(target 可能在本区间外)不追,会计
    近似(声明留痕用途,误差仅影响 tokens_after 数值精度,不影响任何决策)。"""
    msgs: list[dict] = []
    pending: Optional[int] = None       # 待配对 tool 的空 content response seq
    for ev in events:
        t = ev.type
        if t == "user.message":
            msgs.append({"role": "user", "content": ev.payload["content"]})
        elif t == "context.compacted":
            msgs.append({"role": "system",
                         "content": f"[已压缩 {ev.payload['ranges']}] "
                                    f"{ev.payload.get('summary', '')}"})
        elif t == "llm.response":
            c = ev.payload.get("content") or ""
            if c:
                msgs.append({"role": "assistant", "content": c})
            else:
                pending = ev.seq
        elif t == "tool.result" and pending is not None:
            msgs.append({"role": "tool", "content": ev.payload["summary"],
                         "name": ev.payload["name"]})
            pending = None
        # 其余事件(guard.*/approval.*/plan.*/session.*/tool.call…)不进 LLM 投影
    return msgs


def render_segment(events: list[Envelope], max_chars: int = 6000) -> str:
    """任务段 → 可摘要文本(决策轮文本化;tool 结果截断,安全审计细节不入文本)。

    SECURITY.md §3.1:guard.*/approval.* 细节一律不进入摘要输入——模型摘要
    不可能编造其未见内容(结构上保证 guard 拒绝记录不进摘要)。"""
    lines: list[str] = []
    for ev in events:
        t = ev.type
        if t in ("guard.rejected", "guard.evaluated", "approval.requested",
                 "approval.granted", "approval.denied", "approval.timeout",
                 "tool.call"):
            continue                    # 安全审计/审批细节不进摘要(§3.1)
        if t == "user.message":
            lines.append(f"用户: {ev.payload['content']}")
        elif t == "agent.message":
            lines.append(f"助手: {ev.payload['content']}")
        elif t == "llm.response":
            c = ev.payload.get("content") or ""
            if c:
                lines.append(f"模型: {c}")
        elif t == "tool.result":
            summ = (ev.payload.get("summary") or "")[:200]   # 工具原始结果截断
            lines.append(f"工具[{ev.payload.get('name', '?')}]: {summ}")
        elif t == "plan.proposed":
            lines.append(f"计划目标: {ev.payload.get('goal', '')}")
        elif t == "goal.updated":
            gid = ev.payload.get("goal_id", "")
            st = ev.payload.get("status", "")
            lines.append(f"目标[{gid}]: {st} "
                         f"{ev.payload.get('note') or ev.payload.get('desc') or ''}")
        elif t == "todo.updated":
            todos = ev.payload.get("todos") or []
            # payload 已 model_dump 归一:todo 项为 dict(兼容对象形态)
            done_n = sum(1 for it in todos
                         if (it.get("done") if isinstance(it, dict)
                             else getattr(it, "done", False)))
            lines.append(f"待办更新: {done_n}/{len(todos)} 项完成")
        elif t == "context.compacted":
            lines.append(f"(此前折叠: {ev.payload['ranges']})")
    text = "\n".join(x for x in lines if x).strip()
    return text[:max_chars]


def render_compacted_message(env: Envelope) -> str:
    """compacted 声明的人类可读渲染(EVENT-SCHEMA §4.2 摘要代区间锚文本)。

    说明:会话 reducer 对 context.compacted 的实际插桩格式由 session.
    _fold_history 决定(`[已压缩 {ranges}] {summary}`,ranges 为 Python 列表字面
    量);本函数按 compaction 规格提供 [lo-hi] 紧凑形态,供压缩报告与
    kv_prefix_plan 前缀内容估算使用(与 reducer 无耦合,纯只读工具)。"""
    p = env.payload
    rng = ",".join(f"[{lo}-{hi}]" for lo, hi in p.get("ranges", []))
    return f"[已压缩 {rng}] {p.get('summary', '')}"


# ================================================================= Compactor
class Compactor:
    """上下文压缩引擎(F058)。

    参数(锁死值来自 PARAMETER-ANCHOR,读 config loop.compact 可调):
        keep_recent_rounds = 12   保留最近 N 轮原文(保留锚)
        window_ratio       = 0.75 触发①阈值(派生 ≥75% 窗口)
        min_new_rounds     = 10   触发②阈值(距上次压缩新增 ≥10 轮)
        summary_budget     = 400  每段 LLM 摘要 token 预算
        window_tokens      = None 窗口兜底(scope.window_tokens 数据源优先)
        config             = None Settings 快照(读 loop.compact.* 回落)

    ctx 所需成员(鸭子注入,均按需 getattr):
        session: SessionLog 事件日志(events_after/events_between/stats/
                 derive_history/append 只读+声明写)
        scope:   window_tokens 数据源(可缺 → config/构造兜底)
        llm:     summarize(prompt, budget=...) 摘要出口(可缺 → LLM-399 降级)
        agent/loop: 状态源(BUSY 判定;可缺 = 视为空闲边界)
        bus:     sysprompt.compacted 信号出口(可缺 = 日志降级)

    错误(全走 raise_code/构造 PyHError):EVT-100(区间与既有折叠重叠 = 内部
    bug 拒写)、PERS-202(强同步声明落盘失败,由 session.append 上抛透传)、
    BUSY(会话 running 中直接调 compact)、LLM-399(摘要出口未装配/空摘要)。
    """

    def __init__(self, *,
                 keep_recent_rounds: int = DEFAULT_KEEP_RECENT_ROUNDS,
                 window_ratio: float = DEFAULT_WINDOW_RATIO,
                 min_new_rounds: int = DEFAULT_MIN_NEW_ROUNDS,
                 summary_budget: int = DEFAULT_SUMMARY_BUDGET,
                 window_tokens: Optional[int] = None,
                 config: Any = None) -> None:
        self._keep_recent_rounds = int(keep_recent_rounds)
        self._window_ratio = float(window_ratio)
        self._min_new_rounds = int(min_new_rounds)
        self._summary_budget = int(summary_budget)
        self._window_tokens = window_tokens
        self.config = config
        if config is not None:
            self._apply_config(config)

    def _apply_config(self, cfg: Any) -> None:
        """config 回落:loop.compact.* 覆盖锁死默认(仅当显式配置存在)。"""
        compact = getattr(getattr(cfg, "loop", None), "compact", None)
        if compact is None:
            return
        for key, attr in (("trigger_ratio", "_window_ratio"),
                          ("min_new_rounds", "_min_new_rounds"),
                          ("keep_recent_rounds", "_keep_recent_rounds"),
                          ("summarize_budget_tokens", "_summary_budget")):
            val = getattr(compact, key, None)
            if isinstance(val, (int, float)) and val > 0:
                setattr(self, attr, val)

    @classmethod
    def from_config(cls, cfg: Any) -> "Compactor":
        """装配便利:从 Settings 快照构建(含 loop.compact 与窗口回落)。"""
        return cls(config=cfg)

    # ==================================================== 内部只读辅助
    def _events(self, ctx: Any) -> list[Envelope]:
        """全量事件快照(seq 升序;经 session 公开只读 API)。"""
        session = ctx.session
        return list(session.events_after(0))

    def _declared_ranges(self, ctx: Any) -> list[list[int]]:
        """已折叠区间索引(来自 session._folded 只读出口 stats(),compacted
        声明吸收时同步登记——与日志可重建一致,INV-01)。"""
        session = ctx.session
        return [list(r) for r in session.stats()["folded_ranges"]]

    @staticmethod
    def _ranges_overlap(a: tuple, b: tuple) -> bool:
        """两闭区间是否重叠。"""
        return a[0] <= b[1] and b[0] <= a[1]

    def _window(self, ctx: Any) -> int:
        """窗口 token 数据源链:scope.window_tokens → config loop.max_context_tokens
        → 构造兜底 → 内置默认 64k。"""
        scope = getattr(ctx, "scope", None)
        win = getattr(scope, "window_tokens", None)
        if isinstance(win, int) and win > 0:
            return win
        if isinstance(self._window_tokens, int) and self._window_tokens > 0:
            return self._window_tokens
        cfg_win = getattr(getattr(self.config, "loop", None),
                          "max_context_tokens", None)
        if isinstance(cfg_win, int) and cfg_win > 0:
            return cfg_win
        return DEFAULT_WINDOW_TOKENS

    def _busy_state(self, ctx: Any) -> Optional[str]:
        """BUSY 判定:agent.state/loop.state 任一非空闲即忙;缺省 = 空闲边界。

        agent 状态字面量(agent.py):init/ready/busy/stopping/closed;
        loop 状态字面量(agent_loop.py):idle/running/…。空闲 = agent ready/
        idle 且 loop idle(或两者缺省)。"""
        agent = getattr(ctx, "agent", None)
        if agent is not None:
            st = getattr(agent, "state", None)
            if st and st not in ("ready", "idle"):
                return f"agent:{st}"
        loop = getattr(ctx, "loop", None)
        if loop is not None:
            st = getattr(loop, "state", None)
            if st and st != "idle":
                return f"loop:{st}"
        return None

    def _assert_boundary(self, ctx: Any) -> None:
        """非请求边界直调 compact → BUSY(压缩只允许在会话空闲时,防竞争)。"""
        busy = self._busy_state(ctx)
        if busy:
            raise PyHError("BUSY", ctx={
                "advice": "压缩仅允许在请求边界(会话空闲)执行;等 running 结束",
                "state": busy, "module": "compaction"})

    # ==================================================== ① 触发判定
    def _turns_since_last_compact(self, ctx: Any) -> int:
        """自上次 context.compacted 以来新增的人机轮数(user.message 计数)。"""
        last_seq = 0
        for ev in self._events(ctx):
            if ev.type == "context.compacted":
                last_seq = ev.seq
        return sum(1 for ev in self._events(ctx)
                   if ev.seq > last_seq and ev.type == "user.message")

    def should_compact(self, ctx: Any) -> bool:
        """触发判定(F058 双条件;纯判定,不抛):
        ① 派生历史 ≥75% 窗口;② 距上次压缩新增 ≥10 轮。缺一不触发。"""
        window = self._window(ctx)
        if window <= 0:
            return False
        hist_tokens = estimate_tokens(ctx.session.derive_history())
        if hist_tokens < self._window_ratio * window:    # ① 未到 75%
            return False
        if self._turns_since_last_compact(ctx) < self._min_new_rounds:  # ② <10
            return False
        return True

    # ==================================================== ② 候选选择
    def _recent_anchor_seq(self, ctx: Any) -> int:
        """保留锚 = 从日志尾部倒数第 12 个 user.message 的 seq(锚之后的事件
        一律不折叠);总轮 ≤12 → 0(无折叠空间)。"""
        um = [ev.seq for ev in self._events(ctx) if ev.type == "user.message"]
        if len(um) <= self._keep_recent_rounds:
            return 0
        return um[-self._keep_recent_rounds]

    def _segments_before(self, ctx: Any, anchor: int) -> list[dict]:
        """重建完整任务段(F044):扫描 seq ≤ anchor 的事件,segment.start/end
        按 task_id + start_seq 配对;仅收 start/end 均 ≤ anchor 的完整段。"""
        if anchor <= 0:
            return []
        starts: dict[str, Envelope] = {}
        segs: list[dict] = []
        for ev in self._events(ctx):
            if ev.seq > anchor:
                break
            if ev.type == "segment.start":
                starts[ev.payload["task_id"]] = ev
            elif ev.type == "segment.end":
                start = starts.get(ev.payload["task_id"])
                if start is not None and ev.seq <= anchor and \
                        start.seq == ev.payload.get("start_seq"):
                    segs.append({"task_id": start.payload["task_id"],
                                 "start": start.seq, "end": ev.seq,
                                 "start_ts": start.ts})
                    del starts[ev.payload["task_id"]]
        return sorted(segs, key=lambda s: s["start"])     # seq 升序

    def _protected(self, events: list[Envelope]) -> bool:
        """不可折叠集命中判定(SECURITY.md §3.1 + F058):段内含以下任一内容
        → 整段保护、不折叠(整段不切半):
          ① 未完成 plan(proposed/exec.step 无终态 rejected/done/aborted)
             / goal(未 done/abandoned)/ todo(末次更新仍有未 done 项)
          ② 待审批 approval.requested 段内无裁决
          ③ guard.rejected 拒绝记录(单调审计链,永不进摘要)
        段边界假设:完整任务段内自启自结;跨段引用(他段 plan 在本段 exec)一律
        保守保护(引用外部在跑计划 = 不可折叠)。"""
        plan_closed: set[int] = set()     # 已终结 plan_id(proposed 的 seq)
        plans_open: set[int] = set()      # 未终结 plan_id
        goals_open: dict[str, str] = {}   # goal_id → 状态(created/active/paused)
        approvals_pending: set[int] = set()
        todos_unfinished: bool = False
        for ev in events:
            t = ev.type
            if t == "guard.rejected":
                return True                                  # ③ 单调审计链
            if t == "approval.requested":
                approvals_pending.add(ev.seq)                # ② 待审批
            elif t in ("approval.granted", "approval.denied",
                       "approval.timeout"):
                approvals_pending.discard(ev.payload["approval_id"])
            elif t == "plan.proposed":
                plans_open.add(ev.seq)                       # ① plan 开启
            elif t == "plan.exec.step":
                pid = ev.payload["plan_id"]                  # 执行中(段内未见
                if pid not in plan_closed:                   # 终态即未完成)
                    plans_open.add(pid)
            elif t in ("plan.rejected", "plan.done", "plan.aborted"):
                pid = ev.payload["plan_id"]                  # 终态:closed
                plan_closed.add(pid)
                plans_open.discard(pid)
            elif t == "plan.approved":
                plans_open.add(ev.payload["plan_id"])        # 批准 ≠ 完成
            elif t == "goal.created":
                goals_open[ev.payload["goal_id"]] = "created"
            elif t == "goal.updated":
                gid = ev.payload["goal_id"]
                if ev.payload["status"] in ("done", "abandoned"):
                    goals_open.pop(gid, None)                # 终态
                else:
                    goals_open[gid] = ev.payload["status"]   # 仍 active/paused
            elif t == "goal.completed":
                goals_open.pop(ev.payload["goal_id"], None)
            elif t == "todo.updated":
                todos = ev.payload.get("todos") or []
                # payload 经 model_dump 归一:todo 项为 dict(兼容对象形态)
                todos_unfinished = any(
                    not (it.get("done") if isinstance(it, dict)
                         else getattr(it, "done", False))
                    for it in todos)                          # 末次更新口径
        if approvals_pending or plans_open or goals_open or todos_unfinished:
            return True
        return False

    def candidates(self, ctx: Any) -> list[FoldCandidate]:
        """折叠候选选择:最近 12 轮以前的完整任务段,命中不可折叠集/已折叠区间
        的段整体剔除;按 seq 升序返回(纯读,不抛)。"""
        anchor = self._recent_anchor_seq(ctx)
        declared = self._declared_ranges(ctx)
        out: list[FoldCandidate] = []
        for seg in self._segments_before(ctx, anchor):
            rng = (seg["start"], seg["end"])
            events = list(ctx.session.events_between(*rng))
            if self._protected(events):            # 不可折叠集 → 整段排除
                continue
            if any(self._ranges_overlap(rng, d) for d in declared):
                continue                            # 已折叠区间 → 防重复
            out.append(FoldCandidate(task_id=seg["task_id"], range=rng,
                                     start_ts=seg["start_ts"],
                                     n_events=len(events)))
        return out                                  # 升序,不切半

    # ==================================================== ③ 压缩执行
    def _degrade_summary(self, events: list[Envelope]) -> tuple[str, bool]:
        """规则降级摘要(摘要失败分支):只保留决策类事件文本(user/agent 消息、
        plan/goal/todo 状态、会话更名、历史折叠声明),丢弃全部 tool.* 原始结果
        与 guard/approval 细节;截断 ≤600 字;返回 (文本, degraded=True)。"""
        parts: list[str] = []
        for ev in events:
            if ev.type not in _DEGRADE_KEEP:
                continue
            text = _payload_text(ev)
            if text:
                parts.append(text.strip())
        merged = "；".join(parts)
        return merged[:_DEGRADE_MAX_CHARS], True

    async def _call_summarize(self, ctx: Any, prompt: str) -> str:
        """摘要出口单次调用(ctx.llm.summarize;未装配 → LLM-399 走降级)。"""
        llm = getattr(ctx, "llm", None)
        fn = getattr(llm, "summarize", None)
        if not callable(fn):
            raise_code("LLM-399", reason="summarize-unavailable",
                       hint="ctx.llm.summarize 未装配;压缩摘要走规则降级")
        result = fn(prompt, budget=self._summary_budget)
        if hasattr(result, "__await__"):
            result = await result
        return str(result or "")

    async def _summarize_segment(self, ctx: Any,
                                 cand: FoldCandidate) -> SegmentSummary:
        """段摘要(独立 LLM 调用):渲染决策文本 → summarize(budget=400);
        PyHError 重试 1 次仍败 → 上抛交 compact() 降级;空摘要视为失败。"""
        events = list(ctx.session.events_between(*cand.range))
        text = render_segment(events)
        prompt = ("请将以下任务段压缩为不超过 400 token 的中文摘要:保留用户意图、"
                  "最终结论、关键事实与目标/待办结果;省略工具原始输出与安全审计"
                  "细节(guard/审批记录一律不写)。\n" + text)
        try:
            summ = await self._call_summarize(ctx, prompt)
        except PyHError:
            summ = await self._call_summarize(ctx, prompt)   # 重试 1 次
        summary = (summ or "").strip()
        if not summary:
            raise_code("LLM-399", reason="empty-summary",
                       hint="摘要模型返回空文本;该段转规则降级")
        return SegmentSummary(range=cand.range, summary=summary,
                              degraded=False, tokens=estimate_tokens(summary))

    async def _apply(self, ctx: Any,
                     folds: list[SegmentSummary]) -> Optional[Envelope]:
        """遮蔽 + 声明(幂等:空 folds → None):tokens 会计 → 强同步 append
        context.compacted;遮蔽标记随声明事件被 session 吸收(入 _folded)。

        异常:EVT-100(区间与既有折叠重叠 = 内部 bug,拒写防双摘要,调用前由
        compact 预检);PERS-202(session.append 强同步落盘失败上抛,压缩视为
        未发生,repair 后重试)。"""
        if not folds:
            return None
        session = ctx.session
        before = estimate_tokens(session.derive_history())     # 全量投影
        raw_total = 0
        for f in folds:                          # 区间原文投影 token(掩码口径)
            evs = list(session.events_between(*f.range))
            raw_total += estimate_tokens(_projection(evs))
        ranges = [list(f.range) for f in folds]
        summary = "\n".join(f.summary for f in folds)
        # 摘要消息 = reducer 实际插桩形态(含 ranges 包装);after 为其净效果
        added = estimate_tokens(f"[已压缩 {ranges}] {summary}") + _MSG_OVERHEAD
        after = max(0, before - raw_total) + added
        env = await session.append(
            "context.compacted",
            {"ranges": ranges, "summary": summary,
             "tokens_before": before, "tokens_after": after},
            actor="system", sync=True)           # 强同步:声明不丢(§8.1)
        log.info("session=%s compaction applied ranges=%s before=%d after=%d",
                 getattr(session, "sid", "?"), ranges, before, after)
        return env

    async def compact(self, ctx: Any) -> CompactReport:
        """压缩主流程:候选逐段摘要(失败降级)→ 防重叠预检 → 强同步声明 →
        报告;无可折叠候选 → noop 不写事件;全程不碰 JSONL 原文。

        异常表:EVT-100(折叠区间与既有声明重叠 = candidates 应已过滤的内部
        bug)、BUSY(会话 running 中直调)、PERS-202(强同步失败,压缩视为未发生)。"""
        self._assert_boundary(ctx)               # 仅请求边界可压(BUSY 防竞争)
        cands = self.candidates(ctx)
        if not cands:
            return CompactReport(folds=[], tokens_before=0, tokens_after=0,
                                 kept_recent_rounds=self._keep_recent_rounds,
                                 reason="no-candidates", noop=True)
        session = ctx.session
        folds: list[SegmentSummary] = []
        failures = 0
        for cand in cands:                       # 逐段独立摘要(失败→降级不阻塞)
            try:
                folds.append(await self._summarize_segment(ctx, cand))
            except PyHError:
                failures += 1
                evs = list(session.events_between(*cand.range))
                txt, _ = self._degrade_summary(evs)
                if not txt:                      # 空降级兜底:诚实占位,不伪造
                    txt = f"[任务段 {cand.range} 无可保留的决策类文本]"
                folds.append(SegmentSummary(range=cand.range, summary=txt,
                                            degraded=True,
                                            tokens=estimate_tokens(txt)))
        declared = self._declared_ranges(ctx)    # 防双摘要预检(内部 bug 拒写)
        for f in folds:
            if any(self._ranges_overlap(f.range, d) for d in declared):
                raise_code("EVT-100", type_="context.compacted",
                           reason="range-overlap",
                           detail=f"折叠区间 {f.range} 与既有声明重叠"
                                  f"(candidates 应已过滤;内部 bug,拒写)")
        env = await self._apply(ctx, folds)      # 遮蔽+强同步声明
        p = env.payload
        return CompactReport(folds=folds,
                             tokens_before=p["tokens_before"],
                             tokens_after=p["tokens_after"],
                             kept_recent_rounds=self._keep_recent_rounds,
                             reason=f"window:{failures} degraded",
                             noop=False)

    # ==================================================== ④ 前缀规划
    def kv_prefix_plan(self, ctx: Any) -> PrefixPlan:
        """KV 缓存前缀规划:稳定前缀终点 = 最新 compacted 之后(其后只有尾部
        追加,前缀零变动 → LLM 服务端前缀缓存可整段复用)。只读派生计算。"""
        events = self._events(ctx)
        comps = [ev for ev in events if ev.type == "context.compacted"]
        if not comps:
            return PrefixPlan(stable_upto_seq=0, prefix_tokens=0,
                              cacheable=False, blocks=[])
        comp = comps[-1]                         # 最新压缩声明
        head = [ev for ev in events if ev.seq <= comp.seq]
        blocks = [list(r) for r in comp.payload["ranges"]]
        msgs = _projection(head)                 # 稳定前缀的派生消息形态
        return PrefixPlan(stable_upto_seq=comp.seq,
                          prefix_tokens=estimate_tokens(msgs),
                          cacheable=True, blocks=blocks)

    # ==================================================== 请求前门面
    async def run_if_needed(self, ctx: Any) -> Optional[CompactReport]:
        """请求前门面(DIS-CORE §5 消费点):agent-loop 每次 LLM 请求前调用——
        非空闲边界/双条件未齐 → None(不压不炸);压缩成功且非 noop → 经 ctx.bus
        广播 sysprompt.compacted(通知装配层重试,缓存已失效)。"""
        if self._busy_state(ctx):                # 仅请求边界可压(BUSY 防竞争)
            return None
        if not self.should_compact(ctx):         # 双条件未齐 → 不压
            return None
        report = await self.compact(ctx)         # 强同步声明后返回
        if not report.noop:
            self._signal(ctx, "sysprompt.compacted",
                         {"tokens_before": report.tokens_before,
                          "tokens_after": report.tokens_after})
        return report

    @staticmethod
    def _signal(ctx: Any, type_: str, payload: dict) -> None:
        """系统声明信号出口:ctx.bus(emit_sync 优先;异步则排程;失败日志降级)。"""
        bus = getattr(ctx, "bus", None)
        if bus is None:
            log.debug("compaction 信号 %s 未投递:ctx.bus 未装配", type_)
            return
        sink = getattr(bus, "emit_sync", None) or getattr(bus, "emit", None)
        if not callable(sink):
            return
        try:
            result = sink(type_, payload)
            if hasattr(result, "__await__"):
                import asyncio
                try:
                    asyncio.get_running_loop().create_task(result)
                except RuntimeError:             # 无运行中事件循环:日志降级
                    log.debug("compaction 信号 %s 未排程(无事件循环)", type_)
        except Exception as exc:                 # noqa: BLE001 信号失败不阻断
            log.warning("compaction 信号 %s 投递失败:%s", type_, exc)


__all__ = [
    "Compactor", "CompactReport", "FoldCandidate", "PrefixPlan",
    "SegmentSummary", "estimate_tokens", "render_compacted_message",
    "render_segment",
    "DEFAULT_KEEP_RECENT_ROUNDS", "DEFAULT_MIN_NEW_ROUNDS",
    "DEFAULT_SUMMARY_BUDGET", "DEFAULT_WINDOW_RATIO", "DEFAULT_WINDOW_TOKENS",
]
