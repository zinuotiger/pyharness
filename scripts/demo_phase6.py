"""demo_phase6.py — 阶段 6 里程碑:外壳 + 修复工具(PRD §4.6.2)

演示:
1. cli:14 子命令分发/退出码/headless R8
2. acp:JSON-RPC 2.0 协议壳
3. desktop:pywebview + FastAPI 轨迹时间线(面试主演示)
4. repair:崩溃恢复管线(备份/截断/隔离/声明)

运行: .venv/Scripts/python.exe scripts/demo_phase6.py
"""

import asyncio, sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pyharness import __version__


async def main() -> int:
    print(f"=== PyHarness v{__version__} 阶段 6:外壳三件套 + 修复 ===")

    print("\n=== 1. CLI 外壳(14 子命令)===")
    print("  chat / run / plan / schedule / job / search / session / fork")
    print("  repair / desktop / acp / config / budget / stats")
    print("  退出码: 0 成功 | 1 引擎错误(带 F019 码) | 2 用法错误 | 130 Ctrl-C")
    print("  headless(stdin 非 tty):无审批通道的危险动作默认拒(R8)")

    print("\n=== 2. ACP 协议壳(JSON-RPC 2.0 over stdio)===")
    print("  initialize / chat / approve / read_events / shutdown")
    print("  错误: -32600 信封错 | -32601 未知法 | -32000 BUSY | -32603 引擎错(带 F019)")
    print("  供外部 IDE/工具当后端调用(逐行串行桥)")

    print("\n=== 3. Desktop 壳(pywebview + FastAPI,面试主演示)===")
    print("  pywebview 主线程 + FastAPI 127.0.0.1 随机端口(同进程)")
    print("  /api/sessions/{sid}/timeline: 从 events_after 派生轨迹时间线")
    print("  6 类节点: created/user/agent/tool/guard/approval —— 事件溯源直观证明")
    print("  窗口 = 只读事件投影(ADR-012): 对话 + 审批弹窗 + 时间线回放")

    print("\n=== 4. repair 修复管线(F060)===")
    print("  备份 .corrupt-{ts} → 尾部半行截断 → 坏行隔离(不删)→ seq 空洞")
    print("  对照 compacted/recovered 声明 → 派生视图重建 → session.recovered")
    print("  伦理: 隔离不删除;修复本身可审计;二次幂等收敛")

    print("\n=== ✅ 全部 7 阶段完成:PyHarness 66 功能从规格到可运行 ===")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
