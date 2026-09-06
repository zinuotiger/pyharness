"""tests/unit/test_compaction.py — compaction 模块单测 (specs/compaction.py.md F058)

覆盖(任务要求 + spec 关联测试):
    触发双条件:派生 ≥75% 窗口 且 距上次压缩新增 ≥10 轮(边界:9 轮不触发/第 10 轮触发)
    候选选择:最近 12 轮保留锚、完整任务段整段不切半、seq 升序;
              不可折叠集:guard.rejected/待审批 approval/未完成 plan/goal/todo 整段剔除,
              已折叠区间防重复剔除
    摘要:注入式 ctx.llm.summarize(budget=400) 独立调用、重试 1 次
    降级:LLM 失败/未装配/空摘要 → _degrade_summary 规则降级(零 tool.* 原文、
          长度 ≤600、degraded=True、不阻塞)
    只追加 INV-01:原文一行不删不改,只追加一条强同步 context.compacted
              (ranges/summary/tokens_before/tokens_after),seq 继续 max+1
    run_if_needed:空闲边界触发压缩 + sysprompt.compacted 信号;busy → None
    kv_prefix_plan / render_compacted_message
"""
import pytest

from pyharness.core.compaction import (
    Compactor, CompactReport, FoldCandidate, PrefixPlan, SegmentSummary,
    estimate_tokens, render_compacted_message,
)
from pyharness.core.session import SessionLog
from pyharness.errors import PyHError

SID = "s-compact12345"


# ===================================================================== 替身
class FakeLLM:
    """ctx.llm 桩:summarize 记录 (prompt, budget);可配置前 N 次抛错。"""

    def __init__(self, text="该任务段已完成:目标达成,用户意图已满足,关键结果保留。",
                 failures: int = 0, empty: bool = False):
        self.text = text
        self.failures = failures          # 前 N 次调用抛 PyHError
        self.empty = empty                # 恒返回空串(= 空摘要失败)
        self.calls: list[tuple] = []      # (prompt, budget)

    async def summarize(self, prompt, budget=400):
        self.calls.append((prompt, budget))
        if self.failures > 0:
            self.failures -= 1
            raise PyHError("LLM-304", ctx={"module": "test-fake"})
        if self.empty:
            return ""
        return self.text


class BusRecorder:
    """bus 桩:emit_sync 记录 (type, payload)。"""

    def __init__(self):
        self.events = []

    def emit_sync(self, type_, payload):
        self.events.append((type_, dict(payload)))
        return {"delivered": 1}


def mk_ctx(log, *, llm=None, window_tokens=65536, agent_state="ready", bus=None):
    """最小 ctx 门面(鸭子注入;对齐 Agent.Ctx 命名空间)。"""
    from types import SimpleNamespace
    scope = SimpleNamespace(window_tokens=window_tokens)
    agent = None if agent_state is None else SimpleNamespace(state=agent_state)
    return SimpleNamespace(session=log, scope=scope, llm=llm,
                           agent=agent, bus=bus, loop=None)


# ===================================================================== 建造器
async def _bootstrap(log: SessionLog) -> None:
    """会话引导:首事件 session.created(seq=1)。"""
    await log.append("session.created", {"title": "", "model": "deepseek-chat"},
                     actor="system")


async def _user_llm(log: SessionLog, text: str, reply: str = None) -> int:
    """人机一轮(决策类):user.message + llm.response;返回 user.message seq。"""
    ev = await log.append("user.message", {"content": text}, actor="user")
    content = reply if reply is not None else f"已收到:{text[:20]}"
    await log.append("llm.response", {"model": "m", "finish_reason": "stop",
                                      "content": content}, actor="llm")
    return ev.seq


async def _tool_round(log: SessionLog, summary: str, call_id: str = "c1") -> None:
    """工具轮(原始结果事件):空 content response + tool.call + tool.result。"""
    await log.append("llm.response", {"model": "m", "finish_reason": "tool_calls",
                                      "content": "",
                                      "tool_calls": [{"id": call_id,
                                                      "name": "fs.read",
                                                      "arguments": {}}]},
                     actor="llm")
    await log.append("tool.call", {"name": "fs.read", "args": {},
                                   "raw_args": {}, "call_id": call_id},
                     actor="llm")
    await log.append("tool.result", {"name": "fs.read", "call_id": call_id,
                                     "ok": True, "summary": summary,
                                     "truncated": False}, actor="tool")


