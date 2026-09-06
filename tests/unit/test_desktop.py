"""pyharness/desktop.py 单测 — 契约:specs/desktop.py.md(F065 桌面壳,面试主演示)+ ADR-011(/api
错误体复用错误码)+ ADR-012(窗口 = 只读事件投影)+ EVENT-SCHEMA §1.3(轨迹只取持久事件)。

覆盖面(任务要求:时间线逻辑测透 + GUI/服务器全 mock):
    轨迹时间线派生(纯函数,核心):render_timeline_node 六类核心节点逐字段断言——
        user/message/tool/guard/approval(四态)/budget/recovery + created/finished +
        fallback info;参数脱敏(INV-09)、400 截断、payload 缺失不抛
    derive_timeline:seq 序、kinds 过滤(含未知 kind 空结果)、空输入
    API handler(直调,无 HTTP):session_timeline 全量/after_seq 增量/kinds 查询/非法
        after_seq EVT-100/未知会话 EVT-106;messages/event_detail/budget(disabled 与
        enabled+warn 80%)/create_message(门面写:真 append + FakeQueue submit)/审批桥
        (decide approve/deny、坏 decision EVT-100、未知 aid APR-503)/pending 聚合;
        只读端点不写日志(窗口 = 只读投影)
    API 路由(TestClient 真 ASGI,mock 掉服务器/窗口):/api/sessions 空目录、
        timeline 200 全 kind 序列、查询参数、404 错误体 {code,advice}(ADR-011)、
        审批 POST 200/400
    EventStreamHub fan-out:会话过滤/瞬时全广播/游标只进不退/close_all 哨兵+dead
    SSE 流:真实 stream_sse 生成器收事件帧(含 event: 名)、心跳注释行、close 净退注销
    端口与后台服务:pick_free_port 回环随机口、wait_until_listening 探测、
        run_uvicorn 单 worker 禁 reload 生命周期(mock uvicorn)
    优雅停服:stopping/fan-out 摘除/hub close_all/flush 兜底/should_exit/webview.destroy,
        幂等(双击关闭只走一次)
    DesktopBridge js_api:跨线程投递成功 JSON、PyHError → code 体、超时 → BUSY、
        loop 未跑 → BUSY、在途登记清理
    main/run_desktop 生命周期:全 mock 窗口与服务器,成功 0 / 启动失败 1 路径

不真开 GUI/不真起 uvicorn(无显示环境挂);pywebview 惰性导入面(_load_webview)全 mock。
装配风格与 test_acp/test_cli 同:真实 SessionLog(纯内存 FakeStore 重放)+ EventBus。
"""
import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import pyharness.desktop as d
from pyharness.bus import EventBus
from pyharness.core.session import open_session
from pyharness.errors import PyHError, raise_code
from pyharness.events import Envelope

SID = "s-desk-001"          # 会话 id(Envelope session_id min_length=8)
SID2 = "s-desk-002"
TS = "2026-09-07T08:00:00.000000Z"


# ===================================================================== 工具
def ev(seq, type_, actor="system", payload=None, sid=SID) -> Envelope:
    """直接构造信封(绕过 append 校验链;渲染/回放测试用,payload 取真源同形)。"""
    return Envelope.model_validate({
        "seq": seq, "ts": TS, "type": type_, "session_id": sid,
        "actor": actor, "payload": dict(payload or {}),
    })


def _sample_events(sid=SID):
    """典型会话事件流(seq 1-7):六类核心节点 + created 全齐(时间线/API 测试基座)。"""
    return [
        ev(1, "session.created", "system", {"title": "面试演示"}, sid),
        ev(2, "user.message", "user", {"content": "帮我写个爬虫脚本"}, sid),
        ev(3, "agent.message", "agent", {"content": "好的,我先规划一下步骤"}, sid),
        ev(4, "tool.call", "tool", {
            "name": "web_fetch",
            "args": {"url": "https://example.com", "api_key": "sk-test"},
            "raw_args": {"url": "https://example.com", "api_key": "sk-test"}}, sid),
        ev(5, "guard.rejected", "system",
           {"guard": "net-guard", "reason": "目标域名不在白名单", "policy_ref": "POL-07"}, sid),
        ev(6, "approval.requested", "tool",
           {"tool": "fs_write", "args_summary": "写入 /tmp/out.txt",
            "risk": "high", "ttl_ms": 120000}, sid),
        ev(7, "llm.usage", "llm",
           {"model": "deepseek-chat", "in_tokens": 1200, "out_tokens": 340,
            "cost_est": 0.004}, sid),
    ]


class _FakeStore:
    """内存重放真源替身:open_session 只读 replay();flush 记录供强同步写断言。"""

    def __init__(self, events):
        self._events = list(events)
        self.flushed = []

    def replay(self):
        return list(self._events)

    async def flush(self, seq):
        self.flushed.append(seq)


async def _async_app(events=(), **kw) -> d.DesktopApp:
    """异步测试用装配(在调用方事件循环内 open_session;GUI/服务器全部不启)。"""
    events = list(events)
    if "session" not in kw:
        kw["session"] = await open_session(SID, _FakeStore(events))
    if "bus" not in kw:
        kw["bus"] = EventBus()
    return d.DesktopApp(SimpleNamespace(**kw))


def _app(events=(), **kw) -> d.DesktopApp:
    """同步测试用装配(asyncio.run 包一层;无运行中循环处使用)。"""
    return asyncio.run(_async_app(events, **kw))


