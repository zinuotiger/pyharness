"""events 模块单测 — 契约:specs/events.py.md + EVENT-SCHEMA.md §3

覆盖面:词表完整(64 名全量核对:57 词表 + §7 词表外扩展 llm.retry/plan.done/
plan.aborted/schedule.registered/updated/removed/blocked/missed)、Envelope 三要素
(seq/type/ts UTC)、seq 连续性(EVT-101)、非法输入抛码(EVT-100/102/106)、payload
二次强校验、SeqState 分配器、check_seq_gap 空洞自检。
说明:词表完整性断言必须位于本文件最前(见 test_vocab_full_64),其后注册类
测试会向运行期注册表追加合成类型(只增不改,无注销 API)。
"""
import re

import pytest
from pydantic import ValidationError

from pyharness import events as EV
from pyharness.events import (Envelope, EVENT_TYPES, SeqState, check_seq_gap,
                              make_envelope, payload_model_for,
                              register_event_type, validate_envelope,
                              validate_payload)
from pyharness.errors import PyHError

# EVENT-SCHEMA §3 词汇总表 57 名 + §7 词表外扩展 7 名(llm.retry 同款先例:
# plan.done/plan.aborted 由 specs/plan_mode.py.md F046 登记;schedule.registered/
# updated/removed/blocked/missed 由 specs/schedule.py.md F048 登记;A-E 分组,权威顺序)
EXPECTED_ALL = (
    # A 会话(4)
    "session.created", "session.renamed", "session.finished", "session.recovered",
    # B 用户输入侧(5)
    "user.message", "user.message_edited", "user.feedback",
    "user.attachment.image", "user.command",
    # C 模型侧(7,含 llm.retry)
    "llm.request", "llm.response", "llm.usage", "llm.error", "agent.message",
    "llm.chunk", "llm.retry",
    # D 工具侧:guard 与审批链(9)
    "tool.call", "guard.evaluated", "guard.rejected",
    "approval.requested", "approval.granted", "approval.denied", "approval.timeout",
    "tool.result", "tool.error",
    # E 编排与任务段(23)
    "task.enqueued", "task.started", "task.completed", "task.failed",
    "segment.start", "segment.end",
    "plan.proposed", "plan.approved", "plan.rejected", "plan.exec.step",
    "plan.done", "plan.aborted",          # §7 词表外扩展(F046 终态事件)
    "goal.created", "goal.updated", "goal.completed",
    "schedule.trigger", "schedule.registered", "schedule.updated",
    "schedule.removed", "schedule.blocked", "schedule.missed",  # §7 扩展(F048)
    "job.started", "job.completed", "job.failed",
    "subagent.spawned", "subagent.joined", "subagent.failed",
    "workflow.step", "queue.suspended", "queue.resumed",
    # E 系统侧(11)
    "fork.created", "context.compacted",
    "plugin.installed", "plugin.uninstalled", "bus.backpressure",
    "system.cancelled", "system.error", "todo.updated", "syscheck.fail",
    # F032/F014 策略与预算事件(装配后 budget.paused/scope.updated 真实落盘)
    "scope.updated", "budget.paused",
    # #38 反问(user.question/answer 对)
    "user.question", "user.answer",
    # F073 技能系统装载审计
    "skill.used", "skill.installed", "skill.removed", "skill.rollback",
    # 配置热更审计(瞬时)
    "config.updated",
)
assert len(EXPECTED_ALL) == 73, "测试词表清单必须恰为 73 名"

TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T.*Z$")


def _new_state(session_id: str = "s-abc12345", max_seq: int = 0) -> SeqState:
    """开闸会话:rebuild 后 open 且 next = max_seq + 1。"""
    ss = SeqState()
    ss.rebuild(session_id, max_seq)
    return ss


# =====================================================================
# 词表完整
# =====================================================================
def test_vocab_full_64():
    """词表 73 名(72 核心 + config.updated 瞬时审计)。"""
    assert len(EVENT_TYPES) == 73
    assert set(EVENT_TYPES) == set(EXPECTED_ALL)


def test_strong_sync_families_present():
    """强同步三类事件族在列:user.message / guard.rejected / approval.*"""
    for t in ("user.message", "guard.rejected", "approval.requested",
              "approval.granted", "approval.denied", "approval.timeout",
              "session.finished", "session.recovered", "segment.start",
              "fork.created", "context.compacted"):
        assert t in EVENT_TYPES, f"强同步事件 {t} 缺失"
        assert t in EV.SYNC_TYPES, f"{t} 应属 SYNC_TYPES"


