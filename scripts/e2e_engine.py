"""e2e_engine.py — 真实引擎装配端到端(engine.py 验证,收尾波)。

用法: export DEEPSEEK_API_KEY=sk-xxx; .venv/Scripts/python.exe scripts/e2e_engine.py

验证: assemble_real_engine → session.created → task_queue+runner → loop.wake
      → 真实 DeepSeek 回话 → 事件全落盘 → timeline 派生。
"""
import asyncio, os, sys, tempfile
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pyharness.config import load_settings
from pyharness import engine
from pyharness.core.session import open_session
from pyharness.core.task_queue import TaskQueue
from pyharness.persistence import open_store


async def main() -> int:
    assert os.environ.get("DEEPSEEK_API_KEY", "").startswith("sk-"), "key 未设置"
    cfg = load_settings()
    sid = "engine-e2e-0001"
    tmp = Path(tempfile.mkdtemp(prefix="ph-engine-"))
    cfg.storage.sessions_dir = str(tmp)

    # 1. 真实引擎装配
    print("=== 1. assemble_real_engine ===")
    ctx = await engine.assemble_real_engine(cfg, sid=sid, sessions_dir=tmp)
    print(f"ctx: llm={type(ctx.llm).__name__} loop={type(ctx.loop).__name__} "
          f"scope={type(ctx.scope).__name__}")

    # 2. session.created 首事件
    await ctx.session.append("session.created",
                             {"title": "engine e2e", "model": cfg.llm.model},
                             actor="system", sync=True)
    print("created seq=1 落盘 ✅")

    # 3. 发消息 + 任务队列 runner 驱动真实 loop
    print("\n=== 2. 真实对话(DeepSeek)===")
    env = await ctx.session.append("user.message", {"content": "用一句话回答:你是谁?并说 engine e2e 通"},
                                   actor="user", sync=True)
    q = TaskQueue(session=ctx.session, runner=ctx.make_runner())
    tid = await q.submit("用一句话回答:你是谁?并说 engine e2e 通",
                         meta={"session_id": sid})
    # 等任务完成(轮询 _done 终态缓存;泵后台排空,最长 120s)
    for _ in range(240):
        if tid in getattr(q, "_done", {}):
            break
        await asyncio.sleep(0.5)
    res = getattr(q, "_done", {}).get(tid)
    print(f"user.message seq={env.seq} task_id={tid} "
          f"result={res.ok if res else 'TIMEOUT'} "
          f"loop_last_reason={ctx.loop.state_snapshot()['last_reason']}")

    # 4. 事件落盘 → timeline 派生(验证全链)
    print("\n=== 3. 事件审计 ===")
    evs_mem = list(ctx.session.events_after(0))   # 内存 log_ 全量
    types = [e.type for e in evs_mem]
    print(f"内存 {len(evs_mem)} 事件: {types}")
    agent_msgs = [e for e in evs_mem if e.type == "agent.message"]
    if agent_msgs:
        print(f"\nAgent 回复: {agent_msgs[-1].payload.get('content', '')[:300]}")
    # 落盘侧:flush store 后重开验证持久化
    store = ctx.engine_spine.persistence
    await store.flush()
    store2 = open_store(sid, dir=tmp)
    log2 = await open_session(sid, store2)
    evs2 = list(log2.events_after(0))
    print(f"落盘 {len(evs2)} 事件: {[e.type for e in evs2]}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