def _sub_count(bus: EventBus) -> int:
    """总线当前订阅数(精确 + 通配;读内部结构仅测试用)。"""
    return sum(len(v) for v in bus._by_type.values()) + len(bus._wild)


# ===================================================================== 时间线
# 六类核心节点 + 生命周期/兜底:type/payload → 期望字段(逐字段断言,渲染字段齐全)
_RENDER_CASES = [
    ("user.message", {"content": "你好"}, "user", "用户", "info", "user"),
    ("agent.message", {"content": "好的"}, "message", "AI 回复", "success", "agent"),
    ("tool.call", {"name": "fs_read", "args": {"path": "/a.txt"},
                   "raw_args": {"path": "/a.txt"}}, "tool", "调用 fs_read", "info", "tool"),
    ("guard.rejected", {"guard": "fs-guard", "reason": "越权路径",
                        "policy_ref": "POL-03"}, "guard", "guard 拦截", "danger", "system"),
    ("approval.requested", {"tool": "fs_write", "args_summary": "w x",
                            "risk": "high", "ttl_ms": 120000},
     "approval", "审批请求", "info", "tool"),
    ("approval.granted", {"approval_id": 6, "by": "desktop"},
     "approval", "审批通过", "success", "tool"),
    ("approval.denied", {"approval_id": 6, "by": "desktop"},
     "approval", "审批拒绝", "danger", "tool"),
    ("approval.timeout", {"approval_id": 6}, "approval", "审批超时(自动拒绝)", "warn", "tool"),
    ("llm.usage", {"model": "deepseek-chat", "in_tokens": 10, "out_tokens": 5,
                   "cost_est": 0.001}, "budget", "用量", "warn", "llm"),
    ("session.recovered", {"fixed": ["tail-truncated"], "lost": 0,
                           "backup": "s-x.jsonl.bak"},
     "recovery", "崩溃修复声明", "warn", "system"),
    ("session.created", {"title": ""}, "recovery", "session.created", "info", "system"),
    ("session.finished", {"reason": "complete"}, "recovery",
     "session.finished", "info", "system"),
    ("tool.result", {"name": "fs_read", "summary": "ok"}, "info", "tool.result", "info", "tool"),
]


@pytest.mark.parametrize("type_,payload,kind,title,severity,actor", _RENDER_CASES,
                         ids=[c[0] for c in _RENDER_CASES])
def test_render_timeline_node_core_kinds(type_, payload, kind, title, severity, actor):
    """六类核心节点 + created/finished + fallback:kind/title/severity/actor 逐字段断言。"""
    node = d.render_timeline_node(ev(9, type_, actor, payload))
    assert node.kind == kind
    assert node.title == title
    assert node.severity == severity
    assert node.actor == actor
    assert node.seq == 9
    assert node.ts == TS


def test_render_user_message_text_truncated_to_400():
    """用户原文截断 400(防刷屏);detail.text 保留截断标记。"""
    content = "长" * 500
    node = d.render_timeline_node(ev(2, "user.message", "user", {"content": content}))
    assert node.detail["text"].startswith("长" * 399)
    assert node.detail["text"].endswith("…")
    assert len(node.detail["text"]) == 400


def test_render_tool_call_redacts_sensitive_args():
    """tool.call 参数脱敏(INV-09):api_key/secret 打码,明文不进轨迹面板。"""
    node = d.render_timeline_node(ev(4, "tool.call", "tool", {
        "name": "web_fetch",
        "args": {"url": "https://x.com", "api_key": "sk-live-abc"},
        "raw_args": {"url": "https://x.com", "api_key": "sk-live-abc"}}))
    assert node.detail["args"]["api_key"] == "[redacted]"
    assert node.detail["args"]["url"] == "https://x.com"
    assert node.detail["raw_args_masked"]["api_key"] == "[redacted]"
    assert "sk-live-abc" not in json.dumps(node.detail, ensure_ascii=False)


def test_render_guard_rejected_carries_policy_ref():
    """guard 拦截:guard/policy_ref 保留(高亮 + 策略引用,前端可跳转)。"""
    node = d.render_timeline_node(ev(5, "guard.rejected", "system",
                                     {"guard_id": "g1", "reason": "r",
                                      "policy_ref": "POL-07"}))
    assert node.kind == "guard" and node.severity == "danger"
    assert node.detail == {"guard": "g1", "reason": "r", "policy_ref": "POL-07"}


def test_render_approval_requested_detail():
    node = d.render_timeline_node(ev(6, "approval.requested", "tool",
                                     {"tool": "fs_write", "args_summary": "w",
                                      "risk": "high", "ttl_ms": 120000}))
    assert node.detail["tool"] == "fs_write"
    assert node.detail["risk"] == "high" and node.detail["ttl_ms"] == 120000


def test_render_missing_payload_never_raises():
    """payload 字段缺失/空 dict 按 get 兜底——渲染永不因单事件异常中断整条时间线。"""
    for type_ in ("user.message", "agent.message", "tool.call", "guard.rejected",
                  "approval.requested", "llm.usage", "session.recovered"):
        node = d.render_timeline_node(ev(3, type_, "system", {}))
        assert isinstance(node.detail, dict)


