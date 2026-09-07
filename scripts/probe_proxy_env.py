"""probe_proxy_env.py — 验证 trust_env=False 后带代理 env 也能直连 DeepSeek。"""
import asyncio, os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pyharness.config import load_settings
from pyharness import engine
from pyharness.core.llm import LLMClient


async def main():
    # 模拟 exe 环境:显式设置代理 env(之前桌面卡死的场景)
    os.environ["HTTP_PROXY"] = "http://127.0.0.1:7897"
    os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7897"
    print("env 代理已设(模拟 exe 环境)")
    cfg = load_settings()
    # 真 ctx(engine 装配;临时会话目录)
    import tempfile
    from pathlib import Path
    sid = "probe-0001"
    tmp = Path(tempfile.mkdtemp(prefix="ph-probe-"))
    ctx = await engine.assemble_real_engine(cfg, sid=sid, sessions_dir=tmp)
    await ctx.session.append("session.created",
                             {"title": "probe", "model": cfg.llm.model},
                             actor="system", sync=True)
    resp = await ctx.llm.chat([{"role": "user", "content": "只回复两个字:通了"}], ctx=ctx)
    print("回复:", resp.content[:60])


if __name__ == "__main__":
    asyncio.run(main())
