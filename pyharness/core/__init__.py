"""pyharness/core — 脊柱八模块(agent-loop/agent/session/llm/system_prompt/scope/
tools/persistence;PRD §2.2)。阶段 1 已实现:session.py(F009 事件日志门面)与
agent.py(会话实体/ctx 门面);其余模块按 docs/specs/ 编码规格逐阶段落地。
"""
from pyharness.core.agent import Agent, Ctx, create_agent
from pyharness.core.session import (SessionLog, SessionLogProtocol,
                                    SessionStoreProtocol, open_session)

__all__ = [
    # session(F009):append-only 会话事件日志 + 派生视图工厂 + 恢复重建
    "SessionLog", "open_session",
    # 注入协议(SessionStore 最小契约;core/persistence 实现后复用)
    "SessionStoreProtocol", "SessionLogProtocol",
    # agent(会话实体 + ctx 门面):一会话一 agent 句柄/生命周期/finished 归属
    "Agent", "Ctx", "create_agent",
]
