"""Cli output: complete migrated implementation."""
from __future__ import annotations
import asyncio
import json
import logging
import sys
from dataclasses import dataclass, field
from typing import Any, Optional
from pyharness.errors import PyHError


log = logging.getLogger("pyharness.cli")

PROMPT = "pyharness> "                     # chat 提示符(无换行)

EXIT_INTERRUPT = 130                       # Ctrl-C 退出码(KeyboardInterrupt)

OFFLINE_CMDS = frozenset({"config", "budget", "stats"})     # 零网络零 LLM(DEP §5.1 ✅)

SHELL_CMDS = frozenset({"desktop", "acp"})  # 长驻外壳:交出其控制权(F065/F066)

INTERACTIVE_CMDS = frozenset({"chat", "plan"})  # tty 判定:交互命令才注入 channel

_TEXT_CARD_TYPES = frozenset({"agent.message", "tool.call", "tool.result",
                              "tool.error", "guard.rejected", "user.message",
                              "session.finished", "task.completed", "task.failed"})

_END_EVENT_TYPES = frozenset({"session.finished", "task.completed", "task.failed"})

_RENDER_PATTERNS = ("llm.chunk", "agent.*", "tool.*", "guard.*",
                    "approval.*", "session.*", "task.*")

class _CancelledRound(Exception):
    """首次 Ctrl-C 信号:取消当前轮次(interactive_loop 捕获后回循环顶)。"""

@dataclass
class ParseResult:
    """argparse 产物:cmd/positional/flags(flags = vars(ns),含 config/json/session/
    verbose/once 及各子命令专有旗标;positional 为分离字段,同时回填 flags 供
    offline_cmd 消费)。"""

    cmd: str
    positional: list[str]
    flags: dict

@dataclass
class ShellCtx:
    """外壳上下文:ctx = 装配门面;loop = 事件循环;channel = 审批/斜杠交互通道
    (headless 时 None);headless = stdin 非 tty;flags = 全局旗标镜像。"""

    ctx: Any
    loop: Any = None
    channel: Optional[str] = None
    headless: bool = False
    flags: dict = field(default_factory=dict)

@dataclass
class ExitResult:
    """子命令返回体:code = 退出码;payload = 结构化结果(可空;--json 出口)。"""

    code: int
    payload: Optional[dict] = None

@dataclass
class StreamRenderer:
    """事件 → 屏幕/JSONL 渲染器:text 模式增量打字机 + 卡片;json 模式 stdout 只出
    结构化行(人类提示一律 stderr,保机器流纯净)。carriage 标记打字机行未换行。"""

    mode: str = "text"                 # text | json
    seen_seq: int = 0                  # 已见事件 seq(信封面接入后使用)
    carriage: bool = False             # 打字机行进行中(未换行)

    # ---------------------------------------------------- 瞬时增量(llm.chunk)
    def delta(self, payload: dict) -> None:
        text = payload.get("delta", "")
        if self.mode == "json":
            self.event("llm.chunk", payload)          # 机器流:逐事件一行
            return
        if text:
            sys.stdout.write(text)
            sys.stdout.flush()
            self.carriage = True

    # ---------------------------------------------------- 卡片/结构化事件
    def event(self, type_: str, payload: Any) -> None:
        """单事件出口:text 模式按类型画卡片;json 模式逐事件一行 JSONL。"""
        payload = _normalize_payload(payload)
        if self.mode == "json":
            _json_line({"type": type_, "payload": payload})
            return
        if self.carriage:
            sys.stdout.write("\n")                     # 打字机行收尾再画卡片
            self.carriage = False
        card = _text_card(type_, payload)
        if card:
            print(card)

    def newline(self) -> None:
        if self.carriage:
            sys.stdout.write("\n")
            sys.stdout.flush()
            self.carriage = False

def _normalize_payload(payload: Any) -> dict:
    """归一化到**事件 payload**(Envelope/模型/裸 dict 三形态,鸭子类型兼容)。

    总线投递的 canonical 形状是 Envelope(SessionLog._dispatch →
    ``bus.emit(type, env)``),渲染面只消费其内层 ``payload``;故 dump 出信封形状
    (含 session_id/seq/payload 三键)时取内层——否则渲染面按信封字段取值会全部落空
    (工具名/结果/终态理由退化为 ``?``/``{}``/``None``)。瞬时类型(llm.chunk)不经
    信封,仍走裸 dict。
    """
    if isinstance(payload, dict):
        return payload
    dump = getattr(payload, "model_dump", None)
    if callable(dump):
        try:
            data = dump(mode="json")
        except TypeError:                              # 老版 pydantic 无 mode 参
            data = dump()
        inner = data.get("payload") if isinstance(data, dict) else None
        if isinstance(inner, dict) and "session_id" in data and "seq" in data:
            return inner                              # Envelope → 内层 payload
        return data
    return {"raw": str(payload)}

def _trunc(s: str, n: int = 240) -> str:
    """卡片文本截断(防刷屏;超长加省略号)。"""
    return s if len(s) <= n else s[: n - 3] + "..."