async def _seg_turn(log: SessionLog, task_id: str, text: str, reply: str = None,
                    *, tool_summary: str = None) -> tuple[int, int]:
    """完整任务段一轮:segment.start → user/llm(可选工具轮)→ segment.end;
    返回 (start_seq, end_seq)。"""
    st = await log.append("segment.start", {"task_id": task_id}, actor="agent")
    await _user_llm(log, text, reply)
    if tool_summary is not None:
        await _tool_round(log, tool_summary, call_id=f"c-{task_id}")
        await _user_llm(log, "继续", "完成。")
    en = await log.append("segment.end",
                          {"task_id": task_id, "start_seq": st.seq},
                          actor="agent")
    return st.seq, en.seq


async def _seg_with_extra(log: SessionLog, task_id: str, text: str,
                          extras: list) -> tuple[int, int]:
    """任务段中间(user 之后、回复之前)插入 extras 事件序列;
    元素 = (type, payload),payload 占位符:
        "@first" = 本序列首条事件 seq(plan_id 回指 proposed / approval_id
                   回指 requested 用);"@prev" = 紧邻上一条事件 seq。"""
    st = await log.append("segment.start", {"task_id": task_id}, actor="agent")
    await log.append("user.message", {"content": text}, actor="user")
    first_seq = None
    prev_seq = None
    for type_, payload in extras:
        if first_seq is not None:
            payload = {k: (first_seq if v == "@first" else v)
                       for k, v in payload.items()}
        if prev_seq is not None:
            payload = {k: (prev_seq if v == "@prev" else v)
                       for k, v in payload.items()}
        ev = await log.append(type_, payload, actor="system")
        if first_seq is None:
            first_seq = ev.seq
        prev_seq = ev.seq
    await log.append("llm.response", {"model": "m", "finish_reason": "stop",
                                      "content": f"已处理 {text[:10]}"},
                     actor="llm")
    en = await log.append("segment.end",
                          {"task_id": task_id, "start_seq": st.seq},
                          actor="agent")
    return st.seq, en.seq


def _ranges(cands: list) -> list:
    return [list(c.range) for c in cands]


