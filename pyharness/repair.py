"""pyharness/repair.py — 崩溃修复管线(用户面编排)(specs/repair.py.md 契约;F060/F064)

一句话职责:崩溃恢复管线(F060)——启动/打开会话时自动扫描全会话健康(尾部半行/
中部坏行/seq 空洞/派生索引对账),对损坏会话执行"先备份 `.corrupt-{ts}` → 尾部
半行截断 → 中部坏行隔离到 quarantine 文件(不删,主文件原子重写)→ seq 空洞定位
(对照 compacted/recovered 声明)→ FTS/storage 派生视图整体重建 → 强同步追加
session.recovered 声明"的**幂等**修复,产出 RepairReport;不可修复 → 明确报错且
原文件备份留存;交互模式让用户决策"丢弃尾部 or 保留 / 隔离行删留"。

与 persistence 的分工(本模块 docstring 权威):persistence(核心)提供 JSONL 物理
原语(原子写/严格行读/截断检测偏移);**本模块是 F060 修复策略与编排的唯一归属**
(备份命名、隔离文件、空洞判定、报告、recovered 声明、交互决策)。persistence 草案
同名 repair 仅供存储层内部使用;CLI/启动自检的 repair 一律走本模块(修复策略不并存)。
备份命名以 PRD F060 为唯一权威:`{sid}.corrupt-{ts}.jsonl`(persistence 草案的
`.jsonl.bak-{ts}` 旧命名不改动其自身,但本模块一律不用)。

依赖(单向只读,INV-08):errors.raise_code(PERS-201/202 唯一出口)、events
(Envelope 解析/check_seq_gap/词表校验)、persistence 物理原语(open_store/
detect_truncation/default_sessions_dir);core.session.open_session 惰性导入
(declare_recovered 打开目标会话日志);零 LLM 依赖(崩溃恢复不依赖模型可用性)。

修复的伦理 = 数据优先:隔离不删除(隔离档写失败 → 该行保留原地,绝不丢行)、
任何"修坏删更多"都是失败、修复过程本身可审计(session.recovered 事件 + 备份)。

偏离说明(契约 = specs/repair.py.md,以下为与规格伪码冲突/本仓库既有实现约束下的
取舍,均列理由,与 cli.py/compaction.py 同款先例):
1. recovered 事件载荷按**注册词表**(events/payload.py SessionRecoveredPayload,
   extra=forbid,fixed 非空/lost/backup)构造:spec 伪码 payload 含 quarantined 计数
   与 declared_holes 扩展字段,但真实校验链(EVT-100)拒多余字段 → quarantined 改由
   RepairReport + 隔离档承载,declared_holes 经 fixed "seq-holes:[...]" 词条表达
   (persistence._declared_by_compaction 同款口径);lost 从伪码 int 计数改为
   已注册载荷的 list[int](截断半行的候选 seq;报告侧 lost=len)。
2. truncate_tail 对 offset==0 且文件非空(整文件即一条未完成半行,无任何换行)截到 0:
   spec 伪码 offset<=0 早退会让该类文件永不收敛且破坏幂等;未完成即未发生,备份已留存。
3. declare_recovered 不追加 when fixed 为空:注册载荷 fixed min_length=1,
   "用户保留原地"(keep_quarantine=False)不算修复动作 → 空动作一律不产声明。
4. 派生视图对账/FTS 落后提供者:本仓库 SessionQueryIndex 未暴露 max_seq(sid) 查询面,
   且装配层(cli.assemble_ctx)未挂 ctx.session_query/kv_rebuild → scan_session 的
   index_stale 仅在调用方注入 fts_last_seq(sid) 提供者(或 ctx.session_query.max_seq /
   ctx.fts_last_seq 可调用)时才判定,无视图提供者 = 不算落后(否则全会话被误报不健康);
   rebuild_derived_views 对未装配视图降级为日志并返回 False(派生可弃,装配后自动生效),
   对账(max_seq 可查时)不一致 → PERS-201 上抛(spec 异常表)。
5. scan_session 逐行粒度坏行(编码级坏块=单行隔离):整文件"编码级不可修复 .corrupt"
   路径归 persistence(存储层原语);格式语义不符(如首事件非 session.created)由
   session 回放校验(EVT-100/106)处置,本模块不重复造 PERS-201 分支。
6. rebuild_derived_views/auto_scan 因真实依赖形态改 async / 增可选 provider 参数:
   SessionQueryIndex.rebuild 为 async(spec 伪码 def 与真实依赖冲突,以真实形态为准)。
7. 会话被 ctx.session 持有(sid 匹配)时 repair 直接 PERS-202 拒修:文件级重写会让
   活会话的追加句柄/内存 SeqState 失同步(双开 = 空洞主因,DEP §6 禁双开)。
8. quarantine_lines 不再二次 input 问询(交互问询集中在 interactive_confirm 一次问清,
   spec 伪码对同一批坏行问两遍为残留);消费 policy.keep_quarantine 决策。
9. fsync 目录在 Windows 为尽力而为(NTFS 目录句柄受限),rename 原子替换已保证无半写。
"""
from __future__ import annotations

