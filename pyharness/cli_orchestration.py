"""Cli orchestration: complete migrated implementation."""
from __future__ import annotations
from pyharness.cli_runtime import _wire_queue
import asyncio
import json
import sys
from pyharness.errors import raise_code

from pyharness.cli_output import ShellCtx, _json_line
from pyharness.cli_history import _session_lines, _sessions_dir_ctx

async def _cmd_job(sh: ShellCtx, positional: list[str], flags: dict) -> int:
    """job list / show <task_id> / logs <task_id>:任务记录查询(事件源派生)。"""
    sessions_dir = _sessions_dir_ctx(sh)
    from pyharness.persistence import session_log_paths
    action = (positional or ["list"])[0]
    if action not in ("list", "show", "logs"):
        raise_code("EVT-100", advice=f"未知 job 子命令:{action}",
                   hint="可用:job list | job show <task_id> | job logs <task_id>")
    rows: dict[str, dict] = {}
    events_by_tid: dict[str, list] = {}
    if sessions_dir.is_dir():
        for f in session_log_paths(sessions_dir):
            sid = f.stem
            try:
                with _session_lines(f) as fh:      # 会话级真源(段 + 主文件)
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            ev = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        p = ev.get("payload") or {}
                        tid = p.get("task_id") or ev.get("task_id")
                        if not tid or not str(tid).startswith("job:"):
                            continue
                        rec = rows.setdefault(
                            tid, {"task_id": tid, "sid": sid, "status": "queued",
                                  "seq": ev.get("seq"), "reason": None})
                        events_by_tid.setdefault(tid, []).append(ev)
                        t = ev.get("type", "")
                        if t == "job.started":
                            rec["status"] = "running"
                        elif t == "job.completed":
                            rec["status"] = "completed"
                            rec["reason"] = None
                        elif t == "job.failed":
                            rec["status"] = "failed"
                            rec["reason"] = p.get("reason")
                        elif t == "task.completed" and rec["status"] != "failed":
                            rec["status"] = "completed"
                        elif t == "task.failed" and rec["status"] != "completed":
                            rec["status"] = "failed"
                            rec["reason"] = p.get("error") or p.get("reason")
            except Exception:                        # noqa: BLE001 坏文件跳过
                continue
    # 崩溃遗留补正(R14-12):无终态的 job 只有在**宿主进程仍持有该会话**时才可能真在跑
    # —— job 跑在持有会话锁的宿主进程里,宿主崩溃即释放锁。此前一律报 `running` ⇒
    # 日志派生的 `job list/show` 永远显示一个并不存在的运行中任务(模块 docstring 声称
    # "崩溃…自动失败并告警",但全库无终态写入者:实测崩溃遗留 job 恒显示 running)。
    # 此处只**如实显示**、不写事件(读命令保持只读);事件层的恢复写入见 L-26。
    from pyharness.persistence import session_lock_held, session_lock_path
    live: dict[str, bool] = {}
    for rec in rows.values():
        if rec["status"] not in ("queued", "running"):
            continue
        sid = rec.get("sid") or ""
        if sid not in live:
            live[sid] = session_lock_held(
                session_lock_path(sessions_dir / f"{sid}.jsonl"))
        if not live[sid]:                            # 无进程持有 ⇒ 任务不可能在跑
            rec["status"] = "failed"
            rec["reason"] = rec["reason"] or "宿主进程已不在(崩溃遗留)"
    if action in ("show", "logs"):
        want = positional[1] if len(positional) > 1 else None
        if not want:
            raise_code("EVT-100", advice=f"job {action} 需要 task_id",
                       hint=f"用法:pyharness job {action} <task_id>")
        row = rows.get(want)
        if row is None:
            if flags.get("json"):
                _json_line({"task_id": want, "found": False})
            else:
                print(f"无匹配任务:{want}")
            return 1
        events = events_by_tid.get(want, [])
        if flags.get("json"):
            _json_line({"task_id": want, "job": row, "events": events})
            return 0
        print(f"{row['task_id']}  sid={row['sid']}  seq={row['seq']}  "
              f"{row['status']}" + (f"  reason={row['reason']}" if row["reason"] else ""))
        if action == "logs":
            for ev in events:
                p = ev.get("payload") or {}
                print(f"  #{ev.get('seq')} {ev.get('type')} {p}")
        return 0
    ordered = sorted(rows.values(), key=lambda r: (r["sid"], r["seq"]))
    if flags.get("json"):
        _json_line({"tasks": ordered})
        return 0
    if not ordered:
        print("无任务记录")
        return 0
    for r in ordered:
        extra = f"  reason={r['reason']}" if r.get("reason") else ""
        print(f"{r['task_id']}  sid={r['sid']}  seq={r['seq']}  "
              f"{r['status']}{extra}")
    return 0

