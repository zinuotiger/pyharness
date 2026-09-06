"""pyharness/acp.py 单测 — 契约:specs/acp.py.md(F066 权威)+ JSON-RPC 2.0 规范(标准错误码)+
ERR.md §1.4(引擎错 → error.data.code)+ ADR-011(远端只回 code+advice)+ EVENT-SCHEMA §2.1。

覆盖面(任务要求 + 规格模块级测试):
    parse_request:坏 JSON → -32700 / 信封非法 → -32600(非对象/版本/method/id 类型)
        / params 非对象 → -32602 / 合法请求字段 / 通知(id 缺失/null → id=None 不回响应)
    dispatch 方法路由:initialize/chat/approve/read_events/shutdown 命中,未知 → -32601
    initialize:能力清单(protocolVersion/methods/events.cursor/session/engine)、会话新建
        (created=True,门面 create)、会话绑定(open_session 调用 + st.session_id + 复用)、
        终态会话绑定 → EVT-104、门面缺失 → EVT-106、ctx.channel = acp:<client>
    chat:阻塞成功(reason=complete,user.message 强同步落盘 origin=acp:<id>,guard.rejected/
        approval.* 入 summary)、wait=false → queued 秒回、空 text → EVT-100、未初始化 →
        EVT-106、runner 失败 → reason=error、忙拒 st.busy → -32000(data.code=BUSY)
    approve:approve/deny 分发(by 缺省 acp:<client>)、显式 by、async 裁决面兼容、
        非法 approval_id/decision → EVT-100、真 provider 未知 id → APR-503 error.data.code
    read_events:游标推进单调、空批、limit 分页(has_more)、sessionId 显式、未初始化 EVT-106、
        事件 dict 十字段形状
    引擎错误映射:热路径码(GRD-401)无 detail、非热路径 detail 脱敏(secret 键打码/截断)、
        CYC-999 兜底(-32603 + data.code)、BUSY 特判 -32000
    serve:EOF 净退 0、空行容忍、坏行不回崩(回 -32700 后继续)、多请求串行响应序、
        通知零输出(无响应行)、shutdown 通知净退 0(无响应)、shutdown 请求响应后净退 0
    AcpBridge:channel="acp:cli" → client_id 派生 + ctx.channel 回写

装配与 test_commands 同风格:真实 SessionLog(纯内存,无总线)= append 五步校验链全真;
ctx 为 SimpleNamespace 门面替身(注入面 = session/task_queue/config/approval/channel);
TaskQueue 用替身 runner(段内事件带 task_id,同 test_task_queue 先例);asyncio_mode=auto。
"""
import io
import json
import types

import pytest

from pyharness import acp as mod
from pyharness.acp import (AcpBridge, JsonRpcRequest, cmd_approve, cmd_chat,
                           cmd_initialize, cmd_read_events, dispatch, error_resp,
                           engine_error, notify, parse_request, serve)
from pyharness.config import Settings
from pyharness.core.approval import ApprovalProvider
from pyharness.core.session import SessionLog
from pyharness.core.task_queue import TaskQueue
from pyharness.errors import PyHError, raise_code

SID = "s-acp0001"        # 会话 id(Envelope.session_id min_length=8)
SID2 = "s-acp0002"
MODEL = "mock"
CLIENT = "t-acp-01"


# ===================================================================== 替身
class _Runner:
    """任务执行器替身:段内事件带 task_id(agent.message/guard.rejected/approval.*)。

    gate: 非 None 时每次先等 gate(长任务模拟,置 st.busy 观察用);
    exc: 每次 run_for_task 抛 PyHError(→ 任务 failed,reason=error);
    events: False 则无副作用(纯空跑)。
    """

    def __init__(self, session, *, gate=None, exc=None, events=True):
        self.session = session
        self.gate = gate
        self.exc = exc
        self.events = events
        self.runs = []                       # (task_id, intent) 执行序

    async def run_for_task(self, task):
        self.runs.append((task.id, task.intent))
        if self.gate is not None:
            await self.gate.wait()
        if self.events:
            await self.session.append(
                "agent.message", {"content": f"[{task.id}] 完成", "model": MODEL},
                actor="agent", task_id=task.id)
            await self.session.append(
                "guard.rejected",
                {"tool": "write_file", "guard_id": "danger",
                 "reason": "测试拒绝"},
                actor="tool", task_id=task.id)
            await self.session.append(
                "approval.requested",
                {"tool": "exec", "args_summary": "probe"}, actor="tool",
                task_id=task.id)
            await self.session.append(
                "approval.granted", {"approval_id": 7, "by": "human"},
                actor="user", task_id=task.id)
        if self.exc is not None:
            raise self.exc


