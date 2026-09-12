"""probe_exec_approval.py — exec 沙箱族真实 LLM 探针(2026-09-08,P3 真链 #2)。

用法:python scripts/probe_exec_approval.py(DEEPSEEK_API_KEY 在位;直连 DeepSeek)
验证:真实 LLM 决策调 exec.shell_run → guard 高危拦截 → approval.requested →
探针模拟桌面用户 approve → 真实子进程执行 → tool.result/agent.message 回流。
模型若不调工具会 FAIL(打印提示;可重跑,模型行为有随机性)。
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from pyharness.config import load_settings          # noqa: E402
from pyharness.core.task_queue import TaskQueue     # noqa: E402
from pyharness.engine import assemble_real_engine   # noqa: E402

_MARK = f"PYH_EXEC_REAL_{os.getpid()}_OK"


async def main() -> int:
    _envf = pathlib.Path(__file__).resolve().parents[1] / ".env"
    if _envf.exists():
        for _line in _envf.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if _line.startswith("DEEPSEEK_API_KEY="):
                os.environ.setdefault("DEEPSEEK_API_KEY",
                                      _line.split("=", 1)[1].strip())
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="pyh_exec_"))
    cfg = load_settings()
    cfg.storage.root = str(tmp)
    cfg.storage.sessions_dir = str(tmp / "sessions")
    cfg.storage.workspaces_dir = str(tmp / "workspaces")
    cfg.storage.spill_dir = str(tmp / "spill")
    cfg.storage.db_path = str(tmp / "pyharness.db")
    cfg.security.policy.preset = "standard"          # 放宽域约束→exec 可见
    if os.environ.get("DEEPSEEK_API_KEY"):
        # DeepSeek 直连(官方 OpenAI 兼容端点)
        cfg.llm.base_url = os.environ.get("PYH_BASE_URL",
                                          "https://api.deepseek.com/v1")
        cfg.llm.api_key = "env:DEEPSEEK_API_KEY"
        cfg.llm.model = os.environ.get("PYH_MODEL", "deepseek-chat")
    sid = f"s-exec-{os.getpid()}"
    ctx = await assemble_real_engine(cfg, sid=sid, sessions_dir=tmp / "sessions")
    await ctx.session.append("session.created",
                             {"title": "", "model": cfg.llm.model},
                             actor="system", sync=True)
    text = (f"请调用沙箱执行工具 exec.shell_run 运行命令 echo {_MARK},"
            "把输出原样告诉我。这是你唯一的任务。")
    await ctx.session.append("user.message", {"content": text},
                             actor="user", sync=True)
    q = TaskQueue(ctx.session, runner=ctx.task_runner, max_queue=8)
    tid = await q.submit(text)
    provider = getattr(ctx, "approval", None) or getattr(
        getattr(ctx, "engine_spine", None), "approval", None)
    if provider is None:
        print("NO-APPROVAL-PROVIDER: 装配缺 approval 桥"); return 1
    task = asyncio.create_task(q.wait_for(tid))
    approved: set[int] = set()
    while not task.done():
        for ev in ctx.session.events_after(0):
            if (ev.type == "approval.requested"
                    and int(getattr(ev, "seq", 0)) not in approved):
                provider.approve(int(ev.seq), by="probe")
                approved.add(int(ev.seq))
        try:
            # shield:超时轮询不得取消被等待的 task 本体
            await asyncio.wait_for(asyncio.shield(task), timeout=1.5)
            break
        except asyncio.TimeoutError:
            continue
    await task
    evs = list(ctx.session.events_after(0))
    types = [e.type for e in evs]
    print("RESULT:", task.result().ok if not task.cancelled() else "?",
          getattr(task.result(), "code", ""))
    print("EVENTS:", [t for t in types])
    for want in ("approval.requested", "approval.granted",
                 "tool.call", "tool.result"):
        hits = [e.payload for e in evs if e.type == want]
        print(f"{want}: {hits[-2:]}")
    msgs = [e.payload for e in evs if e.type == "agent.message"]
    print("REPLY:", (msgs[-1].get("content", "")[:300] if msgs else ""))
    results = [e.payload for e in evs if e.type == "tool.result"]
    ran = any(r.get("name") == "exec.shell_run" and r.get("ok")
              for r in results)
    echoed = _MARK in (msgs[-1].get("content", "") if msgs else "") or \
        any(_MARK in r.get("summary", "") for r in results)
    ok = (ran and echoed and "approval.granted" in types)
    print("PROBE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(130)
