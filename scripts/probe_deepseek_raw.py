"""probe_deepseek_raw.py — 裸 httpx 调 DeepSeek,看 tools 请求的原始响应。"""
import asyncio, json, os, sys
import httpx

KEY = os.environ.get("DEEPSEEK_API_KEY", "")

TOOLS = [{"type": "function",
          "function": {"name": "fs.list_dir",
                       "description": "列 workspace 内目录条目",
                       "parameters": {"type": "object",
                                      "properties": {"path": {"type": "string"}},
                                      "required": ["path"],
                                      "additionalProperties": False}}}]


async def main():
    async with httpx.AsyncClient(timeout=30, trust_env=False) as c:
        h = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
        # 1) 带 tools
        body = {"model": "deepseek-chat", "messages": [
            {"role": "user", "content": "列出 workspace 里的文件"}],
            "tools": TOOLS, "max_tokens": 256}
        r = await c.post("https://api.deepseek.com/chat/completions",
                         headers=h, json=body)
        print(f"[with tools] HTTP {r.status_code}")
        print(r.text[:800])
        print()
        # 2) 不带 tools 对照
        body2 = {"model": "deepseek-chat", "messages": [
            {"role": "user", "content": "你好"}], "max_tokens": 64}
        r2 = await c.post("https://api.deepseek.com/chat/completions",
                          headers=h, json=body2)
        print(f"[plain] HTTP {r2.status_code}")
        print(r2.text[:400])


if __name__ == "__main__":
    asyncio.run(main())