class _FakeManager:
    """会话门面替身(偏离 1 形状 a):create 建新会话,open_session 按 sid 返回注册日志。

    logs = sid → SessionLog 注册表(纯内存);open_calls/create_calls 为 spy。
    """

    def __init__(self, initial=None):
        self.logs = dict(initial or {})
        self.create_calls = 0
        self.open_calls = []

    async def create(self) -> str:
        self.create_calls += 1
        sid = f"s-acp{1000 + self.create_calls}"   # s-acp1001...
        s = SessionLog(sid=sid)
        await s.append("session.created", {"title": "", "model": MODEL},
                       actor="system")
        self.logs[sid] = s
        return sid

    async def open_session(self, sid):
        self.open_calls.append(sid)
        s = self.logs.get(sid)
        if s is None:
            raise_code("EVT-106", session_id=sid, hint="会话不存在(先 create)")
        return s


# ===================================================================== 工具
async def _boot(sid: str = SID) -> SessionLog:
    """会话引导:session.created(seq=1;此后校验链 EVT-106 开闸)。"""
    s = SessionLog(sid=sid)
    await s.append("session.created", {"title": "", "model": MODEL},
                   actor="system")
    return s


def _ctx(log: SessionLog, *, runner=None, approval=None, config=None,
         manager=None) -> types.SimpleNamespace:
    """ACP 桥 ctx 门面替身:注入面 = session/task_queue/config/approval/channel。

    manager 非 None → ctx.session = 门面(偏离 1 形状 a);否则 ctx.session = 已绑定日志
    (形状 b)。runner 缺省空跑替身;approval 缺省 None(approve 测试另行注入)。
    """
    runner = runner or _Runner(log)
    queue = TaskQueue(session=log, runner=runner)
    cfg = config if config is not None else Settings()
    return types.SimpleNamespace(
        session=manager if manager is not None else log,
        task_queue=queue, config=cfg, approval=approval, channel=None,
        repair=None)


def _req(method: str, rid=None, params=None) -> JsonRpcRequest:
    """构造入站请求(rid=None = 通知)。"""
    return JsonRpcRequest(jsonrpc="2.0", id=rid, method=method,
                          params=params or {})


def _line(method: str, rid, params=None) -> str:
    """协议行 JSON(serve 管道喂入)。"""
    body: dict = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        body["params"] = params
    return json.dumps(body, ensure_ascii=False) + "\n"


def _out_lines(sio: io.StringIO) -> list:
    """读出注入 stdout 的响应行(逐条 json 解析)。"""
    return [json.loads(x) for x in sio.getvalue().splitlines() if x.strip()]


async def _state(client: str = CLIENT) -> mod.AcpState:
    st = mod.AcpState(client_id=client)
    st.busy = False
    return st


def _types(log: SessionLog) -> list:
    return [e.type for e in log.events_after(0)]


def _by_type(log: SessionLog, type_: str) -> list:
    return [e for e in log.events_after(0) if e.type == type_]


# ================================================================ parse_request
def test_parse_bad_json_returns_32700_and_none():
    out = io.StringIO()
    got = parse_request("{not json", write=out)
    assert got is None
    (resp,) = _out_lines(out)
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] is None
    assert resp["error"]["code"] == -32700
    assert "detail" in resp["error"]["data"]


def test_parse_non_object_returns_32600():
    for bad in ('[]', '"str"', "42", "null"):
        out = io.StringIO()
        assert parse_request(bad, write=out) is None
        (resp,) = _out_lines(out)
        assert resp["error"]["code"] == -32600
        assert resp["error"]["message"] == "Invalid Request"