# ===================================================================== 触发判定
class TestShouldCompact:
    async def _log_with_turns(self, n: int, *, text: str = None) -> SessionLog:
        """n 轮人机对话(无段包装;纯触发判定用)。每轮 token 确定(中文 1/字)。"""
        log = SessionLog(sid=SID)
        await _bootstrap(log)
        body = text or ("问" + "题" * 20)       # ~30 token/轮(含结构开销)
        for i in range(n):
            await log.append("user.message", {"content": f"{i}-{body}"},
                             actor="user")
            await log.append("llm.response",
                             {"model": "m", "finish_reason": "stop",
                              "content": f"答{i}-{body}"}, actor="llm")
        return log

    async def test_window_ratio_not_met(self):
        """条件①不满足:窗口 64k、历史很小 → 不触发(即使轮数已 ≥10)。"""
        log = await self._log_with_turns(12)
        comp = Compactor()                       # 默认 64k / 0.75 / 10 轮
        assert comp.should_compact(mk_ctx(log)) is False

    async def test_turns_gate_boundary_ninth_vs_tenth(self):
        """条件②边界:窗口极窄(历史 ≥75%)时,距上次压缩 9 轮不触发、第 10 轮触发。"""
        log = SessionLog(sid=SID)
        await _bootstrap(log)
        # 先落一条压缩声明(触发②以"上次压缩之后"计轮;区间仅为满足载荷校验)
        await log.append("context.compacted", {"ranges": [[1, 1]],
                                                "summary": "早期声明"},
                         actor="system")
        # 9 轮新对话:每轮 ~60 token × 9 = 540 ≥ 0.75×400 = 300(条件①已满足)
        body = "问" + "题" * 25                  # ~30 token/条,一轮 ~60
        for i in range(9):
            await log.append("user.message", {"content": f"{i}-{body}"},
                             actor="user")
            await log.append("llm.response",
                             {"model": "m", "finish_reason": "stop",
                              "content": f"答{i}-{body}"}, actor="llm")
        comp = Compactor(window_tokens=400)
        assert comp.should_compact(mk_ctx(log, window_tokens=400)) is False, \
            "新增 9 轮 < 10 → ②不满足,不得触发"
        await _user_llm(log, f"9-{body}")       # 第 10 轮
        assert comp.should_compact(mk_ctx(log, window_tokens=400)) is True, \
            "新增 ≥10 轮且窗口 ≥75% → 双条件齐,触发"

    async def test_turns_counted_since_last_compact(self):
        """轮计数锚点 = 最新 context.compacted 之后;更早的轮不计入。"""
        log = SessionLog(sid=SID)
        await _bootstrap(log)
        body = "问" + "题" * 20                  # ~50 token/轮(双消息)
        for i in range(8):                       # 压缩前的 8 轮(不计入)
            await log.append("user.message", {"content": f"前{i}-{body}"},
                             actor="user")
            await log.append("llm.response",
                             {"model": "m", "finish_reason": "stop",
                              "content": f"答{i}-{body}"}, actor="llm")
        await log.append("context.compacted", {"ranges": [[2, 6]],
                                                "summary": "折叠前段"},
                         actor="system")
        comp = Compactor(window_tokens=600)
        # 压缩后仅 1 轮 → ②不满足(即使历史 token 已 ≥75% 窗口)
        await _user_llm(log, f"后1-{body}")
        assert comp.should_compact(mk_ctx(log, window_tokens=600)) is False
        for i in range(2, 11):                   # 补到 10 轮
            await log.append("user.message", {"content": f"后{i}-{body}"},
                             actor="user")
            await log.append("llm.response",
                             {"model": "m", "finish_reason": "stop",
                              "content": f"答{i}-{body}"}, actor="llm")
        assert comp.should_compact(mk_ctx(log, window_tokens=600)) is True

    def test_should_compact_never_raises(self):
        """纯判定不抛:窗口缺失/空会话 → False 而非异常。"""
        log = SessionLog(sid=SID)
        from types import SimpleNamespace
        empty_ctx = SimpleNamespace(session=log, scope=None, agent=None)
        comp = Compactor()
        assert comp.should_compact(empty_ctx) is False


