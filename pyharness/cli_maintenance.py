"""Cli maintenance: complete migrated implementation."""
from __future__ import annotations
import json
import sys
from pathlib import Path
from typing import Optional
from pyharness import config as config

from pyharness.cli_output import ShellCtx, _json_line, _report_dict, _report_field, log
from pyharness.cli_settings import _load_settings
from pyharness.cli_sessions import _repairable_unhealthy, _sessions_path

def _skill_cmd(positional: list[str], flags: dict) -> int:
    """skill [name]:列本地技能包目录(离线零装配)或打印某技能正文。

    技能库 = 仓库 skills/(内置示例)+ settings.skills.dir(用户目录);正文经
    SkillManager.load 截断保护(超长截断)。CLI 此前只能由 LLM 调 skill.load
    看技能,人看不到——本条给人一个只读入口(与桌面技能页同源数据)。
    """
    from pyharness.core.skill import SkillManager
    settings = _load_settings(flags.get("config"))
    roots = [Path(__file__).resolve().parents[1] / "skills"]
    extra = getattr(getattr(settings, "skills", None), "dir", None)
    if extra:
        roots.append(Path(str(extra)).expanduser())
    mgr = SkillManager(roots)
    name = " ".join(positional).strip()
    if not name:
        items = mgr.list()
        data = {"roots": [str(r) for r in roots], "count": len(items),
                "skills": items}
        if flags.get("json"):
            _json_line(data)
        else:
            print(f"技能库({len(items)} 个):{', '.join(str(r) for r in roots)}")
            for it in items:
                print(f"  - {it['name']}: {it['description']}")
        return 0
    skill = mgr.load(name)                       # 未知名 → SKL-901(带 advice)
    if flags.get("json"):
        _json_line(skill)
    else:
        print(f"# {skill['name']}\n{skill['description']}\n目录: {skill['dir']}\n")
        print(skill["body"])
    return 0


def _target_cfg_path(flags: dict) -> Optional[str]:
    """config 子命令目标路径:--config 显式优先,否则 ~/.pyharness/config.yaml。"""
    p = flags.get("config")
    if p:
        return str(p)
    return str(Path("~/.pyharness/config.yaml").expanduser())