def test_parse_bad_envelope_returns_32600():
    cases = [  # 版本错 / 缺 method / method 非 str / id 类型非法(bool)
        '{"jsonrpc":"1.0","id":1,"method":"chat"}',
        '{"jsonrpc":"2.0","id":1}',
        '{"jsonrpc":"2.0","id":1,"method":42}',
        '{"jsonrpc":"2.0","id":true,"method":"chat"}',
    ]
    for bad in cases:
        out = io.StringIO()
        assert parse_request(bad, write=out) is None
        (resp,) = _out_lines(out)
        assert resp["error"]["code"] == -32600
        assert resp["id"] in (None, 1)


def test_parse_non_object_params_returns_32602():
    out = io.StringIO()
    assert parse_request('{"jsonrpc":"2.0","id":3,"method":"chat","params":[]}',
                         write=out) is None
    (resp,) = _out_lines(out)
    assert resp["id"] == 3
    assert resp["error"]["code"] == -32602


def test_parse_valid_request_fields():
    out = io.StringIO()
    req = parse_request(
        '{"jsonrpc":"2.0","id":7,"method":"read_events",'
        '"params":{"after_seq":3}}', write=out)
    assert req is not None
    assert req.id == 7 and req.method == "read_events"
    assert req.params == {"after_seq": 3}
    assert out.getvalue() == ""              # 合法请求零输出


def test_parse_notification_when_id_absent_or_null():
    for raw in ('{"jsonrpc":"2.0","method":"chat","params":{"text":"hi"}}',
                '{"jsonrpc":"2.0","id":null,"method":"chat"}',
                '{"jsonrpc":"2.0","method":"shutdown"}'):
        out = io.StringIO()
        req = parse_request(raw, write=out)
        assert req is not None and req.id is None
        assert out.getvalue() == ""


# ============================================================ 方法路由/错误面
async def test_dispatch_unknown_method_32601():
    st = await _state()
    resp = await dispatch(_ctx(await _boot()), st, _req("foobar", 1))
    assert resp["id"] == 1
    assert resp["error"]["code"] == -32601
    assert resp["error"]["data"]["method"] == "foobar"


async def test_dispatch_shutdown_request_result_and_flag():
    log = await _boot()
    st = await _state()
    resp = await dispatch(_ctx(log), st, _req("shutdown", 9))
    assert resp["id"] == 9
    assert resp["result"] == {"shutdown": True}
    assert st.notify_shutdown is True       # serve 回响应后净退 0


async def test_dispatch_unexpected_exception_cyc999():
    log = await _boot()

    class _BoomQueue:                       # submit 内部爆炸 → -32603 CYC-999
        async def submit(self, *a, **k):
            raise RuntimeError("炸了")

    ctx = types.SimpleNamespace(session=log, task_queue=_BoomQueue(),
                                config=Settings(), approval=None, channel=None,
                                repair=None)
    st = await _state()
    st.session_id = SID
    resp = await dispatch(ctx, st, _req("chat", 1, {"text": "hi"}))
    assert resp["error"]["code"] == -32603
    assert resp["error"]["data"]["code"] == "CYC-999"
    assert "炸了" in resp["error"]["data"]["detail"]


# ================================================================ initialize
async def test_initialize_creates_session_and_advertises_capabilities():
    mgr = _FakeManager()
    log0 = await _boot(SID)
    # ctx.session = 门面(manager 持 log0 为初始会话;create 路径新建 SID2)
    ctx = _ctx(log0, manager=mgr)
    st = await _state()
    resp = await dispatch(ctx, st, _req("initialize", 1, {}))
    assert resp["id"] == 1 and "error" not in resp
    r = resp["result"]
    assert r["protocolVersion"] == "1.0"
    assert r["methods"] == ["initialize", "chat", "approve",
                            "read_events", "shutdown"]
    assert r["events"] == {"cursor": True}
    assert r["session"]["created"] is True
    assert r["session"]["id"] in mgr.logs          # 门面 create 已注册
    assert r["engine"]["model"] == Settings().llm.model
    assert r["engine"]["headless_channel"] is False
    assert mgr.create_calls == 1
    assert st.session_id == r["session"]["id"]
    assert st.cursor == 0
    assert ctx.channel == f"acp:{CLIENT}"           # 桥 = 审批通道


async def test_initialize_binds_existing_session():
    log = await _boot(SID)
    mgr = _FakeManager(initial={SID: log})
    ctx = _ctx(log, manager=mgr)
    st = await _state()
    resp = await dispatch(ctx, st, _req("initialize", 1,
                                        {"sessionId": SID,
                                         "clientName": "IDE-1"}))
    r = resp["result"]
    assert r["session"] == {"id": SID, "created": False}
    assert mgr.open_calls == [SID]                  # 绑定走门面 open_session
    assert st.session_id == SID
    assert ctx.channel == f"acp:{CLIENT}"


