"""tests/unit/test_tools_executor.py — 工具执行管道(四关)单测(specs/tools_executor.py.md)。

覆盖清单(spec 四关总表 + 模块级测试 GWT-T7-02~04/INV-04~06/F026/F022/F039):
- 四关顺序与事件序:tool.call → guard.evaluated(allow) → tool.result(GWT-T7-02);
  关1 参数失败零 Provider 调用 + 不写 tool.call(INV-06,TLB-803);extra 字段拒;
  类型强校验并记录实际执行参数(与日志 args 逐字段一致)
- 关2 scope-hidden 终局拒(GRD-401,executor 侧 evaluated+rejected(sync))与
  guard 链 reject(critical → POL-DGR-1)零副作用(INV-05);GRD-402 同 call_id
  重放上抛拒绝
- 关2.5 审批流:denied/timeout 不执行;headless APR-501 直接拒;granted → 执行
  (重入 approval 决策视为已授权,偏离 3);granted 后策略收紧(workspace 变更 →
  POL-FS-1 重入 reject)→ GRD-403 批准作废(偏离 3 语义落点)
- 关3 Provider 执行:同步 handle 经线程池(执行线程 ≠ 事件循环线程)、超时掐断
  TLB-805、取消写 partial 后 re-raise(F025)、registry mark_running/mark_idle
  注销 BUSY 闸联动
- 关4 finalize:输出 schema 校验(通过/失败→TLB-803 输出校验失败)、>2KB 转 spill
  (truncated+spill_ref dict,摘要 ≤2KB,原文入 spill)、≤2KB 直读摘要、spill 未接线
  → PERS-221 tool.error
- 解析(parse_tool_call:嵌套/扁平 wire、补 id、TLB-802/803 族)与 F022 批量串行
  (保序、同工具连败 2 终止该轮)
- 门面透传 register/schemas_for/register_definition 与轮内连败计数 API

注:guard/registry 逻辑全部真实执行(真实 GuardChain + 真实 ToolRegistry +
必要时真实 ApprovalProvider);mock 仅用于 Provider/审批裁决/scope 查权替身。
"""
import asyncio
import threading
import time
import types
from pathlib import Path

import pytest

from pyharness.core.approval import ApprovalProvider
from pyharness.core.tools_executor import (ToolExecutor, render_result_text,
                                           summarize, summarize_text)
from pyharness.core.tools_guard import GuardChain, ToolCall
from pyharness.core.tools_registry import ToolDefinition, ToolRegistry
from pyharness.errors import PyHError

# ===================================================================== 替身
READ_SCHEMA = {"type": "object",
               "properties": {"path": {"type": "string"}},
               "required": ["path"]}
WRITE_SCHEMA = {"type": "object",
                "properties": {"path": {"type": "string"},
                               "content": {"type": "string"},
                               "mode": {"type": "string"}},
                "required": ["path"]}
COUNT_SCHEMA = {"type": "object",
                "properties": {"count": {"type": "integer"}},
                "required": ["count"]}


def mk_defn(name: str = "fs.read_file", *, danger: str = "none",
            schema: dict = READ_SCHEMA, **over) -> ToolDefinition:
    """合法 Definition 工厂(默认读文件形态;over 覆盖 danger/schema/timeout 等)。"""
    base = dict(name=name, description="单测工具", schema=schema,
                danger=danger, owner="builtin")
    base.update(over)
    return ToolDefinition(**base)


class Recorder:
    """Provider 替身:计数 + 记录实参(INV-06 执行 args==日志 args 断言面)。"""

    def __init__(self, result=None, *, delay: float = 0.0,
                 event: threading.Event = None, err: Exception = None) -> None:
        self.calls = 0
        self.last = None
        self.result = {"ok": True} if result is None else result
        self.delay = delay
        self.event = event
        self.err = err
        self.thread_id: int | None = None

    def handle(self, args, ctx):                       # 同步 handle(关3 经线程池)
        self.calls += 1
        self.last = dict(args)
        self.thread_id = threading.get_ident()
        if self.err is not None:
            raise self.err
        if self.delay:
            time.sleep(self.delay)
        if self.event is not None:
            self.event.set()
        return self.result


class _Env:
    """迷你信封(approval 等待器/executor 消费面:seq + payload)。"""

    def __init__(self, seq: int, type_: str, payload: dict) -> None:
        self.seq = seq
        self.type = type_
        self.payload = payload


class AsyncSess:
    """异步事件落点替身(真实 SessionLog 同型:append → 信封;记录强同步标志)。"""

    def __init__(self, sid: str = "s-executor-1") -> None:
        self.sid = sid
        self.events: list[dict] = []
        self._seq = 0

    async def append(self, type_, payload, *, actor, **kw):
        self._seq += 1
        self.events.append({"type": type_, "payload": dict(payload),
                            "actor": actor, "seq": self._seq,
                            "sync": kw.get("sync", False),
                            "trace": kw.get("trace")})
        return _Env(self._seq, type_, dict(payload))

    def of(self, type_) -> list[dict]:
        return [e for e in self.events if e["type"] == type_]

    def last_of(self, type_) -> dict | None:
        evs = self.of(type_)
        return evs[-1] if evs else None