def test_derive_timeline_ordered_all_kinds():
    """时间线 = 日志投影:seq 升序派生全量,瞬时事件不入日志天然缺席。"""
    nodes = d.derive_timeline(_sample_events())
    assert [n.seq for n in nodes] == [1, 2, 3, 4, 5, 6, 7]
    assert [n.kind for n in nodes] == [
        "recovery", "user", "message", "tool", "guard", "approval", "budget"]


def test_derive_timeline_kinds_filter():
    """kinds 逗号过滤(空 = 全部);未知 kind → 空结果,不抛。"""
    events = _sample_events()
    got = d.derive_timeline(events, kinds="tool,guard")
    assert [n.kind for n in got] == ["tool", "guard"]
    assert [n.seq for n in got] == [4, 5]
    assert d.derive_timeline(events, kinds="bogus") == []
    assert len(d.derive_timeline(events, kinds="")) == 7
    assert d.derive_timeline([]) == []


def test_timeline_node_kind_registry():
    """kind 全集合与数据结构表一致(含 fallback info;偏离 11 钉死)。"""
    for k in ("user", "thought", "message", "tool", "guard", "approval",
              "budget", "recovery", "error", "info"):
        assert k in d.TIMELINE_KINDS
    # 渲染产出的 kind 全部落在注册集合内(前端 switch 可穷尽)
    for node in d.derive_timeline(_sample_events()):
        assert node.kind in d.TIMELINE_KINDS
        assert node.severity in ("info", "warn", "danger", "success")


# ===================================================================== API handler(直调)
async def test_timeline_api_full_and_incremental():
    """session_timeline:全量(0)/增量(after_seq 排他下界)/kinds 查询,只读派生。"""
    app = await _async_app(_sample_events())
    full = await app.session_timeline(SID)
    assert full["sid"] == SID and full["base_seq"] == 0
    assert [n["kind"] for n in full["nodes"]] == [
        "recovery", "user", "message", "tool", "guard", "approval", "budget"]
    inc = await app.session_timeline(SID, after_seq=4)
    assert inc["base_seq"] == 4
    # events_after 排他下界:seq > after_seq → 5,6,7(断连续拉语义)
    assert [n["seq"] for n in inc["nodes"]] == [5, 6, 7]
    assert [n["kind"] for n in inc["nodes"]] == ["guard", "approval", "budget"]
    fil = await app.session_timeline(SID, after_seq=5, kinds="approval,budget")
    assert [n["seq"] for n in fil["nodes"]] == [6, 7]
    assert [n["kind"] for n in fil["nodes"]] == ["approval", "budget"]
    got = await app.session_timeline(SID, kinds="tool,guard")
    assert [n["seq"] for n in got["nodes"]] == [4, 5]


async def test_timeline_api_validation_and_unknown_session():
    """非法 after_seq → EVT-100;未知会话 → EVT-106(守卫先建会话)。"""
    app = await _async_app(_sample_events())
    with pytest.raises(PyHError) as e1:
        await app.session_timeline(SID, after_seq="3")   # 非 int(桥误用路径)
    assert e1.value.code == "EVT-100"
    with pytest.raises(PyHError) as e2:
        await app.session_timeline("s-ghost-001")
    assert e2.value.code == "EVT-106"
    with pytest.raises(PyHError) as e3:
        await app.session_timeline("")                   # 空 sid → EVT-100
    assert e3.value.code == "EVT-100"


async def test_read_endpoints_are_read_only():
    """窗口 = 只读投影:全部只读端点跑一遍,日志零写入(ADR-012 不变式)。"""
    app = await _async_app(_sample_events())
    log_ = await app._require_session(SID)
    before = log_.stats()["event_count"]
    await app.session_timeline(SID)
    await app.session_messages(SID)
    await app.budget_dashboard(SID)
    await app.event_detail(SID, 4)
    await app.pending_approvals()
    await app.list_sessions()
    assert log_.stats()["event_count"] == before


async def test_messages_endpoint_derived_from_log():
    """对话流 = derive_messages 派生(聊天气泡源):roles/content/to_seq。"""
    events = [ev(1, "session.created", "system", {}, SID),
              ev(2, "user.message", "user", {"content": "你好"}, SID),
              ev(3, "llm.response", "llm", {"content": "Hi!"}, SID)]
    app = await _async_app(events)
    out = await app.session_messages(SID)
    assert out["sid"] == SID and out["to_seq"] == 3
    assert [(m["role"], m["content"]) for m in out["messages"]] == [
        ("user", "你好"), ("assistant", "Hi!")]


async def test_event_detail_returns_raw_envelope():
    """单事件详情(事件溯源点开看现场):字段齐全;空洞/越界 → EVT-100。"""
    app = await _async_app(_sample_events())
    detail = await app.event_detail(SID, 5)
    assert detail["event"]["type"] == "guard.rejected"
    assert detail["event"]["payload"]["policy_ref"] == "POL-07"
    assert detail["event"]["seq"] == 5
    with pytest.raises(PyHError) as e:
        await app.event_detail(SID, 999)
    assert e.value.code == "EVT-100"