async def test_initialize_rejects_finished_session_evt104():
    log = await _boot(SID)
    await log.append("session.finished", {"reason": "idle"},
                     actor="system", sync=True)
    mgr = _FakeManager(initial={SID: log})
    st = await _state()
    resp = await dispatch(_ctx(log, manager=mgr), st,
                          _req("initialize", 1, {"sessionId": SID}))
    assert resp["error"]["code"] == -32603
    assert resp["error"]["data"]["code"] == "EVT-104"   # 建议新 sessionId
    assert st.session_id is None


async def test_initialize_missing_session_surface_evt106():
    log = await _boot(SID)
    ctx = _ctx(log)                                  # 形状 b:无 create 面
    st = await _state()
    resp = await dispatch(ctx, st, _req("initialize", 1, {}))
    assert resp["error"]["code"] == -32603
    assert resp["error"]["data"]["code"] == "EVT-106"
    assert st.session_id is None


async def test_initialize_bad_session_id_type_evt100():
    log = await _boot(SID)
    mgr = _FakeManager(initial={SID: log})
    st = await _state()
    resp = await dispatch(_ctx(log, manager=mgr), st,
                          _req("initialize", 1, {"sessionId": 123}))
    assert resp["error"]["code"] == -32603
    assert resp["error"]["data"]["code"] == "EVT-100"
    assert "sessionId" in str(resp["error"]["data"]["detail"])


# ====================================================================== chat
async def _chat_ctx(log: SessionLog = None, *, runner=None, **kw) -> tuple[types.SimpleNamespace, mod.AcpState, _Runner]:
    """绑定会话 ctx + runner(st.session_id 预置,initialize 等价)。

    log 缺省新建(调用方须与 ctx 共享同一日志实例,防事件分叉)。
    """
    log = log or await _boot(SID)
    runner = runner or _Runner(log)
    ctx = _ctx(log, runner=runner, **kw)
    st = await _state()
    st.session_id = SID
    st._bound_log = log
    return ctx, st, runner


async def test_chat_wait_true_blocks_to_terminal_with_summary():
    ctx, st, runner = await _chat_ctx()
    resp = await dispatch(ctx, st, _req("chat", 1,
                                        {"text": "跑一个任务", "wait": True}))
    assert "error" not in resp
    r = resp["result"]
    assert r["reason"] == "complete"
    assert r["task_id"] in {t for t, _ in runner.runs}
    assert r["seq_range"] and r["seq_range"][1] >= r["seq_range"][0]
    # summary:终局文本 + 拒绝/审批清单(按 task_id 从会话派生)
    assert r["summary"]["final"] == f"[{r['task_id']}] 完成"
    assert [p["guard_id"] for p in r["summary"]["rejected"]] == ["danger"]
    approvals = r["summary"]["approvals"]                 # approval.* glob(含 requested)
    assert any(p.get("approval_id") == 7 for p in approvals)   # granted 入清单
    assert any(p.get("tool") == "exec" for p in approvals)     # requested 也入清单
    # user.message 强同步落盘,origin 记桥通道(审计同 CLI)
    msgs = _by_type(ctx.session, "user.message")
    assert [m.payload["content"] for m in msgs] == ["跑一个任务"]
    assert msgs[0].origin == f"acp:{CLIENT}"
    assert st.busy is False                      # 终态释放忙锁


async def test_chat_wait_false_returns_queued_immediately():
    ctx, st, _ = await _chat_ctx()
    resp = await dispatch(ctx, st, _req("chat", 1,
                                        {"text": "后台任务", "wait": False}))
    r = resp["result"]
    assert r["reason"] == "queued"
    assert r["summary"] == {}
    assert st.busy is False                      # 秒回不占忙锁


async def test_chat_empty_text_evt100():
    ctx, st, _ = await _chat_ctx()
    for bad in ({}, {"text": "   "}):
        resp = await dispatch(ctx, st, _req("chat", 1, bad))
        assert resp["error"]["code"] == -32603
        assert resp["error"]["data"]["code"] == "EVT-100"
        assert "text" in str(resp["error"]["data"]["detail"])