# ===================================================================== 候选选择
class TestCandidates:
    async def _seg_session(self, n_turns: int, *, marker_at: int = None,
                           extras: list = None,
                           open_at: int = None) -> SessionLog:
        """n_turns 个完整任务段;marker_at 段插入 extras;open_at 段不闭合。"""
        log = SessionLog(sid=SID)
        await _bootstrap(log)
        for i in range(1, n_turns + 1):
            tid = f"t{i}"
            if open_at == i:
                await log.append("segment.start", {"task_id": tid},
                                 actor="agent")
                await _user_llm(log, f"任务{i}")
                continue                          # 悬空段:无 end
            if marker_at == i:
                await _seg_with_extra(log, tid, f"任务{i}", extras or [])
            else:
                await _seg_turn(log, tid, f"任务{i}:整理归档文件")
        return log

    async def test_anchor_keeps_recent_12_rounds(self):
        """保留锚:15 轮 → 仅前 3 段(整段 ≤ 倒数第 12 轮起点)进入候选,升序。"""
        log = await self._seg_session(15)
        comp = Compactor()
        cands = comp.candidates(mk_ctx(log))
        assert len(cands) == 3
        assert [c.task_id for c in cands] == ["t1", "t2", "t3"]
        assert cands == sorted(cands, key=lambda c: c.range[0])
        for c in cands:                           # 整段不切半:4 事件齐全
            assert c.n_events == 4
            assert c.start_ts
        assert cands[0].range == (2, 5)

    async def test_insufficient_rounds_no_folding_space(self):
        """总轮 ≤12 → 保留锚为 0 → 无候选(折叠空间不存在)。"""
        log = await self._seg_session(12)
        comp = Compactor()
        assert comp.candidates(mk_ctx(log)) == []

    async def test_open_segment_excluded(self):
        """未闭合任务段(有 start 无 end)不完整 → 不进候选。"""
        log = await self._seg_session(15, open_at=2)
        comp = Compactor()
        ids = [c.task_id for c in comp.candidates(mk_ctx(log))]
        assert ids == ["t1", "t3"], \
            "悬空段整体不可折叠(段不完整,整段纪律),其余不受影响"

    # ---------- 不可折叠集(每类独占一段;命中 → 整段剔除)
    @pytest.mark.parametrize("extras,excluded", [
        # ③ guard.rejected 单调审计链(永不进摘要)
        ([("guard.rejected", {"tool": "fs.delete_file", "guard_id": "g-danger",
                               "reason": "critical"})], True),
        # ② 待审批 approval.requested 无裁决
        ([("approval.requested", {"tool": "fs.overwrite",
                                  "args_summary": "覆写文件", "risk": "high"})],
         True),
        # ②' 待审批随后有裁决 → 段可折叠(已闭环)
        ([("approval.requested", {"tool": "fs.overwrite",
                                  "args_summary": "覆写文件", "risk": "high"}),
          ("approval.granted", {"approval_id": "@first", "by": "human"})],
         False),
        # ① 未完成 plan:proposed 无任何终态
        ([("plan.proposed", {"goal": "整理归档",
                             "steps": [{"action": "扫描", "tool": "fs.list",
                                        "expected": "文件列表"}]})], True),
        # ①' plan 完整闭环(proposed → approved → done)→ 可折叠
        ([("plan.proposed", {"goal": "整理归档",
                             "steps": [{"action": "扫描", "tool": "fs.list",
                                        "expected": "文件列表"}]}),
          ("plan.approved", {"plan_id": "@first", "who": "user"}),
          ("plan.done", {"plan_id": "@first"})], False),
        # ①'' 已批准未 done(执行中/结果未知)→ 仍不可折叠
        ([("plan.proposed", {"goal": "整理归档",
                             "steps": [{"action": "扫描", "tool": "fs.list",
                                        "expected": "文件列表"}]}),
          ("plan.approved", {"plan_id": "@first", "who": "user"})], True),
        # ① goal active(未 done/abandoned)
        ([("goal.created", {"goal_id": "g-1", "desc": "归档"}),
          ("goal.updated", {"goal_id": "g-1", "status": "active",
                            "progress": 0.5})], True),
        # ①' goal 已 done → 可折叠
        ([("goal.created", {"goal_id": "g-1", "desc": "归档"}),
          ("goal.completed", {"goal_id": "g-1", "status": "done"})], False),
        # ① todo 含未 done 项(末次更新口径)
        ([("todo.updated", {"task_id": "t-m", "todos": [
            {"id": 1, "text": "清理临时文件", "done": False},
            {"id": 2, "text": "写报告", "done": True}]})], True),
        # ①' todo 全 done → 可折叠
        ([("todo.updated", {"task_id": "t-m", "todos": [
            {"id": 1, "text": "清理临时文件", "done": True}]})], False),
    ])
    async def test_protected_sets(self, extras, excluded):
        """不可折叠集逐类验证:命中段整体剔除,其余候选不受影响。"""
        log = await self._seg_session(15, marker_at=2, extras=extras)
        comp = Compactor()
        ids = [c.task_id for c in comp.candidates(mk_ctx(log))]
        if excluded:
            assert ids == ["t1", "t3"], f"含 {extras[0][0]} 的段必须剔除: {ids}"
        else:
            assert ids == ["t1", "t2", "t3"], \
                f"已闭环内容应可折叠: {ids}"

    async def test_extra_beyond_anchor_never_candidate(self):
        """锚之后(最近 12 轮)的段即使含折叠性内容也不得折叠(原文保留)。"""
        log = await self._seg_session(15)
        comp = Compactor()
        cands = comp.candidates(mk_ctx(log))
        last = max(c.range[1] for c in cands)
        # 倒数 12 轮起点 seq = 第 4 段 user.message;锚后无任何候选区间触及
        um = [e.seq for e in log.events_after() if e.type == "user.message"]
        anchor = um[-12]
        assert all(c.range[1] <= anchor for c in cands)
        assert last < um[-1]


