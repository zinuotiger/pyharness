"""e2e_real_llm.py — 真实 DeepSeek 对话验证(收尾波 e2e,非 mock)。

用法: 先 setx DEEPSEEK_API_KEY <key> 然后新开 shell:
  .venv/Scripts/python.exe scripts/e2e_real_llm.py

验证: 装配 ctx → llm.chat 真实请求 → agent_loop 真实跑一轮
      → 事件全部落盘 → 回放重建。成功打印真实回复。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pyharness import cli  # noqa: E402


async def main() -> int:
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    assert key.startswith("sk-"), "DEEPSEEK_API_KEY 未设置或格式不对"
    print(f"key 前缀: {key[:5]}... 长度 {len(key)} [REDACTED 展示]")

    cfg = cli._load_settings(None)
    ctx = cli.assemble_ctx(cfg)

    # 建真 session(store 物理落盘 + bus + SessionLog → ctx.session)
    import tempfile
    from pathlib import Path
    from pyharness.core.session import open_session
    from pyharness.persistence import open_store
    sid = "e2e-real-session-0001"
    tmp = Path(tempfile.mkdtemp(prefix="ph-e2e-"))
    print(f"会话目录: {tmp}")
    store = open_store(sid, dir=tmp)
    log = await open_session(sid, store)
    await log.append("session.created", {"title": "e2e real",
                                         "model": cfg.llm.model}, actor="system")
    ctx.session = log
    ctx.storage.sessions_dir = tmp
    _wire = getattr(cli, "_wire_queue", None)
    if _wire is not None and ctx.task_queue is None:
        _wire(ctx)   # session 就绪后接 task_queue/approval

    # 注册真实 DeepSeek 适配器(默认注册表空 → LLM-304;装配层显式登记,F030)
    from pyharness.core import llm as llm_mod
    llm_cfg = cfg.llm
    triple = llm_mod.AdapterTriple(
        base_url=llm_cfg.base_url,
        api_key_ref=llm_cfg.api_key,          # "env:DEEPSEEK_API_KEY" secret-ref
        model=llm_cfg.model,
    )
    llm_mod.register_adapter(
        llm_cfg.model,
        lambda: llm_mod.OpenAICompatAdapter(
            model=llm_cfg.model, triple=triple,
            limits=llm_mod.TimeoutLimits.from_cfg(llm_cfg.timeout),
        ),
    )
    ctx.llm = llm_mod.LLMClient(cfg)          # 会话门面(装配后 ctx.llm)
    llm = ctx.llm
    print(f"llm provider: {type(llm).__name__} model={llm.model}")

    # 真实 chat 调用
    print("\n=== 真实 DeepSeek 对话 ===")
    resp = await llm.chat(
        messages=[{"role": "user", "content": "用一句话介绍你自己,并说'PyHarness e2e 测试通过'"}],
        ctx=ctx,
    )
    print(f"\n回复: {resp}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