async def test_create_message_appends_and_submits():
    """提问 = 门面写无特权路径:user.message 强同步落真源 + task_queue.submit。"""
    app = await _async_app(_sample_events())

    class _FakeQueue:
        def __init__(self):
            self.calls = []

        async def submit(self, intent, *, meta=None):
            self.calls.append((intent, meta))
            return "t-99"

    fq = _FakeQueue()
    app._queues[SID] = fq                       # 注入队列替身(不启真泵)
    out = await app.create_message(SID, {"text": "  再写一个 demo  "})
    assert out == {"task_id": "t-99", "user_seq": 8}
    assert fq.calls == [("再写一个 demo", {"channel": "desktop", "session_id": SID})]
    env8 = (await app._require_session(SID)).get(8)
    assert env8.type == "user.message" and env8.origin == "desktop"
    # 空文本 EVT-100 拒写(页面永不直写:校验先于 append)
    with pytest.raises(PyHError) as e:
        await app.create_message(SID, {"text": "   "})
    assert e.value.code == "EVT-100"


async def test_budget_dashboard_disabled_without_gate():
    """无预算闸装配 → disabled 标志(前端隐藏面板),不炸不伪造。"""
    app = await _async_app(_sample_events())
    out = await app.budget_dashboard(SID)
    assert out["disabled"] is True
    assert out["used_in_tokens"] == 1200 and out["used_out_tokens"] == 340


async def test_budget_dashboard_usage_aggregation_and_warn():
    """预算仪表盘 = llm.usage 折叠(与 /cost 同源);ratio ≥ 80% → warned(阈值 0.8)。"""
    events = [ev(1, "session.created", "system", {}, SID),
              ev(2, "llm.usage", "llm", {"model": "m", "in_tokens": 100,
                                         "out_tokens": 20, "cost_est": 0.001}, SID),
              ev(3, "llm.usage", "llm", {"model": "m", "in_tokens": 200,
                                         "out_tokens": 80, "cost_est": 0.002}, SID)]

    class _BudgetGate:
        def snapshot(self, sid):
            return SimpleNamespace(limit_cny=1.0, used_cny=0.9)

    app = await _async_app(events, budget=_BudgetGate())
    out = await app.budget_dashboard(SID)
    assert out["disabled"] is False
    assert out["used_in_tokens"] == 300 and out["used_out_tokens"] == 100
    assert out["limit_cny"] == 1.0 and out["used_cny"] == 0.9
    assert out["ratio"] == 0.9 and out["warned"] is True
    assert len(out["recent"]) == 2
    # 未超 80% → 不 warn
    app2 = await _async_app(events, budget=SimpleNamespace(
        snapshot=lambda sid: SimpleNamespace(limit_cny=1.0, used_cny=0.5)))
    assert app2.budget_dashboard is not None
    out2 = await app2.budget_dashboard(SID)
    assert out2["warned"] is False and out2["ratio"] == 0.5


class _FakeApproval:
    """审批 provider 替身(桥薄壳测试;approve/deny 同步面,与真实 ApprovalProvider 同形)。"""

    def __init__(self, pending=None):
        self.calls = []
        self._pending = dict(pending or {})

    def approve(self, aid, *, by):
        self.calls.append(("approve", int(aid), by))
        self._pending.pop(int(aid), None)

    def deny(self, aid, *, by):
        self.calls.append(("deny", int(aid), by))
        self._pending.pop(int(aid), None)


@pytest.mark.parametrize("decision", ["approve", "deny"])
async def test_approval_bridge_decide(decision):
    """审批弹窗裁决桥:by='desktop' 透传 provider;批准/拒绝两路。"""
    fake = _FakeApproval(pending={42: object()})
    app = await _async_app(_sample_events(), approval=fake)
    out = await app.decide_approval(42, {"decision": decision})
    assert out == {"ok": True, "approval_id": 42}
    assert fake.calls == [(decision, 42, "desktop")]
    assert 42 not in fake._pending


async def test_approval_bridge_bad_decision_and_unknown_aid():
    """decision 非法 → EVT-100(400)拒写零副作用;未知 aid 且无 provider → APR-503。"""
    fake = _FakeApproval(pending={42: object()})
    app = await _async_app(_sample_events(), approval=fake)
    with pytest.raises(PyHError) as e1:
        await app.decide_approval(42, {"decision": "maybe"})
    assert e1.value.code == "EVT-100"
    assert fake.calls == []                        # 拒写:零副作用
    # 无 ctx.approval 且无 per-session provider 持有该 aid → APR-503(防跨会话重放)
    app2 = await _async_app(_sample_events())
    with pytest.raises(PyHError) as e2:
        await app2.decide_approval(7, {"decision": "deny"})
    assert e2.value.code == "APR-503"


async def test_pending_approvals_aggregates_sorted():
    """未决审批清单:跨 provider 聚合、去重、approval_id 升序、字段齐全。"""
    def req(tool, risk):
        return SimpleNamespace(tool=tool, args_summary="摘要", danger=risk,
                               ttl_ms=120000, session_id=SID)
    fake = _FakeApproval(pending={6: req("fs_write", "high"),
                                  3: req("net_post", "medium")})
    app = await _async_app(_sample_events(), approval=fake)
    out = await app.pending_approvals()
    assert out["count"] == 2
    assert [p["approval_id"] for p in out["pending"]] == [3, 6]
    assert out["pending"][0]["tool"] == "net_post"
    assert out["pending"][1]["session_id"] == SID