class FakeScope:
    """会话 scope 替身:policy(ScopePolicy 同型)+ can_use 前置查权。"""

    def __init__(self, workspace_root: str, allowed: list[str] | None = None):
        self.policy = types.SimpleNamespace(
            workspace_root=workspace_root, allowed_domains=set(),
            sandbox_level="basic")
        self.allowed = None if allowed is None else set(allowed)
        self.asked: list[str] = []

    def can_use(self, name: str) -> bool:
        self.asked.append(name)
        return self.allowed is None or name in self.allowed


class FakeApproval:
    """审批裁决替身:按脚本逐次返回 verdict(记录 request 现场)。"""

    def __init__(self, verdicts: list[str]) -> None:
        self.verdicts = list(verdicts)
        self.requests: list[tuple] = []

    async def request(self, call, args_summary: str, ctx):
        self.requests.append((call.name, args_summary, ctx))
        v = self.verdicts.pop(0) if self.verdicts else "denied"
        if v == "raise-apr501":
            raise_code_apr501(call)
        return v


def raise_code_apr501(call):
    from pyharness.errors import raise_code
    raise_code("APR-501", tool=call.name, hint="headless/无交互通道:直接拒")


class FakeSpill:
    """ctx.storage.spill 替身(put 记录原文/kind,返回 F039 契约 dict)。"""

    def __init__(self) -> None:
        self.put_calls: list[tuple[str, str]] = []

    async def put(self, text: str, kind: str) -> dict:
        self.put_calls.append((text, kind))
        return {"spilled": True, "ref": f"spill/{kind}/1",
                "chars": len(text), "lines": text.count("\n") + 1,
                "preview": text[:500]}


def make_registry(*defns, providers: dict | None = None) -> ToolRegistry:
    """独立注册表:注册 defns 并绑定 providers(name → Recorder/任意)。"""
    reg = ToolRegistry()
    for d in defns:
        reg.register_tool(d)
    for name, prov in (providers or {}).items():
        reg.bind_provider(name, prov)
    return reg


def make_ctx(sess: AsyncSess, scope: FakeScope, *, chain=None, approval=None,
             spill=None, channel: str = "cli", headless: bool = False):
    """execute 的 ctx 替身(会话门面注入面 = session/scope/guard/approval/storage)。"""
    storage = types.SimpleNamespace(spill=spill) if spill is not None else None
    return types.SimpleNamespace(session=sess, scope=scope, guard=chain,
                                 approval=approval, storage=storage,
                                 channel=channel, headless=headless,
                                 session_id=sess.sid)