import inspect
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from pyharness.errors import PyHError, raise_code
from pyharness.events import (DECLARE_TYPES, Envelope, check_seq_gap,
                              declared_ranges)
from pyharness.persistence import (acquire_session_lock, default_sessions_dir,
                                   detect_truncation,
                                   flush_kwargs_of, is_aux_session_file,
                                   open_store, release_session_lock,
                                   rotated_segment_paths, session_lock_path)


# ===================================================================== 常量
# 空洞合法化声明事件(PRD §3.4:compacted 折叠区间 / recovered 修复区间)
# 判据唯一来源 events.DECLARE_TYPES(R14-9)


# ================================================================= 数据结构


# ================================================================= 基础工具


# ================================================================= 健康扫描


# ================================================================= 修复管线
from pyharness.repair_scan import (log, _QUARANTINE_RAW_CAP, _TAIL_SEQ_PROBE, _DECLARE_TYPES, now_ts, _rotated_segments, _segment_damage, _segment_scan, _seg_label, _session_content_last, _is_aux_file, SessionHealth, QuarantineEntry, RepairReport, RepairPolicy, _parse_or_none, _indexable, _declared_ranges, _iter_text, _iter_bytes, _sessions_dir, _fts_last_seq_provider, _db_path_of, _log_last_seq, scan_session, auto_scan)
from pyharness.repair_recovery import (backup_file as _backup_file, locate_holes, _index_handle, rebuild_derived_views, declare_recovered, _DirectBus, _dropped_seqs)

def _fsync_dir(d: Path) -> None:
    """目录 fsync(rename 持久化保险;Windows 目录句柄受限 → 尽力而为,偏离 9)。"""
    if os.name == "nt":                    # NTFS 目录 fsync 需特殊句柄,rename 原子已够
        return
    try:
        fd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


async def repair_session(ctx: Any, sid: str, *, interactive: bool = False,
                         policy: Optional[RepairPolicy] = None) -> RepairReport:
    """F060 主入口(PRD 验收伪码直译):扫描 → 无损坏空报告(幂等)→ 先备份
    `.corrupt-{ts}` → 尾部半行截断 → 中部坏行隔离(删留决策按 policy)→ seq 空洞
    定位告警 → 派生视图整体重建 → 强同步追加 session.recovered → 返回报告。

    异常表:备份/重写落盘失败 → PERS-202(原文件未动或备份留存);会话被 ctx.session
    持有(sid 匹配,双开)→ PERS-202 拒修;不可修复编码整块由 persistence 层处置。
    """
    sdir = _sessions_dir(ctx)
    path = sdir / f"{sid}.jsonl"
    if not path.exists():                  # 不存在 = 空报告(幂等,新会话前身)
        return RepairReport(sid=sid)
    sess = getattr(ctx, "session", None)   # 禁双开:活会话文件级重写 = 句柄失同步
    if sess is not None and getattr(sess, "sid", None) == sid:
        raise_code("PERS-202", sid=sid,
                   advice="会话正被 ctx.session 持有,禁双开修复(DEP §6);"
                          "先退出该会话进程再 repair")
    lock_path = session_lock_path(path)    # 跨进程独占锁:活会话占用 → PERS-202
    acquire_session_lock(lock_path)
    try:
        return await _repair_locked(ctx, sid, path,
                                    interactive=interactive, policy=policy)
    finally:
        release_session_lock(lock_path)