# ===================================================================== API 路由(TestClient 真 ASGI)
def test_route_timeline_http_and_error_body():
    """真实路由:/timeline 200 全 kind 序列 + 查询参数;kinds/after_seq 生效。"""
    app = _app(_sample_events())
    with TestClient(app.api) as client:
        r = client.get(f"/api/sessions/{SID}/timeline")
        assert r.status_code == 200
        body = r.json()
        assert body["sid"] == SID and body["base_seq"] == 0
        assert [n["kind"] for n in body["nodes"]] == [
            "recovery", "user", "message", "tool", "guard", "approval", "budget"]
        r2 = client.get(f"/api/sessions/{SID}/timeline",
                        params={"after_seq": 5, "kinds": "approval,budget"})
        assert [n["seq"] for n in r2.json()["nodes"]] == [6, 7]
        # ADR-011:未知会话 → 404 + {code, advice}(远端只回码+建议)
        r3 = client.get("/api/sessions/s-ghost-001/timeline")
        assert r3.status_code == 404
        b = r3.json()
        assert b["code"] == "EVT-106" and b["advice"]


def test_route_approval_http():
    """审批弹窗裁决 POST:200 ok;非法 decision → 400 code 体。"""
    fake = _FakeApproval(pending={42: object()})
    app = _app(_sample_events(), approval=fake)
    with TestClient(app.api) as client:
        r = client.post("/api/approvals/42", json={"decision": "approve"})
        assert r.status_code == 200 and r.json() == {"ok": True, "approval_id": 42}
        assert fake.calls == [("approve", 42, "desktop")]
        r2 = client.post("/api/approvals/42", json={"decision": "maybe"})
        assert r2.status_code == 400 and r2.json()["code"] == "EVT-100"


def test_route_list_sessions_empty_dir(tmp_path: Path):
    """自装配会话管理器:空目录 → 会话列表空(不炸不造文件)。"""
    app = d.DesktopApp(SimpleNamespace(
        session=None, bus=None, storage=SimpleNamespace(sessions_dir=tmp_path)))
    with TestClient(app.api) as client:
        r = client.get("/api/sessions")
        assert r.status_code == 200
        assert r.json() == {"sessions": [], "count": 0}
    assert list(tmp_path.iterdir()) == []           # 只读:零落盘


# ===================================================================== EventStreamHub
async def test_hub_fanout_session_filter_and_cursor():
    """SSE fan-out:持久事件按会话过滤投递、游标只进不退;瞬时事件全客户端广播。"""
    hub = d.EventStreamHub()
    me_a = d.StreamClient(sid=SID)
    me_b = d.StreamClient(sid=SID2)
    me_all = d.StreamClient(sid="")
    async with hub.register(me_a), hub.register(me_b), hub.register(me_all):
        env5 = ev(5, "user.message", "user", {"content": "hi"}, SID)
        await hub.forward("user.message", env5)
        assert me_b.queue.empty()                       # 会话过滤
        type_a, text_a = me_a.queue.get_nowait()
        assert type_a == "user.message"
        data = json.loads(text_a)
        assert data["seq"] == 5 and data["session_id"] == SID
        type_all, _ = me_all.queue.get_nowait()
        assert type_all == "user.message"
        assert me_a.last_seq == 5 and me_b.last_seq == 0 and me_all.last_seq == 5
        # 瞬时 llm.chunk(裸 dict,无会话作用域)不推进游标、全客户端广播(流式碎片刻画)
        await hub.forward("llm.chunk", {"text": "流式"})
        for c in (me_a, me_b, me_all):
            t, txt = c.queue.get_nowait()
            assert t == "llm.chunk"
            body = json.loads(txt)
            assert body["type"] == "llm.chunk"
            assert body["payload"]["text"] == "流式"
        assert me_a.last_seq == 5                        # 瞬时事件无 seq,游标不动
    assert hub.clients == set()                          # 上下文退出即注销


async def test_hub_close_all_sentinel():
    """优雅停服:hub.close_all 置 dead + None 哨兵 + 清空客户端(SSE 生成器净退)。"""
    hub = d.EventStreamHub()
    c = d.StreamClient(sid="")
    async with hub.register(c):
        hub.close_all()
        assert c.dead is True
        assert c.queue.get_nowait() is None
        assert hub.clients == set()
    # 幂等:重复 close 不炸
    hub.close_all()


# ===================================================================== SSE 流
class _FakeRequest:
    """SSE 请求替身:is_disconnected 可控(测试期恒 False,由 close 哨兵收尾)。"""

    def __init__(self):
        self.disconnected = False

    async def is_disconnected(self):
        return self.disconnected


async def test_sse_stream_delivers_events_then_clean_exit():
    """真实 stream_sse:连接注册游标 → 事件帧(带 event: 名)→ close_all 净退注销。"""
    app = await _async_app(_sample_events())
    resp = await app.stream_sse(_FakeRequest(), sid=SID, after_seq=0)
    collected = []

    async def pump():
        async for chunk in resp.body_iterator:
            collected.append(chunk)

    task = asyncio.create_task(pump())
    # 等生成器进入并注册游标(轮询防竞态)
    for _ in range(100):
        if len(app.hub.clients) == 1:
            break
        await asyncio.sleep(0.01)
    assert len(app.hub.clients) == 1
    await app.hub.forward("user.message",
                          ev(8, "user.message", "user", {"content": "继续"}, SID))
    await app.hub.forward("agent.message",
                          ev(9, "agent.message", "agent", {"content": "好"}, SID))
    for _ in range(100):
        if len(collected) == 2:
            break
        await asyncio.sleep(0.01)
    assert collected[0].startswith("event: user.message\n")
    assert json.loads(collected[0].split("data: ", 1)[1])["seq"] == 8
    assert collected[1].startswith("event: agent.message\n")
    app.hub.close_all()                                  # 哨兵 → 生成器净退
    await asyncio.wait_for(task, timeout=3)
    assert app.hub.clients == set()                      # 注销完成