async def test_chat_without_initialize_evt106():
    log = await _boot(SID)
    runner = _Runner(log)
    ctx = _ctx(log, runner=runner)
    st = await _state()                          # session_id 未绑定
    resp = await dispatch(ctx, st, _req("chat", 1, {"text": "hi"}))
    assert resp["error"]["data"]["code"] == "EVT-106"


async def test_chat_explicit_session_id_without_initialize():
    log = await _boot(SID)
    runner = _Runner(log)
    ctx = _ctx(log, runner=runner)
    st = await _state()                          # 未 initialize
    resp = await dispatch(ctx, st, _req("chat", 1,
                                        {"text": "带会话", "sessionId": SID}))
    assert "error" not in resp
    assert resp["result"]["reason"] == "complete"
    assert st.session_id is None                 # 不隐式改绑定(仅本次生效)


async def test_chat_runner_failure_reason_error():
    log = await _boot(SID)
    runner = _Runner(log, exc=PyHError("LLM-310", ctx={"advice": "降级链全败"}))
    ctx, st, _ = await _chat_ctx(runner=runner)
    resp = await dispatch(ctx, st, _req("chat", 1, {"text": "会失败"}))
    r = resp["result"]
    assert r["reason"] == "error"                # 引擎错 = 任务终态,非桥错
    assert r["summary"]["final"] == "LLM-310"


async def test_chat_busy_rejected_32000():
    ctx, st, _ = await _chat_ctx()
    st.busy = True                               # 上一条 chat 未完成(偏离 9 防御面)
    resp = await dispatch(ctx, st, _req("chat", 1, {"text": "并发"}))
    assert resp["error"]["code"] == -32000
    assert resp["error"]["data"]["code"] == "BUSY"
    assert st.busy is True                       # 拒绝不改忙位(原任务仍持有)


# ==================================================================== approve
class _FakeApproval:
    """裁决入口替身:记录 approve/deny,并落 approval.granted(模拟 provider 强同步)。"""

    def __init__(self, log):
        self.log = log
        self.calls = []                          # (decision, aid, by)

    async def approve(self, aid, *, by):
        self.calls.append(("approve", aid, by))
        await self.log.append("approval.granted",
                              {"approval_id": aid, "by": by},
                              actor="user", sync=True)

    async def deny(self, aid, *, by):
        self.calls.append(("deny", aid, by))
        await self.log.append("approval.denied",
                              {"approval_id": aid, "by": by},
                              actor="user", sync=True)


async def test_approve_routes_to_provider_and_records():
    log = await _boot(SID)
    approval = _FakeApproval(log)
    ctx, st, _ = await _chat_ctx(approval=approval)
    resp = await dispatch(ctx, st, _req("approve", 1,
                                        {"approval_id": 3,
                                         "decision": "approve"}))
    assert resp["result"] == {"ok": True, "approval_id": 3,
                              "decision": "approve"}
    assert approval.calls == [("approve", 3, f"acp:{CLIENT}")]
    assert _by_type(log, "approval.granted")[0].payload["by"] == f"acp:{CLIENT}"


async def test_approve_deny_and_explicit_by():
    log = await _boot(SID)
    approval = _FakeApproval(log)
    ctx, st, _ = await _chat_ctx(approval=approval)
    resp = await dispatch(ctx, st, _req("approve", 2,
                                        {"approval_id": 5, "decision": "deny",
                                         "by": "acp:someone"}))
    assert resp["result"]["decision"] == "deny"
    assert approval.calls == [("deny", 5, "acp:someone")]


async def test_approve_bad_params_evt100():
    log = await _boot(SID)
    approval = _FakeApproval(log)
    ctx, st, _ = await _chat_ctx(approval=approval)
    for bad in ({"approval_id": "3", "decision": "approve"},     # 非 int
                {"approval_id": 3, "decision": "maybe"},         # 非法决策
                {"approval_id": True, "decision": "approve"}):   # bool 非 id
        resp = await dispatch(ctx, st, _req("approve", 1, bad))
        assert resp["error"]["data"]["code"] == "EVT-100"