async def _repair_locked(ctx: Any, sid: str, path: Path, *,
                         interactive: bool,
                         policy: Optional[RepairPolicy]) -> RepairReport:
    """repair_session 的持锁突变段(扫描 → 备份 → 截断/隔离 → 重建 → 声明)。

    调用方(repair_session)已持会话独占锁并经 ctx.session 双开守卫,故本段对目标
    文件的全部读-改-写处于单写者保护下(跨进程活写者已被锁挡在 repair 之外,不再
    出现"repair 关句柄整写时他进程活句柄写的字节静默消失")。
    """
    health = await scan_session(path, fts_last_seq=_fts_last_seq_provider(ctx))
    seg_damage = _segment_damage(path)          # 轮转段损坏面(只读;R13-5)
    if health.healthy and not seg_damage:       # 二次 repair:无动作(幂等)
        return RepairReport(sid=sid)
    pol = policy
    if pol is None:
        pol = (interactive_confirm(health) if interactive else RepairPolicy())
    backup = backup_file(path)             # ① 修复前强制备份 .corrupt-{ts}
    fixed: list[str] = []
    lost_seqs: list[int] = []
    kept: list[QuarantineEntry] = []
    if health.tail_truncated and pol.drop_tail:      # ② 尾部半行:截断续跑
        lost_seqs = _dropped_seqs(path, health.tail_offset)
        truncate_tail(path, health.tail_offset or 0)  # offset=0 = 整文件半行(偏离 2)
        fixed.append("tail-truncated")
    if health.bad_lines:                   # ③ 中部坏行:隔离不自动删
        kept = await quarantine_lines(path, health.bad_lines, pol)
        fixed += [f"quarantine-{e.line_no}" for e in kept if e.archived]
    # ⑤' 轮转段同口径修复(R13-5):它们是真源的一部分,损坏必须可见且可修 ——
    #     段级同样"修复前强制备份";尾部半行截断、中部坏行隔离(不删、字节日志保真)。
    for seg, torn_off, bad_lines in seg_damage:
        label = _seg_label(seg)
        backup_file(seg)                             # 段级备份(失败即 PERS-202 中止)
        if torn_off is not None and pol.drop_tail:
            lost_seqs += _dropped_seqs(seg, torn_off)
            truncate_tail(seg, torn_off or 0)
            fixed.append(f"{label}-tail-truncated")
        if bad_lines:
            entries = await quarantine_lines(seg, bad_lines, pol)
            kept += entries
            fixed += [f"{label}-quarantine-{e.line_no}"
                      for e in entries if e.archived]
    holes_alert = locate_holes(health)     # ④ seq 空洞:未声明 → 告警 F031 深查
    if holes_alert and not interactive:
        fixed.append("seq-holes:" + repr(holes_alert))
    rebuilt = False
    if health.index_stale or fixed:        # ⑤ 派生视图整体重建(原则 1)
        rebuilt = await rebuild_derived_views(ctx, sid)
    if rebuilt and "views-rebuilt" not in fixed:
        fixed.append("views-rebuilt")      # 结构表词条:重建动作入审计
    seq = await declare_recovered(ctx, sid, fixed, lost_seqs, kept, backup)
    return RepairReport(sid=sid, fixed=fixed, quarantined=kept,
                        lost=len(lost_seqs), backup_path=backup,
                        holes_alert=holes_alert, recovered_seq=seq)