async def test_sse_stream_heartbeat_ping(monkeypatch):
    """SSE 心跳注释行保活(15s 超时无事件 → ': ping';测速压到 0.05s)。"""
    monkeypatch.setattr(d, "SSE_HEARTBEAT_S", 0.05)
    app = await _async_app()
    resp = await app.stream_sse(_FakeRequest(), sid="")
    gen = resp.body_iterator
    async with asyncio.timeout(2):
        first = await gen.__anext__()
    assert first == ": ping\n\n"
    app.hub.close_all()                                  # 哨兵 → 循环退出
    async with asyncio.timeout(2):
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()
    assert app.hub.clients == set()


async def test_sse_stream_session_filter():
    """SSE 会话过滤:非目标会话事件不推给该流(前端按会话订阅)。"""
    app = await _async_app(_sample_events())
    resp = await app.stream_sse(_FakeRequest(), sid=SID2, after_seq=0)
    collected = []

    async def pump():
        async for chunk in resp.body_iterator:
            collected.append(chunk)

    task = asyncio.create_task(pump())
    for _ in range(100):
        if len(app.hub.clients) == 1:
            break
        await asyncio.sleep(0.01)
    await app.hub.forward("user.message", ev(8, "user.message", "user",
                                             {"content": "x"}, SID))
    await asyncio.sleep(0.05)
    assert collected == []                               # SID 事件不进 SID2 流
    app.hub.close_all()
    await asyncio.wait_for(task, timeout=3)


# ===================================================================== 端口与后台服务
def test_pick_free_port_loopback_random():
    """随机端口:127.0.0.1 回环系统分配口(仅回环,F065 边界)。"""
    p1 = d.pick_free_port()
    assert isinstance(p1, int) and 1024 <= p1 <= 65535
    assert d.HOST == "127.0.0.1"                         # 禁绑非回环


def test_wait_until_listening_probe(monkeypatch):
    """就绪探测:可连即 True;超时 False(开窗前不弹空窗,调用方退 1)。"""
    monkeypatch.setattr(d, "_probe_port", lambda port: True)
    assert d.wait_until_listening(9999, timeout=0.3) is True
    monkeypatch.setattr(d, "_probe_port", lambda port: False)
    assert d.wait_until_listening(9999, timeout=0.15) is False


def test_run_uvicorn_single_worker_no_reload(monkeypatch):
    """uvicorn 后台线程序:单 worker、禁 reload、127.0.0.1;serve 返回即收尾复位。"""
    captured = {}

    class _FakeServer:
        def __init__(self, config):
            self.config = config
            self.should_exit = False

        async def serve(self, sockets=None):
            captured["served"] = True
            self.should_exit = True                       # 模拟退出信号

    class _FakeConfig:
        def __init__(self, api, **kw):
            captured["kw"] = kw

    fake_uvicorn = SimpleNamespace(Config=_FakeConfig, Server=_FakeServer)
    monkeypatch.setattr(d, "uvicorn", fake_uvicorn)
    app = SimpleNamespace(api=object(), server=None, loop=None)
    d.run_uvicorn(app, 43210)
    assert captured["served"] is True
    assert captured["kw"] == {"host": "127.0.0.1", "port": 43210,
                              "log_level": "warning", "workers": 1}
    assert app.server is None and app.loop is None        # 线程收尾复位


async def test_wait_listening_async_timeout_raises(monkeypatch):
    """异步就绪探测超时 → CYC-999(启动取消,不弹空窗)。"""
    monkeypatch.setattr(d, "_probe_port", lambda port: False)
    app = await _async_app()
    with pytest.raises(PyHError) as e:
        await d.wait_listening_async(app, 9999, timeout=0.15)
    assert e.value.code == "CYC-999"


# ===================================================================== 优雅停服
class _HubSpy:
    """hub 替身:close_all 计数(幂等断言用)。"""

    def __init__(self):
        self.close_calls = 0
        self.clients = set()

    def close_all(self):
        self.close_calls += 1


class _FakePersist:
    def __init__(self):
        self.flushed = 0

    def flush_sync(self):
        self.flushed += 1


class _FakeWvDestroy:
    def __init__(self):
        self.destroyed = 0

    def destroy(self):
        self.destroyed += 1


def test_shutdown_gracefully_sequence_and_idempotent(monkeypatch):
    """关窗 = 优雅停服:stopping → fan-out 摘除 → hub close → flush 兜底 →
    should_exit → webview.destroy;幂等(双击关闭只走一次)。"""
    bus = EventBus()
    persist = _FakePersist()
    app = _app(_sample_events(), bus=bus, persistence=persist)
    before = _sub_count(bus)
    assert before > 0                                     # desktop 全事件订阅在位
    spy_hub = _HubSpy()
    app.hub = spy_hub
    server = SimpleNamespace(should_exit=False)
    app.server = server
    fake_wv = _FakeWvDestroy()
    monkeypatch.setattr(d, "_load_webview", lambda: fake_wv)

    app.shutdown_gracefully()
    app.shutdown_gracefully()                             # 第二次 = 幂等空转
    assert app.stopping.is_set() and app._graceful_done is True
    assert spy_hub.close_calls == 1
    assert server.should_exit is True
    assert persist.flushed == 1
    assert fake_wv.destroyed == 1
    assert _sub_count(bus) == 0                           # 事件 fan-out 全部摘除