async def wait_until(pred, timeout: float = 4.0) -> None:
    """轮询等条件(审批裁决/事件经后台任务异步到达)。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("wait_until 超时:条件未满足")


def ordered_types(sess: AsyncSess) -> list[str]:
    """会话事件 type 序列(seq 序,审计断言用)。"""
    return [e["type"] for e in sess.events]


# ================================================================ 解析(F022)
class TestParseToolCall:
    def test_parse_openai_nested_wire(self):
        ex = ToolExecutor()
        raw = {"id": "call_1", "type": "function",
               "function": {"name": "fs.read_file",
                            "arguments": '{"path": "a.txt"}'}}
        c = ex.parse_tool_call(raw, parent_seq=7)
        assert isinstance(c, ToolCall)
        assert c.name == "fs.read_file"
        assert c.raw_args == {"path": "a.txt"}
        assert c.call_id == "call_1"
        assert c.parent_seq == 7

    def test_parse_flat_wire_and_generated_id(self):
        ex = ToolExecutor()
        c = ex.parse_tool_call({"name": "fs.read_file", "arguments": '{}'})
        assert c.name == "fs.read_file"
        assert c.raw_args == {}
        assert len(c.call_id) == 8 and c.call_id.isalnum()  # 缺 id 补生成
        assert c.parent_seq is None

    @pytest.mark.parametrize("bad", [None, "", "Bad.name", "1bad", "a b"])
    def test_parse_illegal_name_tlb802(self, bad):
        with pytest.raises(PyHError) as ei:
            ToolExecutor().parse_tool_call(
                {"id": "c1", "function": {"name": bad, "arguments": "{}"}})
        assert ei.value.code == "TLB-802"               # 幻觉名入口拦截

    def test_parse_bad_json_tlb803(self):
        with pytest.raises(PyHError) as ei:
            ToolExecutor().parse_tool_call(
                {"function": {"name": "fs.read_file",
                              "arguments": "{broken"}})
        assert ei.value.code == "TLB-803"

    def test_parse_non_object_json_tlb803(self):
        with pytest.raises(PyHError) as ei:
            ToolExecutor().parse_tool_call(
                {"function": {"name": "fs.read_file",
                              "arguments": "[1, 2]"}})
        assert ei.value.code == "TLB-803"

    def test_parse_non_dict_raw_tlb803(self):
        for raw in (None, "oops", ["fs.read_file"]):
            with pytest.raises(PyHError) as ei:
                ToolExecutor().parse_tool_call(raw)
            assert ei.value.code == "TLB-803"


# ================================================================ 四关执行
class TestExecutePipeline:
    async def test_happy_full_pipeline_event_order(self, tmp_path: Path):
        """GWT-T7-02:事件序 tool.call→guard.evaluated(allow)→tool.result。"""
        sess, prov = AsyncSess(), Recorder({"content": "hi"})
        defn = mk_defn()
        reg = make_registry(defn, providers={defn.name: prov})
        scope = FakeScope(str(tmp_path))
        chain = GuardChain(session=sess)                # 真实链(事件落点注入)
        ctx = make_ctx(sess, scope, chain=chain)
        r = await ToolExecutor(reg).execute(
            ToolCall(name="fs.read_file", raw_args={"path": "a.txt"},
                     call_id="call_1", parent_seq=3), ctx)
        assert r.ok and r.summary == '{"content":"hi"}'
        assert r.truncated is False and r.elapsed_ms > 0
        assert ordered_types(sess) == ["tool.call", "guard.evaluated",
                                       "tool.result"]
        call_ev = sess.of("tool.call")[0]
        assert call_ev["actor"] == "tool"
        assert call_ev["trace"] == {"parent_seq": 3}
        assert call_ev["payload"] == {"name": "fs.read_file",
                                      "args": {"path": "a.txt"},
                                      "raw_args": {"path": "a.txt"},
                                      "call_id": "call_1"}
        ev = sess.of("guard.evaluated")[0]
        assert ev["payload"]["decision"] == "allow"
        assert prov.calls == 1 and prov.last == {"path": "a.txt"}  # INV-06

    async def test_inv06_coercion_logged_and_executed(self, tmp_path: Path):
        """强校验 int 化:执行 args(3)==日志 args(3),raw_args 保留模型原话 "3"。"""
        sess, prov = AsyncSess(), Recorder()
        defn = mk_defn(name="calc.add", schema=COUNT_SCHEMA)
        reg = make_registry(defn, providers={defn.name: prov})
        ctx = make_ctx(sess, FakeScope(str(tmp_path)), chain=GuardChain(session=sess))
        r = await ToolExecutor(reg).execute(
            ToolCall(name="calc.add", raw_args={"count": "3"},
                     call_id="c2"), ctx)
        assert r.ok
        call_ev = sess.of("tool.call")[0]["payload"]
        assert call_ev["args"] == {"count": 3}          # 校验后 int
        assert call_ev["raw_args"] == {"count": "3"}    # 模型原话(双份存档)
        assert prov.last == {"count": 3} and prov.last == call_ev["args"]

    async def test_param_fail_extra_field_zero_provider(self, tmp_path: Path):
        """TLB-803(多余字段 extra=forbid):零 Provider、不写 tool.call。"""
        sess, prov = AsyncSess(), Recorder()
        defn = mk_defn()
        reg = make_registry(defn, providers={defn.name: prov})
        ctx = make_ctx(sess, FakeScope(str(tmp_path)), chain=GuardChain(session=sess))
        r = await ToolExecutor(reg).execute(
            ToolCall(name="fs.read_file",
                     raw_args={"path": "a.txt", "extra": 1}, call_id="c3"), ctx)
        assert not r.ok and r.summary.startswith("参数校验失败(TLB-803)")
        assert prov.calls == 0                          # INV-06:零调用
        assert sess.of("tool.call") == []               # 失败不写 tool.call
        err = sess.of("tool.error")[0]
        assert err["payload"]["code"] == "TLB-803"
        assert err["payload"]["call_id"] == "c3"
        assert err["trace"] is None                     # 无父响应(单次直调)

    async def test_param_wrong_type_zero_provider(self, tmp_path: Path):
        """TLB-803(path=123 禁止隐式 str() 转换执行,INV-06)。"""
        sess, prov = AsyncSess(), Recorder()
        defn = mk_defn()
        reg = make_registry(defn, providers={defn.name: prov})
        ctx = make_ctx(sess, FakeScope(str(tmp_path)), chain=GuardChain(session=sess))
        r = await ToolExecutor(reg).execute(
            ToolCall(name="fs.read_file", raw_args={"path": 123},
                     call_id="c4"), ctx)
        assert not r.ok and r.summary.startswith("参数校验失败")
        assert prov.calls == 0

    async def test_unknown_tool_tlb802_fed_back(self, tmp_path: Path):
        """TLB-802(幻觉工具名):tool.error 回喂,零执行、不写 tool.call。"""
        sess, prov = AsyncSess(), Recorder()
        reg = make_registry(mk_defn(), providers={"fs.read_file": prov})
        ctx = make_ctx(sess, FakeScope(str(tmp_path)), chain=GuardChain(session=sess))
        r = await ToolExecutor(reg).execute(
            ToolCall(name="ghost.delete_all", raw_args={}, call_id="c5"), ctx)
        assert not r.ok
        assert sess.of("tool.error")[0]["payload"]["code"] == "TLB-802"
        assert prov.calls == 0

    async def test_scope_hidden_terminal_reject(self, tmp_path: Path):
        """关2a scope-hidden:executor 侧 evaluated+rejected(sync),GRD-401。"""
        sess, prov = AsyncSess(), Recorder()
        defn = mk_defn()
        reg = make_registry(defn, providers={defn.name: prov})
        scope = FakeScope(str(tmp_path), allowed=[])    # fs.read_file 不可见
        ctx = make_ctx(sess, scope, chain=GuardChain(session=sess))
        ex = ToolExecutor(reg)
        r = await ex.execute(ToolCall(name="fs.read_file",
                                      raw_args={"path": "a.txt"},
                                      call_id="c6"), ctx)
        assert not r.ok and r.summary.startswith("guard 拒绝")
        assert prov.calls == 0                          # INV-05:零副作用
        evs = sess.of("guard.evaluated")[0]["payload"]
        assert evs["decision"] == "deny"
        assert evs["guard_ids"] == ["scope-hidden"]
        rej = sess.of("guard.rejected")[0]
        assert rej["sync"] is True                      # 强同步(审计锚)
        assert rej["payload"]["guard_id"] == "scope-hidden"
        assert rej["payload"]["policy_ref"] == "GRD-401"
        assert "c6" in ex.rejected_ids()                # GRD-402 防重放登记
        with pytest.raises(PyHError) as ei:             # 同 call_id 重放 → GRD-402
            await ex.execute(ToolCall(name="fs.read_file",
                                      raw_args={"path": "a.txt"},
                                      call_id="c6"), ctx)
        assert ei.value.code == "GRD-402"
        assert prov.calls == 0

    async def test_guard_reject_critical_zero_side_effects(self, tmp_path: Path):
        """GWT-T7-03:critical → 链 reject(POL-DGR-1),Provider 计数 0。"""
        sess, prov = AsyncSess(), Recorder()
        defn = mk_defn(name="fs.delete", danger="critical",
                       schema={"type": "object",
                               "properties": {"path": {"type": "string"}},
                               "required": ["path"]})
        reg = make_registry(defn, providers={defn.name: prov})
        ctx = make_ctx(sess, FakeScope(str(tmp_path)), chain=GuardChain(session=sess))
        r = await ToolExecutor(reg).execute(
            ToolCall(name="fs.delete", raw_args={"path": "x"},
                     call_id="c7"), ctx)
        assert not r.ok and r.summary == "guard 拒绝,未执行"
        assert prov.calls == 0
        ev = sess.of("guard.evaluated")[0]["payload"]
        assert ev["decision"] == "deny"                 # 词表别名
        rej = sess.of("guard.rejected")[0]["payload"]
        assert rej["guard_id"] == "g-danger"
        assert rej["policy_ref"] == "POL-DGR-1"
        assert sess.of("tool.result") == []             # 拒绝无 result

    async def test_missing_wiring_fails_closed(self, tmp_path: Path):
        """装配缺件(无 guard)→ CYC-999 fail-closed,不执行。"""
        sess, prov = AsyncSess(), Recorder()
        reg = make_registry(mk_defn(), providers={"fs.read_file": prov})
        ctx = make_ctx(sess, FakeScope(str(tmp_path)), chain=None)
        with pytest.raises(PyHError) as ei:
            await ToolExecutor(reg).execute(
                ToolCall(name="fs.read_file", raw_args={"path": "a"},
                         call_id="c8"), ctx)
        assert ei.value.code == "CYC-999"
        assert prov.calls == 0


# ================================================================ 审批(关2.5)
class TestApprovalFlow:
    def _writer(self, sess: AsyncSess, prov: Recorder,
                tmp_path: Path, *, danger: str = "high") -> tuple:
        """fs.write_file 装配(真实链);danger=high → g-danger 审批决策;
        danger=none + 目标已存在 → g-overwrite(POL-OVW-1)审批决策。"""
        defn = mk_defn(name="fs.write_file", danger=danger, schema=WRITE_SCHEMA)
        reg = make_registry(defn, providers={defn.name: prov})
        scope = FakeScope(str(tmp_path))
        chain = GuardChain(session=sess)                # channel 缺省视同有
        return reg, scope, chain

    @pytest.mark.parametrize("verdict", ["denied", "timeout"])
    async def test_verdict_not_granted_no_execute(self, tmp_path: Path,
                                                  verdict: str):
        """denied/timeout = 不执行(安全默认);approval.requested 已留痕。"""
        sess, prov = AsyncSess(), Recorder()
        reg, scope, chain = self._writer(sess, prov, tmp_path)
        ap = FakeApproval([verdict])
        ctx = make_ctx(sess, scope, chain=chain, approval=ap)
        r = await ToolExecutor(reg).execute(
            ToolCall(name="fs.write_file",
                     raw_args={"path": "a.txt", "content": "x"},
                     call_id="c10"), ctx)
        assert not r.ok and r.summary == f"审批{verdict},未执行"
        assert prov.calls == 0                          # 不执行
        assert ap.requests and ap.requests[0][0] == "fs.write_file"
        assert sess.of("guard.evaluated")[0]["payload"]["decision"] == \
            "need_approval"
        assert sess.of("tool.result") == [] and sess.of("tool.error") == []

    async def test_headless_apr501_no_execute(self, tmp_path: Path):
        """headless 无通道 → APR-501 直接拒(approval.request 恒 raise)。"""
        sess, prov = AsyncSess(), Recorder()
        reg, scope, chain = self._writer(sess, prov, tmp_path)
        ctx = make_ctx(sess, scope, chain=chain,
                       approval=ApprovalProvider(channel=None, headless=True),
                       channel=None, headless=True)
        ex = ToolExecutor(reg)
        r = await ex.execute(ToolCall(name="fs.write_file",
                                      raw_args={"path": "a.txt"},
                                      call_id="c11"), ctx)
        assert not r.ok and "APR-501" in r.summary
        assert prov.calls == 0
        assert sess.of("approval.requested") == []      # 零事件零等待
        assert "c11" in ex.rejected_ids()

    async def test_granted_real_approval_executes(self, tmp_path: Path):
        """granted(真实 ApprovalProvider)→ 重入链(approval 决策视为已授权,
        偏离 3)→ Provider 执行;工具写入文件(真副作用验证执行发生)。"""
        sess, prov = AsyncSess(), Recorder({"ok": True})
        reg, scope, chain = self._writer(sess, prov, tmp_path, danger="high")
        prov_ap = ApprovalProvider(channel="cli")
        ctx = make_ctx(sess, scope, chain=chain, approval=prov_ap)
        ex = ToolExecutor(reg)
        call = ToolCall(name="fs.write_file",
                        raw_args={"path": "sub/x.txt", "content": "new"},
                        call_id="c12")
        task = asyncio.create_task(ex.execute(call, ctx))
        await wait_until(lambda: sess.of("approval.requested"))
        aid = sess.of("approval.requested")[-1]["seq"]  # approval_id=请求 seq
        prov_ap.approve(aid, by="cli:alice")            # 人类批准(强同步)
        r = await asyncio.wait_for(task, 5)
        assert r.ok
        assert prov.calls == 1
        assert prov.last == {"path": "sub/x.txt", "content": "new", "mode": None}
        assert sess.of("approval.granted")              # 授权事实留痕
        assert len(sess.of("guard.evaluated")) == 2     # 首次 + 重入各一条
        assert sess.last_of("tool.result")["payload"]["ok"] is True

    async def test_granted_then_policy_tightened_grd403(self, tmp_path: Path):
        """GWT-T7-04:granted 期间策略收紧(workspace 变更 → 重入 POL-FS-1 拒)
        → GRD-403 批准作废,Provider 未执行。"""
        sess, prov = AsyncSess(), Recorder({"ok": True})
        ws1 = tmp_path / "ws1"
        sub = ws1 / "sub"
        sub.mkdir(parents=True)
        f = sub / "x.txt"
        f.write_text("old", encoding="utf-8")
        # danger=none + 目标已存在 → g-overwrite 审批(g-danger 不短路,
        # 收紧时重入可由 g-fs-path 出新拒;high 会被 g-danger 短路,见类注释)
        reg, scope, chain = self._writer(sess, prov, ws1, danger="none")
        prov_ap = ApprovalProvider(channel="cli")
        ctx = make_ctx(sess, scope, chain=chain, approval=prov_ap)
        ex = ToolExecutor(reg)
        call = ToolCall(name="fs.write_file",
                        raw_args={"path": str(f), "content": "new"},
                        call_id="c13")
        task = asyncio.create_task(ex.execute(call, ctx))
        await wait_until(lambda: sess.of("approval.requested"))
        aid = sess.of("approval.requested")[-1]["seq"]
        prov_ap.approve(aid, by="cli:alice")
        # 批准落地后、重入 evaluate 前收紧策略(同步窗口内完成,无竞态)
        scope.policy.workspace_root = str(tmp_path / "ws2-elsewhere")
        r = await asyncio.wait_for(task, 5)
        assert not r.ok and r.summary == "审批后 guard 重入拒绝(GRD-403 语义)"
        assert prov.calls == 0                          # 批准不作废执行
        rej = sess.of("guard.rejected")[-1]["payload"]
        assert rej["policy_ref"] == "POL-FS-1"          # 新拒=越界
        assert "c13" in ex.rejected_ids()
        assert sess.of("tool.result") == []


# ================================================================ 关3 执行
class TestProviderExecution:
    async def test_sync_handler_runs_in_threadpool(self, tmp_path: Path):
        """同步 handle 经线程池(执行线程 ≠ 事件循环线程,不阻塞 loop)。"""
        sess, prov = AsyncSess(), Recorder({"ok": True})
        defn = mk_defn()
        reg = make_registry(defn, providers={defn.name: prov})
        ctx = make_ctx(sess, FakeScope(str(tmp_path)), chain=GuardChain(session=sess))
        loop_tid = threading.get_ident()
        r = await ToolExecutor(reg).execute(
            ToolCall(name="fs.read_file", raw_args={"path": "a"},
                     call_id="c20"), ctx)
        assert r.ok
        assert prov.thread_id != loop_tid
        assert prov.thread_id is not None

    async def test_timeout_tlb805(self, tmp_path: Path):
        """F017:超时(defn.timeout_s)掐断 → tool.error(TLB-805)回喂。"""
        sess, prov = AsyncSess(), Recorder({"ok": True}, delay=2.0)
        defn = mk_defn(timeout_s=1)                     # 1s 超时 < 2s 阻塞
        reg = make_registry(defn, providers={defn.name: prov})
        ctx = make_ctx(sess, FakeScope(str(tmp_path)), chain=GuardChain(session=sess))
        t0 = time.monotonic()
        r = await ToolExecutor(reg).execute(
            ToolCall(name="fs.read_file", raw_args={"path": "a"},
                     call_id="c21"), ctx)
        assert not r.ok and r.summary.startswith("执行超时")
        assert time.monotonic() - t0 < 2.0              # 掐断生效
        err = sess.of("tool.error")[0]["payload"]
        assert err["code"] == "TLB-805"
        assert sess.of("tool.result") == []             # 失败恰一条 error
        # 线程已被弃(掐断点不等待其返回)——timeout 语义 = executor 已收敛返回
        assert sess.last_of("tool.error")["payload"]["message"].startswith(
            "执行超时(1s)")

    async def test_cancel_writes_partial_and_reraises(self, tmp_path: Path):
        """F025:执行中取消 → partial tool.result(ok=False,truncated)+ re-raise。"""
        sess, prov = AsyncSess(), Recorder({"ok": True})
        started = threading.Event()
        release = threading.Event()
        prov.event, prov.delay = started, None

        def blocking_handle(args, ctx):
            prov.calls += 1
            prov.last = dict(args)
            started.set()
            release.wait(10)                            # 阻塞至测试放行
            return prov.result

        defn = mk_defn()
        reg = make_registry(defn)
        reg.bind_provider(defn.name, types.SimpleNamespace(handle=blocking_handle))
        ctx = make_ctx(sess, FakeScope(str(tmp_path)), chain=GuardChain(session=sess))
        ex = ToolExecutor(reg)
        task = asyncio.create_task(
            ex.execute(ToolCall(name="fs.read_file", raw_args={"path": "a"},
                                call_id="c22"), ctx))
        await asyncio.to_thread(started.wait, 2)        # 等 Provider 已启动
        task.cancel()                                   # 用户取消(F025)
        with pytest.raises(asyncio.CancelledError):
            await task                                  # 不吞,按协议 re-raise
        partial = sess.last_of("tool.result")
        assert partial is not None                      # 已发生副作用如实写
        p = partial["payload"]
        assert p["ok"] is False and p["truncated"] is True
        assert "partial" in p["summary"]
        release.set()                                   # 放行后台线程(防悬挂)

    async def test_unregister_busy_gate_via_running(self, tmp_path: Path):
        """关3 mark_running/mark_idle 联动:在途调用中注销 → BUSY 拒。"""
        sess = AsyncSess()
        started, release = threading.Event(), threading.Event()

        def slow(args, ctx):
            started.set()
            release.wait(10)
            return {"ok": True}

        defn = mk_defn()
        reg = make_registry(defn)
        reg.bind_provider(defn.name, types.SimpleNamespace(handle=slow))
        ctx = make_ctx(sess, FakeScope(str(tmp_path)), chain=GuardChain(session=sess))
        ex = ToolExecutor(reg)
        task = asyncio.create_task(
            ex.execute(ToolCall(name="fs.read_file", raw_args={"path": "a"},
                                call_id="c23"), ctx))
        await asyncio.to_thread(started.wait, 2)        # Provider 在途
        with pytest.raises(PyHError) as ei:
            reg.unregister(defn.name)                   # 注销闸:在途 → BUSY
        assert ei.value.code == "BUSY"
        release.set()
        r = await asyncio.wait_for(task, 5)
        assert r.ok                                     # 调用正常收尾
        reg.unregister(defn.name)                       # 已 idle,注销放行


# ================================================================ 关4 finalize
class TestFinalize:
    def _setup(self, tmp_path: Path, prov: Recorder, *,
               output_schema=None, timeout_s: int = 60):
        sess = AsyncSess()
        defn = mk_defn(output_schema=output_schema, timeout_s=timeout_s)
        reg = make_registry(defn, providers={defn.name: prov})
        scope = FakeScope(str(tmp_path))
        ctx = make_ctx(sess, scope, chain=GuardChain(session=sess))
        return ToolExecutor(reg), sess, scope, ctx

    async def test_output_schema_pass(self, tmp_path: Path):
        """关4 输出契约:声明且通过 → tool.result。"""
        schema = {"type": "object",
                  "properties": {"ok": {"type": "boolean"}},
                  "required": ["ok"]}
        ex, sess, scope, ctx = self._setup(tmp_path, Recorder({"ok": True}),
                                           output_schema=schema)
        r = await ex.execute(ToolCall(name="fs.read_file",
                                      raw_args={"path": "a"},
                                      call_id="c30"), ctx)
        assert r.ok
        assert sess.last_of("tool.result")["payload"]["ok"] is True
        assert sess.of("tool.error") == []

    async def test_output_schema_fail_tool_error(self, tmp_path: Path):
        """关4 输出校验失败 → tool.error(TLB-803 输出校验失败),无 result。"""
        schema = {"type": "object",
                  "properties": {"ok": {"type": "boolean"}},
                  "required": ["ok"]}
        ex, sess, scope, ctx = self._setup(tmp_path, Recorder({"ok": "maybe"}),
                                           output_schema=schema)
        r = await ex.execute(ToolCall(name="fs.read_file",
                                      raw_args={"path": "a"},
                                      call_id="c31"), ctx)
        assert not r.ok and r.summary.startswith("输出校验失败")
        err = sess.of("tool.error")[0]["payload"]
        assert err["code"] == "TLB-803"
        assert sess.of("tool.result") == []             # 恰一条终局事件

    async def test_long_output_spills(self, tmp_path: Path):
        """F039:输出 >2KB → spill 原文落私有区,result 带 truncated+spill_ref,
        摘要 ≤2KB(上下文不放大输出)。"""
        big = "x" * 5000                                # 5KB > SPILL_THRESHOLD
        ex, sess, scope, ctx = self._setup(tmp_path, Recorder({"content": big}))
        spill = FakeSpill()
        ctx = make_ctx(sess, scope, chain=ctx.guard, spill=spill)
        r = await ex.execute(ToolCall(name="fs.read_file",
                                      raw_args={"path": "a"},
                                      call_id="c32"), ctx)
        assert r.ok and r.truncated is True
        assert r.spill_ref and r.spill_ref["ref"].startswith("spill/fs.read_file")
        assert len(r.summary) <= 2000 and "spill:" in r.summary
        payload = sess.last_of("tool.result")["payload"]
        assert payload["truncated"] is True
        put_text, kind = spill.put_calls[0]
        assert kind == "fs.read_file"
        assert payload["spill_ref"]["chars"] == len(put_text)  # spill 原文即档案
        assert "x" * 5000 in put_text                   # 原文入 spill(不丢事实)

    async def test_small_output_plain_summary(self, tmp_path: Path):
        """≤2KB 输出:直读摘要,不落 spill。"""
        ex, sess, scope, ctx = self._setup(tmp_path, Recorder({"content": "hello"}))
        spill = FakeSpill()
        ctx = make_ctx(sess, scope, chain=ctx.guard, spill=spill)
        r = await ex.execute(ToolCall(name="fs.read_file",
                                      raw_args={"path": "a"},
                                      call_id="c33"), ctx)
        assert r.ok and r.truncated is False and r.spill_ref is None
        assert spill.put_calls == []
        assert sess.last_of("tool.result")["payload"]["summary"] == \
            '{"content":"hello"}'

    async def test_spill_unwired_pers221(self, tmp_path: Path):
        """storage.spill 未接线 + 超长输出 → tool.error(PERS-221)回喂。"""
        ex, sess, scope, ctx = self._setup(tmp_path, Recorder({"content": "y" * 5000}))
        r = await ex.execute(ToolCall(name="fs.read_file",
                                      raw_args={"path": "a"},
                                      call_id="c34"), ctx)
        assert not r.ok
        err = sess.of("tool.error")[0]["payload"]
        assert err["code"] == "PERS-221"
        assert sess.of("tool.result") == []


# ================================================================ F022 批量
class TestExecuteToolCalls:
    def _reg(self, prov: Recorder, name: str = "fs.read_file",
             schema: dict = READ_SCHEMA) -> ToolRegistry:
        defn = mk_defn(name=name, schema=schema)
        reg = make_registry(defn)
        reg.bind_provider(defn.name, prov)
        return reg

    async def test_serial_order_and_parent_trace(self, tmp_path: Path):
        """批量串行保序;每条事件 trace 关联父 llm.response(§3.5 配对)。"""
        sess, prov = AsyncSess(), Recorder({"ok": 1})
        reg = self._reg(prov)
        ctx = make_ctx(sess, FakeScope(str(tmp_path)), chain=GuardChain(session=sess))
        raw_calls = [
            {"id": "b1", "name": "fs.read_file", "arguments": '{"path": "a"}'},
            {"id": "b2", "name": "fs.read_file", "arguments": '{"path": "b"}'},
        ]
        results = await ToolExecutor(reg).execute_tool_calls(
            raw_calls, ctx, parent_seq=11)
        assert [r.ok for r in results] == [True, True]
        assert prov.calls == 2
        paths = [c["payload"]["args"]["path"] for c in sess.of("tool.call")]
        assert paths == ["a", "b"]                      # 串行保序
        assert all(c["trace"] == {"parent_seq": 11}
                   for c in sess.of("tool.call"))

    async def test_param_fail_streak_breaks_turn(self, tmp_path: Path):
        """F026:同工具参数连败 2 → 终止该轮,第 3 条不执行。"""
        sess, prov = AsyncSess(), Recorder({"ok": 1})
        reg = self._reg(prov)
        ctx = make_ctx(sess, FakeScope(str(tmp_path)), chain=GuardChain(session=sess))
        raw_calls = [
            {"id": "b1", "name": "fs.read_file", "arguments": '{"path": "a", "x": 1}'},
            {"id": "b2", "name": "fs.read_file", "arguments": '{"path": 123}'},
            {"id": "b3", "name": "fs.read_file", "arguments": '{"path": "ok"}'},
        ]
        results = await ToolExecutor(reg).execute_tool_calls(raw_calls, ctx)
        assert len(results) == 2                        # 第 3 条被终止
        assert all(not r.ok for r in results)
        assert all(r.summary.startswith("参数校验失败") for r in results)
        assert prov.calls == 0                          # 零执行
        assert all(e["payload"]["call_id"] in ("b1", "b2")
                   for e in sess.of("tool.error"))

    async def test_parse_fail_streak_breaks_turn(self, tmp_path: Path):
        """解析失败(非法工具名形态)连败 2 → 终止轮;错误事件 name 保留尝试名。"""
        sess, prov = AsyncSess(), Recorder({"ok": 1})
        reg = self._reg(prov)
        ctx = make_ctx(sess, FakeScope(str(tmp_path)), chain=GuardChain(session=sess))
        raw_calls = [
            {"id": "b1", "name": "GhostTool", "arguments": "{}"},  # NAME_RE 拒
            {"id": "b2", "name": "GhostTool", "arguments": "{}"},
            {"id": "b3", "name": "fs.read_file", "arguments": '{"path": "ok"}'},
        ]
        results = await ToolExecutor(reg).execute_tool_calls(raw_calls, ctx)
        assert len(results) == 2                        # 连败 2 终止(b3 未执行)
        assert prov.calls == 0
        errs = [e["payload"] for e in sess.of("tool.error")]
        assert [e["code"] for e in errs] == ["TLB-802", "TLB-802"]
        assert [e["name"] for e in errs] == ["GhostTool", "GhostTool"]


# ================================================================ 门面与杂项
class TestFacadeAndHelpers:
    def test_register_and_schemas_for_passthrough(self):
        reg = ToolRegistry()
        ex = ToolExecutor(reg)
        name = ex.register(mk_defn(), provider=Recorder())
        assert name == "fs.read_file"
        assert reg.has("fs.read_file")
        scope = types.SimpleNamespace(can_use=lambda n: n == "fs.read_file")
        schemas = ex.schemas_for(scope)
        assert len(schemas) == 1
        assert schemas[0]["function"]["name"] == "fs.read_file"

    def test_register_definition_alias_roundtrip(self):
        ex = ToolExecutor()
        defn = mk_defn(name="fs.list_dir")
        assert ex.register_definition(defn) == "fs.list_dir"
        assert ex.schemas_for()[0]["function"]["name"] == "fs.list_dir"
        ex.unregister_definition("fs.list_dir")
        assert ex.schemas_for() == []
        with pytest.raises(PyHError) as ei:
            ex.unregister_definition("fs.list_dir")
        assert ei.value.code == "TLB-802"               # 不存在幂等拒绝

    def test_turn_failure_counters(self):
        ex = ToolExecutor()
        assert ex.mark_turn_failure("t1") == 1
        assert ex.mark_turn_failure("t1") == 2
        assert ex._check_fail_streak("t1") is True
        assert ex._check_fail_streak("t2") is False
        ex.reset_turn_failures()                        # 每轮起点清零
        assert ex._check_fail_streak("t1") is False

    def test_summarize_and_text_helpers(self):
        assert summarize(None) == "(无参数)"
        s = summarize({"content": "x" * 500, "path": "a/b"})
        assert s.startswith("path=a/b")
        assert "…" in s                                 # 超长值截断
        s2 = summarize({"k1": 1, "k2": [1, 2, 3]})
        assert "k2=<list 3项>" in s2
        assert len(summarize_text("字" * 3000)) <= 2000
        assert summarize_text("short") == "short"
        # 文本化:str 原样 / dict JSON / None 空串
        assert render_result_text("hi") == "hi"
        assert render_result_text({"a": 1}) == '{"a":1}'
        assert render_result_text(None) == ""
        assert render_result_text(42) == "42"

    async def test_duck_llm_toolcall_coerced(self, tmp_path: Path):
        """execute 兼容 llm.ToolCall 形态鸭子(name/raw_args/call_id)。"""
        sess, prov = AsyncSess(), Recorder({"ok": True})
        reg = self_reg(prov)
        ctx = make_ctx(sess, FakeScope(str(tmp_path)), chain=GuardChain(session=sess))
        duck = types.SimpleNamespace(name="fs.read_file",
                                     raw_args={"path": "duck"},
                                     call_id="call_duck", id="call_duck")
        r = await ToolExecutor(reg).execute(duck, ctx)
        assert r.ok
        assert prov.last == {"path": "duck"}
        assert sess.of("tool.result")[0]["payload"]["call_id"] == "call_duck"


def self_reg(prov: Recorder) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register_tool(mk_defn())
    reg.bind_provider("fs.read_file", prov)
    return reg