# ===================================================================== 摘要与降级
class TestSummarize:
    async def _candidate_session(self, n_total: int = 13,
                                  first_kwargs: dict = None,
                                  text="任务{0}") -> tuple[SessionLog,
                                                          tuple[int, int]]:
        """n_total 个人机任务段(首段可用 first_kwargs 定制,如工具密集);
        返回 (log, 首段范围)——首段是唯一折叠候选。"""
        log = SessionLog(sid=SID)
        await _bootstrap(log)
        kw = dict(first_kwargs or {})
        st, en = await _seg_turn(log, "t1", text.format(1), **kw)
        for i in range(2, n_total + 1):
            await _seg_turn(log, f"t{i}", text.format(i))
        return log, (st, en)

    async def test_llm_summarize_injected_with_budget_and_prompt(self):
        """注入式摘要:budget=400 传入;prompt 含用户意图与关键事实、工具原始
        结果截断(尾部不进 prompt)。"""
        tail = "X" * 500                          # 工具原始结果超长尾部
        log, rng = await self._candidate_session(
            first_kwargs={"tool_summary": f"扫描完成,找到 42 个文件{tail}"},
            text="任务{0}:按主题归档文件")
        llm = FakeLLM(text="归档任务完成:42 个文件已按主题分类。")
        comp = Compactor()
        report = await comp.compact(mk_ctx(log, llm=llm))
        assert not report.noop
        assert report.folds[0].range == rng
        assert llm.calls and llm.calls[0][1] == 400     # 摘要预算
        prompt = llm.calls[0][0]
        assert "按主题归档文件" in prompt          # 用户意图入摘要输入
        assert "42 个文件" in prompt               # 截断内的工具结果仍在
        assert tail not in prompt                  # 超 200 字尾部被截断
        # LLM 摘要原样入声明(非降级)
        assert report.folds and not report.folds[0].degraded

    async def test_retry_once_then_success(self):
        """摘要失败重试 1 次:首次 PyHError → 重试成功 → 正常摘要(degraded=False)。"""
        log, _ = await self._candidate_session()
        llm = FakeLLM(text="重试后成功摘要。", failures=1)
        comp = Compactor()
        report = await comp.compact(mk_ctx(log, llm=llm))
        assert len(llm.calls) == 2                 # 单候选:失败 1 + 重试成功 1
        assert report.folds and not report.folds[0].degraded
        assert "重试后成功摘要" in report.folds[0].summary

    async def test_llm_failure_degrades_rule_summary(self):
        """摘要两次失败 → 规则降级:只留决策文本、零 tool.* 原文、≤600 字、
        degraded=True、压缩仍完成不阻塞。"""
        secret = "工具原始结果机密内容" + "S" * 400
        log = SessionLog(sid=SID)
        await _bootstrap(log)
        st = await log.append("segment.start", {"task_id": "t1"}, actor="agent")
        await log.append("user.message",
                         {"content": "把 secret.txt 归档到 backup"}, actor="user")
        await _tool_round(log, secret)
        await log.append("llm.response", {"model": "m", "finish_reason": "stop",
                                          "content": "已归档完成"}, actor="llm")
        await log.append("segment.end", {"task_id": "t1", "start_seq": st.seq},
                         actor="agent")
        for i in range(2, 14):                     # 首段(含工具轮)+ 12 轮保留
            await _seg_turn(log, f"t{i}", f"后续{i}")
        llm = FakeLLM(failures=99)                 # 永不成功
        comp = Compactor()
        report = await comp.compact(mk_ctx(log, llm=llm))
        assert report.folds and report.folds[0].degraded is True
        assert "secret.txt" in report.folds[0].summary       # 用户决策文本保留
        assert "S" * 100 not in report.folds[0].summary      # 工具原文不泄
        assert len(report.folds[0].summary) <= 600
        n_degraded = sum(1 for f in report.folds if f.degraded)
        assert report.reason == f"window:{n_degraded} degraded"
        # 不阻塞:声明仍强同步落盘
        assert log.stats()["event_count"] > 0

    async def test_llm_unavailable_degrades(self):
        """ctx.llm.summarize 未装配 → LLM-399 → 规则降级,不抛给调用方。"""
        log, _ = await self._candidate_session(text="任务{0}:整理归档文件")
        comp = Compactor()
        report = await comp.compact(mk_ctx(log, llm=None))   # 无 llm 注入
        assert report.folds and report.folds[0].degraded is True
        assert "任务1" in report.folds[0].summary

    async def test_empty_llm_summary_degrades(self):
        """LLM 返回空摘要 → 视为失败 → 规则降级。"""
        log, _ = await self._candidate_session()
        comp = Compactor()
        report = await comp.compact(mk_ctx(log, llm=FakeLLM(empty=True)))
        assert report.folds[0].degraded is True