def test_request_shutdown_sets_flag_then_graceful_stops():
    """closing 事件入口 request_shutdown 置位;真正停服在 webview.start 返回后。"""
    app = _app(_sample_events(), bus=None)
    app.request_shutdown()
    assert app.stopping.is_set()
    assert app._graceful_done is False                    # 停服未跑(等 start 返回)


def test_shutdown_graceful_tolerates_missing_facades():
    """无 bus/persistence/server/webview(单测裸装配)也干净退出,不抛跨边界异常。"""
    app = d.DesktopApp(SimpleNamespace(session=None, bus=None))
    app.shutdown_gracefully()                             # 不炸即过
    assert app.stopping.is_set()
    app.shutdown_gracefully()                             # 幂等


# ===================================================================== DesktopBridge(js_api)
class _LoopThread:
    """后台事件循环线程(webview 线程 → 引擎 loop 的 run_coroutine_threadsafe 等价面)。"""

    def __enter__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        return self.loop

    def __exit__(self, *exc):
        loop = self.loop

        async def _cancel_all():
            for t in asyncio.all_tasks(loop):
                t.cancel()

        try:
            fut = asyncio.run_coroutine_threadsafe(_cancel_all(), loop)
            fut.result(timeout=2)
        except Exception:                                 # noqa: BLE001
            pass
        loop.call_soon_threadsafe(loop.stop)
        self.thread.join(timeout=2)
        loop.close()


class _BridgeTarget:
    """桥的目标替身:只含 js_api 会调用的 async 方法(测试桥壳,不含引擎业务)。"""

    def __init__(self):
        self.loop = None
        self.calls = []

    async def create_message(self, sid, body):
        self.calls.append(("submit", sid, body))
        return {"task_id": "t-1", "user_seq": 2}

    async def decide_approval(self, aid, body):
        self.calls.append(("decide", aid, body))
        if aid == 13 and body.get("decision") == "deny":
            raise_code("APR-503", approval_id=aid, hint="已裁决/未知 id")
        return {"ok": True, "approval_id": aid}

    async def session_timeline(self, sid, after_seq=0):
        return {"sid": sid, "base_seq": after_seq, "nodes": []}

    async def list_sessions(self):
        return {"sessions": [], "count": 0}

    async def stall(self):
        await asyncio.sleep(30)
        return "never"


def _bridge(bridge, name, *args):
    return json.loads(getattr(bridge, name)(*args))


def test_bridge_methods_roundtrip_json():
    """js_api 薄壳:webview 线程方法 → 引擎 loop 投递,返回 JSON 字符串。"""
    with _LoopThread() as loop:
        target = _BridgeTarget()
        target.loop = loop
        bridge = d.DesktopBridge(target)
        out = _bridge(bridge, "submit", SID, "写个 demo")
        assert out == {"task_id": "t-1", "user_seq": 2}
        assert target.calls == [("submit", SID, {"text": "写个 demo"})]
        out2 = _bridge(bridge, "approve", 42, "approve")
        assert out2 == {"ok": True, "approval_id": 42}
        out3 = _bridge(bridge, "approve", 13, "deny")
        assert out3["code"] == "APR-503" and "advice" in out3      # 错误 = code 体
        out4 = _bridge(bridge, "replay_frame", SID, "3")
        assert out4["base_seq"] == 3
        out5 = _bridge(bridge, "list_sessions")
        assert out5 == {"sessions": [], "count": 0}
        assert bridge._pending_calls == {}                          # 在途登记清零


def test_bridge_engine_error_returns_code_body():
    """引擎 PyHError 经桥 → {code, advice}(ADR-011;远端不吐 ctx 敏感值)。"""
    with _LoopThread() as loop:
        target = _BridgeTarget()
        target.loop = loop
        bridge = d.DesktopBridge(target)
        out = _bridge(bridge, "approve", 13, "deny")
        assert out == {"code": "APR-503",
                       "advice": "已裁决/未知 id"}     # ctx hint 入 advice(可行动提示)
        assert "approval_id" not in out                # 其余 ctx 不进远端体


def test_bridge_timeout_returns_busy(monkeypatch):
    """引擎忙超时(60s 缺省)→ 本地 BUSY 错误体,不悬挂(测速压到 0.05s)。"""
    monkeypatch.setattr(d, "BRIDGE_TIMEOUT_S", 0.05)
    with _LoopThread() as loop:
        target = _BridgeTarget()
        target.loop = loop
        bridge = d.DesktopBridge(target)
        out = json.loads(bridge._run(target.stall()))
        assert out["code"] == "BUSY"


def test_bridge_without_running_loop_returns_busy():
    """loop 未运行(服务未就绪)→ 立即 BUSY,不抛跨线程异常。"""
    target = _BridgeTarget()
    target.loop = None
    bridge = d.DesktopBridge(target)
    out = json.loads(bridge.list_sessions())
    assert out["code"] == "BUSY"


