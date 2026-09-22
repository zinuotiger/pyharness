"""安全:参数篡改(parameter tampering)——批准与执行必须绑定同一参数集合。

证明点：**人类批准的是参数集合 A，执行参数集合 B 时系统拒绝**，且文件未被改动。
"""
from __future__ import annotations

from pyharness.core.tools_guard import ToolCall


class _EchoApproval:
    """审批替身:总批准,并把 executor 传入的绑定指纹原样"记住"。"""

    def __init__(self, granted_binding):
        self._granted = granted_binding
        self.seen_binding = None

    async def request(self, call, args_summary, ctx, *, ttl_ms=None, binding=None):
        self.seen_binding = binding
        return "granted"

    def grant_binding(self, call_id):
        return self._granted

    def approval_ref_of(self, call_id):
        return 1


async def test_tampered_binding_is_rejected_and_file_untouched(sec):
    """批准绑定 ≠ 本次调用绑定 ⇒ GRD-403,Provider 零调用,文件原样。"""
    target = sec.ws / "note.txt"
    target.write_text("ORIGINAL", encoding="utf-8")
    sec.ctx.approval = _EchoApproval("BINDING-FOR-DIFFERENT-ARGS")

    res = await sec.executor.execute(
        ToolCall(name="fs.write_file",
                 raw_args={"path": "note.txt", "content": "TAMPERED"},
                 call_id="tamp-1"), sec.ctx)

    assert res.ok is False
    assert "绑定校验失败" in res.summary and "GRD-403" in res.summary
    assert sec.provider_calls("fs.write_file") == 0       # ★ 零执行
    assert target.read_text(encoding="utf-8") == "ORIGINAL"   # ★ 零副作用
    assert [e.type for e in sec.session.events_after(0)].count("tool.result") == 0


async def test_matching_binding_executes(sec):
    """对照组：绑定一致时**必须放行执行**（否则上一条用例会因"永远拒绝"而假绿）。"""
    target = sec.ws / "note.txt"
    target.write_text("ORIGINAL", encoding="utf-8")

    class _SelfEcho:
        async def request(self, call, args_summary, ctx, *, ttl_ms=None,
                          binding=None):
            self.b = binding
            return "granted"

        def grant_binding(self, call_id):
            return self.b

        def approval_ref_of(self, call_id):
            return 1

    sec.ctx.approval = _SelfEcho()
    res = await sec.executor.execute(
        ToolCall(name="fs.write_file",
                 raw_args={"path": "note.txt", "content": "APPROVED"},
                 call_id="tamp-2"), sec.ctx)
    assert res.ok is True, f"绑定一致应放行,实际 {res.summary}"
    assert sec.provider_calls("fs.write_file") == 1
    assert target.read_text(encoding="utf-8") == "APPROVED"


async def test_binding_is_bound_to_full_argument_set(sec):
    """绑定指纹覆盖**全部参数**:仅改一个字符即应得到不同绑定。"""
    from pyharness.core.tools_executor import _approval_binding

    a = _approval_binding(ToolCall(name="fs.write_file", raw_args={},
                                   call_id="x"),
                          {"path": "f.txt", "content": "amount=100"})
    b = _approval_binding(ToolCall(name="fs.write_file", raw_args={},
                                   call_id="x"),
                          {"path": "f.txt", "content": "amount=1000"})
    assert a != b


async def test_approval_without_channel_fails_closed(sec):
    """headless 无通道时审批不可用 ⇒ APR-501,零执行(不静默放行)。"""

    class _NoChannel:
        async def request(self, call, args_summary, ctx, *, ttl_ms=None,
                          binding=None):
            from pyharness.errors import raise_code
            raise_code("APR-501", tool=call.name)

    sec.ctx.approval = _NoChannel()
    target = sec.ws / "note.txt"
    target.write_text("ORIGINAL", encoding="utf-8")
    res = await sec.executor.execute(
        ToolCall(name="fs.write_file",
                 raw_args={"path": "note.txt", "content": "X"}, call_id="apr-1"),
        sec.ctx)
    assert res.ok is False
    assert "APR-501" in res.summary
    assert sec.provider_calls("fs.write_file") == 0
    assert target.read_text(encoding="utf-8") == "ORIGINAL"