def _text_card(type_: str, payload: dict) -> Optional[str]:
    """单事件文本卡片;未列类型返回 None(text 模式静默)。"""
    if type_ not in _TEXT_CARD_TYPES:
        return None
    if type_ == "agent.message":
        return payload.get("content", "") or ""
    if type_ == "user.message":
        return ""
    if type_ == "tool.call":
        name = payload.get("tool") or payload.get("name") or "?"
        args = payload.get("args_summary") or payload.get("args") or {}
        return f"[工具] {name} {_trunc(json.dumps(args, ensure_ascii=False))}"
    if type_ == "tool.result":
        data = payload.get("content") or payload.get("summary") or payload.get("result")
        return f"[结果] {_trunc(str(data))}"
    if type_ == "tool.error":
        return f"[工具错误] {_trunc(payload.get('error') or str(payload))}"
    if type_ == "guard.rejected":
        return f"[拒绝] {_trunc(json.dumps(payload, ensure_ascii=False))}"
    if type_ == "session.finished":
        return f"[会话结束] {payload.get('reason', '')}"
    if type_ == "task.completed":
        return (f"[任务完成] {payload.get('task_id', '')} "
                f"{payload.get('reason', '')}").strip()
    if type_ == "task.failed":
        return (f"[任务失败] {payload.get('task_id', '')} "
                f"{payload.get('error', '')} {payload.get('reason', '')}".strip())
    return None

def _json_line(obj: dict) -> None:
    """stdout 结构化行(唯一机器出口;禁人类文本混入)。flush 保 --json 管道实时。"""
    print(json.dumps(obj, ensure_ascii=False), flush=True)

def _emit_plain(text: str, *, err: bool = False) -> None:
    """人类文本出口:统一走 stderr——json 模式下保 stdout 机器流纯净;text 模式下
    stderr 同样可见。err 参数保留(调用方对称),不再据其分流(原 `if err or True`
    恒真死分支,2026-09-13 清理)。"""
    print(text, file=sys.stderr, flush=err)

def _emit_error(e: PyHError, *, json_mode: bool) -> None:
    """引擎错误出口(ERR §1.4):只回 code+advice(hint 优先——raise_code 现场
    提示比登记册通用 advice 更可行动;ctx 敏感明细不外泄)。"""
    detail = (e.ctx.get("advice") or e.ctx.get("hint") or e.spec.advice)
    if json_mode:
        _json_line({"type": "system.error",
                    "payload": {"code": e.code, "hint": str(detail)}})
    else:
        print(f"{e.code}:{detail}", file=sys.stderr)

def _result_ok(res: Any) -> bool:
    """任务结果成功归一(真实 TaskResult / mock 多面兼容)。"""
    if res is None:
        return True
    ok = getattr(res, "ok", None)
    if ok is not None:
        return bool(ok)
    reason = getattr(res, "reason", None)
    return reason in (None, "complete", "cancelled_by_user")

def _result_code(res: Any) -> str:
    return str(getattr(res, "code", "") or "")

def _result_summary(res: Any) -> str:
    val = getattr(res, "summary", None)
    if callable(val):
        try:
            val = val()
        except Exception:                              # noqa: BLE001 摘要面缺失
            val = None
    if not val:
        val = getattr(res, "reason", "ok") or "ok"
    return str(val)

def _result_reason(res: Any) -> str:
    return str(getattr(res, "reason", "") or "")

def _rejected_list(res: Any) -> list:
    """拒绝清单读取(鸭子类型):events_of('guard.rejected') / .rejected 均可。"""
    fn = getattr(res, "events_of", None)
    if callable(fn):
        try:
            return list(fn("guard.rejected") or [])
        except Exception:                              # noqa: BLE001 无该面
            return []
    return list(getattr(res, "rejected", None) or [])

async def _wait_and_render(sh: ShellCtx, task_id: str) -> Any:
    """wait_for 终态 + 并行总线渲染:渲染子任务随 wait 返回强制取消(终态事件或
    取消兜底双通道,杜绝悬挂订阅)。"""
    render = None
    bus = getattr(sh.ctx, "bus", None)
    if bus is not None:
        render = asyncio.create_task(_render_events(sh, task_id))
    try:
        return await sh.ctx.task_queue.wait_for(task_id)
    finally:
        if render is not None:
            render.cancel()
            try:
                await render
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 渲染清理
                pass