# ===================================================================== 主流程
class TestCompact:
    async def _tool_session(self) -> tuple[SessionLog, list]:
        """2 个工具密集(长原文)早期段 + 12 个近期段;返回 (log, 候选段区间)。"""
        log = SessionLog(sid=SID)
        await _bootstrap(log)
        segs = []
        for i in range(1, 3):                      # 早期工具密集段(可折叠)
            st, en = await _seg_turn(log, f"old{i}", f"早期任务{i}:整理文件",
                                     tool_summary="文件清单详情 " + "D" * 900)
            segs.append((st, en))
        for i in range(3, 15):                     # 近期保留段
            await _seg_turn(log, f"new{i}", f"近期任务{i}")
        return log, segs

    async def test_compact_appends_declaration_only_append_only(self):
        """只追加铁律(INV-01):原文事件零改动,仅尾追一条 context.compacted;
        折叠区间原事件仍可读、内容逐字节不变;seq 继续 max+1。"""
        log, segs = await self._tool_session()
        comp = Compactor()
        cands = comp.candidates(mk_ctx(log))
        assert [c.range for c in cands] == segs   # 恰 2 个候选(整段)
        before_events = list(log.events_after())
        before_msgs = log.derive_history()
        before_max = log.stats()["seq"]
        report = await comp.compact(mk_ctx(log, llm=FakeLLM()))
        assert not report.noop
        # ① 只追加:事件数 +1,前 N 条逐事件完全一致(信封内容不可变)
        events = list(log.events_after())
        assert log.stats()["event_count"] == before_max + 1
        assert events[:-1] == before_events
        assert events[-1].seq == before_max + 1   # max+1 续写,空洞不回填
        # ② 声明事件形态(EVENT-SCHEMA §3.5.5)
        env = events[-1]
        assert env.type == "context.compacted" and env.actor == "system"
        assert env.payload["ranges"] == [list(s) for s in segs]   # 整段闭区间
        assert "该任务段已完成" in env.payload["summary"]
        assert env.payload["tokens_before"] > env.payload["tokens_after"] > 0
        # ③ 折叠区间原文仍在(压缩但不撒谎):get 可读、派生仍含原文
        first_seq = segs[0][0]
        assert log.get(first_seq + 1).payload["content"].startswith("早期任务1")
        assert len(before_msgs) < len(log.derive_history())   # 摘要消息追加
        # ④ 遮蔽标记入索引(stats 只读出口)
        assert env.payload["ranges"] == log.stats()["folded_ranges"]
        # ⑤ 报告口径与声明一致
        assert report.tokens_before == env.payload["tokens_before"]
        assert report.tokens_after == env.payload["tokens_after"]
        assert report.kept_recent_rounds == 12
        assert report.folds[0].range == tuple(segs[0])

    async def test_compacted_summary_enters_derived_history(self):
        """派生层遮蔽:摘要 system 消息进入派生历史(置于声明位置),原文仍在。"""
        log, segs = await self._tool_session()
        comp = Compactor()
        await comp.compact(mk_ctx(log, llm=FakeLLM(
            text="归档任务完成:文件按主题归类,共 42 项。")))
        msgs = log.derive_history()
        last = msgs[-1]
        assert last["role"] == "system"
        lo0, hi0 = segs[0]
        # 摘要消息含首段折叠区间 + 摘要文本(多段合并于同一 ranges 列表)
        assert last["content"].startswith(f"[已压缩 [[{lo0}, {hi0}]")
        assert "归档任务完成" in last["content"]
        # 原文可展开核对:早期 user.message 仍在派生流(压缩但不撒谎)
        assert any(m["role"] == "user"
                   and "早期任务1" in m["content"] for m in msgs)

    async def test_noop_when_no_candidates(self):
        """无可折叠候选 → noop 报告,不写任何事件(事件数不变)。"""
        log = SessionLog(sid=SID)
        await _bootstrap(log)
        for i in range(1, 13):                    # 仅 12 轮:保留锚 0
            await _seg_turn(log, f"t{i}", f"任务{i}")
        n = log.stats()["event_count"]
        comp = Compactor()
        report = await comp.compact(mk_ctx(log, llm=FakeLLM()))
        assert report.noop is True
        assert report.reason == "no-candidates"
        assert report.folds == []
        assert log.stats()["event_count"] == n, "noop 不得写事件"

    async def test_already_folded_segments_never_refolded(self):
        """防重复:同一折叠区间的段二次压缩被剔除 → 第二次 noop。"""
        log, _ = await self._tool_session()
        comp = Compactor()
        r1 = await comp.compact(mk_ctx(log, llm=FakeLLM()))
        assert not r1.noop and len(r1.folds) == 2
        n = log.stats()["event_count"]
        r2 = await comp.compact(mk_ctx(log, llm=FakeLLM()))
        assert r2.noop is True                    # 全部候选已被折叠
        assert log.stats()["event_count"] == n, "重复压缩不得再写声明"

    async def test_busy_boundary_rejected(self):
        """会话 running 中(agent busy)直调压缩 → BUSY,零副作用。"""
        log = SessionLog(sid=SID)
        await _bootstrap(log)
        for i in range(1, 14):
            await _seg_turn(log, f"t{i}", f"任务{i}")
        n = log.stats()["event_count"]
        comp = Compactor()
        ctx = mk_ctx(log, llm=FakeLLM(), agent_state="busy")
        with pytest.raises(PyHError) as ei:
            await comp.compact(ctx)
        assert ei.value.code == "BUSY"
        assert log.stats()["event_count"] == n, "BUSY 拒写零副作用"

    async def test_multi_fold_single_declaration(self):
        """多候选一次压缩:ranges 含全部折叠段(升序、不重叠),单条声明。"""
        log = SessionLog(sid=SID)
        await _bootstrap(log)
        segs = []
        for i in range(1, 4):                      # 3 个简单早期段
            st, en = await _seg_turn(log, f"t{i}", f"任务{i}")
            segs.append((st, en))
        for i in range(4, 16):                     # +12 近期 → 15 轮
            await _seg_turn(log, f"t{i}", f"任务{i}")
        comp = Compactor()
        cands = comp.candidates(mk_ctx(log))
        assert [c.range for c in cands] == segs
        report = await comp.compact(mk_ctx(log, llm=FakeLLM()))
        assert len(report.folds) == 3
        events = list(log.events_after())
        assert events[-1].payload["ranges"] == [list(s) for s in segs]
        assert report.folds[1].range == segs[1]