def test_bridge_approve_bad_aid():
    """approve 传非数字 aid → EVT-100 本地体(js 侧字符串兜底)。"""
    with _LoopThread() as loop:
        target = _BridgeTarget()
        target.loop = loop
        bridge = d.DesktopBridge(target)
        out = json.loads(bridge.approve("abc", "approve"))
        assert out["code"] == "EVT-100"
        assert target.calls == []                                   # 拒写零副作用


# ===================================================================== 入口生命周期(全 mock)
class _FakeWindow:
    """pywebview 窗口替身:events.closing 挂关窗回调(列表 += 兼容)。"""

    def __init__(self):
        self.events = SimpleNamespace(closing=[])


class _FakeWv:
    def __init__(self):
        self.windows = []
        self.started = False

    def create_window(self, title, url, js_api=None, width=0, height=0):
        self.windows.append((title, url, js_api, width, height))
        return _FakeWindow()

    def start(self, debug=False):
        self.started = True


_UNSET = object()


def _stub_entry(monkeypatch, *, wv=_UNSET, listening=True, ctx=None):
    """main/run_desktop 依赖全替身:装配 ctx、uvicorn 线程、就绪探测、webview。

    wv 缺省 = 可用假窗(成功路径);wv=None = WebView2/环境不可用 → 退 1 路径。
    """
    if ctx is None:
        ctx = SimpleNamespace(bus=EventBus())
    recorded = {}

    monkeypatch.setattr(d, "_bootstrap_desktop", lambda cfg_path: ctx)
    monkeypatch.setattr(d, "run_uvicorn", lambda app, port: recorded.setdefault(
        "ran", (app, port)))
    monkeypatch.setattr(d, "wait_until_listening", lambda port, timeout=15: listening)
    if wv is _UNSET:                                   # 缺省:可用假窗
        wv = _FakeWv()
    if wv is None:
        monkeypatch.setattr(d, "_load_webview", lambda: None)
    else:
        monkeypatch.setattr(d, "_load_webview", lambda: wv)
    return recorded, wv


def test_main_success_path_returns_0(monkeypatch):
    """入口全链路(mock 窗口/服务器):装配 → 随机口 → uvicorn 线程 → 开窗 →
    webview.start 阻塞返回 → 优雅停服 → 0。"""
    recorded, fake_wv = _stub_entry(monkeypatch)
    assert d.main() == 0
    assert fake_wv.started is True
    assert len(fake_wv.windows) == 1
    title, url, bridge, width, height = fake_wv.windows[0]
    assert title == "PyHarness" and width == 1280 and height == 800
    assert url.startswith("http://127.0.0.1:")
    assert isinstance(bridge, d.DesktopBridge)
    app, port = recorded["ran"]
    assert app.url == url and app.stopping.is_set()        # 关窗后优雅停服已跑


def test_main_bootstrap_failure_returns_1(monkeypatch, capsys):
    """装配失败(CFG-601)→ stderr 提示退 1,不弹窗。"""

    def _boom(cfg_path):
        raise PyHError("CFG-601", ctx={"advice": "配置非法字段:llm.model"})

    monkeypatch.setattr(d, "_bootstrap_desktop", _boom)
    assert d.main() == 1
    assert "CFG-601" in capsys.readouterr().err


def test_main_wait_timeout_no_empty_window(monkeypatch):
    """就绪超时 → 退 1 不弹空窗(uvicorn 起不来不白开)。"""
    recorded, fake_wv = _stub_entry(monkeypatch, listening=False)
    assert d.main() == 1
    assert fake_wv.windows == [] and fake_wv.started is False


def test_main_webview_missing_returns_1(monkeypatch, capsys):
    """pywebview/WebView2 不可用 → stderr 提示退 1(优雅停服已跑)。"""
    recorded, _ = _stub_entry(monkeypatch, wv=None)
    assert d.main() == 1
    assert "WebView2" in capsys.readouterr().err
    assert recorded["ran"][0].stopping.is_set()


def test_main_webview_start_unexpected_returns_1(monkeypatch):
    """webview.start 未预期异常 → 日志 + 优雅停服 + 退 1。"""

    class _BoomWv:
        def create_window(self, *a, **kw):
            return _FakeWindow()

        def start(self, debug=False):
            raise RuntimeError("GUI broken")

    recorded, _ = _stub_entry(monkeypatch, wv=_BoomWv())
    assert d.main() == 1
    assert recorded["ran"][0].stopping.is_set()


async def test_run_desktop_cli_bridge_returns_0(monkeypatch):
    """CLI desktop 子命令桥:复用已装配 ctx,同一生命周期,返回 0。"""
    ctx = SimpleNamespace(bus=EventBus())
    recorded = {}
    monkeypatch.setattr(d, "run_uvicorn", lambda app, port: recorded.setdefault(
        "ran", (app, port)))
    monkeypatch.setattr(d, "_load_webview", lambda: _FakeWv())
    monkeypatch.setattr(d, "wait_listening_async", _null_async)
    fake_wv_holder = {}

    def _fake_create(wv, app, bridge):
        fake_wv_holder["wv"] = wv

    monkeypatch.setattr(d, "_create_window", _fake_create)
    assert await d.run_desktop(ctx) == 0
    assert recorded["ran"][0].stopping.is_set()
    assert fake_wv_holder["wv"].started is True        # 窗口 start 后优雅停服
    assert isinstance(fake_wv_holder["wv"], _FakeWv)


async def _null_async(app, port, timeout=15):
    """wait_listening_async 替身(直接通过)。"""
    return None