async def test_approve_real_provider_unknown_id_apr503():
    log = await _boot(SID)
    provider = ApprovalProvider(session=log, bus=None)  # 无 pending → APR-503
    ctx, st, _ = await _chat_ctx(approval=provider)
    resp = await dispatch(ctx, st, _req("approve", 1,
                                        {"approval_id": 99999,
                                         "decision": "approve"}))
    assert resp["error"]["code"] == -32603
    assert resp["error"]["data"]["code"] == "APR-503"      # 防重放原码透传


async def test_approve_missing_provider_cyc999():
    ctx, st, _ = await _chat_ctx(approval=None)              # 桥未装配审批
    resp = await dispatch(ctx, st, _req("approve", 1,
                                        {"approval_id": 1, "decision": "approve"}))
    assert resp["error"]["data"]["code"] == "CYC-999"


# =============================================================== read_events
async def test_read_events_cursor_advances_and_batch_shape():
    log = await _boot(SID)
    ctx, st, _ = await _chat_ctx(log)
    # 追加若干事件:created(1) + 3 条
    for i in range(3):
        await log.append("agent.message", {"content": f"m{i}", "model": MODEL},
                         actor="agent")
    resp = await dispatch(ctx, st, _req("read_events", 1, {}))
    r = resp["result"]
    assert r["from_seq"] == 0 and r["to_seq"] == 4
    assert len(r["events"]) == 4
    assert r["has_more"] is False
    # 事件 dict 十字段形状(seq/ts/type/session_id 必在)
    first = r["events"][0]
    assert first["type"] == "session.created" and first["seq"] == 1
    assert first["session_id"] == SID and "ts" in first
    assert st.cursor == 4                                   # 游标推进到 to_seq
    # 再读:空批(from=to=当前游标)
    resp2 = await dispatch(ctx, st, _req("read_events", 2, {}))
    r2 = resp2["result"]
    assert r2["events"] == [] and r2["from_seq"] == 4 and r2["to_seq"] == 4
    assert r2["has_more"] is False


async def test_read_events_after_seq_and_limit_pagination():
    log = await _boot(SID)
    ctx, st, _ = await _chat_ctx(log)
    for i in range(6):
        await log.append("agent.message", {"content": f"m{i}", "model": MODEL},
                         actor="agent")
    resp = await dispatch(ctx, st, _req("read_events", 1,
                                        {"after_seq": 0, "limit": 4}))
    r = resp["result"]
    assert len(r["events"]) == 4 and r["has_more"] is True
    assert [e["seq"] for e in r["events"]] == [1, 2, 3, 4]
    # 断线续拉:from to_seq 继续(等价 SSE 重连语义)
    resp2 = await dispatch(ctx, st, _req("read_events", 2,
                                         {"after_seq": r["to_seq"], "limit": 4}))
    r2 = resp2["result"]
    assert [e["seq"] for e in r2["events"]] == [5, 6, 7]
    assert r2["has_more"] is False
    assert st.cursor == 7                                   # 单调至最新


async def test_read_events_uninitialized_evt106():
    log = await _boot(SID)
    runner = _Runner(log)
    ctx = _ctx(log, runner=runner)
    st = await _state()                                      # 未 initialize
    resp = await dispatch(ctx, st, _req("read_events", 1, {}))
    assert resp["error"]["data"]["code"] == "EVT-106"


async def test_read_events_explicit_session_id_and_bad_params():
    log = await _boot(SID)
    ctx, st, _ = await _chat_ctx(log)
    await log.append("agent.message", {"content": "x", "model": MODEL},
                     actor="agent")
    # 未 initialize 但显式 sessionId → 可读(只读投影)
    st.session_id = None
    resp = await dispatch(ctx, st, _req("read_events", 1,
                                        {"sessionId": SID}))
    assert "error" not in resp and len(resp["result"]["events"]) == 2
    # 非法游标/limit → EVT-100(带 sessionId 直连,绕过未绑定检查先命中参数校验)
    for bad in ({"cursor": "abc", "sessionId": SID},
                {"limit": "很多", "sessionId": SID},
                {"after_seq": True, "sessionId": SID}):
        resp = await dispatch(ctx, st, _req("read_events", 1, bad))
        assert resp["error"]["data"]["code"] == "EVT-100"