# ================================================================= 交互决策
def _ask(prompt: str, default: bool = True) -> bool:
    """交互问询([Y/n] 语义);无输入(EOF/非 tty)→ 安全默认。
    特殊:prompt 含 '[K]保留' 时按 k 视同 n(用户显式保留 = 拒绝默认动作)。
    """
    try:
        ans = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return default
    if not ans:
        return default
    if "[k]" in prompt.lower() and ans in ("k", "keep"):
        return False                       # 用户选保留(拒绝截断/隔离默认动作)
    return not ans.startswith("n")


def interactive_confirm(health: SessionHealth) -> RepairPolicy:
    """交互决策(tty):尾部半行 → [T]截断续跑(默认)/[K]保留退出;中部坏行 → 隔离
    默认(保留原地可选);空洞 → F031 深查提示(不回填)。headless(auto)= 全部安全
    默认,不悬挂等待。EOFError → 回退 auto 默认(安全侧:截断+隔离,偏离 8 单次问询)。
    """
    print(f"[repair] {health.sid} 检测:尾部截断={health.tail_truncated} "
          f"坏行={len(health.bad_lines)} 空洞={health.holes}")
    drop_tail = True
    if health.tail_truncated:
        ans = _ask("尾部半行未完成事实,已备份。[T]截断续跑(默认) [K]保留退出: ",
                   default=True)
        if not ans:                          # 用户选择保留:会话不可续跑
            drop_tail = False
    keep_q = True
    if health.bad_lines:
        keep_q = _ask(f"隔离 {len(health.bad_lines)} 行坏数据? [Y/n](n=保留原地): ",
                      default=True)
    if health.holes:
        print(f"[repair] 存在未声明 seq 空洞 {health.holes} → F031 深查线索;"
              "空洞不回填,append 恒 max+1")
    return RepairPolicy(mode="interactive", drop_tail=drop_tail,
                        keep_quarantine=keep_q)


# ================================================================= 隔离区查看
def view_quarantine(sid: str, sessions_dir: Path) -> list[QuarantineEntry]:
    """隔离区查看(删留决策数据源):列出 `{sid}.quarantine-*.jsonl` 全部隔离条目
    (行号/原文/原因/归档)。repair 本身永不删除;隔离档自持结构(非 Envelope,不强
    校验),隔离档自身损坏 → 跳过该行并日志(隔离档非真源,损坏无害)。
    """
    out: list[QuarantineEntry] = []
    d = Path(sessions_dir)
    for q in sorted(d.glob(f"{sid}.quarantine-*.jsonl")):
        try:
            lines = q.read_text(encoding="utf-8").splitlines()
        except OSError as e:
            log.warning("repair: 隔离档读失败跳过 %s why=%s", q, e)
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError as exc:
                log.warning("repair: 隔离档行损坏跳过 %s:%s", q, exc)
                continue
            out.append(QuarantineEntry(
                line_no=int(e.get("line_no", 0)),
                raw=str(e.get("raw", ""))[:_QUARANTINE_RAW_CAP],
                reason=str(e.get("reason", "parse-fail")),
                archived=bool(e.get("archived", True))))
    return out


__all__ = [
    # 数据结构
    "SessionHealth", "QuarantineEntry", "RepairReport", "RepairPolicy",
    # 函数清单(spec 契约面)
    "auto_scan", "scan_session", "repair_session", "backup_file",
    "truncate_tail", "quarantine_lines", "locate_holes",
    "rebuild_derived_views", "declare_recovered", "interactive_confirm",
    "view_quarantine", "now_ts",
]


def backup_file(path: Path) -> Path:
    """Delegate archival work with the facade-owned timestamp source."""
    return _backup_file(path, _clock=now_ts, _sync_directory=_fsync_dir)




def truncate_tail(path: Path, offset: int) -> None:
    """尾部半行原子截断:截断到 offset(最后完整行末尾)+ fsync。

    半行内未完成事件按 lost 声明(declare_recovered),绝不尝试补全;offset==0 且
    文件非空(整文件即半行,无任何换行)→ 截到 0(偏离 2:未完成即未发生)。
    异常:OSError → PERS-202(备份已存在,报错中止,禁止继续)。
    """
    if offset is None:
        return
    if offset <= 0 and path.stat().st_size == 0:
        return                              # 空文件:无半行不动
    try:
        with open(path, "r+b") as f:
            f.truncate(offset)              # 截断点 = 最后完整 \\n 后(字节级)
            f.flush()
            os.fsync(f.fileno())
    except OSError as e:
        raise_code("PERS-202", op="truncate_tail", path=str(path), why=str(e),
                   advice="截断失败(备份已在 .corrupt-{ts});文件可能半截,禁止继续")
    log.warning("repair: 尾部半行截断于 offset=%d path=%s", offset, path)


