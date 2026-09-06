"""pyharness/core/goal.py — 会话级目标管理 (specs/goal.py.md 契约;F047 目标管理)

F047:长任务把用户目标拆成可核对子目标(活动目标 ≤8 个),create/update/check/
abandon 全部事件化(goal.created/updated/completed);目标状态只从事件回放重建
(**溯源可回放**,INV-01);汇报"完成"必须先过核对(证据不足→追问,不落 completed,
goal.completed 恒由 system 写——SECURITY S-1 防伪造"已执行");活动目标渲染进
系统提示词段 + 防跑偏提醒注入(软提示,不硬限制 agent 行为,soft anchor)。

事件模型纪律(INV-01):GoalManager._goals 内存注册表**只由事件派生**——一切"状态
写入"先 await session.append 对应 goal.* 事件(落定成功),再把内存 Goal 字段对齐到
事件载荷;任何内存字段不是第二真源。goal.updated/completed 前必有 goal.created
(created_seq = goal.created 事件 seq,升序单调);completed(done 终态)后不可再
updated(BUSY 拒)。checklist 打勾为瞬态工作数据(事件词表无 checklist 字段,
EVENT-SCHEMA §3.5.2),不参与事件回放;持久化字段(id/desc/status/progress/note/
task_id/created_seq)全部可由事件重建。

偏离说明(相对 specs/goal.py.md 伪码):
1. goal.updated 载荷按 EVENT-SCHEMA §3.5.2 + events.payload 模型为准(伪码过时,
   同 agent_loop 偏离 4 先例):payload 模型必填 status,故本模块每次 updated 均
   携带当前 status 值(进度更新事件亦然);created 载荷缺 task_id/progress 字段属
   词表模型缺口,已在 events/payload.py 补可选字段(见该文件注释)。
2. 事件先落、状态后对齐:伪码先改内存再 append;本实现先 append(成功返回)再改
   内存——append 校验拒写(失败)时内存与日志保持一致,零副作用,INV-01 更严。
   唯一例外是 checklist 打勾与 claim_done 路径的 progress 赋值(瞬态核对输入,
   事件词表不承载,先落内存供 verify_completion 纯函数核对)。
3. claim_done=True 且带 progress 时先落一条 goal.updated(progress)再 verify——
   伪码只落 completed;补落 updated 使 progress 变化可回放(INV-01 一致性),
   不改变"追问一次/不落 completed"语义。
4. round_end() 每轮先 _round_no += 1 再扫本轮事件(_round_no 即"本轮轮号",
   与数据表"agent-loop 每轮 +1 注入,round_end 同步"口径一致);drift 判定同口径。
5. g-N 单调由实例计数器 _gid_seq 分配;rebuild 时按现存 goal_id 数值后缀续号,
   保证折叠部分旧目标后仍不重号(compaction F058 联动)。
6. goal.updated 载荷为**全量状态快照**(status/note/progress 恒携带):events 层
   payload 模型 canonical 化时恒补齐缺省键(如 note=None),伪码"缺键保留"的重建
   语义会拿显式 None 抹掉旧 note——快照写保证事件流回放逐字段一致(INV-01),
   与 todo.updated 全量替换先例同构。
7. goal() 分发先校验 op 合法性再定位 goal_id(伪码先 _get 后判 op,会使未知 op
   在缺 id 时误报 EVT-101;以异常表"op 非法 → EVT-100"语义优先)。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from pyharness.errors import PyHError, raise_code

log = logging.getLogger("pyharness.goal")

# 状态四值取自 EVENT-SCHEMA §3.5.2 词表(PRD open/in_progress 收敛为 active)
GoalStatus = Literal["active", "paused", "done", "abandoned"]


def _clamp01(v: float) -> float:
    """进度夹到 [0,1](float 化;非法输入由调用方校验)。"""
    return min(1.0, max(0.0, float(v)))


# ---------------------------------------------------------------- 数据结构
@dataclass
class CheckItem:
    """可核对子目标清单项:逐条打勾(done)。"""

    text: str
    done: bool = False


@dataclass
class Goal:
    """会话级目标(id g-N 单调;状态只由事件回放重建,INV-01)。"""

    id: str
    desc: str
    checklist: list[CheckItem] = field(default_factory=list)
    progress: float = 0.0                       # 0..1,clamp 后落事件
    status: GoalStatus = "active"
    task_id: Optional[str] = None               # F044 关联任务(弱耦合字符串引用)
    created_seq: int = 0                        # = goal.created 事件 seq(升序排序键)
    note: Optional[str] = None

    def summary(self) -> str:
        """用户可读一行摘要(goal 命令 list/创建/更新/放弃的返回文本)。"""
        parts = (f"[{self.id}] {self.desc} | 状态:{self.status}"
                 f" | 进度:{self.progress:.0%}")
        if self.task_id:
            parts += f" | 关联任务:{self.task_id}"
        return parts


@dataclass
class CompletionVerdict:
    """完成核对结论(verify_completion 纯函数返回;ok=False → 追问不落 completed)。"""

    ok: bool
    missing: list[str]                          # 未过核对项明细(证据缺口)


@dataclass
class GoalCheck:
    """核对结果(goal_check 返回;reply = 追问或确认文本)。"""

    goal_id: str
    ok: bool
    claim_done: bool
    missing: list[str]
    reply: str


# ------------------------------------------------------------------ GoalManager
class GoalManager:
    """会话级目标注册表与状态机(F047)。

    消费 session.append(写 goal.* 事件)/session 事件读(关联任务 failed 判定);
    被 system-prompt(F010 goal 段装配)、agent-loop(round_end 漂移判定)、
    compaction(F058 未完成 goal 不可折叠)、命令层(goal op 分发)消费。
    内存态只由事件派生:直接构造(新会话空表)或 rebuild_from_events(恢复重建)。

    私有字段: _goals(注册表,created_seq 升序)/ _active_limit(=8 活动上限)/
    _last_goal_round(最近含 goal 活动的轮号)/ _round_no(当前轮号)/ _gid_seq(g-N
    单调计数器,rebuild 按现存 id 续号)。
    """

    def __init__(self, session: Any = None, *, active_limit: int = 8,
                 round_no: int = 0) -> None:
        """构造:session = 会话事件落点(SessionLog 或同型 async append 协议)。

        session 可为 None(rebuild_from_events 只读重建用;写操作将 EVT-100 拒)。
        active_limit = 活动(active+paused)目标上限,F047 边界默认 8。
        """
        self.session = session
        self._goals: dict[str, Goal] = {}
        self._active_limit = int(active_limit)
        self._last_goal_round: Optional[int] = None
        self._round_no = int(round_no)
        self._gid_seq: int = 0

    # ============================================================ 内部辅助
    def _require_session(self) -> Any:
        """写事件前置:未注入会话 → EVT-100(读投影实例不可写)。"""
        if self.session is None:
            raise_code("EVT-100", hint="GoalManager 未注入 session,无法写 goal.* 事件",
                       advice="构造时注入 SessionLog 或同型 append 协议对象")
        return self.session

    def _alloc_gid(self) -> str:
        """g-N 单调分配(计数器前置递增;append 失败留号空洞无害——只需单调唯一)。"""
        self._gid_seq += 1
        return f"g-{self._gid_seq}"

    def _bump_gid_counter(self, gid: str) -> None:
        """按现存 goal_id 续号(rebuild/折叠后不重号;非 g-N 形态忽略)。"""
        if gid.startswith("g-") and gid[2:].isdigit():
            self._gid_seq = max(self._gid_seq, int(gid[2:]))

    def _get(self, goal_id: Optional[str]) -> Goal:
        """定位目标;不存在 → EVT-101(updated/completed 前必有 created 的引用校验)。"""
        g = self._goals.get(goal_id or "")
        if g is None:
            raise_code("EVT-101", goal_id=goal_id,
                       hint=f"goal {goal_id} 不存在:先 create 再操作",
                       advice="先 create;查 goal_id 拼写")
        return g

    def _nondone(self) -> int:
        """活动(active+paused)目标计数——超限判定用(done/abandoned 不计)。"""
        return len(self.list_active())

    def _touch_activity(self) -> None:
        """目标活动打点:最近含 goal 活动轮号 = 当前轮(漂移静默计数清零)。"""
        self._last_goal_round = self._round_no

    def _iter_session_events(self) -> list[Any]:
        """会话事件只读遍历(session.replay/events_after 语义;供关联任务判定)。"""
        s = self.session
        if s is None:
            return []
        if hasattr(s, "events_after"):          # SessionLog 增量读(seq 升序)
            return list(s.events_after(0))
        if hasattr(s, "replay"):                # 存储层真源回放
            return list(s.replay())
        if hasattr(s, "events"):                # 测试替身/轻量落点
            return list(s.events)
        return []

    def _task_failed(self, task_id: str) -> bool:
        """关联任务是否已 failed(终态事件单调:task.failed 后不可再 completed)。

        只读扫描会话事件,不另存第二份任务状态(INV-01)。
        """
        for e in self._iter_session_events():
            if getattr(e, "type", "") == "task.failed":
                p = getattr(e, "payload", {}) or {}
                if p.get("task_id") == task_id:
                    return True
        return False

    # ============================================================ 命令分发
    async def goal(self, args: dict, ctx: Any) -> str:
        """F047 验收伪代码直译的 op 分发器 → 用户可读 summary。

        args = {"op", "text", "id", "progress", "checked", "claim_done",
                "status", "note", "task_id", …};ctx = 会话实体(取 ctx.task_id
        兜底关联,F044)。op 非法 → EVT-100;goal_id 不存在 → EVT-101。
        """
        op = args.get("op")
        if op is None:
            raise_code("EVT-100", hint="goal 命令缺 op 字段",
                       advice="op ∈ create/update/check/abandon/list")
        if op not in ("create", "update", "check", "abandon", "list"):
            # op 合法性先于定位(伪码先 _get 后判 op,会使未知 op 在缺 id 时误报
            # EVT-101;以异常表"op 非法 → EVT-100"语义优先——见偏离说明)
            raise_code("EVT-100", hint=f"未知 goal op: {op}",
                       advice="op ∈ create/update/check/abandon/list")
        gid = args.get("id")
        if op == "create":                      # 创建:可关联当前任务 ctx.task_id
            g = await self.goal_create(
                args.get("text", ""),
                task_id=args.get("task_id") or getattr(ctx, "task_id", None))
            return g.summary()
        if op == "list":
            return "\n".join(g.summary() for g in self.list_active()) or "无活动目标"
        g = self._get(gid)                      # 其余 op 先定位;不存在 → EVT-101
        if op == "update":                      # 改 note/显式切状态(paused 等)
            await self.goal_update(gid, note=args.get("note"),
                                   status=args.get("status"))
            return g.summary()
        if op == "check":                       # 核对:打勾/报进度/声称完成
            r = await self.goal_check(gid, progress=args.get("progress"),
                                      checked=args.get("checked"),
                                      claim_done=bool(args.get("claim_done")))
            return r.reply
        await self.goal_abandon(gid, reason=args.get("reason", ""))  # op=abandon
        return g.summary()

    # ============================================================ 目标操作
    async def goal_create(self, text: str, *, task_id: Optional[str] = None) -> Goal:
        """新建活动 Goal 并写 goal.created(actor=agent)。

        活动目标已达 active_limit(≤8)→ BUSY 拒(须先 done/abandoned 旧的);
        text 空白 → EVT-100。事件先落,内存后对齐(INV-01)。
        """
        if not text or not text.strip():
            raise_code("EVT-100", hint="goal 描述为空",
                       advice="提供可核对的目标描述文本")
        if self._nondone() >= self._active_limit:   # 活动目标 ≤8(F047 边界)
            raise PyHError("BUSY", ctx={
                "advice": f"活动目标已达 {self._active_limit} 个上限;"
                          f"请先完成/放弃旧目标", "active": self._nondone()})
        sess = self._require_session()
        desc = text.strip()
        gid = self._alloc_gid()
        # 事件先落:状态机只认事件(INV-01);seq/actor 由框架打
        env = await sess.append("goal.created",
                                {"goal_id": gid, "desc": desc,
                                 "task_id": task_id}, actor="agent")
        g = Goal(id=gid, desc=desc, status="active", task_id=task_id,
                 created_seq=env.seq)
        self._goals[gid] = g                    # 事件落定 → 注册(created_seq=事件 seq)
        self._touch_activity()
        return g

    async def goal_update(self, goal_id: str, *, note: Optional[str] = None,
                          status: Optional[str] = None,
                          progress: Optional[float] = None) -> None:
        """改 note/显式切状态(paused↔active)/报进度,统一写 goal.updated。

        done 目标不可再 updated(EVENT-SCHEMA 词表校验 → BUSY);status 只收
        词表值(active/paused/abandoned;done 只能经核对完成写入);无变更项
        幂等返回不写事件。
        """
        g = self._get(goal_id)                  # 不存在 → EVT-101(前必有 created)
        if g.status == "done":                  # completed 后不可再 updated
            raise PyHError("BUSY", ctx={
                "hint": f"目标 {goal_id} 已完成,不可再更新",
                "advice": "完成态不可逆;新需求请新建目标"})
        if status is not None and status not in ("active", "paused", "abandoned"):
            raise_code("EVT-100", hint=f"非法 goal 状态 {status}",
                       advice="状态 ∈ active/paused/done/abandoned(词表四值;"
                              "done 只能经核对完成写入)")
        if note is None and status is None and progress is None:
            return                              # 幂等:无变更项不写事件
        # 全量快照载荷:updated 事件携带目标完整状态(status/note/progress)。原因:
        # payload 模型 canonical 化恒补齐全部键(缺省 None),rebuild 若按"缺键保留"
        # 语义会拿显式 None 抹掉旧值——快照语义保证回放逐字段一致(INV-01)。
        new_status = status if status is not None else g.status
        new_note = note if note is not None else g.note
        new_progress = (_clamp01(progress) if progress is not None else g.progress)
        payload: dict[str, Any] = {"goal_id": goal_id, "status": new_status,
                                   "note": new_note, "progress": new_progress}
        sess = self._require_session()
        await sess.append("goal.updated", payload, actor="agent")
        # 事件落定 → 内存对齐到载荷(INV-01:内存 ≡ 事件派生)
        g.status = new_status
        g.note = new_note
        g.progress = new_progress
        self._touch_activity()

    async def goal_check(self, goal_id: str, *,
                         progress: Optional[float] = None,
                         checked: Optional[list[str]] = None,
                         claim_done: bool = False) -> GoalCheck:
        """核对(打勾/报进度/完成声明)。

        checked 项置 done、报 progress;claim_done=True 时先 verify_completion
        验证据:过核 → 写 goal.completed(actor=system);不过 → 返回追问文本,
        状态不变(未完成却说完成 → 追问一次,F047 边界,不落 completed)。
        """
        g = self._get(goal_id)
        if g.status == "done":                  # 幂等:已完成核对直接确认返回
            return GoalCheck(goal_id, ok=True, claim_done=True, missing=[],
                             reply="该目标已完成")
        sess = self._require_session()
        if checked:
            done_set = set(checked)
            for c in g.checklist:               # 打勾(子目标核对,瞬态工作数据)
                if c.text in done_set:
                    c.done = True
        if progress is not None:                # 报进度(clamp;claim 前先入内存供核对)
            g.progress = _clamp01(progress)
        if not claim_done:
            await sess.append("goal.updated",
                              {"goal_id": goal_id, "status": g.status,
                               "note": g.note, "progress": g.progress},
                              actor="agent")
            self._touch_activity()
            return GoalCheck(goal_id, ok=True, claim_done=False, missing=[],
                             reply=f"已记录进度 {g.progress:.0%}")
        # claim_done:带进度先补落一条 updated(INV-01:进度变化可回放),再验证据
        if progress is not None:
            await sess.append("goal.updated",
                              {"goal_id": goal_id, "status": g.status,
                               "note": g.note, "progress": g.progress},
                              actor="agent")
        verdict = self.verify_completion(g)     # 声称完成 → 先过核对(纯函数)
        if not verdict.ok:                      # 证据不足:追问一次,不落 completed
            self._touch_activity()
            return GoalCheck(goal_id, ok=False, claim_done=True,
                             missing=verdict.missing,
                             reply=f"你说已完成,但核对项未过:"
                                   f"{'、'.join(verdict.missing)};"
                                   f"逐条核对(goal check)后再报完成。")
        await self.mark_completed(g)            # 过核 → completed(actor=system)
        return GoalCheck(goal_id, ok=True, claim_done=True, missing=[],
                         reply="核对通过,目标已完成")

    def verify_completion(self, goal: Goal) -> CompletionVerdict:
        """完成核对(纯函数,S-1 防伪闸):progress≥1.0 且 checklist 全 done 且
        关联 task 无 failed;返回 ok 与 missing 明细。零写副作用,供 goal_check
        与测试直接调用;ok=False → 追问(不落 completed)。
        """
        missing: list[str] = []
        if goal.progress < 1.0:                 # 进度未满
            missing.append(f"进度仅 {goal.progress:.0%}")
        for c in goal.checklist:                # 子目标逐条核对(PRD F047)
            if not c.done:
                missing.append(f"未打勾: {c.text}")
        if goal.task_id and self._task_failed(goal.task_id):
            missing.append(f"关联任务 {goal.task_id} 未成功结束")
        return CompletionVerdict(ok=not missing, missing=missing)

    async def mark_completed(self, goal: Goal) -> None:
        """框架侧完成(仅供核对过核后调用):写 goal.completed(actor 恒 system)。

        完成事实只能由框架核对后落事件;agent/用户路径只能经 goal_check
        (claim_done) 间接触发,无直接公开调用口之外的入口(S-1)。
        """
        if goal.status == "done":               # 幂等(重复过核无害)
            return
        sess = self._require_session()
        await sess.append("goal.completed",
                          {"goal_id": goal.id, "status": "done"},
                          actor="system")       # actor=system 硬编码(S-1)
        goal.status = "done"
        self._touch_activity()

    async def goal_abandon(self, goal_id: str, *, reason: str = "") -> None:
        """放弃目标:写 goal.updated{status:abandoned}(词表事件,非新类型)。

        abandoned 目标释放活动名额并进入 compaction 可折叠集;终态幂等返回。
        """
        g = self._get(goal_id)
        if g.status in ("done", "abandoned"):   # 终态幂等返回
            return
        sess = self._require_session()
        note = reason or None
        await sess.append("goal.updated",
                          {"goal_id": goal_id, "status": "abandoned",
                           "note": note, "progress": g.progress},
                          actor="agent")        # 快照载荷:model canonical 恒补 progress
        g.status = "abandoned"                  # 事件落定 → 内存对齐(INV-01)
        g.note = note                           # 与载荷一致(reason 空则清旧 note)
        self._touch_activity()

    # ============================================================ 看板/渲染
    def list_active(self) -> list[Goal]:
        """活动目标看板源:active+paused,按 created_seq 升序;≤8 由 create 保证。"""
        return [g for g in sorted(self._goals.values(), key=lambda x: x.created_seq)
                if g.status in ("active", "paused")]    # done/abandoned 不入板

    def render_goal_segment(self, *, max_tokens: int = 800) -> str:
        """活动目标板 → 系统提示词段(F010 每次 LLM 调用携带;软锚定不硬限制)。

        无活动目标返回空串(零开销不注入);超长按 max_tokens 截断防超窗。
        """
        acts = self.list_active()
        if not acts:
            return ""
        lines = ["<goals> 当前活动目标(软提醒:汇报完成必须先过核对):"]
        for g in acts:
            pend = [c.text for c in g.checklist if not c.done]   # 待核子目标
            lines.append(f"- [{g.id}] {g.desc} | 进度 {g.progress:.0%}"
                         f" | 待核:{','.join(pend) if pend else '无'}"
                         + (f" | 关联任务 {g.task_id}" if g.task_id else ""))
        return "\n".join(lines)[:max_tokens]    # 截断防超窗(余量 ≥25% 由 F058 控)

    # ============================================================ 漂移判定
    def round_end(self, events: list[Any]) -> None:
        """agent-loop 每轮末喂本轮事件:轮号 +1 后扫 goal.* / 带 goal 溯源任务。

        轮内出现 goal 关联活动(goal.* 事件或 task.enqueued 携带 meta.goal_id)
        → _last_goal_round = 本轮(静默轮计数清零);否则静默轮累计。
        """
        self._round_no += 1
        for e in events:
            t = getattr(e, "type", "")
            p = getattr(e, "payload", {}) or {}
            if t.startswith("goal.") or (t == "task.enqueued"
                                         and (p.get("meta") or {}).get("goal_id")):
                self._last_goal_round = self._round_no   # 本轮有目标活动

    def drift_reminder(self, *, quiet_rounds: int = 3) -> Optional[str]:
        """离题提醒(确定性规则):有活动目标但连续 quiet_rounds(≥3)轮无任何
        goal 关联活动 → 返回提醒文本(注入下一轮系统提示,软提示不阻断);
        否则 None。会话从未有目标活动 → 不判定不提醒。
        """
        if not self.list_active():              # 无活动目标:不判定不提醒
            return None
        if self._last_goal_round is None:       # 会话从未有目标活动:不判定
            return None
        if self._round_no - self._last_goal_round >= quiet_rounds:
            return ("提示:已连续数轮未推进任何活动目标(goal 板见上)。"
                    "若已偏离原目标,请说明理由或更新/放弃目标,避免跑偏。")
        return None

    # ============================================================ 事件溯源重建
    @classmethod
    def rebuild_from_events(cls, events: list[Any]) -> "GoalManager":
        """事件溯源重建(INV-01):顺序重放 goal.created/updated/completed 重建
        注册表(seq 升序,只读不写回)——崩溃恢复/会话恢复后内存态与日志一致。

        孤儿 updated/completed(前无 created)属日志损坏,由 EVENT-SCHEMA 校验层
        拦截;此处防御性跳过并告警,重放不中断。created_seq = 事件 seq;
        g-N 计数器按现存 id 续号(折叠部分旧目标后新建仍单调唯一)。
        """
        m = cls()                               # 全新空注册表(无 session:只读投影)
        for e in events:                        # 顺序重放(seq 升序)
            p = getattr(e, "payload", {}) or {}
            if e.type == "goal.created":
                gid = p["goal_id"]
                m._goals[gid] = Goal(id=gid, desc=p.get("desc") or "",
                                     task_id=p.get("task_id"), status="active",
                                     created_seq=e.seq)
                m._bump_gid_counter(gid)
            elif e.type == "goal.updated":
                g = m._goals.get(p.get("goal_id"))
                if g is None:
                    log.warning("orphan goal.updated seq=%s", e.seq)
                    continue                    # 防御跳过,不中断重放
                g.progress = p.get("progress", g.progress)
                g.status = p.get("status", g.status)     # paused/abandoned 迁移
                g.note = p.get("note", g.note)
            elif e.type == "goal.completed":    # actor=system;终态
                g = m._goals.get(p.get("goal_id"))
                if g is not None:
                    g.status = "done"
        return m                                # 内存态 ≡ 日志派生态(INV-01)


__all__ = [
    "GoalManager", "Goal", "CheckItem", "GoalCheck", "CompletionVerdict",
    "GoalStatus",
]
