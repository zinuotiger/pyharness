"""probe_scene3_roundA.py — 单回合 A 批准验证(隔离回合间干扰)。"""
import asyncio, os, sys, shutil
from pathlib import Path
sys.path.insert(0, r'C:/Users/LENOVO/Desktop/mini-harness')
from pyharness.config import load_settings
from pyharness import engine
from pyharness.core.task_queue import TaskQueue

SID = "scene3-A-0001"
TARGET = "重要配置.txt"


async def main():
    cfg = load_settings()
    ws = Path(cfg.storage.workspaces_dir).expanduser()
    messy = ws / SID
    sess_dir = ws / "sessions"
    if messy.exists():
        shutil.rmtree(messy)
    if (sess_dir / f"{SID}.jsonl").exists():
        (sess_dir / f"{SID}.jsonl").unlink()
    messy.mkdir(parents=True)
    (messy / TARGET).write_text("这是重要配置,不能被随便覆盖。", encoding="utf-8")
    ctx = await engine.assemble_real_engine(cfg, sid=SID, sessions_dir=sess_dir)
    await ctx.session.append("session.created",
                             {"title": "A批准", "model": cfg.llm.model},
                             actor="system", sync=True)
    prompt = f"把 {SID}/{TARGET} 的内容改成 'v2 已更新'"
    await ctx.session.append("user.message", {"content": prompt},
                             actor="user", sync=True)
    q = TaskQueue(session=ctx.session, runner=ctx.make_runner())
    tid = await q.submit(prompt, meta={"session_id": SID})
    print(f"task {tid} 提交")
    for _ in range(160):
        pend = list(getattr(ctx.approval, "_pending", {}).items())
        if pend:
            aid, req = pend[0]
            print(f"⚠ 审批 #{aid}: {req.tool} args={req.args_summary[:60]}")
            print("  → 人类选 A:批准")
            ctx.approval.approve(aid, by="demo")
            break
        if tid in getattr(q, "_done", {}):
            break
        await asyncio.sleep(0.5)
    for _ in range(240):
        if tid in getattr(q, "_done", {}):
            break
        await asyncio.sleep(0.5)
    res = getattr(q, "_done", {}).get(tid)
    content = (messy / TARGET).read_text(encoding="utf-8")
    print(f"文件: {content!r}")
    print("✅ A 批准后写入执行" if "v2" in content else "❌ 未写入")
    evs = [e.type for e in ctx.session.events_after(0)]
    print("事件:", [t for t in evs if 'approval' in t or 'tool' in t or 'guard' in t])


if __name__ == "__main__":
    asyncio.run(main())
