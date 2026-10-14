"""Message projection: complete migrated implementation."""
from __future__ import annotations
from typing import TYPE_CHECKING
import json
import logging
from dataclasses import asdict, is_dataclass
from typing import Any
if TYPE_CHECKING:
    pass


log = logging.getLogger("pyharness.application.service")

def _with_message_origin(log_: Any, msgs: list[dict]) -> list[dict]:
    """给派生消息附**来源标记**(只供 UI 判断消息来自谁,不参与 LLM 请求)。

    定时任务到点触发时,Scheduler 会以 ``actor="user"`` 写一条 ``user.message``,
    其 ``origin="schedule:<name>"``(schedule._fire)。``derive_messages`` 只投影
    role/content,且**同时喂给 agent_loop 的 LLM 请求与 compaction 的 token 估算**,
    故**不改其输出契约**;改在本层按 1:1 对应关系**复制**并附加键:

      - ``session._fold_history`` 对每条 ``user.message`` 恰产出一条 ``role=user``
        消息(``user.message_edited`` 是替换内容而非增删),故第 k 条 user 消息 ↔
        第 k 条 user.message 事件;
      - 复制字典,避免污染 ``derive_messages`` 的 history 缓存对象。

    普通用户输入同样写 ``origin=None``(键恒在,便于前端统一判断)。
    """
    events = [e for e in log_.events_after(0) if e.type == 'user.message']
    origins = [getattr(e, 'origin', None) for e in events]
    answers = [e for e in log_.events_after(0) if e.type == 'agent.message']
    j = 0
    out: list[dict] = []
    k = 0
    for m in msgs:
        row = dict(m)
        if row.get("role") == "user":
            row["origin"] = origins[k] if k < len(origins) else None
            if k < len(events):
                row['seq'] = events[k].seq
                row['artifact_ids'] = (events[k].payload.get('meta') or {}).get('platform', {}).get('artifact_ids', [])
            k += 1
        elif row.get('role') == 'assistant' and row.get('content') and not row.get('tool_calls'):
            if j < len(answers): row['seq'] = answers[j].seq
            j += 1
        out.append(row)
    if k != len(origins):                  # 不变式破损:宁可少标记也不错标
        log.warning("user.message 与派生 user 消息数不一致(events=%d msgs=%d)",
                    len(origins), k)
        for row in out:
            row.pop("origin", None)
            row.pop('seq', None)
            row.pop('artifact_ids', None)
    if j != len(answers):
        for row in out:
            if row.get('role') == 'assistant': row.pop('seq', None)
    return out

def _as_dict(value: Any) -> dict:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return dict(value)
    return dict(vars(value))

def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))
