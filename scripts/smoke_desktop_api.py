"""smoke_desktop_api.py — 桌面壳 API 冒烟(不开窗):根路由 HTML + 会话闭环形状。

用法: .venv/Scripts/python.exe scripts/smoke_desktop_api.py
验证:
1. GET / → 200 text/html 含 'PyHarness Desktop'
2. POST /api/sessions?sid=smoke-0001 {text} → 应有 task 或结构化错误(引擎未接线)
3. GET /api/sessions/{sid}/timeline → 应回 nodes(会话 created 至少)
"""
import asyncio, os, sys, threading, tempfile
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pyharness.desktop import DesktopApp, pick_free_port, run_uvicorn, wait_until_listening
from pyharness import cli

import httpx


async def main() -> int:
    cfg = cli._load_settings(None)
    # 会话目录指向临时目录(不污染真实 ~/.pyharness)
    tmp = Path(tempfile.mkdtemp(prefix="ph-smoke-"))
    cfg.storage.sessions_dir = str(tmp)
    cfg.storage.db_path = str(tmp / "ph.db")
    ctx = cli.assemble_ctx(cfg)
    ctx.storage.sessions_dir = tmp

    app = DesktopApp(ctx=ctx)
    port = pick_free_port()
    threading.Thread(target=run_uvicorn, args=(app, port), daemon=True).start()
    if not wait_until_listening(port):
        print("❌ uvicorn 未就绪")
        return 1
    base = f"http://127.0.0.1:{port}"

    async with httpx.AsyncClient(base_url=base, trust_env=False) as c:
        # 1. 根路由
        r = await c.get("/")
        print(f"GET / → {r.status_code} html={r.headers.get('content-type')} "
              f"len={len(r.text)} has_title={'PyHarness Desktop' in r.text}")
        # 2. 新建会话 → 发消息(引擎未接线 → 期待 task_id 或结构化错)
        r = await c.post("/api/sessions")
        sid = r.json().get("sid")
        print(f"POST create_session → {r.status_code} sid={sid}")
        r = await c.post(f"/api/sessions/{sid}/messages", json={"text": "你好"})
        print(f"POST create_message → {r.status_code} body={r.text[:200]}")
        # 3. timeline(会话已 created,应至少有节点)
        r = await c.get(f"/api/sessions/{sid}/timeline")
        print(f"GET timeline → {r.status_code} nodes={len(r.json().get('nodes', []))}")
        # 4. budget
        r = await c.get(f"/api/budget/{sid}")
        print(f"GET budget → {r.status_code} disabled={r.json().get('disabled')}")
    app.shutdown_gracefully()
    print("\n✅ 冒烟完成")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