async def _cmd_schedule(sh: ShellCtx, positional: list[str], flags: dict) -> int:
    """schedule list / add/remove/pause/resume(--session <sid>)。"""
    from pyharness.core.schedule import Scheduler
    from pyharness.persistence import session_log_paths
    sessions_dir = _sessions_dir_ctx(sh)
    action = (positional or ["list"])[0]
    if action == "list":
        jobs: dict[str, dict] = {}
        if sessions_dir.is_dir():
            for f in session_log_paths(sessions_dir):
                sid = f.stem
                try:
                    with _session_lines(f) as fh:   # 会话级真源(段 + 主文件)
                        for line in fh:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                ev = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            p = ev.get("payload") or {}
                            name = p.get("name") or p.get("job")
                            t = ev.get("type", "")
                            key = f"{sid}:{name}"
                            if t == "schedule.registered" and name:
                                jobs[key] = {"name": name, "sid": sid,
                                             "kind": p.get("kind"),
                                             "expr": p.get("expr"),
                                             "paused": bool(p.get("paused")),
                                             "next_fire_at": p.get("next_fire_at")}
                            elif t == "schedule.updated" and name and key in jobs:
                                jobs[key]["paused"] = bool(p.get("paused",
                                                                 jobs[key]["paused"]))
                            elif t == "schedule.removed" and name:
                                jobs.pop(key, None)
                except Exception:                    # noqa: BLE001 坏文件跳过
                    continue
        ordered = sorted(jobs.values(), key=lambda r: (r["sid"], r["name"]))
        if flags.get("json"):
            _json_line({"jobs": ordered})
            return 0
        if not ordered:
            print("无定时任务")
            return 0
        for j in ordered:
            state = "暂停" if j["paused"] else "运行"
            print(f"{j['sid']}:{j['name']}  {j['kind']} {j['expr']}  {state}"
                  f"  下次 {j.get('next_fire_at')}")
        return 0
    if action not in ("add", "remove", "pause", "resume"):
        raise_code("EVT-100", advice=f"未知 schedule 子命令:{action}",
                   hint="可用:list | add <name> <kind> <expr> <intent> | "
                        "remove|pause|resume <name>")
    sid = flags.get("session")
    if not sid:
        raise_code("EVT-100", advice="管理定时任务需要 --session <sid>",
                   hint="用法:pyharness schedule add ... --session <sid>")
    from pyharness.core.session import open_session
    from pyharness.persistence import flush_kwargs_of, open_store
    sid = str(sid)
    if not (sessions_dir / f"{sid}.jsonl").exists():
        raise_code("EVT-106", session_id=sid, hint="目标会话不存在")
    store = open_store(sid, dir=sessions_dir,
                       **flush_kwargs_of(sh.ctx.settings))
    log_ = await open_session(sid, store)
    sched = Scheduler.rebuild_for_session(log_)
    try:
        if action == "add":
            if len(positional) < 4:
                raise_code("EVT-100", advice="add 缺参数",
                           hint="用法:schedule add <name> <kind> <expr> <intent>")
            intent = " ".join(positional[4:])
            await sched.register(positional[1], positional[2],
                                 positional[3], {"intent": intent})
        elif action in ("remove", "pause", "resume"):
            if len(positional) < 2:
                raise_code("EVT-100", advice=f"{action} 需要 name",
                           hint=f"用法:schedule {action} <name> --session <sid>")
            fn = {"remove": sched.remove, "pause": sched.pause,
                  "resume": sched.resume}[action]
            await fn(positional[1])
    finally:
        store.flush()
        store.close()
    msg = {"ok": True, "action": action, "session_id": sid,
           "name": positional[1] if len(positional) > 1 else None}
    if flags.get("json"):
        _json_line(msg)
    else:
        print(f"schedule {action} 完成(sid={sid})")
    return 0