def test_transient_types_constants():
    """瞬时事件:llm.chunk 注册且标记 transient;registry.updated 仅总线无 payload 模型。"""
    assert EV.TRANSIENT_TYPES == frozenset(
        {"llm.chunk", "registry.updated", "config.updated"})
    assert payload_model_for("llm.chunk") is not None
    assert EV.is_transient("llm.chunk") is True
    assert "registry.updated" not in EVENT_TYPES      # 无 §3 字段表,不入词表
    assert EV.is_transient("user.message") is False


def test_actors_six_values():
    """actor 六枚举锁定。"""
    assert EV.ACTORS == ("user", "agent", "llm", "tool", "system", "plugin")


def test_payload_model_for_unknown_returns_none():
    """未注册类型 → None(由校验链按 EVT-102 拒)。"""
    assert payload_model_for("ghost.event") is None
    assert EV.is_registered("ghost.event") is False


def test_register_duplicate_rejected():
    """词表冻结:重复注册(任何类型)→ EVT-102,注册表不被污染。"""
    from pyharness.events.vocab import EVENT_TYPES as V
    before = dict(V)
    with pytest.raises(PyHError) as ei:
        register_event_type("session.created", dict)
    assert ei.value.code == "EVT-102"
    assert dict(V) == before, "EVT-102 后注册表不得变化"


def test_register_plugin_type_and_validate():
    """插件运行时注册 plugin.<id>.<name>:注册→查询→payload 校验→重复拒。"""
    # 注意:本测试向运行期注册表追加一个合成类型(只增不改,无注销 API)
    type_ = "plugin:echo.ping"
    with pytest.raises(PyHError) as ei:
        validate_payload(type_, {})
    assert ei.value.code == "EVT-102"
    register_event_type(type_, payload_model_for("agent.message"), transient=False)
    assert EV.is_registered(type_)
    assert payload_model_for(type_) is payload_model_for("agent.message")
    with pytest.raises(PyHError) as ei:
        register_event_type(type_, dict)
    assert ei.value.code == "EVT-102"


# =====================================================================
# Envelope 三要素与信封字段校验
# =====================================================================
def _good_raw(**overrides) -> dict:
    raw = {
        "seq": 1, "ts": "2026-09-06T07:00:00.123456Z", "type": "user.message",
        "session_id": "s-abc12345", "actor": "user",
        "payload": {"content": "你好"},
    }
    raw.update(overrides)
    return raw


def test_envelope_three_elements():
    """Envelope 必带 seq/type/ts 三要素;ts 为 UTC(正则 + Z 结尾)。"""
    env = Envelope(**_good_raw())
    assert env.seq == 1
    assert env.type == "user.message"
    assert TS_RE.match(env.ts), f"ts 非 ISO8601 UTC:{env.ts}"
    assert env.ts.endswith("Z")


def test_envelope_extra_field_rejected():
    """extra='forbid':多余字段 → 校验链 EVT-100。"""
    raw = _good_raw(extra_field=1)
    with pytest.raises(PyHError) as ei:
        validate_envelope(raw, _new_state())
    assert ei.value.code == "EVT-100"
    assert "extra_field" in ei.value.ctx["detail"]


def test_envelope_bad_ts_rejected():
    """ts 非 UTC(无 Z / 非 ISO8601) → EVT-100。"""
    for bad in ("2026-09-06T07:00:00+00:00", "20260906T070000Z",
                "not-a-date", ""):
        with pytest.raises(PyHError) as ei:
            validate_envelope(_good_raw(ts=bad), _new_state())
        assert ei.value.code == "EVT-100", f"ts={bad!r} 应拒"


def test_envelope_bad_actor_and_seq_zero_rejected():
    """actor 非法 / seq < 1 → EVT-100。"""
    with pytest.raises(PyHError) as ei:
        validate_envelope(_good_raw(actor="robot"), _new_state())
    assert ei.value.code == "EVT-100"
    with pytest.raises(PyHError) as ei:
        validate_envelope(_good_raw(seq=0), _new_state())
    assert ei.value.code == "EVT-100"