# =============================================================== 通知/净退
async def test_notify_no_output_and_no_side_effect():
    log = await _boot(SID)
    runner = _Runner(log)
    ctx = _ctx(log, runner=runner)
    st = await _state()
    st.session_id = SID
    out = io.StringIO()
    # chat 通知:不响应、不触发引擎副作用(偏离 5:无 id 无法回报,宁丢不丢结果)
    req = parse_request('{"jsonrpc":"2.0","method":"chat",'
                        '"params":{"text":"hi"}}', write=out)
    await notify(ctx, st, req)
    assert out.getvalue() == ""                            # 零响应行
    assert runner.runs == []                               # 零副作用


async def test_notify_shutdown_sets_flag():
    st = await _state()
    await notify(None, st, _req("shutdown", None))
    assert st.notify_shutdown is True


# ================================================================= serve 主循环
async def _booted_ctx(log=None, **kw):
    """serve 冒烟 ctx:绑定会话(形状 b)+ 空跑 runner + 注入 stdout。"""
    log = log or await _boot(SID)
    runner = _Runner(log)
    ctx = _ctx(log, runner=runner, **kw)
    return ctx


async def test_serve_eof_exits_zero_silently():
    ctx = await _booted_ctx()
    code = await serve(ctx, stdin=io.StringIO(""), stdout=io.StringIO())
    assert code == 0


async def test_serve_notification_and_blank_lines_no_output():
    ctx = await _booted_ctx()
    out = io.StringIO()
    feed = "\n  \n" + _line("chat", None, {"text": "hi"}) + "\n"
    code = await serve(ctx, stdin=io.StringIO(feed), stdout=out)
    assert code == 0
    assert out.getvalue() == ""                            # 通知零输出


async def test_serve_shutdown_notification_exits_zero_no_response():
    ctx = await _booted_ctx()
    out = io.StringIO()
    code = await serve(ctx, stdin=io.StringIO(_line("shutdown", None)),
                       stdout=out)
    assert code == 0
    assert out.getvalue() == ""                            # 通知不回响应


async def test_serve_serial_requests_ordered_responses():
    ctx = await _booted_ctx()
    out = io.StringIO()
    feed = "".join([
        _line("initialize", 1, {"sessionId": SID}),
        _line("read_events", 2, {}),
        _line("shutdown", 3),
    ])
    code = await serve(ctx, stdin=io.StringIO(feed), stdout=out)
    assert code == 0
    resps = _out_lines(out)
    assert [r["id"] for r in resps] == [1, 2, 3]           # 串行响应序一致
    assert "error" not in resps[0]
    assert resps[1]["result"]["from_seq"] == 0             # created 事件可读
    assert resps[2]["result"] == {"shutdown": True}


async def test_serve_bad_line_returns_error_and_continues():
    ctx = await _booted_ctx()
    out = io.StringIO()
    feed = "".join([
        "{oops\n",                                           # -32700,不崩桥
        _line("initialize", 1, {"sessionId": SID}),
        _line("shutdown", 2),
    ])
    code = await serve(ctx, stdin=io.StringIO(feed), stdout=out)
    assert code == 0
    resps = _out_lines(out)
    assert resps[0]["error"]["code"] == -32700
    assert [r["id"] for r in resps[1:]] == [1, 2]


async def test_serve_chat_end_to_end():
    log = await _boot(SID)
    ctx = await _booted_ctx(log)
    out = io.StringIO()
    feed = "".join([
        _line("initialize", 1, {"sessionId": SID}),
        _line("chat", 2, {"text": "做个事", "wait": True}),
        _line("shutdown", 3),
    ])
    code = await serve(ctx, stdin=io.StringIO(feed), stdout=out)
    assert code == 0
    resps = _out_lines(out)
    assert resps[1]["result"]["reason"] == "complete"
    assert resps[1]["result"]["summary"]["final"].startswith("[t-")
    # user.message 入日志(桥内 append 与队列同柄)
    assert "user.message" in _types(log)


async def test_serve_unknown_method_32601_then_continue():
    ctx = await _booted_ctx()
    out = io.StringIO()
    feed = "".join([
        _line("nope", 1),
        _line("shutdown", 2),
    ])
    code = await serve(ctx, stdin=io.StringIO(feed), stdout=out)
    assert code == 0
    resps = _out_lines(out)
    assert resps[0]["error"]["code"] == -32601
    assert resps[1]["result"]["shutdown"] is True


