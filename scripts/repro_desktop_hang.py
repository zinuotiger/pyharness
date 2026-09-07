"""repro_desktop_hang.py — 复现桌面卡死:API 发消息,观察事件进展与超时。"""
import asyncio, httpx

PORT = 54233
BASE = f"http://127.0.0.1:{PORT}"


async def main():
    async with httpx.AsyncClient(base_url=BASE, trust_env=False,
                                 timeout=httpx.Timeout(90.0)) as c:
        r = await c.post("/api/sessions")
        sid = r.json()["sid"]
        print(f"会话: {sid}")
        r = await c.post(f"/api/sessions/{sid}/messages",
                         json={"text": "你好,请回复一句话"})
        print(f"发消息: {r.json()}")
        # 轮询 timeline 60s,打印事件变化
        last_types = []
        for i in range(60):
            r = await c.get(f"/api/sessions/{sid}/timeline")
            nodes = r.json()["nodes"]
            types = [n["kind"] for n in nodes]
            if types != last_types:
                print(f"[{i*3}s] 轨迹 {len(nodes)} 节点: {types}")
                last_types = types
            msgs = [n for n in nodes if n["kind"] == "message"]
            if msgs:
                print(f"\n✅ Agent 回复: {msgs[-1]['title'][:200]}")
                return 0
            await asyncio.sleep(3)
        print("\n⚠ 60s 无回复(卡死复现)")
        return 1


if __name__ == "__main__":
    asyncio.run(main())