async def _render_events(sh: ShellCtx, task_id: str) -> None:
    """事件流渲染:订阅 llm.chunk(瞬时)/agent.message/tool.call/guard.rejected/
    approval.* 等;text 模式增量打字机 + 卡片,approval 触发交互提示;json 模式
    逐事件一行 JSONL 到 stdout(机器可读事件流)。终态事件或外部取消即收尾。"""
    renderer = StreamRenderer(mode="json" if sh.flags.get("json") else "text")
    q: asyncio.Queue = asyncio.Queue()
    bus = getattr(sh.ctx, "bus", None)
    if bus is None:
        return                                      # 无总线:渲染退化为摘要兜底
    subscriptions = []
    try:
        for pattern in _RENDER_PATTERNS:
            sub = bus.subscribe(pattern, lambda t, p: q.put_nowait((t, p)),
                                owner="cli")
            subscriptions.append(sub)
        while True:
            type_, payload = await q.get()
            if type_.startswith("approval."):
                await prompt_approval(sh, type_, payload)   # 审批交互(F015)
                continue
            if type_ == "llm.chunk":
                renderer.delta(_normalize_payload(payload))
                continue
            renderer.event(type_, payload)          # 卡片/json 行
            if type_ in _END_EVENT_TYPES:
                break
    finally:
        bus.unsubscribe_all("cli")
        renderer.newline()

async def prompt_approval(sh: ShellCtx, type_: str, payload: Any) -> None:
    """审批交互(F015 人类裁决):**仅** approval.requested 且通道非 None → 打印工具/
    参数摘要与风险级,键盘 [y/N] 裁决 → approve/deny;headless(channel=None)不提示
    ——裁决侧 APR-501 自动拒(零等待,勿悬挂)。

    身份契约(D1b):approval_id = 该 approval.requested 事件的 **Envelope.seq**
    (events/payload.py:189 载荷契约 / approval.py:292 生产赋值 / acp.py cmd_approve
    入口校验 三处同源;ApprovalRequestedPayload 四字段不含 approval_id)。结果事件
    (granted/denied/timeout)载荷虽含 approval_id,但不是人类动作请求 → 一律不提问。
    """
    if type_ != "approval.requested":
        return                                      # 结果事件:不提问(防误裁决)
    if sh.headless or sh.ctx.channel is None:
        return                                      # 无通道即拒:裁决侧已处理
    aid = getattr(payload, "seq", None)             # canonical identity(信封 seq)
    if not isinstance(aid, int):
        return                                      # 非信封来源:fail-safe 不提问
    payload = _normalize_payload(payload)           # 展示面:内层载荷
    approval = getattr(sh.ctx, "approval", None)
    ttl = payload.get("ttl_ms", "")
    risk = payload.get("risk", "?")
    tool = payload.get("tool", type_)
    print(f"\n[审批] {tool} {_trunc(str(payload.get('args_summary', '')), 160)}"
          f" (risk={risk}, ttl={ttl}ms)", file=sys.stderr)
    try:
        ans = (await asyncio.to_thread(input, "")).strip().lower()
    except (EOFError, KeyboardInterrupt):           # 用户中止 = 拒绝(等价 deny)
        ans = "n"
    verdict_fn = (approval.approve if ans in {"y", "yes"} else approval.deny)
    if approval is not None and callable(verdict_fn):
        out = verdict_fn(aid, by="cli")
        if hasattr(out, "__await__"):
            await out                              # 裁决入日志(approval.granted/denied)

def _report_field(report: Any, name: str, default: Any = "") -> Any:
    """报告字段鸭子读取:list → len(fixed/quarantined 为动作清单或计数);缺失回落
    默认(见偏离 12)。"""
    val = getattr(report, name, None)
    if val is None:
        val = default
    if isinstance(val, (list, tuple, set)):
        return len(val) if name in {"fixed", "lost", "quarantined"} else val
    return val

def _report_dict(report: Any, sid: str) -> dict:
    """报告 → dict(json 出口):优先 asdict()/dataclasses;否则显式字段拼装
    (json 保留原始形状:fixed/quarantined 动作清单或行号,与 repair 模块报告面
    一致;文本出口才用计数,见 _report_field)。"""

    def _raw(name, default):
        val = getattr(report, name, None)
        return default if val is None else val

    fn = getattr(report, "asdict", None)
    if callable(fn):
        try:
            out = dict(fn())
            out["sid"] = sid
            return out
        except Exception:                            # noqa: BLE001
            log.debug("repair report asdict failed", exc_info=True)
    import dataclasses as _dc
    if _dc.is_dataclass(report):
        out = _dc.asdict(report)
        out["sid"] = sid
        return out
    return {"sid": sid,
            "fixed": _raw("fixed", []),
            "lost": _raw("lost", 0),
            "backup_path": _raw("backup_path", None),
            "quarantined": _raw("quarantined", [])}

def exit_code_for(e: PyHError) -> int:
    """引擎码 → 进程码(码域内一律 1;用法错误由 argparse 退 2,此处不覆盖;
    中断 130 由 KeyboardInterrupt 路径负责)。"""
    return 130 if isinstance(e, KeyboardInterrupt) else 1

# Preserve historical import and pickle identities through the compatibility facade.
_CancelledRound.__module__ = 'pyharness.cli'
ParseResult.__module__ = 'pyharness.cli'
ShellCtx.__module__ = 'pyharness.cli'
ExitResult.__module__ = 'pyharness.cli'
StreamRenderer.__module__ = 'pyharness.cli'
