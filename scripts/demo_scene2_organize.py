"""demo_scene2_organize.py — 场景二:整理文件夹(无人值守工具链,面试录屏用)。

需求文档 §3.2 验收:用户一句话 → agent 自己规划 → 列目录 → 读文件 →
归类 → 写新文件 → 汇报。全程不插手。
"""
import asyncio, os, sys, tempfile
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pyharness.config import load_settings
from pyharness import engine
from pyharness.core.task_queue import TaskQueue

# 演示杂文件夹(4 篇笔记,2 主题)
FILES = {
    "报销流程备忘.txt": "报销需要发票和审批单,财务部每周三处理,提交后 5 个工作日到账。",
    "周会记录-产品组.txt": "周会讨论了新功能排期:下周一上线搜索优化,周三灰度。",
    "年度体检提醒.txt": "体检预约在 12 月前完成,带身份证,空腹项目需提前 8 小时禁食。",
    "需求评审纪要.txt": "评审通过:登录页改版,涉及 3 个接口,后端排期 2 周。",
}
TOPICS = {"工作": ["报销", "周会", "评审", "产品", "功能", "接口", "上线"],
          "生活": ["体检", "预约", "健身", "买菜"]}


async def main() -> int:
    assert os.environ.get("DEEPSEEK_API_KEY", "").startswith("sk-"), "key 未设置"
    cfg = load_settings()
    sid = "scene2-demo-0001"

    # 演示目录 = workspace 根(与 engine 装配同源)
    ws = Path(cfg.storage.workspaces_dir).expanduser()
    messy = ws / sid
    sess_dir = ws / "sessions"
    if messy.exists():
        import shutil
        shutil.rmtree(messy)
    if (sess_dir / f"{sid}.jsonl").exists():      # 清上次运行残留(created 唯一)
        (sess_dir / f"{sid}.jsonl").unlink()
    messy.mkdir(parents=True)
    for name, content in FILES.items():
        (messy / name).write_text(content, encoding="utf-8")
    print(f"演示文件夹: {messy}")
    print(f"  内容: {list(FILES)}")

    # 装配真实引擎(fs.* 工具 + guard + 审批)
    ctx = await engine.assemble_real_engine(cfg, sid=sid, sessions_dir=ws / "sessions")
    await ctx.session.append("session.created",
                             {"title": "场景二 整理文件夹", "model": cfg.llm.model},
                             actor="system", sync=True)

    prompt = (f"帮我整理文件夹 {sid} 里的笔记,按主题归类:先列出目录,逐个读文件内容,"
              f"把文件复制到对应的主题子文件夹里(文件名前面加主题前缀即可),"
              f"最后汇报你整理了几个文件、分了几类。")
    env = await ctx.session.append("user.message", {"content": prompt},
                                   actor="user", sync=True)

    q = TaskQueue(session=ctx.session, runner=ctx.make_runner())
    tid = await q.submit(prompt, meta={"session_id": sid})
    print(f"\n任务已提交 task_id={tid},agent 开始干活(无人值守)...\n")

    # 等完成(最长 240s)
    for _ in range(480):
        if tid in getattr(q, "_done", {}):
            break
        await asyncio.sleep(0.5)
    res = getattr(q, "_done", {}).get(tid)
    print(f"任务结果: ok={res.ok if res else 'TIMEOUT'} "
          f"code={res.code if res and not res.ok else '—'}")

    # 事件审计:工具链调用轨迹
    evs = list(ctx.session.events_after(0))
    print(f"\n=== 事件流水({len(evs)} 条)===")
    for e in evs:
        t, p = e.type, e.payload
        if t == "tool.call":
            print(f"  🔧 {p.get('name')} args={str(p.get('args',''))[:90]}")
        elif t == "tool.result":
            ok = p.get("ok")
            print(f"  {'✅' if ok else '❌'} tool.result ok={ok} "
                  f"summary={str(p.get('summary',''))[:80]}")
        elif t == "agent.message":
            print(f"  💬 agent: {str(p.get('content',''))[:120]}")
        elif t in ("guard.evaluated", "guard.rejected"):
            print(f"  🛡 {t}: {str(p)[:90]}")
        elif t in ("llm.request",):
            pass
        else:
            print(f"  · {t}")

    # 结果目录
    print(f"\n=== 整理结果(workspace/{sid})===")
    for p in sorted(messy.rglob("*")):
        if p.is_file():
            print(f"  📄 {p.relative_to(ws)} ({p.stat().st_size}B)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
