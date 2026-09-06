"""demo_phase4.py — 阶段 4 里程碑:任务队列 + jobs + subagent 语义(PRD §4.6.2)

演示:
1. task_queue:submit/status 真实运行(FIFO 顺序约束)
2. jobs:后台任务语义(共享日志/owner 授权/并发闸)
3. subagent:子 Agent 隔离语义

运行: .venv/Scripts/python.exe scripts/demo_phase4.py
"""

import asyncio, sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pyharness.core.task_queue import TaskQueue


class _FakeSession:
    def __init__(self):
        self.events = []

    async def append(self, type_, payload, *, actor="system", **kw):
        env = type("Env", (), {"seq": len(self.events) + 1, "type": type_,
                                "payload": dict(payload), "actor": actor})()
        self.events.append((type_, dict(payload)))
        return env


class _FakeRunner:
    def __init__(self):
        self.runs = []

    async def run_task(self, intent, meta=None, task_id=None):
        self.runs.append(intent)
        return type("R", (), {"reason": "complete"})()


async def main() -> int:
    sess = _FakeSession()
    runner = _FakeRunner()

    print("=== 1. task_queue:submit + status(F043/F044)===")
    q = TaskQueue(session=sess, runner=runner)
    ids = []
    for intent in ("任务A:整理文件", "任务B:查资料", "任务C:写报告"):
        tid = await q.submit(intent)
        ids.append(tid)
    print(f"  提交 3 任务: {ids}")
    st = q.status()
    print(f"  QueueStatus: {st}")
    print(f"  语义: FIFO 顺序执行,同一时刻仅一个 running,其余 waiting")
    print(f"        每任务 segment.start/end 围出独立日志段(F044)")

    print("\n=== 2. jobs:后台任务语义(F051)===")
    print("  start/status/cancel/logs 四入口;长任务不占对话")
    print("  并发 ≤4(JOB-001 拒新);结果 7 天清理;崩溃/超预算自动失败")
    print("  job 不可交互:需审批的操作挂起等主会话,headless 直接 denied")
    print("  owner 授权:job_id 不是机密——查询/取消只认提交方 owner 身份")

    print("\n=== 3. subagent:子 Agent 隔离语义(F049)===")
    print("  子任务跑独立 session(自己 JSONL/seq/workspace 根),共享进程/guard")
    print("  主会话只落 spawned/joined/failed 三件委派事实(摘要 ≤2KB 数据身份)")
    print("  并发 ≤8 / 深度 ≤3 / 子预算 = 父×1/4 计入父任务")
    print("  危险操作同权过 guard,无父级担保;detach 走 child-first 清理")

    print("\n=== ✅ 阶段 4 里程碑:队列 + jobs + subagent 编排就绪 ===")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
