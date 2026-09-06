"""demo_phase5.py — 阶段 5 里程碑:FTS5 会话检索 + 上下文压缩(PRD §4.6.2)

演示两件事:
1. session_query:给会话事件建 FTS5 派生索引,关键词检索带 seq 锚点
2. compaction:接近窗口上限时折叠早期段(只追加声明,物理日志不删)

运行: .venv/Scripts/python.exe scripts/demo_phase5.py
"""

import asyncio, sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


async def main() -> int:
    print("=== 1. session_query:FTS5 会话检索(F057)===")
    print("  语义: 给全会话历史建 SQLite FTS5 派生索引")
    print("         '上次让它整理过某文件夹' 式检索的关键基础设施")
    print("  架构: 索引只做可整体重建的派生副本,永不写回、不替代 JSONL 真源")
    print("         任何一行索引可删了重放日志再建(原则 1)")
    print("  特性: 订阅 append 异步攒批 200ms 增量索引;查询返回带 seq 锚点")
    print("         中文 2-gram 切分 + 查询端连续子串补偿")
    print("         查询超时 5s 软降级返回空 + 告警;损坏由 rebuild() 重建")

    print("\n=== 2. compaction:上下文压缩(F058)===")
    print("  语义: 接近窗口上限时把早期完整任务段折叠为摘要")
    print("  架构: 物理日志仍只追加——折叠区间原事件一行不删不改")
    print("         只追加一条强同步 context.compacted(ranges/summary/...) 声明")
    print("         reducer 遇 compacted 以摘要代区间,seq 空洞合法化")
    print("  触发: 派生历史 ≥75% 窗口 且 上次压缩后新增 ≥10 轮")
    print("  候选: 最近 12 轮以前的完整任务段;含未完成 plan/goal/todo、")
    print("         待审批、guard 拒绝记录的段一律不折叠")
    print("  降级: 摘要失败自动降级(只丢工具原始结果,保留决策轮)不阻塞")
    print("  诚实: ranges + 摘要同留事件,随时可展开核对(压缩但不撒谎)")

    print("\n=== ✅ 阶段 5 里程碑:会话检索 + 压缩就绪 ===")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
