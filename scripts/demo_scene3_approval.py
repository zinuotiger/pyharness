"""demo_scene3_approval.py — 场景三:危险拦截审批(A 批准 / B 拒绝)。

用法: python scripts/demo_scene3_approval.py A   # 现场选 A 批准
      python scripts/demo_scene3_approval.py B   # 现场选 B 拒绝

流程: agent 想覆写 workspace 里已存在的重要文件 → guard 转审批弹窗 →
人类裁决: A=放行执行 / B=拒绝拦截。跑完看文件是否被动 + 事件审计。
"""
import asyncio, os, sys, shutil
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pyharness.config import load_settings
from pyharness import engine
from pyharness.core.task_queue import TaskQueue

DECISION = sys.argv[1].upper() if len(sys.argv) > 1 else "B"
assert DECISION in ("A", "B"), "参数须为 A(批准)或 B(拒绝)"
SID = f"scene3-{DECISION}-0001"
TARGET = "重要配置.txt"


async def main() -> int:
    assert os.environ.get("DEEPSEEK_API_KEY", "").startswith("sk-"), "key 未设置"
    cfg = load_settings()
    ws = Path(cfg.storage.workspaces_dir).expanduser()
    messy = ws / SID
    sess_dir = ws / "sessions"
    for p in (messy, sess_dir / f"{SID}.jsonl"):
        if p.exists():
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
    messy.mkdir(parents=True)
    (messy / TARGET).write_text("这是重要配置,不能被随便覆盖。", encoding="utf-8")

    print(f"=== 场景三:危险操作审批(人类选 {DECISION})===\n")
    print(f"预置: workspace/{SID}/{TARGET} = 重要配置文件(已存在)\n")

    ctx = await engine.assemble_real_engine(cfg, sid=SID, sessions_dir=sess_dir)
    await ctx.session.append("session.created",
                             {"title": f"场景三 审批{DECISION}",
                              "model": cfg.llm.model}, actor="system", sync=True)
    prompt = f"把 {SID}/{TARGET} 的内容改成 'v2 已更新',然后汇报"
    await ctx.session.append("user.message", {"content": prompt},
                             actor="user", sync=True)
    q = TaskQueue(session=ctx.session, runner=ctx.make_runner())
    tid = await q.submit(prompt, meta={"session_id": SID})
    print(f"🤖 agent 已开工,目标:覆写 {TARGET}(已存在文件 → 应触发审批)\n")

    # 等审批请求弹出(最长 90s)
    aid = None
    for _ in range(180):
        pend = list(getattr(ctx.approval, "_pending", {}).items())
        if pend:
            aid, req = pend[0]
            print(f"  ⚠️ 审批请求 #{aid}")
            print(f"     工具: {req.tool}")
            print(f"     参数: {req.args_summary[:100]}")
            print(f"     风险: {getattr(req, 'danger', 'high')}")
            print(f"\n  ──────────────────────────────────────────")
            print(f"  🧑‍💻 人类裁决:选 {DECISION} {'✅ 批准,放行执行' if DECISION == 'A' else '🚫 拒绝,拦截操作'}")
            print(f"  ──────────────────────────────────────────\n")
            if DECISION == "A":
                ctx.approval.approve(aid, by="demo")
            else:
                ctx.approval.deny(aid, by="demo")
            break
        if tid in getattr(q, "_done", {}):
            print("⚠ 任务已结束但未见审批请求(agent 未尝试覆写)")
            break
        await asyncio.sleep(0.5)

    # 等任务终态
    for _ in range(240):
        if tid in getattr(q, "_done", {}):
            break
        await asyncio.sleep(0.5)
    res = getattr(q, "_done", {}).get(tid)
    print(f"任务终态: ok={res.ok if res else '?'}")

    content = (messy / TARGET).read_text(encoding="utf-8")
    print(f"\n文件最终内容: {content!r}")
    if DECISION == "A":
        print("✅ A 批准 → 覆写执行成功(granted 后重入 guard 链放行)")
        ok = "v2" in content
    else:
        print("✅ B 拒绝 → 文件未被改动(critical 拦截语义:人类拒绝=不执行)")
        ok = "v2" not in content
    print("结果符合预期 ✅" if ok else "结果异常 ❌")

    # 事件审计(审批链完整证据)
    evs = [e for e in ctx.session.events_after(0)
           if e.type in ("tool.call", "guard.evaluated", "guard.rejected",
                         "approval.requested", "approval.granted",
                         "approval.denied", "tool.result", "agent.message")]
    print(f"\n事件审计({len(evs)}):")
    for e in evs:
        p = e.payload
        extra = {"approval.requested": f"tool={p.get('tool')}",
                 "approval.granted": f"by={p.get('by')}",
                 "approval.denied": f"by={p.get('by')}",
                 "tool.call": f"{p.get('name')}",
                 "guard.evaluated": f"d={p.get('decision')} g={p.get('guard_id')}",
                 "tool.result": f"ok={p.get('ok')}"}.get(e.type, "")
        print(f"  {e.seq:>2} {e.type:<22} {extra}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