# ================================================================ AcpBridge
async def test_acp_bridge_channel_derives_client_id():
    log = await _boot(SID)
    runner = _Runner(log)
    ctx = _ctx(log, runner=runner)
    out = io.StringIO()
    feed = "".join([
        _line("initialize", 1, {"sessionId": SID}),
        _line("shutdown", 2),
    ])
    code = await AcpBridge(ctx, channel="acp:cli").serve(stdin=io.StringIO(feed),
                                                         stdout=out)
    assert code == 0
    resps = _out_lines(out)
    assert resps[0]["result"]["session"]["id"] == SID
    assert ctx.channel == "acp:cli"           # cli.py.md 装配语义回写
    # 客户端 id 缺省 pid 形态
    st = await _state()
    assert st.client_id == CLIENT


# ====================================================== 引擎错误映射(ADR-011)
def _mk_err(code: str, ctx: dict) -> PyHError:
    try:
        raise_code(code, **ctx)
    except PyHError as e:
        return e
    raise AssertionError("raise_code 未抛错")   # pragma: no cover


def test_engine_error_hot_codes_no_detail():
    e = _mk_err("GRD-401", {"tool": "exec", "secret_key": "hunter2",
                            "token": "sk-xxx"})
    resp = engine_error(5, e)
    assert resp["id"] == 5 and resp["error"]["code"] == -32603
    data = resp["error"]["data"]
    assert data["code"] == "GRD-401"
    assert "detail" not in data                # 热路径码只留 code+message+advice
    blob = json.dumps(resp, ensure_ascii=False)
    assert "hunter2" not in blob and "sk-xxx" not in blob


def test_engine_error_detail_redacted_and_truncated():
    e = _mk_err("EVT-100", {"field": "text", "api_key": "sk-leak",
                            "hint": "x" * 500})
    data = engine_error(1, e)["error"]["data"]
    assert data["code"] == "EVT-100"
    assert data["detail"]["field"] == "text"
    assert data["detail"]["api_key"] == "[redacted]"   # 敏感键打码(INV-09)
    assert len(data["detail"]["hint"]) == 200          # 截断


def test_engine_error_busy_code_maps_to_32000():
    # 直接构造 PyHError("BUSY")(未登记码先例,同 task_queue);dispatch 特判 → -32000
    e = PyHError("BUSY", ctx={"advice": "等上一条"})
    assert e.code == "BUSY"
    assert error_resp(1, -32000, "Busy", {"code": "BUSY"})["error"]["code"] == -32000


def test_error_resp_shape():
    resp = error_resp(3, -32602, "Invalid params", {"k": "v"})
    assert resp == {"jsonrpc": "2.0", "id": 3,
                    "error": {"code": -32602, "message": "Invalid params",
                              "data": {"k": "v"}}}
    assert "data" not in error_resp(3, -32601, "x")["error"]


# ======================================================== 纯函数/防御面
async def test_cmd_approve_deny_missing_initialize_is_allowed():
    """approve 不需要会话绑定:裁决按 approval_id 路由 provider 内部请求。"""
    log = await _boot(SID)
    approval = _FakeApproval(log)
    ctx = _ctx(log, runner=_Runner(log), approval=approval)
    st = await _state()                                  # 未 initialize
    resp = await dispatch(ctx, st, _req("approve", 1,
                                        {"approval_id": 2,
                                         "decision": "approve"}))
    assert resp["result"]["ok"] is True


async def test_read_events_after_seq_replay_resume():
    """断线重连语义:新连接(游标复位)用上次 to_seq 续拉不重不漏。"""
    log = await _boot(SID)
    ctx, st, _ = await _chat_ctx(log)
    for i in range(3):
        await log.append("agent.message", {"content": f"m{i}", "model": MODEL},
                         actor="agent")
    r1 = (await dispatch(ctx, st, _req("read_events", 1,
                                       {"limit": 2})))["result"]
    st2 = await _state()                                 # 断线:新连接游标 0
    st2.session_id = SID                                 # 重连 = initialize(sessionId) 绑定
    r2 = (await dispatch(ctx, st2, _req("read_events", 2,
                                        {"after_seq": r1["to_seq"]})))["result"]
    assert [e["seq"] for e in r2["events"]] == [3, 4]    # 续拉不重不漏
    assert r2["from_seq"] == r1["to_seq"]