# ===================================================================== run_if_needed
class TestRunIfNeeded:
    async def test_busy_returns_none(self):
        """非空闲边界:run_if_needed 不压不炸(返回 None,零副作用)。"""
        log = SessionLog(sid=SID)
        await _bootstrap(log)
        for i in range(1, 15):
            await _seg_turn(log, f"t{i}", f"任务{i}")
        n = log.stats()["event_count"]
        comp = Compactor()
        ctx = mk_ctx(log, llm=FakeLLM(), agent_state="busy", bus=BusRecorder())
        assert await comp.run_if_needed(ctx) is None
        assert log.stats()["event_count"] == n

    async def test_conditions_unmet_returns_none(self):
        """双条件未齐:不压不写事件,不发信号。"""
        log = SessionLog(sid=SID)
        await _bootstrap(log)
        for i in range(1, 15):
            await _seg_turn(log, f"t{i}", f"任务{i}")
        bus = BusRecorder()
        comp = Compactor()                        # 窗口大 → 条件①不满足
        ctx = mk_ctx(log, llm=FakeLLM(), window_tokens=65536, bus=bus)
        assert await comp.run_if_needed(ctx) is None
        assert bus.events == []

    async def test_hit_compacts_and_signals(self):
        """空闲 + 双条件齐 → 压缩执行并广播 sysprompt.compacted。"""
        log = SessionLog(sid=SID)
        await _bootstrap(log)
        body = "问" + "题" * 20                   # 每轮 ~50 token × 14 ≈ 700
        for i in range(1, 15):
            await _seg_turn(log, f"t{i}", f"{body}")
        bus = BusRecorder()
        comp = Compactor()                        # 窄窗 150:历史 ≥75%
        ctx = mk_ctx(log, llm=FakeLLM(), window_tokens=150, bus=bus)
        report = await comp.run_if_needed(ctx)
        assert report is not None and not report.noop
        types = [t for t, _ in bus.events]
        assert "sysprompt.compacted" in types
        sig_payload = [p for t, p in bus.events
                       if t == "sysprompt.compacted"][0]
        assert sig_payload["tokens_after"] < sig_payload["tokens_before"]


