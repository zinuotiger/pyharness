"""Execution result: complete migrated implementation."""
from __future__ import annotations
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Optional


log = logging.getLogger("pyharness.tools_executor")

DEFAULT_TOOL_TIMEOUT: int = 60          # F017 工具默认超时(PARAMETER-ANCHOR 锁定)

SPILL_THRESHOLD: int = 2048             # 结果文本 >2KB → spill(F039,DIS-SEAM §6.1 G1)

SUMMARY_MAX_CHARS: int = 2000           # tool.result summary ≤2KB(payload max 2048)

DEFAULT_MAX_ARG_FAILURES: int = 2

PARSE_FAIL_NAME: str = "?"              # 解析失败事件 name 回落值(缺工具名时,审计占位)

_ZOMBIE_POOL_LIMIT: int = 32            # 僵尸线程池保留上限(超出即裁剪已空闲池)

_SUMMARY_PRIORITY: tuple[str, ...] = (
    "url", "domain", "host", "target", "path", "src", "dst", "dir",
    "mode", "command", "cmd", "shell", "message", "content",
)

_SUMMARY_TAIL_KEYS: frozenset = frozenset(
    {"command", "cmd", "code", "input", "content", "text", "script",
     "prompt", "message", "args", "path", "query"})

_TAIL_KEEP: int = 24              # 尾部保留宽度(头尾双展示:尾 ≥24 字符可见)

@dataclass(frozen=True)
class ExecResult:
    """单次工具调用结果(spec 数据结构表;F022 批量入口消费)。

    summary ≤2KB(回喂/审计);truncated/spill_ref 由关4 finalize 产出
    (超长 → spill 私有区,大结果不进上下文/日志);elapsed_ms = Provider 实耗。
    """

    ok: bool
    summary: str
    truncated: bool = False
    spill_ref: Optional[dict] = None     # {ref, chars, lines, preview}(事件同型)
    elapsed_ms: float = 0.0

@dataclass(frozen=True)
class ApprovalRoundResult:
    """关2.5 审批编排结果(S3-2-2;取代"allow"/摘要文本的混合字符串返回契约)。

    字段语义(互不等价):
      - ``decision`` : 本轮治理决策——未获批时为 **D1**;获批重入后为 **D2**
        (``D2.supersedes == D1.decision_id``);
      - ``approved`` : **人类是否批准**(granted);
      - ``executed`` : 是否**放行至关3**(获批 ≠ 放行:重入 reject / 绑定不符时不放行);
      - ``message``  : 用户可见文案(``executed=False`` 时由调用方原样回喂)。

    边界:``denied``/``timeout``/``APR-501`` 一律 **不产生 D2、不再次 authorize、
    不执行 provider**(该语义归 approval transport + executor,R1)。
    """

    decision: Any
    approved: bool
    executed: bool
    message: str = ""

def summarize_text(text: str, max_chars: int = SUMMARY_MAX_CHARS) -> str:
    """文本截断摘要(≤max_chars;截断处补 … 记号,防静默丢尾)。"""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"

def _short_value(v: Any, width: int = 80, key: Optional[str] = None) -> str:
    """摘要值短化:dict/list 只报结构;字符串截断;None/bool/int 直写。

    内容承载键(key ∈ _SUMMARY_TAIL_KEYS)超宽时头尾双展示(保留尾部 _TAIL_KEEP
    字符)——审批视图不得把危险命令段藏在省略号后;普通键单侧截断。
    """
    if isinstance(v, dict):
        return f"<dict {len(v)}键>"
    if isinstance(v, (list, tuple)):
        return f"<list {len(v)}项>"
    if v is None:
        return "null"
    s = str(v)
    if len(s) <= width:
        return s
    if key is not None and key in _SUMMARY_TAIL_KEYS:
        head = s[: max(0, width - _TAIL_KEEP - 2)]
        return f"{head} … {s[-_TAIL_KEEP:]}"
    return s[: width - 1] + "…"

def summarize(args: Any, *, max_chars: int = 400) -> str:
    """Consumer 层参数摘要化(审批展示用;spec 速览表 def summarize(args))。

    不由 Provider/LLM 生成(防注入操纵摘要,SECURITY §5):只做键值短化 + 动作
    键优先排序;超长截断。content/command 类键头尾双展示(尾部可见,防截断隐藏)。
    """
    if not isinstance(args, dict) or not args:
        return "(无参数)"
    ordered = sorted(args, key=lambda k: (_SUMMARY_PRIORITY.index(k)
                                          if k in _SUMMARY_PRIORITY else 99, k))
    parts: list[str] = []
    for k in ordered:
        seg = f"{k}={_short_value(args[k], key=k)}"
        if sum(len(p) for p in parts) + len(seg) > max_chars:
            parts.append("…")
            break
        parts.append(seg)
    return ", ".join(parts)

def _approval_binding(call: Any, args: dict) -> str:
    """审批绑定指纹:sha1(tool + 规范化参数序列化)。

    唯一用途 = 把"人类批准"与"实际执行"绑定到同一调用:granted 后重入 guard
    得到 approval 决策时,校验本指纹 == 审批提供器为该 call_id 记录的绑定,
    不一致 = 批准针对的是不同参数(注入/篡改),拒绝执行(GRD-403)。参数序列化
    用 sort_keys 定序(同一调用内同 args 确定性一致;跨会话信任仍走 approval 层
    自己的 canonical_json)。
    """
    canon = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(f"{call.name}\n{canon}".encode("utf-8")).hexdigest()


# Preserve historical import and pickle identities through the compatibility facade.
ExecResult.__module__ = 'pyharness.core.tools_executor'
ApprovalRoundResult.__module__ = 'pyharness.core.tools_executor'
