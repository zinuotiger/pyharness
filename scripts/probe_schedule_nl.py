"""probe_schedule_nl.py — Phase 2 真实 LLM 验证:自然语言 → schedule 工具参数。

用法(需 DEEPSEEK_API_KEY 在环境变量中):
    .venv/Scripts/python.exe scripts/probe_schedule_nl.py

本脚本**真实调用 LLM**(非 fake),验证 Phase 2 的核心命题:
    自然语言 → LLM tool-call → schedule 工具参数 → 现有 Scheduler

覆盖两类:
  A. 三种触发方式的映射 —— 每日 cron / 一次性 at / 间隔 interval
  B. 「不猜」—— 缺内容与时间歧义时,模型必须先经 user.ask 追问,
     不得自行编造任务内容;追问后按用户答复建任务

B 类把 spine.ask 换成**自动应答替身**(模拟用户回答),以便无真人值守时
也能跑完 ask → 答复 → 建任务的完整闭环;替身会打印模型实际问了什么。

本脚本不进默认测试套件(tests/ 之外,pytest testpaths 不含 scripts/)。
确定性(与 LLM 无关)的闭环与边界验证见 tests/unit/test_schedule_nl_phase2.py。
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pyharness import cli                                          # noqa: E402
from pyharness.core.task_queue import TaskQueue                    # noqa: E402
from pyharness.engine import assemble_real_engine                  # noqa: E402

MAP_CASES = [
    ("每日", "每天早上8点提醒我开始工作。", "cron", "0 8 * * *"),
    ("一次性", "明天早上8点提醒我开会。", "at", None),      # expr 动态(取决于日期)
    ("间隔", "每隔一小时提醒我喝水。", "interval", "3600"),
]

ASK_CASES = [
    ("缺内容", "每天早上8点设置一个提醒。", "提醒我站起来活动一下", "cron"),
    ("歧义", "设置一个8点的定时任务。", "每天早上的8点,内容是提醒我吃药", "cron"),
]


class AutoAnswer:
    """spine.ask 替身:记录模型的追问并立即回一个固定答复(模拟用户)。"""

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.questions: list[str] = []

    async def request(self, question, *, options=None, ttl_s=120):
        self.questions.append(str(question))
        return self.answer


def _cfg(tmp: pathlib.Path):
    cfg = cli._load_settings(None)
    cfg.storage.root = str(tmp)
    cfg.storage.sessions_dir = str(tmp / "sessions")
    cfg.storage.workspaces_dir = str(tmp / "workspaces")
    cfg.storage.spill_dir = str(tmp / "spill")
    cfg.storage.db_path = str(tmp / "pyharness.db")
    return cfg


async def _run(text: str, *, answer: str | None = None) -> tuple[dict, list[str]]:
    """跑一轮真实闭环,返回 (摘要, 模型追问列表)。answer 非空则装 ask 替身。"""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="ph-schednl-"))
    cfg = _cfg(tmp)
    ctx = await assemble_real_engine(cfg, sid="s-nlprobe00001",
                                     sessions_dir=pathlib.Path(
                                         cfg.storage.sessions_dir))
    stub = None
    if answer is not None:
        stub = AutoAnswer(answer)
        ctx.engine_spine.ask = stub            # 必须在 agent 首次装配前替换

    await ctx.session.append("session.created",
                             {"title": "", "model": cfg.llm.model},
                             actor="system", sync=True)
    await ctx.session.append("user.message", {"content": text}, actor="user",
                             sync=True)
    q = TaskQueue(ctx.session, runner=ctx.task_runner, max_queue=8)
    tid = await q.submit(text)
    res = await asyncio.wait_for(q.wait_for(tid), timeout=180.0)
    ctx.engine_spine.schedule.stop()

    evs = list(ctx.session.events_after(0))
    sched_calls = [e for e in evs
                   if e.type == "tool.call" and e.payload.get("name") == "schedule"]
    regs = [e for e in evs if e.type == "schedule.registered"]
    errs = [e for e in evs if e.type == "tool.error"]
    msgs = [e for e in evs if e.type == "agent.message"]
    out = {
        "ok": res.ok,
        "sched_calls": len(sched_calls),
        "errors": len(errs),
        "registered": regs[0].payload if regs else None,
        "reply": str(msgs[-1].payload.get("content", "")) if msgs else "",
        "error_codes": [e.payload.get("code") for e in errs],
    }
    return out, (stub.questions if stub else [])


async def main() -> int:
    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("DEEPSEEK_API_KEY 未设置:本脚本需要真实 LLM,跳过。")
        return 2
    failures = 0

    print("=" * 70)
    print("A. 三种触发方式的自然语言映射")
    for label, text, want_kind, want_expr in MAP_CASES:
        out, _ = await _run(text)
        reg = out["registered"]
        print(f"\n[{label}] {text}")
        print(f"  schedule 调用={out['sched_calls']} 失败={out['errors']} "
              f"{out['error_codes'] or ''}")
        if not reg:
            print("  ✗ 未注册任务")
            failures += 1
            continue
        ok = reg["kind"] == want_kind and (want_expr is None
                                           or reg["expr"] == want_expr)
        failures += 0 if ok else 1
        print(f"  {'✓' if ok else '✗'} kind={reg['kind']} expr={reg['expr']!r} "
              f"intent={reg['template']['intent']!r}")
        print(f"  回复:{out['reply'][:80]!r}")

    print("\n" + "=" * 70)
    print("B. 「不猜」——缺内容 / 歧义必须先追问,不得编造")
    for label, text, answer, want_kind in ASK_CASES:
        out, questions = await _run(text, answer=answer)
        reg = out["registered"]
        print(f"\n[{label}] {text}")
        print(f"  追问次数={len(questions)}")
        for qq in questions:
            print(f"    模型问:{qq[:120]}")
        if not questions:
            print("  ✗ 未追问(违反「不猜」要求)")
            failures += 1
        if not reg:
            print("  ✗ 追问后仍未建任务")
            failures += 1
            continue
        # 内容必须来自用户答复,而不是模型编造
        same = answer[:6] in reg["template"]["intent"] or \
            reg["template"]["intent"][:6] in answer
        print(f"  {'✓' if same else '✗'} kind={reg['kind']} "
              f"intent={reg['template']['intent']!r}(用户答复={answer!r})")
        failures += 0 if same else 1

    print("\n" + "=" * 70)
    print(f"结果:{'全部通过' if failures == 0 else f'{failures} 项未通过'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
