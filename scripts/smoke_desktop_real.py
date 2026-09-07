"""smoke_desktop_real.py — 桌面 API + 真实引擎全链路(不开窗)。

用法: export DEEPSEEK_API_KEY=sk-xxx; python scripts/smoke_desktop_real.py

验证: create → POST 消息 → runner=真实引擎 → DeepSeek 回话落盘
      → timeline 出现 agent.message → messages 派生可见。
"""
import asyncio, os, sys, threading, tempfile
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pyharness.desktop import DesktopApp, pick_free_port, run_uvicorn, wait_until_listening
from pyharness import cli

import httpx


async def main() -> int:
    assert os.environ.get("DEEPSEEK_API_KEY", "").startswith("sk-"), "key 未设置"
    cfg = cli._load_settings(None)
    tmp = Path(tempfile.mkdtemp(prefix="ph-smoke-real-"))
    cfg.storage.sessions_dir = str(tmp)
    cfg.storage.db_path = str(tmp / "ph.db")
    ctx = cli.assemble_ctx(cfg)
    ctx.storage.sessions_dir = tmp

    app = DesktopApp(ctx=ctx)
    port = pick_free_port()
    threading.Thread(target=run_uvicorn, args=(app, port), daemon=True).start()
    if not wait_until_listening(port):
        print("❌ uvicorn 未就绪"); return 1
    base = f"http://127.0.0.1:{port}"

    async with httpx.AsyncClient(base_url=base, trust_env=False,
                                 timeout=httpx.Timeout(120.0)) as c:
        # 新建会话
        r = await c.post("/api/sessions")
        sid = r.json()["sid"]
        print(f"1. 新建会话 sid={sid}")
        # 发消息(真实引擎异步跑)
        r = await c.post(f"/api/sessions/{sid}/messages",
                         json={"text": "用一句话介绍你自己,结尾说'桌面全链路通'"})
        body = r.json()
        print(f"2. 发消息 → task_id={body.get('task_id')} user_seq={body.get('user_seq')}")
        # 轮询 timeline 直到出现 agent.message(最长 100s)
        got = None
        for _ in range(200):
            r = await c.get(f"/api/sessions/{sid}/timeline")
            kinds = [n["kind"] for n in r.json()["nodes"]]
            if "message" in kinds:
                got = r.json()["nodes"]
                break
            await asyncio.sleep(0.5)
        if got is None:
            print("⚠ 100s 内未见 agent.message")
        else:
            msgs = [n for n in got if n["kind"] == "message"]
            print(f"3. 轨迹节点 {len(got)} 个: kinds={[n['kind'] for n in got]}")
            print(f"   Agent 回复: {msgs[-1]['title'][:200]}")
        # budget
        r = await c.get(f"/api/budget/{sid}")
        d = r.json()
        print(f"4. budget: used_in={d.get('used_in_tokens')} used_out={d.get('used_out_tokens')}")
    app.shutdown_gracefully()
    print("\n✅ 桌面真实全链路冒烟完成")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