def _scan_usage(sessions_dir: Path) -> dict:
    """会话日志只读扫描(离线派生):逐**会话**数事件、尾部终态与 llm.usage 成本聚合。

    单文件不可读(被占用/权限)只跳过并告警,**不中止整条离线命令**——与
    ``_scan_unhealthy`` 同款容错(RT-02:此前一个坏文件使 budget/stats 整体
    以 CYC-999 退出)。``sessions`` 仍记扫描面(含被跳过者),聚合值只含可读者。

    2026-09-21 R14-3:改为按**会话**聚合(``session_data_paths`` = 轮转段 + 主文件,
    每事件恰计一次)。此前按 ``glob("*.jsonl")`` **逐文件**计,而 ``{sid}.N.jsonl`` /
    ``{sid}.corrupt-*`` / ``{sid}.quarantine-*`` 全被当作独立会话 ⇒ 修复备份是主文件的
    **字节副本** ⇒ 事件与成本**重复计**,隔离档的坏行也计入事件数(实测:1 会话 → 报
    "3 个会话 / 3 事件")。终态只取**主文件**尾部(段不承载终态)。
    """
    from pyharness.persistence import session_data_paths, session_log_paths
    sessions = session_log_paths(sessions_dir)
    total_events = 0
    finished = 0
    cost = 0.0
    for main in sessions:
        tail_type = ""
        n = 0
        try:
            for f in session_data_paths(main):
                with open(f, encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        n += 1
                        try:
                            ev = json.loads(line)
                        except json.JSONDecodeError:
                            continue              # 坏行不计(repair 域)
                        if f == main:             # 终态只见于主文件尾部(按值比较:
                            tail_type = ev.get("type", "")   # Path(p) 非同对象)
                        if ev.get("type") == "llm.usage":
                            cost += float(
                                (ev.get("payload") or {}).get("cost_est") or 0.0)
        except OSError as e:                      # 单会话不可读:跳过并告警,不中止
            log.warning("budget 扫描跳过不可读会话 sid=%s why=%s", main.stem, e)
            continue
        total_events += n
        if tail_type == "session.finished":
            finished += 1
    return {"sessions": len(sessions), "events": total_events,
            "finished": finished, "cost_est": round(cost, 4)}

def _budget_cmd(flags: dict) -> int:
    """budget 报表(离线,F032 数据):任务硬闸/月度上限(配置锁死)+ 实测成本聚合。"""
    settings = _load_settings(flags.get("config"))
    b = settings.budget
    usage = _scan_usage(_sessions_path(settings))
    data = {
        "task": {"max_cost_yuan": b.task.max_cost_yuan,
                 "max_in_tokens": b.task.max_in_tokens,
                 "max_out_tokens": b.task.max_out_tokens,
                 "warn_ratio": b.warn_ratio},
        "monthly": {"limit_yuan": b.monthly.limit_yuan,
                    "alert_ratio": b.monthly.alert_ratio},
        "usage": {"cost_est": usage["cost_est"],
                  "sessions_scanned": usage["sessions"]},
    }
    if flags.get("json"):
        _json_line(data)
        return 0
    lim = b.monthly.limit_yuan
    limit_txt = f"¥{lim:.2f}(未启用)" if lim <= 0 else f"¥{lim:.2f}"
    print(f"任务预算硬闸: ¥{b.task.max_cost_yuan:.2f}/任务 "
          f"(in≤{b.task.max_in_tokens:,} out≤{b.task.max_out_tokens:,} tok, "
          f"告警 {b.warn_ratio:.0%})")
    print(f"月度上限: {limit_txt}(告警 {b.monthly.alert_ratio:.0%})")
    print(f"实测估算: ¥{usage['cost_est']:.4f}({usage['sessions']} 个会话)")
    return 0

def _stats_cmd(flags: dict) -> int:
    """stats 会话统计(只读派生):会话数/事件数/终态数。"""
    settings = _load_settings(flags.get("config"))
    data = _scan_usage(_sessions_path(settings))
    if flags.get("json"):
        _json_line({"sessions": data["sessions"], "events": data["events"],
                    "finished": data["finished"],
                    "sessions_dir": str(_sessions_path(settings))})
        return 0
    print(f"会话数: {data['sessions']}")
    print(f"事件总数: {data['events']}")
    print(f"已终态会话: {data['finished']}")
    print(f"日志目录: {_sessions_path(settings)}")
    return 0

async def repair_cmd(sh: ShellCtx, sid: Optional[str] = None) -> int:
    """repair 子命令桥(F060):对 sid(缺省 = 自动扫描首损)执行修复管线;报告输出;
    不可修复 → 明确报错 + 备份留存(PERS-201 上抛)。"""
    from pyharness.repair import auto_scan, repair_session     # 惰性(偏离 7)
    if not sid:
        # R11-2:带上 db_path ⇒ 索引落后对账一并生效(此前该对账无提供者,恒不启用)
        _db = str(getattr(getattr(getattr(sh.ctx, "settings", None), "storage", None),
                          "db_path", "") or "")
        hits = await auto_scan(sh.ctx.storage.sessions_dir,
                               db_path=_db or None)
        # R14-7:与启动自检同一候选筛选点(跳过活会话)—— 否则唯一"待修"是活会话时
        # 整条命令直接抛 PERS-202,且够不到真正损坏的会话。
        sid = _repairable_unhealthy(hits, sh.ctx.storage.sessions_dir)
    if not sid:
        print("[repair] 无损坏会话")
        return 0
    interactive = sys.stdin.isatty()
    report = await repair_session(sh.ctx, sid, interactive=interactive)
    if sh.flags.get("json"):
        _json_line(_report_dict(report, sid))
    else:
        print(f"[repair] {sid}: fixed={_report_field(report, 'fixed')} "
              f"lost={_report_field(report, 'lost', 0)} "
              f"backup={_report_field(report, 'backup_path', '')} "
              f"quarantined={_report_field(report, 'quarantined', 0)}")
    return 0
