"""probe_real_chain.py — 真实 DeepSeek 端到端探针(2026-09-08,P3 真链 #1)。

用法:python scripts/probe_real_chain.py(DEEPSEEK_API_KEY 环境变量需在位)
验证:engine 真实装配 → agent 真链 → goal.*/todo.* 工具被真实模型调用 →
纯文本终态 → F042 自动标题落盘。断言失败 exit 1;零网络不可跑(直连 DeepSeek)。
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


async def main() -> int:
    # .env 注入(仓库根;防误提交已入 .gitignore)——key 值不进代码/日志
    _envf = pathlib.Path(__file__).resolve().parents[1] / ".env"
    if _envf.exists():
        for _line in _envf.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if _line.startswith("DEEPSEEK_API_KEY="):
                os.environ.setdefault("DEEPSEEK_API_KEY",
                                      _line.split("=", 1)[1].strip())
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="pyh_real_"))
    cfg = load_settings()
    if os.environ.get("DEEPSEEK_API_KEY"):
        # DeepSeek 直连(官方 OpenAI 兼容端点)
        cfg.llm.base_url = os.environ.get("PYH_BASE_URL",
                                          "https://api.deepseek.com/v1")
        cfg.llm.api_key = "env:DEEPSEEK_API_KEY"
        cfg.llm.model = os.environ.get("PYH_MODEL", "deepseek-chat")
        print("LLM-ROUTE: deepseek direct, model", cfg.llm.model,
              file=sys.stderr)
    else:
        # key 未注入 → 走时代中转(OpenAI 兼容;glm-5.3 支持工具调用)
        cfg.llm.base_url = os.environ.get("PYH_BASE_URL",
                                          "https://api.shidongai.com/v1")
        cfg.llm.api_key = "env:SHIDONGAI_API_KEY"
        cfg.llm.model = os.environ.get("PYH_MODEL", "glm-5.3")
        print("LLM-ROUTE: shidongai via SHIDONGAI_API_KEY, model",
              cfg.llm.model, file=sys.stderr)
    cfg.storage.root = str(tmp)
    cfg.storage.sessions_dir = str(tmp / "sessions")
    cfg.storage.workspaces_dir = str(tmp / "workspaces")
    cfg.storage.spill_dir = str(tmp / "spill")
    cfg.storage.db_path = str(tmp / "pyharness.db")
    sid = f"s-real-{os.getpid()}"
    ctx = await assemble_real_engine(cfg, sid=sid, sessions_dir=tmp / "sessions")
    await ctx.session.append("session.created",
                             {"title": "", "model": cfg.llm.model},
                             actor="system", sync=True)
    text = ("帮我整理面试要讲的三条主线,每条建一个待办;再建一个目标"
            "'周五前把面试串讲练熟';最后用一句话总结你在做什么")
    await ctx.session.append("user.message", {"content": text},
                             actor="user", sync=True)
    q = TaskQueue(ctx.session, runner=ctx.task_runner, max_queue=8)
    tid = await q.submit(text)
    res = await asyncio.wait_for(q.wait_for(tid), timeout=240)
    evs = list(ctx.session.events_after(0))
    types = [e.type for e in evs]
    print("RESULT:", res.ok, getattr(res, "code", ""))
    print("EVENTS:", [t for t in types])
    for want in ("goal.created", "todo.updated", "agent.message",
                 "session.renamed"):
        hits = [e.payload for e in evs if e.type == want]
        print(f"{want}: {hits[:3]}")
    ok = (res.ok and all(t in types for t in
                         ("goal.created", "todo.updated", "agent.message",
                          "session.renamed")))
    print("PROBE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(130)
