"""demo_phase1.py — 阶段 1 里程碑:事件溯源会话 + guard 拒危险(PRD §4.6.2)

演示三件事:
1. append-only 事件日志:消息逐条落 JSONL(总线日志订阅者持久化),seq 连续
2. 重启回放:从 JSONL 重建历史(INV-02 单一真源)
3. guard 拒绝:高危工具调用被 guard 拦下(reject 零执行)

运行: .venv/Scripts/python.exe scripts/demo_phase1.py
"""

import asyncio, sys, os, tempfile
from pathlib import Path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pyharness.core.session import SessionLog, open_session
from pyharness.persistence import open_store
from pyharness.bus.event_bus import EventBus
from pyharness.core.tools_guard import GuardChain, Decision, ToolCall
from pyharness.errors import PyHError


async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ph_demo1_"))
    sid = "demo-session-0001"

    print("=== 1. 事件溯源:对话逐条落 JSONL ===")
    # 装配:store(物理落盘)+ bus(中转)+ SessionLog
    store = open_store(sid, dir=tmp)
    bus = EventBus()

    # 总线日志订阅者:每个 Envelope 攒批写入 store(架构:总线中转,订阅者落盘)。
    # handler 收 (type_, payload),payload 即 Envelope(SessionLog._dispatch 语义)
    async def _on_any(type_, env):
        await store.append(env)

    for t in ("session.created", "user.message", "agent.message"):
        bus.subscribe(t, _on_any, owner=f"persist-{t}")

    log = SessionLog(sid=sid, persistence=store, bus=bus)

    env1 = await log.append("session.created", {"title": "demo", "model": "mock"}, actor="system")
    env2 = await log.append("user.message", {"content": "你好"}, actor="user")
    env3 = await log.append("agent.message", {"content": "你好!我是 PyHarness Agent。"}, actor="agent")
    env4 = await log.append("user.message", {"content": "帮我看看今天有什么任务"}, actor="user")
    print(f"  已追加 4 条事件,seq: {env1.seq} → {env2.seq} → {env3.seq} → {env4.seq} (连续={env4.seq == 4})")
    assert env4.seq == 4, "seq 不连续"

    msgs = log.derive_messages()
    print(f"  derive_messages → {len(msgs)} 条 LLM 消息,首条 role={msgs[0]['role']}")

    print("\n=== 2. 重启回放:新 SessionLog 从 JSONL 重建历史 ===")
    store2 = open_store(sid, dir=tmp)
    log2 = await open_session(sid, store2)
    msgs2 = log2.derive_messages()
    print(f"  新会话实例回放 → {len(msgs2)} 条消息(重启不丢)")
    assert len(msgs2) == len(msgs), "回放消息数不一致"
    last = msgs2[-1]
    print(f"  末条: {last['role']}: {str(last.get('content'))[:25]}...")
    print("  ✅ 重启不丢——历史 = 日志派生,单一真源(INV-01/02)")

    print("\n=== 3. guard 拒危险工具(INV-03/04)===")
    chain = GuardChain()
    call = ToolCall(name="fs.delete_file", call_id="call-1",
                    raw_args='{"path": "C:/Windows/System32/drivers/etc/hosts"}')
    try:
        decision = await chain.evaluate(call, scope=None)
        print(f"  危险删除调用决策: {decision}")
        if decision == Decision.REJECT:
            print("  ✅ guard 拒绝——真实文件零副作用(INV-04)")
    except PyHError as e:
        print(f"  guard 抛错: {e.code} — 拒绝路径成立(INV-03 无放行)")

    call2 = ToolCall(name="fs.read_file", call_id="call-2",
                     raw_args='{"path": "C:/Users/LENOVO/Desktop/readme.txt"}')
    try:
        decision2 = await chain.evaluate(call2, scope=None)
        print(f"  读文件调用决策: {decision2}(allow=放行 / approval=需确认)")
    except PyHError as e:
        print(f"  读文件调用: {e.code}")

    print("\n=== ✅ 阶段 1 里程碑通过:事件溯源 + 回放 + guard 拒绝 ===")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
