#!/usr/bin/env python
"""scripts/remediation_run.py — Governed Auto-Fix Loop executor (v1.0).

一句话职责:消费 `scripts/audit_run.py` 的**治理判定结果**,对**已被授权**的
Fix Unit 执行受治理的自动修复——资格校验 → 有限修复 → 定向测试 → 全量回归 →
**新鲜重审** → 循环防护 → 人工复核/续跑。

**职责分离(硬约束)**:
    audit_run.py        负责"这个问题**能不能**进入自动修复"(发现/分类/治理决策/M1-M5)
    remediation_run.py  负责"这个**已被授权**的问题**怎么修**"(执行/验证/重审/证据)

本模块**不**判定资格、**不**改 finding 分类、**不**碰 M1-M5 gate、**不**写 audit
manifest——它**读取** audit 侧的事实,只写自己的 remediation manifest。

**Fix Unit 不是 Finding**:`Finding → Root Cause → Fix Unit`。一个 Fix Unit 可含
多个 finding,但**只按共同根因聚类**;跨边界(架构/治理/权限/Protocol/目标/范围)
一律**不得自动合并**,直接进 HUMAN_REVIEW。

**Fail-closed 资格模型**:默认 `HUMAN_REVIEW`;只有**全部**合格条件满足才 `AUTO_FIX`。
任一高风险条件无法确认 ⇒ `HUMAN_REVIEW` + 对应 Hx——**不猜测、不自动选架构方案、
不扩大 scope**。

**自指防护**:自动修复修改的正是被审计的系统。故每个 Fix Unit 在修复后**必须**
重新取证(`FRESH_REAUDIT`),**不得**复用修复前的证据。

用法:
    python scripts/remediation_run.py units       --run-id <ID>
    python scripts/remediation_run.py classify    --run-id <ID>
    python scripts/remediation_run.py eligibility --run-id <ID>
    python scripts/remediation_run.py run         --run-id <ID> --unit FIX-001
    python scripts/remediation_run.py run         --run-id <ID> --all
    python scripts/remediation_run.py authorize   --run-id <ID> --unit FIX-001 --by <who>
    python scripts/remediation_run.py status      --run-id <ID>
    python scripts/remediation_run.py rules

本文件不参与 `pyharness/` 装配,不被 `pyharness` import,不在不变量扫描边界内。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

import yaml

_ROOT = Path(__file__).resolve().parents[1]
_FIX_DIR = _ROOT / ".audit" / "remediation"
_AUDIT_RUNS = _ROOT / ".audit" / "runs"

# ============================================================ 硬性保护上限
MAX_FIX_ROUNDS = 3          # 整 Run 最大自动修复轮数
MAX_ATTEMPTS = 2            # 单个 Fix Unit 最大尝试次数
MAX_FILES = 3               # 单次修改文件数上限
MAX_LINES = 50              # 单次修改增删行数上限

# ============================================================ 禁止修改面
# Auto-Fix **不得**触碰这些路径。命中即 fail-closed。
#   .ai-coding/           Protocol v0.3 —— H3
#   tests/                不得改测试来让测试通过(要求 11)
#   scripts/audit_run.py  不得改审计门让自己消失(要求 11)
#   .git/ uv.lock         Git 内部 / 依赖锁定
FORBIDDEN_PREFIXES: tuple[str, ...] = (
    ".ai-coding/", "tests/", ".git/", ".audit/",
)
FORBIDDEN_EXACT: tuple[str, ...] = (
    "scripts/audit_run.py", "uv.lock", "pyproject.toml", ".gitignore",
)
# 唯一豁免:`.audit/fixtures/` 是**明确标注的 synthetic 目标区**——用于在不触碰
# PyHarness 的前提下验证修复机制(Case A)。它**不是** run state,也**不是**产品代码。
# 该豁免是**精确子路径**,不放宽其余 `.audit/` 面。
ALLOWED_OVERRIDES: tuple[str, ...] = (".audit/fixtures/",)
# 允许修改的根目录(白名单——结构性防线,优先于事后扫描)
ALLOWED_ROOTS: tuple[str, ...] = ("scripts/", "pyharness/", "docs/", "examples/",
                                  ".audit/fixtures/")
ALLOWED_ROOT_FILES: tuple[str, ...] = ("README.md", "LIMITATIONS.md", "CODE-MATRIX.md")

# ============================================================ 高风险判据
# 任一命中 ⇒ 不得自动修复,升级对应 Hx。
GOVERNANCE_PATHS: tuple[str, ...] = (
    "pyharness/governance/", "pyharness/core/approval.py",
    "pyharness/core/tools_guard.py",
)
ARCHITECTURE_PATHS: tuple[str, ...] = (
    "pyharness/events/payload.py", "pyharness/events/vocab.py",
    "pyharness/engine.py",
)
PROTOCOL_PATHS: tuple[str, ...] = (".ai-coding/",)

# ============================================================ Fix Unit 生命周期
UNIT_STATES: tuple[str, ...] = (
    "PROPOSED", "ELIGIBILITY_CHECK", "HUMAN_REVIEW", "AUTHORIZED",
    "IMPLEMENTING", "TARGETED_TEST", "FULL_REGRESSION", "FRESH_REAUDIT",
    "CLOSED", "FAILED", "PAUSED",
)

# ============================================================ 规则表(自描述)
ELIGIBILITY_DISQUALIFIERS: tuple[tuple[str, str, str], ...] = (
    ("D-1", "severity == P0", "H4"),
    ("D-2", "跨模块(>1 顶层包)", "H4"),
    ("D-3", "架构边界(事件载荷/词表/装配链)", "H4"),
    ("D-4", "治理/权限边界(governance·approval·guard)", "H4"),
    ("D-5", "scope expansion(目标 ∉ 批次 scope)", "H4"),
    ("D-6", "Protocol 修改(.ai-coding/)", "H3"),
    ("D-7", "破坏性操作(删除/denylist 命令)", "H1"),
    ("D-8", "多个未裁决架构方案", "H4"),
    ("D-9", "缺定向测试判据", "NONE"),
    ("D-10", "不可回滚(已提交/非本 Run)", "H1"),
    ("D-11", "产品目标/方向变化", "H2"),
    ("D-12", "对外发布(push/release)", "H5"),
    ("D-13", f"文件数 > {MAX_FILES}", "H4"),
    ("D-14", f"改动行数 > {MAX_LINES}", "H4"),
)
ELIGIBILITY_REQUIREMENTS: tuple[str, ...] = (
    "E-1 severity ∈ {P1,P2,P3}",
    "E-2 单模块 且 不触 D-3/D-4 面",
    "E-3 归属已授权批次",
    "E-4 detect: 非空(可复跑判闭合)",
    "E-5 targeted_tests 非空",
    "E-6 全部目标文件可回滚(未提交 ∧ 属本 Run)",
    "E-7 目标文件 ⊆ 批次 scope",
    "E-8 修改面 ⊆ 允许根目录白名单",
)


# ==================================================================== IO
def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _fix_path(run_id: str) -> Path:
    _FIX_DIR.mkdir(parents=True, exist_ok=True)
    return _FIX_DIR / f"{run_id}.yaml"


def load_audit(run_id: str) -> dict:
    p = _AUDIT_RUNS / f"{run_id}.yaml"
    if not p.exists():
        raise SystemExit(f"[remed] audit run not found: {p}")
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def load_fix(run_id: str) -> dict:
    p = _fix_path(run_id)
    if not p.exists():
        return {"RUN_ID": run_id, "ENRICH": {}, "FIX_UNITS": [], "HISTORY": [], "ROUNDS": 0}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def save_fix(run_id: str, doc: dict) -> Path:
    doc["UPDATED_AT"] = _now()
    p = _fix_path(run_id)
    p.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=100),
                 encoding="utf-8")
    return p


def _git(*args: str) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=_ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30)
        return (r.stdout or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _uncommitted() -> set[str]:
    """原始 porcelain(不 strip 首行前缀)。"""
    try:
        r = subprocess.run(["git", "status", "--porcelain"], cwd=_ROOT,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30)
    except Exception:  # noqa: BLE001
        return set()
    return {ln[3:].strip().strip('"') for ln in (r.stdout or "").splitlines() if len(ln) > 3}


def _is_tracked(path: str) -> bool:
    """文件是否已被 git 跟踪。未跟踪 ⇒ 本 Run 新建,回滚 = 删除即可。"""
    try:
        r = subprocess.run(["git", "ls-files", "--error-unmatch", path], cwd=_ROOT,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30)
    except Exception:  # noqa: BLE001 只读探测失败 ⇒ 保守视为未跟踪
        return False
    return r.returncode == 0


# ============================================================ Phase 3: 聚类
def _root_cause_key(f: dict) -> str:
    """finding → 根因键。缺省用 finding 的 root_cause;无则退化为不聚类(单例)。"""
    rc = str(f.get("root_cause") or "").strip()
    return rc if rc else f"__singleton__:{f['id']}"


def _touched_areas(files: list[str]) -> dict[str, bool]:
    joined = " ".join(files)
    return {
        "governance": any(p in joined for p in GOVERNANCE_PATHS),
        "architecture": any(p in joined for p in ARCHITECTURE_PATHS),
        "protocol": any(p in joined for p in PROTOCOL_PATHS),
    }


def build_units(audit: dict, doc: Optional[dict] = None) -> list[dict]:
    """按**共同根因**聚类 findings → Fix Units(Phase 3)。

    只按 root_cause 聚类;**不**按"方便"合并不同根因。跨边界由 eligibility 拒绝,
    不在此处静默合并。

    **修复侧属性经 `doc["ENRICH"]` 提供**(`{finding_id: {files, root_cause,
    targeted_tests, change_plan, ...}}`)——由 AI Developer 产出,**不写入 audit
    manifest**(保持 audit_run.py 的归属不被侵入)。
    """
    enrich = (doc or {}).get("ENRICH") or {}
    groups: dict[str, list[dict]] = {}
    for f in audit.get("FINDINGS") or []:
        if str(f.get("state", "")).upper() == "CLOSED":
            continue
        merged = dict(f)
        merged.update({k: v for k, v in (enrich.get(f["id"]) or {}).items() if v})
        groups.setdefault(_root_cause_key(merged), []).append(merged)

    units: list[dict] = []
    for i, (rc, fs) in enumerate(sorted(groups.items()), 1):
        files: list[str] = []
        for f in fs:
            for p in (f.get("files") or []):
                if p not in files:
                    files.append(p)
        areas = _touched_areas(files)
        units.append({
            "id": f"FIX-{i:03d}",
            "root_cause": rc if not rc.startswith("__singleton__") else "",
            "finding_ids": [f["id"] for f in fs],
            "files": files,
            "change_type": str(fs[0].get("change_type") or "unspecified"),
            "risk_level": max((f.get("sev", "P3") for f in fs), default="P3"),
            "architecture_boundary": areas["architecture"],
            "governance_boundary": areas["governance"],
            "permission_boundary": areas["governance"],
            "protocol_boundary": areas["protocol"],
            "requires_human": False,
            "eligibility": None,
            "eligibility_reason": "",
            "escalation": None,
            "targeted_tests": sorted({t for f in fs for t in (f.get("targeted_tests") or [])}),
            "verification_conditions": [f.get("detect", "") for f in fs if f.get("detect")],
            "change_plan": list(fs[0].get("change_plan") or []),
            "status": "PROPOSED",
            "attempts": 0,
            "changed_files": [],
            "diff_hash": "",
            "evidence": {},
        })
    return units


# ============================================================ Phase 4: 资格
def _count_plan_lines(plan: list[dict]) -> int:
    return sum(len(str(step.get("find", "")).splitlines()) +
               len(str(step.get("replace", "")).splitlines()) for step in plan)


def eligibility(unit: dict, audit: dict) -> dict:
    """Fail-closed 资格判定。默认 HUMAN_REVIEW,全部满足才 AUTO_FIX。"""
    files = list(unit.get("files") or [])
    plan = list(unit.get("change_plan") or [])
    batches = (audit.get("BATCHES") or {})
    findings = {f["id"]: f for f in (audit.get("FINDINGS") or [])}

    d: list[tuple[str, str, Optional[str]]] = []

    sev = str(unit.get("risk_level", "P3"))
    if sev == "P0":
        d.append(("D-1", "severity P0", "H4"))

    tops = {p.split("/")[0] for p in files if "/" in p}
    if len(tops) > 1:
        d.append(("D-2", f"跨顶层包: {sorted(tops)}", "H4"))

    if unit.get("architecture_boundary"):
        d.append(("D-3", "触及架构边界面", "H4"))
    if unit.get("governance_boundary") or unit.get("permission_boundary"):
        d.append(("D-4", "触及治理/权限边界面", "H4"))
    if unit.get("protocol_boundary"):
        d.append(("D-6", "触及 Protocol 面", "H3"))

    # D-5 / E-7:目标 ⊆ 批次 scope
    scopes: set[str] = set()
    for bid in {findings[i].get("batch") for i in unit["finding_ids"] if i in findings}:
        if bid and bid in batches:
            scopes |= set(batches[bid].get("scope") or [])
    if files and not set(files) <= scopes:
        d.append(("D-5", f"越出批次 scope: {sorted(set(files) - scopes)}", "H4"))

    # D-7:破坏性(纯删除)
    for step in plan:
        if str(step.get("replace", "")) == "" and str(step.get("find", "")).strip():
            d.append(("D-7", f"纯删除步骤: {step.get('file')}", "H1"))
            break

    # D-9 / E-5:定向测试
    if not unit.get("targeted_tests"):
        d.append(("D-9", "无定向测试判据", None))

    # E-4 / E-6:detect 非空 + 可回滚
    if not any(str(f.get("detect", "")).strip() for f in
               (findings[i] for i in unit["finding_ids"] if i in findings)):
        d.append(("D-9", "相关 finding 无 detect:", None))
    # D-10 回滚边界(AFL-INT-04 修复)。
    #
    # 本 Fix Unit 的**声明文件集就是本 Run 的 RUN_FILES** —— 对应
    # `audit_run.rollback_guard` 的**第 2 档(显式白名单)**,而非第 3 档(git 派生回落)。
    # 修复前误落第 3 档:已提交且**干净**的文件不在 `_uncommitted()` 里 ⇒ D-10 恒命中
    # ⇒ 任何对已提交文件的修复都被拒(真实自动修复路径不可达)。
    #
    # 仍 fail-closed(**两条都不放宽**):
    #   ① 文件在本 Run 的 COMMITTED_FILES(已提交基线) ⇒ 拒
    #   ② 文件**已有在途未提交改动**(且非本 Run 自己改的) ⇒ 拒 —— 回滚会波及在途工作
    committed = set(audit.get("COMMITTED_FILES") or [])
    dirty = _uncommitted()
    own = set(unit.get("changed_files") or [])       # 本 Run 前几轮已改的文件
    bad = [p for p in files
           if not p.startswith(ALLOWED_OVERRIDES)
           and (p in committed or (p in dirty and p not in own and _is_tracked(p)))]
    if bad:
        d.append(("D-10", f"不可回滚(已提交基线/已有在途改动): {bad}", "H1"))

    # D-13 / D-14:范围上限
    if len(files) > MAX_FILES:
        d.append(("D-13", f"{len(files)} 文件 > {MAX_FILES}", "H4"))
    lines = _count_plan_lines(plan)
    if lines > MAX_LINES:
        d.append(("D-14", f"{lines} 行 > {MAX_LINES}", "H4"))

    # E-8:允许根目录白名单(结构防线)
    for p in files:
        ok = (p.startswith(ALLOWED_ROOTS) if "/" in p else p in ALLOWED_ROOT_FILES)
        if not ok:
            d.append(("E-8", f"不在允许根目录白名单: {p}", "H4"))
            break
    for p in files:
        if p.startswith(ALLOWED_OVERRIDES):
            continue                        # 精确豁免:.audit/fixtures/(synthetic 目标区)
        if p.startswith(FORBIDDEN_PREFIXES) or p in FORBIDDEN_EXACT:
            d.append(("E-8", f"命中禁止面: {p}",
                      "H3" if p.startswith(".ai-coding/") else "H4"))
            break

    if d:
        hx = next((h for _c, _r, h in d if h), None)
        return {"eligibility": "HUMAN_REVIEW", "reason": [f"{c}: {r}" for c, r, _h in d],
                "escalation": hx}
    return {"eligibility": "AUTO_FIX", "reason": ["全部合格条件满足"], "escalation": None}


# ============================================================ Phase 5-7: 执行
def _apply_plan(unit: dict) -> list[str]:
    changed: list[str] = []
    for step in unit.get("change_plan") or []:
        rel = str(step["file"])
        fp = _ROOT / rel
        if not fp.exists():
            continue
        txt = fp.read_text(encoding="utf-8")
        find, repl = str(step.get("find", "")), str(step.get("replace", ""))
        if find and find in txt:
            fp.write_text(txt.replace(find, repl), encoding="utf-8")
            if rel not in changed:
                changed.append(rel)
    return changed


def _diff_hash(files: list[str]) -> str:
    h = hashlib.sha256()
    for f in sorted(files):
        fp = _ROOT / f
        h.update(f.encode())
        if fp.exists():
            h.update(fp.read_bytes())
    return h.hexdigest()[:16]


def _run_cmd(cmd: str, timeout: int = 900) -> dict:
    r = subprocess.run(cmd, shell=True, cwd=_ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    return {"cmd": cmd, "exit": int(r.returncode), "out": out[-2000:], "ts": _now()}


def run_unit(run_id: str, unit: dict, doc: dict) -> dict:
    """执行单个 Fix Unit 的完整生命周期(Phase 5-7)。"""
    audit = load_audit(run_id)
    unit["status"] = "ELIGIBILITY_CHECK"
    el = eligibility(unit, audit)
    unit["eligibility"] = el["eligibility"]
    unit["eligibility_reason"] = el["reason"]
    unit["escalation"] = el["escalation"]

    if el["eligibility"] != "AUTO_FIX":
        unit["status"] = "HUMAN_REVIEW"
        unit["requires_human"] = True
        save_fix(run_id, doc)
        return unit

    if unit["attempts"] >= MAX_ATTEMPTS:
        unit["status"] = "FAILED"
        unit["escalation"] = "H4"
        save_fix(run_id, doc)
        return unit

    before = _diff_hash(unit["files"])
    unit["attempts"] += 1
    unit["status"] = "IMPLEMENTING"
    unit["changed_files"] = _apply_plan(unit)
    save_fix(run_id, doc)

    # 定向测试
    unit["status"] = "TARGETED_TEST"
    tt = [_run_cmd(t) for t in unit.get("targeted_tests") or []]
    unit["evidence"]["targeted_test"] = tt
    if any(t["exit"] != 0 for t in tt):
        unit["status"] = "FAILED"
        save_fix(run_id, doc)
        return unit

    # 全量回归
    unit["status"] = "FULL_REGRESSION"
    reg = _run_cmd(f"{sys.executable} -m pytest "
                   "--ignore=tests/unit/test_desktop_native.py "
                   "--ignore=tests/unit/test_shell_parity.py -p no:cacheprovider --no-cov")
    unit["evidence"]["regression"] = reg
    if reg["exit"] != 0:
        unit["status"] = "FAILED"
        save_fix(run_id, doc)
        return unit

    # FresH RE-AUDIT:重新取证(不复用修复前证据)
    unit["status"] = "FRESH_REAUDIT"
    ra = [_run_cmd(c) for c in unit.get("verification_conditions") or []]
    unit["evidence"]["re_audit"] = ra
    after = _diff_hash(unit["files"])
    unit["diff_hash_prev"], unit["diff_hash"] = before, after
    unit["status"] = "CLOSED" if all(x["exit"] == 0 for x in ra) else "FAILED"
    save_fix(run_id, doc)
    return unit


# ============================================================ Phase 8: 循环防护
def no_progress(doc: dict) -> dict:
    """same-diff / oscillation / no-progress / max-rounds 检测(Phase 8)。

    判据优先级(更具体者优先):OSCILLATION > NO_PROGRESS > MAX_ROUNDS > OK
    —— 前两者更具诊断价值,能指出"为什么会卡住"。
    """
    hist = doc.get("HISTORY") or []
    hashes = [h.get("diff_hash") for h in hist if h.get("diff_hash")]
    rounds = int(doc.get("ROUNDS", 0))
    stalled = len(hashes) >= 2 and hashes[-1] == hashes[-2]
    # 振荡 = 回到**非紧邻**的此前状态(A→B→A),而非原地不动(那是 NO_PROGRESS)
    oscillating = (len(hashes) >= 3 and hashes[-1] in hashes[:-1]
                   and hashes[-1] != hashes[-2])
    if oscillating:
        verdict = "OSCILLATION"
    elif stalled:
        verdict = "NO_PROGRESS"
    elif rounds >= MAX_FIX_ROUNDS:
        verdict = "MAX_ROUNDS"
    else:
        verdict = "OK"
    return {"rounds": rounds, "max_rounds": MAX_FIX_ROUNDS,
            "repeated_diff": stalled or oscillating, "stalled": stalled,
            "oscillating": oscillating, "verdict": verdict}


# ==================================================================== CLI
def _print_rules() -> None:
    print("== Hard limits ==")
    print(f"  MAX_FIX_ROUNDS={MAX_FIX_ROUNDS}  MAX_ATTEMPTS={MAX_ATTEMPTS}  "
          f"MAX_FILES={MAX_FILES}  MAX_LINES={MAX_LINES}")
    print("== Forbidden (Auto-Fix 不得触碰) ==")
    for p in FORBIDDEN_PREFIXES + FORBIDDEN_EXACT:
        print(f"  {p}")
    print("== Allowed roots ==")
    print(f"  {', '.join(ALLOWED_ROOTS)} | {', '.join(ALLOWED_ROOT_FILES)}")
    print("== Eligibility disqualifiers (任一命中 ⇒ 不自动修复) ==")
    for c, r, h in ELIGIBILITY_DISQUALIFIERS:
        print(f"  {c}  {r:<44} -> {h}")
    print("== Eligibility requirements (全部满足才 AUTO_FIX) ==")
    for r in ELIGIBILITY_REQUIREMENTS:
        print(f"  {r}")
    print("== Unit lifecycle ==")
    print(f"  {' -> '.join(UNIT_STATES)}")


def _cmd_rules(_a) -> int:
    _print_rules()
    return 0


def _cmd_classify(a) -> int:
    audit = load_audit(a.run_id)
    doc = load_fix(a.run_id)
    doc["FIX_UNITS"] = build_units(audit, doc)
    save_fix(a.run_id, doc)
    print(f"[remed] {len(doc['FIX_UNITS'])} fix unit(s)")
    for u in doc["FIX_UNITS"]:
        print(f"  {u['id']}  findings={u['finding_ids']}  files={len(u['files'])}  "
              f"risk={u['risk_level']}  status={u['status']}")
    return 0


def _cmd_units(a) -> int:
    doc = load_fix(a.run_id)
    for u in doc.get("FIX_UNITS") or []:
        print(json.dumps({k: u[k] for k in
                          ("id", "finding_ids", "root_cause", "risk_level",
                           "eligibility", "status", "attempts", "escalation")},
                         ensure_ascii=False))
    return 0


def _cmd_eligibility(a) -> int:
    audit = load_audit(a.run_id)
    doc = load_fix(a.run_id)
    if not doc.get("FIX_UNITS"):
        doc["FIX_UNITS"] = build_units(audit, doc)
    for u in doc["FIX_UNITS"]:
        el = eligibility(u, audit)
        u["eligibility"], u["eligibility_reason"] = el["eligibility"], el["reason"]
        u["escalation"] = el["escalation"]
        if el["eligibility"] != "AUTO_FIX":
            u["status"], u["requires_human"] = "HUMAN_REVIEW", True
        print(f"  {u['id']}  {el['eligibility']:13} H={el['escalation']}  {el['reason']}")
    save_fix(a.run_id, doc)
    return 0


def _cmd_run(a) -> int:
    doc = load_fix(a.run_id)
    if not doc.get("FIX_UNITS"):
        doc["FIX_UNITS"] = build_units(load_audit(a.run_id), doc)
    targets = doc["FIX_UNITS"] if a.all else [u for u in doc["FIX_UNITS"] if u["id"] == a.unit]
    if not targets:
        print(f"[remed] unit not found: {a.unit}")
        return 1
    for u in targets:
        if doc.get("ROUNDS", 0) >= MAX_FIX_ROUNDS:
            print(f"[remed] MAX_FIX_ROUNDS reached ({MAX_FIX_ROUNDS}) ⇒ 停止")
            break
        doc["ROUNDS"] = int(doc.get("ROUNDS", 0)) + 1
        r = run_unit(a.run_id, u, doc)
        doc.setdefault("HISTORY", []).append(
            {"unit": u["id"], "status": r["status"], "diff_hash": r.get("diff_hash", "")})
        print(f"[remed] {u['id']} -> {r['status']}"
              + (f"  ESCALATE {r['escalation']}" if r.get("escalation") else ""))
        np_ = no_progress(doc)
        if np_["verdict"] != "OK":
            print(f"[remed] loop protection: {np_['verdict']} ⇒ HUMAN_REVIEW")
            u["status"], u["requires_human"] = "HUMAN_REVIEW", True
            u["escalation"] = "H4"
            break
    save_fix(a.run_id, doc)
    return 0


def _cmd_authorize(a) -> int:
    doc = load_fix(a.run_id)
    for u in doc.get("FIX_UNITS") or []:
        if u["id"] == a.unit:
            u["status"], u["requires_human"] = "AUTHORIZED", False
            u.setdefault("evidence", {})["authorization"] = {"by": a.by, "ts": _now()}
            save_fix(a.run_id, doc)
            print(f"[remed] {a.unit} AUTHORIZED by {a.by} ⇒ 可 resume")
            return 0
    print(f"[remed] unit not found: {a.unit}")
    return 1


def _cmd_status(a) -> int:
    doc = load_fix(a.run_id)
    print(f"RUN      : {a.run_id}")
    print(f"ROUNDS   : {doc.get('ROUNDS', 0)}/{MAX_FIX_ROUNDS}")
    print(f"UNITS    : {len(doc.get('FIX_UNITS') or [])}")
    for u in doc.get("FIX_UNITS") or []:
        print(f"  {u['id']}  {u['status']:14} elig={u['eligibility']} "
              f"attempts={u['attempts']} H={u['escalation']}")
    print(f"LOOP     : {json.dumps(no_progress(doc), ensure_ascii=False)}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser(prog="remediation_run",
                                description="PyHarness Governed Auto-Fix Loop executor")
    p.add_argument("--run-id", required=False, default=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn, needs_unit in (("rules", _cmd_rules, False),
                                 ("classify", _cmd_classify, False),
                                 ("units", _cmd_units, False),
                                 ("eligibility", _cmd_eligibility, False),
                                 ("status", _cmd_status, False)):
        s = sub.add_parser(name)
        s.set_defaults(fn=fn)
    s = sub.add_parser("run")
    s.add_argument("--unit", default=None)
    s.add_argument("--all", action="store_true")
    s.set_defaults(fn=_cmd_run)
    s = sub.add_parser("authorize")
    s.add_argument("--unit", required=True)
    s.add_argument("--by", required=True)
    s.set_defaults(fn=_cmd_authorize)
    a = p.parse_args(argv)
    if not a.run_id and a.fn is not _cmd_rules:
        print("[remed] --run-id required")
        return 2
    return int(a.fn(a))


if __name__ == "__main__":
    raise SystemExit(main())