def test_envelope_short_session_id_rejected():
    """session_id min_length=8 强制。"""
    with pytest.raises(PyHError) as ei:
        validate_envelope(_good_raw(session_id="s-1"), _new_state())
    assert ei.value.code == "EVT-100"


def test_direct_envelope_validation_error():
    """直接构造坏信封(绕过链)也抛 pydantic ValidationError(契约:拒写)。"""
    with pytest.raises(ValidationError):
        Envelope.model_validate(_good_raw(ts="no-z"))


# =====================================================================
# payload 二次强校验(EVT-100)与类型注册(EVT-102)
# =====================================================================
def test_validate_payload_canonicalizes():
    """成功校验返回规范化 dict(含默认字段)。"""
    assert validate_payload("user.message", {"content": "hi"}) == {
        "content": "hi", "meta": None}
    assert validate_payload("user.command", {"name": "plan"}) == {
        "name": "plan", "args": ""}


def test_validate_payload_field_rules():
    """payload 字段规则违例 → EVT-100 且 detail 带字段明细。"""
    cases = [
        ("user.message", {"content": ""}, "content"),          # 空消息
        ("user.message", {"content": "x", "junk": 1}, "junk"),  # extra 字段
        ("llm.usage", {"model": "m", "in_tokens": -1, "out_tokens": 0}, "in_tokens"),
        ("session.renamed", {"new_title": ""}, "new_title"),
        ("session.renamed", {"new_title": "长" * 65}, "new_title"),
        ("approval.requested", {"tool": "x", "args_summary": "s",
                                "risk": "low"}, "risk"),
        ("guard.evaluated", {"tool": "x", "decision": "maybe"}, "decision"),
    ]
    for type_, payload, field in cases:
        with pytest.raises(PyHError) as ei:
            validate_payload(type_, payload)
        assert ei.value.code == "EVT-100", (type_, payload)
        assert field in ei.value.ctx["detail"], (type_, payload)


def test_validate_payload_unknown_type_evt102():
    """payload 校验遇未注册类型 → EVT-102(校验链第 2 步)。"""
    with pytest.raises(PyHError) as ei:
        validate_payload("ghost.event", {})
    assert ei.value.code == "EVT-102"


def test_llm_response_content_or_tools():
    """llm.response:content 与 tool_calls 至少其一,双空 → EVT-100。"""
    with pytest.raises(PyHError) as ei:
        validate_payload("llm.response",
                         {"model": "m", "finish_reason": "stop",
                          "content": "", "tool_calls": None})
    assert ei.value.code == "EVT-100"
    assert "content" in ei.value.ctx["detail"] or "tool_calls" in ei.value.ctx["detail"]
    # 任一满足即通过
    ok = validate_payload("llm.response",
                          {"model": "m", "finish_reason": "tool_calls",
                           "content": "", "tool_calls": [{"id": "c1"}]})
    assert ok["tool_calls"] == [{"id": "c1"}]


def test_compacted_ranges_overlap_rejected():
    """context.compacted.ranges:区间非法(lo>hi / 重叠)→ EVT-100。"""
    with pytest.raises(PyHError):
        validate_payload("context.compacted", {"ranges": [[5, 2]], "summary": "s"})
    with pytest.raises(PyHError):
        validate_payload("context.compacted",
                         {"ranges": [[1, 4], [4, 6]], "summary": "s"})
    ok = validate_payload("context.compacted",
                          {"ranges": [[1, 4], [6, 9]], "summary": "s"})
    assert ok["ranges"] == [[1, 4], [6, 9]]


# =====================================================================
# seq 连续性与 SeqState
# =====================================================================
def test_seqstate_rebuild_and_next():
    """rebuild(max_seq) → next=max_seq+1 并开闸;无记录 → 期望 1。"""
    ss = SeqState()
    assert ss.next_seq("s-abc12345") == 1
    ss.rebuild("s-abc12345", 10)
    assert ss.next_seq("s-abc12345") == 11
    assert "s-abc12345" in ss.open
    assert ss.next_seq("s-other999") == 1          # 另一会话独立


