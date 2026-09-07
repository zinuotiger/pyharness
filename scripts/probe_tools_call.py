"""probe_tools_call.py — 真 LLM + tools 参数探测(看 DeepSeek 拒绝原因)。"""
import asyncio, json, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, r'C:/Users/LENOVO/Desktop/mini-harness')
from pyharness.config import load_settings
from pyharness import engine


async def main():
    cfg = load_settings()
    tmp = Path(tempfile.mkdtemp(prefix='ph-probe-t-'))
    ctx = await engine.assemble_real_engine(cfg, sid='probet-0001',
                                            sessions_dir=tmp)
    await ctx.session.append('session.created',
                             {'title': 'probe', 'model': cfg.llm.model},
                             actor='system', sync=True)
    schemas = ctx.tools.schemas_for(ctx.scope)
    print('下发 tools:', json.dumps(schemas, ensure_ascii=False)[:600])
    try:
        resp = await ctx.llm.chat(
            [{'role': 'user', 'content': '列出 workspace 里的文件'}],
            tools=schemas, ctx=ctx)
        print('OK:', resp.content[:100] if resp.content else '(空)')
        print('tool_calls:', resp.tool_calls)
    except Exception as e:
        import traceback
        print('失败:', type(e).__name__)
        print(str(e)[:500])


if __name__ == '__main__':
    asyncio.run(main())