async def _cmd_plan(sh: ShellCtx, positional: list[str], flags: dict, *, new_session, attach_engine) -> int:
    """plan <goal>:LLM 提案 → 交互批准 → 逐步执行(F045/46 真链)。"""
    from pyharness.core.plan_mode import PlanManager
    goal = " ".join(positional).strip()
    if not goal:
        raise_code("EVT-100", advice="plan 需要目标文本",
                   hint="用法:pyharness plan <目标>")
    if sh.ctx.session is None:
        sh.ctx.session = await new_session(sh.ctx.settings, sh.ctx.bus)
        _wire_queue(sh.ctx)
    await attach_engine(sh)
    pm = getattr(getattr(sh.ctx, "engine_spine", None), "plan", None)
    if pm is None:
        pm = PlanManager()
    p = await pm.plan_propose(goal, sh.ctx)
    summary = {"plan_id": p.id, "goal": p.goal, "selfcheck_ok": p.selfcheck_ok,
               "expires_at": str(p.expires_at),
               "steps": [{"action": s.action, "tool": s.tool,
                          "expected": s.expected, "risk": s.risk}
                         for s in p.steps]}
    if flags.get("json"):
        _json_line(summary)
    else:
        print(f"方案 {p.id}: {p.goal}(selfcheck={'ok' if p.selfcheck_ok else 'fail'})")
        for i, s in enumerate(p.steps, 1):
            print(f"  {i}. [{s.risk}] {s.tool}: {s.action} → {s.expected}")
    if sh.headless or sh.ctx.channel is None:
        print("headless 不自动批准;方案已落盘,可用桌面/交互 chat 后续确认",
              file=sys.stderr)
        return 0
    try:
        ans = (await asyncio.to_thread(
            input, f"批准方案 {p.id}? [y/N]: ")).strip().lower()
    except (EOFError, KeyboardInterrupt):
        ans = "n"
    if ans not in ("y", "yes"):
        await pm.plan_reject(p.id, who="cli", reason="cli-rejected", ctx=sh.ctx)
        print("已拒绝")
        return 0
    await pm.plan_approve(p.id, who="cli", ctx=sh.ctx)
    tasks = list(getattr(pm, "_exec_tasks", set()) or ())
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    final = pm.get(p.id)
    status = final.status if final is not None else "?"
    print(f"方案完成状态: {status}")
    return 0 if status == "completed" else 1

async def _cmd_workflow(sh: ShellCtx, positional: list[str], flags: dict, *, new_session, attach_engine) -> int:
    """workflow <步骤1> [步骤2 …]:当前(或新建)会话内顺序编排(#45 真链)。

    每步经队列提交真实 AgentLoop(与 chat 同一条 runner seam),workflow.step
    事件留痕(started/done/failed);stop_on_fail=True = 首败即停。CLI 此前无
    workflow 入口(管理面只在桌面壳),本条补齐。
    """
    from pyharness.core.workflow import WorkflowRunner, queue_submit_adapter
    steps = [s for s in positional if str(s).strip()]
    if not steps:
        raise_code("EVT-100", advice="workflow 需要至少一个步骤文本",
                   hint='用法:pyharness workflow "步骤1" "步骤2" …')
    if sh.ctx.session is None:
        sh.ctx.session = await new_session(sh.ctx.settings, sh.ctx.bus)
        _wire_queue(sh.ctx)
    await attach_engine(sh)
    queue = getattr(sh.ctx, "task_queue", None)
    log_ = sh.ctx.session
    if queue is None or log_ is None:
        raise_code("CYC-999", cmd="workflow",
                   hint="会话或任务队列未装配(引擎未接)")
    runner = WorkflowRunner(log_, queue_submit_adapter(log_, queue), name="cli")
    results = await runner.run(steps, stop_on_fail=True)
    ok = bool(results) and all(r["ok"] for r in results)
    sid = str(getattr(log_, "sid", ""))
    if flags.get("json"):
        _json_line({"sid": sid, "ok": ok, "results": results})
    else:
        print(f"工作流 {len(results)} 步(会话 {sid}):")
        for r in results:
            mark = "✅" if r["ok"] else "❌"
            print(f"  {mark} [{r['index']}] {str(r['intent'])[:60]} → "
                  f"{str(r['summary'])[:100]}")
    return 0 if ok else 1