def _append_quarantine(qpath: Path, record: dict) -> bool:
    """隔离档逐条追加写(自持结构,非 Envelope;≤8KB 防爆已在调用方截断)。

    失败(磁盘/权限)→ 记日志返回 False:调用方把该行保留在主文件(隔离≠丢数据)。
    """
    try:
        with open(qpath, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            fh.flush()
        return True
    except OSError as e:
        log.warning("repair: 隔离档写失败(行保留原地) qpath=%s why=%s", qpath, e)
        return False


async def quarantine_lines(path: Path, bad: list[int],
                           pol: RepairPolicy) -> list[QuarantineEntry]:
    """中部坏行隔离(不删,删留用户定):坏行抽到 `{sid}.quarantine-{ts}.jsonl`
    (行号/原文/原因,≤8KB/条),好行写临时文件,fsync+rename 原子替换主文件。

    pol.keep_quarantine=False(交互问询选"保留原地")→ 不重写主文件,只返回
    reason="user-keep" 的只读记录(报警留痕);隔离档写失败 → 该行保留原地且
    archived=False(数据优先:隔离≠删除,任何丢行都是失败)。
    异常:临时文件/rename 失败 → PERS-202(已备份存在)。
    """
    if not bad:
        return []
    badset = set(bad)
    if not pol.keep_quarantine:            # 用户保留原地(不删不隔离)
        out: list[QuarantineEntry] = []
        for no, text in _iter_text(path):
            if no in badset:
                out.append(QuarantineEntry(
                    line_no=no, raw=(text or "")[:_QUARANTINE_RAW_CAP],
                    reason="user-keep", archived=False))
        return out
    qpath = path.with_name(f"{path.stem}.quarantine-{now_ts()}.jsonl")
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    kept: list[QuarantineEntry] = []
    try:
        with open(tmp, "wb") as out_fh:
            for no, rawb in _iter_bytes(path):
                if no not in badset:
                    out_fh.write(rawb)             # 好行原样保留(含原行尾,字节往返)
                    continue
                text = rawb.rstrip(b"\r\n").decode("utf-8", "replace")[:_QUARANTINE_RAW_CAP]
                entry = QuarantineEntry(line_no=no, raw=text,
                                        reason="parse-fail", archived=True)
                if _append_quarantine(qpath, {"line_no": no, "raw": text,
                                              "reason": "parse-fail",
                                              "archived": True}):
                    kept.append(entry)
                else:                        # 隔离档写失败:行保留原地(不丢数据)
                    out_fh.write(rawb)       # 原样(含原行尾)
                    kept.append(QuarantineEntry(line_no=no, raw=text,
                                                reason="parse-fail",
                                                archived=False))
            out_fh.flush()
            os.fsync(out_fh.fileno())        # 数据落盘再换名(无半写,DIS §8)
        os.replace(tmp, path)                # rename 原子替换:旧完整或新完整
        _fsync_dir(path.parent)
    except OSError as e:
        if tmp.exists():
            try:
                tmp.unlink()                 # 失败清理,不留临时残留
            except OSError:
                pass
        raise_code("PERS-202", op="quarantine", path=str(path), why=str(e),
                   advice="隔离重写失败(备份已在 .corrupt-{ts});重跑 repair")
    if not qpath.exists():                   # 全部隔离失败且行已保留:无隔离档
        log.warning("repair: 坏行全部保留原地(隔离档写失败),见报告 archived=False")
    log.info("repair: 中部坏行隔离 path=%s archived=%d kept=%d",
             qpath, sum(1 for e in kept if e.archived), len(kept))
    return kept

