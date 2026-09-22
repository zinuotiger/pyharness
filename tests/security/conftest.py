"""tests/security/ — 安全测试：**证明被禁止的行为确实失败**。

与 `tests/unit` 的分工：unit 证明"功能按设计工作"；security 证明"**绕过/伪造/
篡改/丢失上下文时系统拒绝**，且拒绝是**零副作用**的"。

断言纪律（本目录强制）：

1. **不得只断言日志字符串**——凡涉及"没有执行"，必须给出**外部副作用证据**
   （文件是否存在/内容是否改变）或**Provider 零调用**证据；
2. **不得只断言 ``execution=false`` 之类的字段**——那是被测系统自述，不是证据；
3. 每条用例应能在**撤掉对应防线**时失败（有鉴别力），否则它不是安全测试。

夹具：``sec_exec``（轻量真实管道）/ ``e2e_factory``（真实引擎，见
``tests/conftest.py``）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from pyharness.config import load_settings
from pyharness.core import llm as llm_mod
from pyharness.core import tool_fs
from pyharness.core.scope import BudgetLimits, Scope, ScopePolicy
from pyharness.core.tools_executor import ToolExecutor
from pyharness.core.tools_guard import GuardChain
from pyharness.core.tools_registry import ToolRegistry
from pyharness.governance import (AuditSystem, DecisionEngine, EvidenceCollector,
                                  GovernanceContext, ReceiptStore)
from pyharness.governance.decision import Decision, Principal, PrincipalKind


class FakeSession:
    """最小 SessionLog 替身：记录 append 并可按 seq 回放（供治理重放面消费）。"""

    def __init__(self, sid: str = "s-sec-000001") -> None:
        self.session_id = sid
        self.sid = sid
        self._ev: list = []
        self._seq = 0

    async def append(self, type_, payload, *, actor="system", sync=False,
                     trace=None, origin=None, task_id=None):
        self._seq += 1
        env = SimpleNamespace(seq=self._seq, type=type_, payload=payload,
                              actor=actor, trace=trace or {},
                              session_id=self.session_id, origin=origin)
        self._ev.append(env)
        return env

    def events_after(self, seq):
        return [e for e in self._ev if e.seq > int(seq or 0)]


class PolicyEngineStub:
    """GovernanceContext 需要的最小策略面（链 + 指纹）。"""

    def __init__(self, guard) -> None:
        self.chain = guard

    def current(self, scope=None):
        return SimpleNamespace(fingerprint=lambda: "fp-sec")

    def fingerprint(self) -> str:
        return "fp-sec"


class CountingProvider:
    """包裹真实 Provider，**计数调用次数**——"零执行"的强证据。"""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.calls = 0

    async def handle(self, args, ctx):
        self.calls += 1
        return await self._inner(args, ctx)


class SecHarness:
    """真实管道 + 可注入身份的 ctx。"""

    def __init__(self, tmp, *, channel="desktop", governance=True,
                 danger_marks=True) -> None:
        self.tmp = tmp
        self.ws = tmp / "workspaces"
        self.ws.mkdir(parents=True, exist_ok=True)
        cfg = load_settings()
        cfg.storage.root = str(tmp)
        cfg.storage.workspaces_dir = str(self.ws)
        self.session = FakeSession()
        self.reg = ToolRegistry()
        tool_fs.register(self.reg)
        self.guard = GuardChain(session=self.session,
                                validator=self.reg.validate_args)
        pol = ScopePolicy()
        pol.workspace_root = str(self.ws)
        if danger_marks:
            pol.danger_marks = [
                {"pattern": d.name, "level": str(getattr(d, "danger", "none"))}
                for d in self.reg.iter_definitions()
                if str(getattr(d, "danger", "none")) != "none"]
        self.scope = Scope(pol, BudgetLimits.from_cfg(cfg),
                           session_id=self.session.sid,
                           counters=llm_mod.UsageCounters(),
                           session=self.session)
        self.gov = (GovernanceContext(
            policy=PolicyEngineStub(self.guard),
            decisions=DecisionEngine(), receipts=ReceiptStore(),
            evidence=EvidenceCollector(session=self.session),
            audit=AuditSystem(session=self.session)) if governance else None)
        self.executor = ToolExecutor(self.reg)
        self.providers: dict[str, CountingProvider] = {}
        for name in ("fs.write_file", "fs.read_file", "fs.delete_file",
                     "fs.list_dir"):
            inner = tool_fs.PROVIDERS[name]
            cp = CountingProvider(inner)
            self.providers[name] = cp
            self.reg._providers[name] = cp          # 替换 Provider(计数面)
        self.ctx = SimpleNamespace(
            session=self.session, scope=self.scope, guard=self.guard,
            governance=self.gov, approval=None,
            storage=SimpleNamespace(spill=None), channel=channel)

    def provider_calls(self, name: str) -> int:
        return self.providers[name].calls


@pytest.fixture
def sec(tmp_path):
    return SecHarness(tmp_path)


__all__ = ["FakeSession", "SecHarness", "CountingProvider",
           "Principal", "PrincipalKind", "Decision", "pytest"]