def test_make_envelope_created_flow():
    """make_envelope 全链:created(seq=1)→ commit → message(seq=2) 单调 +1。"""
    ss = _new_state("s-abc12345")
    env1 = make_envelope("s-abc12345", "session.created", "system",
                         {"title": "", "model": "deepseek-chat"}, seq_state=ss)
    assert env1.seq == 1
    assert env1.type == "session.created"
    assert TS_RE.match(env1.ts) and env1.ts.endswith("Z")
    ss.commit(env1)
    env2 = make_envelope("s-abc12345", "user.message", "user",
                         {"content": "你好"}, seq_state=ss)
    assert env2.seq == 2
    assert env2.actor == "user"
    ss.commit(env2)
    env3 = make_envelope("s-abc12345", "agent.message", "agent",
                         {"content": "收到"}, trace={"parent_seq": 2},
                         seq_state=ss)
    assert env3.seq == 3
    assert env3.trace == {"parent_seq": 2}


def test_seq_gap_rejected_evt101():
    """seq 跳号/重复 → EVT-101,ctx 含 expected/got。"""
    ss = _new_state("s-abc12345")           # next = 1
    with pytest.raises(PyHError) as ei:
        validate_envelope(_good_raw(seq=5), ss)
    assert ei.value.code == "EVT-101"
    assert ei.value.ctx["expected"] == 1 and ei.value.ctx["got"] == 5
    # 已提交 seq=1 后再写 seq=1 → 重复 → EVT-101
    ss.commit(Envelope(**_good_raw(seq=1)))
    with pytest.raises(PyHError) as ei:
        validate_envelope(_good_raw(seq=1, type="user.message"), ss)
    assert ei.value.code == "EVT-101"
    assert ei.value.ctx["expected"] == 2


def test_validate_envelope_returns_canonical_payload():
    """校验通过的 Envelope payload 已规范化(默认字段补齐)。"""
    env = validate_envelope(_good_raw(payload={"content": "hi"}), _new_state())
    assert env.payload == {"content": "hi", "meta": None}


# =====================================================================
# 会话状态(EVT-106)与全链
# =====================================================================
def test_evt106_before_created():
    """会话未 created(未开闸)写非 created 事件 → EVT-106。"""
    ss = SeqState()                          # 全新,无任何会话记录
    with pytest.raises(PyHError) as ei:
        make_envelope("s-abc12345", "user.message", "user",
                      {"content": "hi"}, seq_state=ss)
    assert ei.value.code == "EVT-106"
    # 但 session.created(seq=1)是 opening 事件,未开闸也允许(异常表:首事件必须是它)
    env = make_envelope("s-abc12345", "session.created", "system",
                        {"title": "", "model": "m"}, seq_state=ss)
    assert env.seq == 1 and env.type == "session.created"


def test_chain_unknown_type_evt102():
    """五步链第 2 步:type 未注册 → EVT-102(即使信封字段全对)。"""
    ss = _new_state()
    with pytest.raises(PyHError) as ei:
        validate_envelope(_good_raw(type="ghost.event"), ss)
    assert ei.value.code == "EVT-102"


def test_chain_session_open_param():
    """session_open=False 显式传入且非 created → EVT-106(状态机侧语义透传)。"""
    raw = _good_raw(seq=1)
    with pytest.raises(PyHError) as ei:
        validate_envelope(raw, _new_state(), session_open=False)
    assert ei.value.code == "EVT-106"


# =====================================================================
# check_seq_gap 空洞自检
# =====================================================================
def test_check_seq_gap_basic():
    assert check_seq_gap([1, 2, 3, 5], []) == [4]
    assert check_seq_gap([1, 2, 3, 5], [(4, 4)]) == []     # 声明覆盖 → 无洞
    assert check_seq_gap([], []) == []
    assert check_seq_gap([1, 2], [(2, 9)]) == []           # max 之上区间无关
    assert check_seq_gap([1, 3, 7], []) == [2, 4, 5, 6]
    assert check_seq_gap([1, 3, 7], [(4, 6)]) == [2]
    assert check_seq_gap([1, 3, 7], [(4, 5), (6, 6)]) == [2]


# =====================================================================
# 信封冻结与可选字段
# =====================================================================
def test_envelope_frozen_and_optionals():
    """frozen:改字段被拒;origin/task_id/trace 可省(脊柱事件)。"""
    env = Envelope.model_validate(_good_raw(origin=None, task_id=None, trace=None))
    with pytest.raises(Exception):
        env.seq = 99
    env2 = Envelope.model_validate(_good_raw(
        origin="cap:tool_fs", task_id="t-7", trace={"parent_seq": 0}))
    assert env2.origin == "cap:tool_fs"
    assert env2.task_id == "t-7"
