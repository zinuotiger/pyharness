"""Real streaming probe: loop.streaming -> bus llm.chunk -> final agent.message."""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pyharness.config import load_settings
from pyharness.engine import assemble_real_engine


async def main() -> int:
    cfg = load_settings()
    cfg.loop.streaming = True
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg.storage.root = str(root)
        cfg.storage.sessions_dir = str(root / "sessions")
        cfg.storage.workspaces_dir = str(root / "workspaces")
        cfg.storage.spill_dir = str(root / "spill")
        cfg.storage.db_path = str(root / "pyharness.db")
        ctx = await assemble_real_engine(
            cfg, sid="s-stream-real-0001", sessions_dir=root / "sessions")
        chunks = []
        seen = []
        sub = ctx.bus.subscribe(
            "llm.*",
            lambda t, p: (seen.append(t),
                          chunks.append(p) if t == "llm.chunk" else None),
            owner="stream-probe")
        try:
            await ctx.session.append("session.created",
                                     {"title": "", "model": cfg.llm.model},
                                     actor="system", sync=True)
            prompt = "用一句话说明什么是事件溯源。"
            await ctx.session.append("user.message", {"content": prompt},
                                     actor="user", sync=True)
            tid = await ctx.task_queue.submit(
                prompt, meta={"channel": "probe"})
            res = await asyncio.wait_for(ctx.task_queue.wait_for(tid), timeout=60)
            assert res.ok, res
            assert chunks, "没有收到 llm.chunk"
            assert any(e.type == "agent.message" for e in ctx.session.events_after(0))
            print(f"chunks={len(chunks)}")
            print("STREAMING_REAL_PASS")
            return 0
        finally:
            try:
                ctx.bus.unsubscribe(sub)
            except Exception:
                pass
            await ctx.engine_spine.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