# ===================================================================== 前缀规划/渲染
class TestPrefixAndRender:
    async def test_kv_prefix_plan_before_any_compact(self):
        """从未压缩:无稳定前缀锚 → cacheable=False。"""
        log = SessionLog(sid=SID)
        await _bootstrap(log)
        for i in range(1, 14):
            await _seg_turn(log, f"t{i}", f"任务{i}")
        plan = Compactor().kv_prefix_plan(mk_ctx(log))
        assert isinstance(plan, PrefixPlan)
        assert plan.cacheable is False and plan.stable_upto_seq == 0

    async def test_kv_prefix_plan_after_compact(self):
        """压缩后:稳定前缀终点 = 最新 compacted seq,块 = 折叠区间。"""
        log = SessionLog(sid=SID)
        await _bootstrap(log)
        for i in range(1, 14):
            await _seg_turn(log, f"t{i}", f"任务{i}")
        comp = Compactor()
        await comp.compact(mk_ctx(log, llm=FakeLLM()))
        events = list(log.events_after())
        comp_seq = events[-1].seq
        plan = comp.kv_prefix_plan(mk_ctx(log))
        assert plan.cacheable is True
        assert plan.stable_upto_seq == comp_seq
        assert plan.blocks == [[2, 5]]
        assert plan.prefix_tokens > 0

    async def test_render_compacted_message(self):
        """渲染钩子:ranges 紧凑形态 + 摘要(EVENT-SCHEMA §4.2 摘要代区间文本)。"""
        log = SessionLog(sid=SID)
        await _bootstrap(log)
        await _user_llm(log, "你好")
        await _user_llm(log, "归档")
        env = await log.append("context.compacted",
                               {"ranges": [[2, 3], [4, 5]],
                                "summary": "两段均已归档完成",
                                "tokens_before": 100, "tokens_after": 30},
                               actor="system")
        text = render_compacted_message(env)
        assert "[2-3],[4-5]" in text and "归档完成" in text

    def test_estimate_tokens_deterministic(self):
        """token 估算:同输入同输出;消息列表含结构开销;文本/消息混合可算。"""
        a = estimate_tokens("中文测试归档")
        b = estimate_tokens("中文测试归档")
        assert a == b > 0
        msgs = [{"role": "user", "content": "你好"}, {"role": "assistant",
                                                      "content": "收到"}]
        assert estimate_tokens(msgs) > estimate_tokens(msgs[0])
        assert estimate_tokens(None) == 0
