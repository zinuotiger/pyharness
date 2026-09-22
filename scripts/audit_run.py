#!/usr/bin/env python
"""PyHarness M1-M5 Autonomous Audit Orchestrator.

一句话职责:把 functional-runtime-audit 的 M1–M5 从"用户逐阶段口头驱动"变成
"一次 Audit Run 内自动连续推进"——阶段判定、Gate 判定、失败恢复与升级判定
全部由**机械谓词**给出,不由自然语言替代。

上位约束(不可绕过):
    `.ai-coding/PROTOCOL.md` v0.3 (FROZEN) = 上位约束。
    - Protocol Gate 决定"是否允许执行";Audit Gate 决定"执行结果是否满足审计要求"。
    - 二者冲突时 **Protocol 优先**;不得通过改 Audit 规则绕过 Protocol Gate。
    - 本模块**不修改** Protocol,也不创建 v0.4;发现的协议缺口只登记为
      Protocol Improvement Candidate(见 `PIC` 常量)。

复用原则(不重复实现检测能力):
    本模块**只做阶段决策与状态持久化**,不内建任何检测器。证据一律通过
    `collect` 调用仓库既有能力(pytest / 既有 probe / git 只读命令),只记录
    命令与真实输出。既有可复用面见 `REUSABLE_DETECTORS`。

安全边界:
    - rollback 仅限**本次 Run 创建、尚未 commit** 的修改(见 `rollback_guard`)。
    - 历史改写 / force push / `reset --hard` 到历史 commit 一律禁止(ESCALATE H1)。
    - scope 不允许自动扩大;越界即 H4。

用法:
    python scripts/audit_run.py rules
    python scripts/audit_run.py init --objective "..." [--run-id ID]
    python scripts/audit_run.py status          [--run-id ID]
    python scripts/audit_run.py decide          [--run-id ID]
    python scripts/audit_run.py advance         [--run-id ID] [--force]
    python scripts/audit_run.py recover         [--run-id ID]
    python scripts/audit_run.py collect --kind regression --cmd "pytest ..."
    python scripts/audit_run.py escalate --h H4 --what "..."
    python scripts/audit_run.py close           [--run-id ID]
    python scripts/audit_run.py check-rollback --files a.py b.py

本文件不参与 `pyharness/` 装配,不被 `pyharness` import,不在不变量扫描边界内
(见 `tests/invariants/test_inv_core.py` 模块 docstring 的类别式边界)。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

_ROOT = Path(__file__).resolve().parents[1]
_RUNS_DIR = _ROOT / ".audit" / "runs"

PROTOCOL_REF = ".ai-coding/PROTOCOL.md@v0.3 (FROZEN)"
AUDIT_METHOD_REF = "functional-runtime-audit@v2.0"

# ===================================================================== 规则表
# 下面五张表是本编排器的**全部判据来源**。它们只是数据的代码化表达——判据本身
# 不在此定义,而是取自:Protocol v0.3 §7 Quality Gates / §9 Convergence,以及
# functional-runtime-audit v2.0 的 C1–C8 与 Stop 条件。此处仅做机械谓词化。

STAGES: tuple[str, ...] = ("M1", "M2", "M3", "M4", "M5")

STAGE_NAME: dict[str, str] = {
    "M1": "Audit", "M2": "Planning", "M3": "Implementation",
    "M4": "Verification", "M5": "Closure",
}

# 每阶段的 Gate:谓词名 -> 说明。谓词实现在 _GATES 表。
STAGE_GATES: dict[str, tuple[str, ...]] = {
    "M1": ("has_findings", "all_findings_detected"),
    "M2": ("all_findings_routed", "has_batches", "batches_scoped"),
    "M3": ("batch_files_in_scope", "no_unscoped_edits"),
    "M4": ("regression_ran", "regression_green", "release_gate_pass"),
    "M5": ("targets_closed", "c_gate_meets_bar", "no_reopening"),
}

# C1–C8(skill 定义)。mechanical=False 者必须由人给出 attestation,不得自动置真。
C_CHECKS: dict[str, tuple[str, bool, str]] = {
    "C1": ("Capability Exists", True, "每个 target finding 有实现/变更记录"),
    "C2": ("Runtime Reachable", True, "有 L3 证据,或该 finding 为文档类(不要求运行路径)"),
    "C3": ("Behavior Verified", True, "存在至少一次真实执行输出"),
    "C4": ("Evidence Generated", True, "detect 命令与实测输出均已记录"),
    "C5": ("Boundary Controlled", False, "须人工 attest:每条边界给出显式残余"),
    "C6": ("Regression Passed", True, "四维回归有实测且通过"),
    "C7": ("Public Claim Accurate", True, "活跃副本扫描无未限定 overclaim"),
    "C8": ("State Consistent", True, "状态文件间无冲突声明;checkpoint 无漂移"),
}

# Stop / Convergence(全真才 AUDIT COMPLETE)
STOP_CHECKS: dict[str, str] = {
    "S1": "全部 mandatory finding = CLOSED",
    "S2": "全部 required gate = PASS",
    "S3": "回归 PASS(CORE-02 所判档位)",
    "S4": "证据完整(每 finding 有 detect + 实测输出)",
    "S5": "公开声明准确(unqualified overclaim 计数 = 0)",
    "S6": "checkpoint 状态一致(无漂移)",
    "S7": "blocker 计数 = 0",
    "S8": "无协议要求的后续阶段",
    "S9": "剩余工作全部为 optional / deferred",
}

# Human Escalation(H1–H5)。key 为触发标签,value 为说明。
ESCALATION: dict[str, str] = {
    "H1": "不可逆/高风险 Git:force push · history rewrite · 已提交历史的破坏性回退 · "
          "大规模不可逆删除 · 破坏性数据迁移",
    "H2": "项目目标 / 产品方向发生变化",
    "H3": "修改、升级或废弃 Protocol v0.3",
    "H4": "规则无法裁决多个有长期架构影响的方案 · 需改核心架构边界 · 需扩大既定 scope",
    "H5": "push / 正式 release / 对外发布",
}

# Protocol Improvement Candidate —— 只登记,不得自动写入 Protocol(Protocol §13)。
PIC: tuple[dict[str, str], ...] = (
    {"id": "PIC-1", "gap": "Protocol v0.3 未定义「审计驱动的多批次闭环」与 GATE-01~04 的交互"},
    {"id": "PIC-2", "gap": "§2 豁免条款未覆盖「审计声明同步」这类批量文档修正"},
    {"id": "PIC-3", "gap": "协议未定义 [auto]/[confirm] 式预授权批次语义"},
)

# 既有可复用检测面(orchestrator 只调用,不重写)。
#
# `{PY}` = **当前项目解释器占位符**(VAL-B1):执行期由 `_resolve_cmd` 替换为
# `sys.executable`。不得依赖 PATH 中的 bare `python` —— Windows 上它可能解析到
# Windows Store 存根,导致命令**不执行却可能返回 0**(静默假通过)。
REUSABLE_DETECTORS: tuple[dict[str, str], ...] = (
    {"kind": "regression", "cmd": "{PY} -m pytest --ignore=tests/unit/test_desktop_native.py "
                                  "--ignore=tests/unit/test_shell_parity.py"},
    {"kind": "runtime-probe", "cmd": "{PY} scripts/e2e_engine.py"},
    {"kind": "tool-probe", "cmd": "{PY} scripts/probe_engine_tools.py"},
    {"kind": "mcp-probe", "cmd": "{PY} scripts/probe_mcp_stdio.py"},
    {"kind": "stream-probe", "cmd": "{PY} scripts/probe_streaming_real.py"},
    {"kind": "drift", "cmd": "git diff --name-only"},
    {"kind": "scope", "cmd": "git status --porcelain"},
)

# RECOVER 的可达条件(谓词 -> 可重采它的检测器 kind)。
#
# 语义(严格限定,不做通用"自动修复"):某阶段 Gate 失败**可归因于证据缺失/陈旧**
# (而非真实失败),且该证据由 `REUSABLE_DETECTORS` 中的白名单命令产出 ⇒ 自动重采
# 即可解除,**不涉及人工决策、不扩大 scope**。
# 未列入此表的谓词(如 regression_green / targets_closed)代表**真实状态**,
# 重采无意义 ⇒ 不走 RECOVER,走 RETRY/ESCALATE。
EVIDENCE_REFRESHABLE: dict[str, str] = {
    "regression_ran": "regression",
}

# 证据完整性要求(VAL-B1):kind -> 产出必须匹配的形态。
#
# `exit == 0` 不足以证明"命令真实执行并给出预期结论":空跑/未执行同样可能返回 0。
# 声明在此表中的 kind,其证据必须**同时**满足 exit=0 且产出匹配该形态,才算可信。
EVIDENCE_REQUIRE: dict[str, "re.Pattern[str]"] = {
    "regression": re.compile(r"\d+\s+(passed|failed|error)", re.IGNORECASE),
}

# 派生依赖:证据缺失时,这些谓词**必然**随之失败 ⇒ 不构成独立的"真实失败",
# 不得因此阻断 RECOVER。例如 regression_ran=False(无证据)时 regression_green
# 必然也为 False —— 它们是同一原因的两个投影,不是两个问题。
DERIVED_FROM: dict[str, str] = {
    "regression_green": "regression_ran",
}


def _recoverable_failures(fail: list[str]) -> Optional[list[str]]:
    """全为"证据缺失型"失败(含其派生投影)时返回可重采的谓词;否则 None。"""
    refreshed = [n for n in fail if n in EVIDENCE_REFRESHABLE]
    if not refreshed:
        return None
    rest = [n for n in fail if n not in EVIDENCE_REFRESHABLE]
    if all(DERIVED_FROM.get(n) in refreshed for n in rest):
        return refreshed
    return None

# 可作为命令首词的**可执行体白名单**。精确匹配,非前缀 —— 见 `is_allowlisted`。
_ALLOWED_EXECUTABLES: tuple[str, ...] = (
    "{PY}", "python", "python3", "pytest", "git", "grep", "sha256sum",
)
# git 只读子命令白名单。旧的前缀写法("git status")会放过 `git statuses` 这类
# 同前缀非子命令,故改为对 argv[1] 精确判定。
_ALLOWED_GIT_SUBCOMMANDS: tuple[str, ...] = ("diff", "status", "rev-parse", "log")

# 单命令约束:不得出现 shell 控制操作符。一条 detect 应当是**一条**可复现命令,
# 而不是串联/管道/替换。
_SHELL_CONTROL: tuple[str, ...] = (";", "&&", "||", "|", "`", "$(", "\n", "\r")

# 结果注解记号(VAL-B3)。`grep X -> 0` 把**观测结果**写进了命令字段 ——
# 这是 detect 字段最常见的散文形态:以白名单词开头,末尾却标注结论。
# 结果属于 EVIDENCE 的 `out`,不属于命令。
_RESULT_ANNOTATION: tuple[str, ...] = ("->", "=>", "→")

# 明确禁止(命中即 H1,拒绝执行)
_CMD_DENY: tuple[str, ...] = (
    "push", "reset --hard", "rebase", "filter-branch", "commit", "clean -",
    "checkout --", "restore", "rm -rf", "branch -D", "stash drop", "--force", "-f ",
)

# 项目解释器占位符(VAL-B1):只有**已声明的检测器**能使用它。白名单校验发生在
# 占位符形式上(`{PY} …`),执行前才替换为 `sys.executable`。这样既保证解释器正确,
# 又**不**放宽 fail-closed —— 任意 `C:\\path\\python.exe …` 仍不在白名单内。
_PY_TOKEN = "{PY}"


def _resolve_cmd(cmd: str) -> str:
    """把 `{PY}` 替换为当前运行环境的真实解释器路径。"""
    if _PY_TOKEN not in cmd:
        return cmd
    exe = sys.executable or "python"
    if " " in exe:                      # 含空格的路径须引号包裹
        exe = f'"{exe}"'
    return cmd.replace(_PY_TOKEN, exe)


# ===================================================================== 谓词层
def _findings(m: dict) -> list[dict]:
    return list(m.get("FINDINGS") or [])


def _mandatory(m: dict) -> list[dict]:
    return [f for f in _findings(m) if str(f.get("sev", "")).upper() in ("P0", "P1")]


def _batches(m: dict) -> dict:
    return dict(m.get("BATCHES") or {})


def _evidence(m: dict, kind: Optional[str] = None) -> list[dict]:
    ev = list(m.get("EVIDENCE") or [])
    return [e for e in ev if kind is None or e.get("kind") == kind] if kind else ev


def _regression(m: dict) -> list[dict]:
    return _evidence(m, "regression")


def _evidence_executed(e: dict) -> bool:
    """该条证据是否**真实执行过**(VAL-B1)。

    - 已声明形态的 kind(如 regression)⇒ 产出必须匹配该形态;
    - 未声明形态的 kind ⇒ 至少满足「exit == 0 或 有产出」之一
      (二者皆无 = 命令根本没跑起来)。

    **不看 exit 是否为 0 来判定"是否执行"** —— 回归跑失败(exit!=0)仍是"执行过";
    空产出且非零退出才是"没执行"。
    """
    pat = EVIDENCE_REQUIRE.get(str(e.get("kind", "")))
    out = e.get("out") or ""
    if pat is not None:
        return bool(pat.search(out))
    return int(e.get("exit", 1)) == 0 or bool(out.strip())


def _evidence_trusted(e: dict) -> bool:
    """该条证据是否**可信成功**(VAL-B1)= 真实执行过 **且** exit == 0。

    空产出 + exit=0(Store 存根式空跑)⇒ **不可信**(fail-closed)。
    """
    return _evidence_executed(e) and int(e.get("exit", 1)) == 0


def _gates(m: dict) -> dict:
    return dict(m.get("GATES") or {})


# ===================================================================== 证据判据(VAL-B2)
# 本块解决同一类缺陷的三处投影:**判据只看"字段有没有值",不看"证据是不是真的"**。
# 三处都是"非空即通过",于是散文与占位符可以冒充证据:
#
#   all_findings_detected  'frozen-audit detect for F-01'  → 非空 ⇒ 过(执行时 exit!=0)
#   evidence_complete      'closed via B1 (ba0aab8)'       → 非空 ⇒ 过(指不到任何记录)
#   c_gate_meets_bar       C_VOTES[cid] = 'PASS'           → 非空 ⇒ 过(全仓无代码写它)
#
# 修法统一为:**证据必须是一个能解析到真实执行记录的指针**。判据全部取自既有能力,
# 不新建检测器(见模块 docstring 的复用原则)。

_LEGACY_KEY = "legacy"
_LEGACY_REASON_KEY = "legacy_reason"


def _is_legacy(f: dict) -> bool:
    return bool(f.get(_LEGACY_KEY))


def _legacy_reason(f: dict) -> str:
    return str(f.get(_LEGACY_REASON_KEY, "")).strip()


def legacy_exemptions(m: dict) -> list[dict]:
    """生效中的 legacy 豁免。

    豁免是**显式承认**(旧 Run 无据闭合),不是静默放行 —— 调用方必须把它打印
    出来。无 `legacy_reason` 的标记不构成豁免(见各判据)。
    """
    return [f for f in _findings(m) if _is_legacy(f)]


def _detect_ok(f: dict) -> bool:
    """`detect` 是否为**白名单内可执行**命令(VAL-B2)。

    仅"字段非空"不足以证明可复现:占位句子同样非空。此处做静态策略判定
    (白名单 ∩ 非禁止),**不在此处执行** —— 判据评估发生在 `status`/`decide`
    这类只读路径上,不得产生副作用。
    """
    d = str(f.get("detect", "")).strip()
    return bool(d) and is_denied_command(d) is None and is_allowlisted(d)


def _evidence_pointers(container: object) -> Optional[list[dict]]:
    """把 `evidence` / `C_VOTES[cid]` 的值解析为**证据指针列表**。

    合法形态:非空 list,且元素全为 dict。其余一律返回 None(判为不满足)。
    """
    if not isinstance(container, list) or not container:
        return None
    if not all(isinstance(x, dict) for x in container):
        return None
    return list(container)


def _pointer_resolves(m: dict, ptr: dict) -> bool:
    """该指针是否解析到一条**可信** EVIDENCE 记录(VAL-B2)。

    匹配键 = (kind, 解析后的 cmd)。指针里写的可能是 `{PY} …`,而 EVIDENCE
    记录存的是执行期解析后的命令,故比对前先 `_resolve_cmd`。
    """
    kind = str(ptr.get("kind", "")).strip()
    cmd = str(ptr.get("cmd", "")).strip()
    if not kind or not cmd:
        return False
    want = _resolve_cmd(cmd)
    return any(e.get("kind") == kind and e.get("cmd") == want and _evidence_trusted(e)
               for e in _evidence(m))


def _detect_ran(m: dict, f: dict) -> bool:
    """该 finding 的 `detect` 是否**已真实执行并记录**(VAL-B3)。

    这是 anti-prose 的**语义兜底**。`is_allowlisted` 是结构判定,原则上可以被新的
    散文形态绕过(换一种注解写法即可);而"这条命令到底跑没跑过"不能被绕过 ——
    它只认 EVIDENCE 里与该 finding 的 detect **逐字对应**的可信记录。

    这也是 skill 的原义:M4→M5 的前提是 "every target finding's `detect:` command
    has been **re-run**"。
    """
    d = str(f.get("detect", "")).strip()
    if not d:
        return False
    want = _resolve_cmd(d)
    return any(e.get("cmd") == want and _evidence_trusted(e) for e in _evidence(m))


# ---- M1
def has_findings(m: dict) -> bool:
    return len(_findings(m)) >= 1


def all_findings_detected(m: dict) -> bool:
    """每条 finding 必须带**可复现**的 `detect:` 命令 —— 否则闭环不可判定。

    VAL-B2:原先只判"非空字符串",于是 'frozen-audit detect for F-01' 这类占位
    句子同样通过,而 skill 的 M1→M2 前提是 "**reproducible** detect: command"。
    现要求 detect 是 argv 级合规的单条命令(见 `is_allowlisted`)且不命中
    `_CMD_DENY`。

    legacy 豁免须带 `legacy_reason`;无名豁免不认。
    """
    for f in _findings(m):
        if _is_legacy(f):
            if not _legacy_reason(f):
                return False
            continue
        if not _detect_ok(f):
            return False
    return True


# ---- M2
def all_findings_routed(m: dict) -> bool:
    """每条 finding 必须已分类(A 修 / B 接受 / C 延期)。"""
    return all(str(f.get("route", "")).upper() in ("A", "B", "C") for f in _findings(m))


def has_batches(m: dict) -> bool:
    return len(_batches(m)) >= 1


def batches_scoped(m: dict) -> bool:
    """每个批次必须有显式文件范围与可验证判据。"""
    for b in _batches(m).values():
        if not b.get("scope") or not b.get("criterion"):
            return False
    return True


# ---- M3
def batch_files_in_scope(m: dict) -> bool:
    """已落地批次的改动文件必须 ⊆ 其声明范围。"""
    for b in _batches(m).values():
        if b.get("state") != "DONE":
            continue
        scope = set(b.get("scope") or [])
        changed = set(b.get("changed_files") or [])
        if not changed <= scope:
            return False
    return True


def no_unscoped_edits(m: dict) -> bool:
    return bool(m.get("SCOPE_OK", True)) and not m.get("UNSCOPED_EDITS")


# ---- M4
def regression_ran(m: dict) -> bool:
    """S3a:回归**确实执行过**(存在产出形态匹配的证据)。

    VAL-B1:`exit == 0` 或"存在记录"都不足以证明命令真的跑了 —— 静默空跑同样
    可能返回 0 且留下一条记录。故只认 `_evidence_executed`(不看 exit)。
    """
    return any(_evidence_executed(e) for e in _regression(m))


def regression_green(m: dict) -> bool:
    """S3b:回归全绿 —— 无失败记录,且至少一条**可信成功**(真实执行 + exit=0)。

    VAL-B1:若全部 `exit == 0` 但**无一**产出预期证据(空跑)⇒ 亦为 False(fail-closed)。
    """
    regs = _regression(m)
    if not regs:
        return False
    if any(int(e.get("exit", 1)) != 0 for e in regs):
        return False
    return any(_evidence_trusted(e) for e in regs)


def release_gate_pass(m: dict) -> bool:
    return str(_gates(m).get("RELEASE", "")).upper() == "PASS"


# ---- M5
# finding 分类(决定是否阻塞当前 Run 的收敛)。
#   mandatory   —— 未关闭即阻塞收敛(S1)
#   optional    —— 可延期,不阻塞当前 Run
#   deferred    —— 明确延期,不阻塞当前 Run
#   observation —— 观察项,不阻塞当前 Run
# 缺省 = mandatory(fail-closed:未分类的 finding 不得被静默放行)。
CLASSES: tuple[str, ...] = ("mandatory", "optional", "deferred", "observation")


def _class_of(f: dict) -> str:
    c = str(f.get("class") or "mandatory").strip().lower()
    return c if c in CLASSES else "mandatory"


def _targets(m: dict) -> list[dict]:
    """**会阻塞收敛**的 finding = 已纳入批次 且 分类为 mandatory。

    非 mandatory 者(optional / deferred / observation)即使带批次也**不阻塞**
    当前 Run —— 它们由 S9 归类为剩余可延期工作。
    """
    return [f for f in _findings(m)
            if f.get("batch") and _class_of(f) == "mandatory"]


def _nonblocking(m: dict) -> list[dict]:
    """带批次但非 mandatory ⇒ 可延期,不阻塞收敛。"""
    return [f for f in _findings(m)
            if f.get("batch") and _class_of(f) != "mandatory"]


def targets_closed(m: dict) -> bool:
    t = _targets(m)
    return bool(t) and all(str(f.get("state", "")).upper() == "CLOSED" for f in t)


def c_gate_meets_bar(m: dict) -> bool:
    """C1–C8:机械项必须给出**证据指针**;非机械项(C5)必须有人工 attestation。

    VAL-B2:机械项原先只读一个 'PASS' 字符串,而全仓无任何代码写 `C_VOTES` ——
    等于手填即通过。skill 要求的是 "measured grounds for every one",故现要求每条
    机械项的值是**证据指针列表**且全部解析成功(与 finding.evidence 同构),
    使"有无实测依据"成为可判定的。

    C5 是唯一的非机械项:它承载的是"每条边界给出显式残余"这类不可机械判定的
    人工判断,故仍走 attestation —— 但要求是非空字符串,不接受真值判定。
    """
    att = dict(m.get("C_ATTEST") or {})
    votes = dict(m.get("C_VOTES") or {})
    for cid, (_name, mechanical, _desc) in C_CHECKS.items():
        if mechanical:
            ptrs = _evidence_pointers(votes.get(cid))
            if ptrs is None or not all(_pointer_resolves(m, p) for p in ptrs):
                return False
        else:
            if not str(att.get(cid) or "").strip():
                return False
    return True


def no_reopening(m: dict) -> bool:
    return int(m.get("REOPENINGS", 0) or 0) == 0


def evidence_complete(m: dict) -> bool:
    """S4:CLOSED 的 finding 必须**指向真实证据**;mandatory 必须具备可复现 detect。

    VAL-B2:原先只做真值判定,于是散文(如 'closed via B1 (ba0aab8)')即可满足
    "证据完整"。现要求 `evidence` 为**证据指针列表**,每条解析到一条可信
    EVIDENCE 记录(见 `_pointer_resolves`)。

    VAL-B3:再加一条**语义**条件 —— 该 finding 的 `detect` 本身必须已真实执行并
    留证(`_detect_ran`)。只看 `evidence` 指针是不够的:指针可以被指向另一条
    命令的记录,而 `detect` 字段仍是一句散文。

    与分类模型一致:非 mandatory 的未闭合项**不**因其缺证据而使 S4 失败。
    legacy 豁免须带 `legacy_reason`。
    """
    for f in _findings(m):
        if _is_legacy(f):
            if not _legacy_reason(f):
                return False
            continue
        if str(f.get("state", "")).upper() == "CLOSED":
            ptrs = _evidence_pointers(f.get("evidence"))
            if ptrs is None or not all(_pointer_resolves(m, p) for p in ptrs):
                return False
            if not _detect_ran(m, f):
                return False
        if _class_of(f) == "mandatory" and not _detect_ok(f):
            return False
    return True


_GATES: dict[str, Callable[[dict], bool]] = {
    "has_findings": has_findings,
    "all_findings_detected": all_findings_detected,
    "all_findings_routed": all_findings_routed,
    "has_batches": has_batches,
    "batches_scoped": batches_scoped,
    "batch_files_in_scope": batch_files_in_scope,
    "no_unscoped_edits": no_unscoped_edits,
    "regression_ran": regression_ran,
    "regression_green": regression_green,
    "release_gate_pass": release_gate_pass,
    "targets_closed": targets_closed,
    "c_gate_meets_bar": c_gate_meets_bar,
    "no_reopening": no_reopening,
}


# ---- Stop / Convergence
def stop_predicates(m: dict) -> dict[str, bool]:
    t = _targets(m)
    gates = {k: v for k, v in _gates(m).items() if v is not None}
    return {
        "S1": bool(t) and all(str(f.get("state", "")).upper() == "CLOSED" for f in t),
        "S2": bool(gates) and all(str(v).upper() == "PASS" for v in gates.values()),
        "S3": regression_green(m),
        "S4": evidence_complete(m),
        "S5": int(m.get("UNQUALIFIED_OVERCLAIMS", 0) or 0) == 0,
        "S6": not bool(m.get("DRIFT")),
        "S7": len(m.get("BLOCKERS") or []) == 0,
        "S8": m.get("CURRENT_STAGE") == "M5" and _stage_gate(m, "M5"),
        "S9": all(_class_of(f) != "mandatory" for f in _findings(m)
                  if str(f.get("state", "")).upper() != "CLOSED"),
    }


def _stage_gate(m: dict, stage: str) -> bool:
    return all(_GATES[name](m) for name in STAGE_GATES.get(stage, ()))


# =========================================================== Stage Decision Engine
def decide(m: dict) -> dict:
    """统一阶段决策。返回 {verdict, stage, reasons, escalate_h, next_action}。

    verdict ∈ CONTINUE | RETRY | RECOVER | ESCALATE | STOP
    不得由自然语言判断替代。
    """
    stage = str(m.get("CURRENT_STAGE", "M1"))
    retries = int(m.get("RETRIES", 0) or 0)
    limit = int(m.get("RETRY_LIMIT", 2) or 2)

    # 1) blocker 优先
    blockers = m.get("BLOCKERS") or []
    if blockers:
        h = "H4" if any(b.get("scope_expansion") for b in blockers) else None
        return _v("ESCALATE", stage, [f"blocker: {b.get('what', '?')}" for b in blockers],
                  h, "resolve blocker")

    # 2) STOP:仅在 M5 且全部 Stop 谓词为真
    if stage == "M5":
        sp = stop_predicates(m)
        if all(sp.values()):
            return _v("STOP", stage, ["S1–S9 全真 ⇒ AUDIT COMPLETE"], None, "none")
        # M5 上 retry 无意义:未闭合的 target 要么需新授权批次(scope 决策),
        # 要么属 deferred/optional。二者都不是"重试当前阶段"能解决的。
        open_t = [f["id"] for f in _targets(m)
                  if str(f.get("state", "")).upper() != "CLOSED"]
        fail = [k for k, ok in sp.items() if not ok]
        reasons = [f"Stop 谓词未满足: {fail}"]
        if open_t:
            reasons.append(f"未闭合 target: {open_t} ⇒ 闭合需新授权批次(scope 决策)")
        deferrable = [f["id"] for f in _targets(m)
                      if str(f.get("state", "")).upper() != "CLOSED"
                      and str(f.get("class", "mandatory")) in ("optional", "deferred")]
        if deferrable:
            reasons.append(f"可延期项: {deferrable} ⇒ 转 Deferred/Optional")
        h = "H4" if open_t else None
        return _v("ESCALATE" if h else "CONTINUE", stage, reasons, h,
                  "human decision required" if h else "mark deferred, then close")

    # 3) 当前阶段 Gate
    if _stage_gate(m, stage):
        nxt = _next_stage(stage)
        if nxt is None:
            fail = [k for k, ok in stop_predicates(m).items() if not ok]
            return _v("ESCALATE", stage, [f"M5 已过 Gate 但 Stop 谓词未满足: {fail}"],
                      None, "inspect stop predicates")
        return _v("CONTINUE", stage, [f"{stage} Gate PASS ⇒ 进入 {nxt}"], None,
                  f"advance to {nxt}")

    # 4) 失败:先判**是否可由本 Run 自动恢复**(RECOVER),再判 RETRY / ESCALATE
    fail = [n for n in STAGE_GATES.get(stage, ()) if not _GATES[n](m)]
    refreshed = _recoverable_failures(fail)
    if refreshed:
        kinds = sorted({EVIDENCE_REFRESHABLE[n] for n in refreshed})
        return _v("RECOVER", stage,
                  [f"{stage} Gate 缺证据(非真实失败): {fail} ⇒ 自动重采 {kinds} 后重验"],
                  None, f"re-collect {kinds} then re-verify {stage}")
    if retries < limit:
        return _v("RETRY", stage, [f"{stage} Gate 未过: {fail} (retry {retries + 1}/{limit})"],
                  None, f"retry {stage}")
    return _v("ESCALATE", stage, [f"{stage} Gate 连续 {retries} 次未过: {fail}"],
              "H4", "human decision required")


def _v(verdict: str, stage: str, reasons: list[str], h: Optional[str],
       nxt: str) -> dict:
    return {"verdict": verdict, "stage": stage, "reasons": reasons,
            "escalate_h": h, "next_action": nxt}


def _next_stage(stage: str) -> Optional[str]:
    i = STAGES.index(stage) if stage in STAGES else -1
    return STAGES[i + 1] if 0 <= i < len(STAGES) - 1 else None


# =========================================================== Escalation / Safety
def is_denied_command(cmd: str) -> Optional[str]:
    """返回命中的禁止片段;None = 允许。"""
    low = cmd.lower()
    for d in _CMD_DENY:
        if d in low:
            return d
    return None


def _cmd_argv(cmd: str) -> Optional[list[str]]:
    """把命令切为 argv;引号不配对等不可解析时返回 None。"""
    try:
        argv = shlex.split(cmd)
    except ValueError:
        return None
    return argv or None


def is_allowlisted(cmd: str) -> bool:
    """是否为**单条、首词在白名单内**的可执行命令(VAL-B3)。

    旧实现是**前缀匹配**,于是 `'grep 75 payload -> 0'` 这类散文照样通过 ——
    它以白名单词开头,末尾却标注了观测结果。实测中这类假阳性占通过数的 9/11。

    现改为 argv 级判定,五条全部满足才放行:

      1. 可被 `shlex` 解析(引号配对);
      2. 不含 shell 控制操作符 ⇒ 是**一条**命令,不是串联/管道/替换;
      3. 不含结果注解记号 ⇒ 命令字段里不得夹带结论;
      4. `argv[0]` **精确**等于可执行体白名单(非前缀:`pytest_foo` 不放行);
      5. `argv[0] == git` 时 `argv[1]` 必须在只读子命令白名单内
         (非前缀:`git statuses` 不放行)。

    这是**结构**判定,不执行任何东西;是否真的执行过由 `_detect_ran` 语义兜底。
    """
    s = cmd.strip()
    if not s:
        return False
    if any(t in s for t in _SHELL_CONTROL):
        return False
    if any(t in s for t in _RESULT_ANNOTATION):
        return False
    argv = _cmd_argv(s)
    if argv is None:
        return False
    if argv[0] not in _ALLOWED_EXECUTABLES:
        return False
    if argv[0] == "git":
        return len(argv) >= 2 and argv[1] in _ALLOWED_GIT_SUBCOMMANDS
    return True


def _uncommitted_files() -> set[str]:
    """git 派生的当前未提交文件集(已跟踪改动 + 新增未跟踪)。只读。

    注意:必须用 **原始** stdout。porcelain 的 3 字符状态前缀含前导空格
    (` M path`),对整体做 strip 会把首行前缀吃掉一格,导致路径解析错位。
    """
    try:
        r = subprocess.run(["git", "status", "--porcelain"], cwd=_ROOT,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30)
    except Exception:  # noqa: BLE001 只读探测失败 ⇒ 返回空集(fail-closed)
        return set()
    res: set[str] = set()
    for line in (r.stdout or "").splitlines():
        if len(line) > 3:
            res.add(line[3:].strip().strip('"'))
    return res


def rollback_guard(files: list[str], m: dict) -> dict:
    """rollback 边界:仅允许回退**本 Run 创建、尚未 commit** 的改动。

    判定顺序(fail-closed):
      1. 出现在 COMMITTED_FILES ⇒ 已提交 ⇒ 禁止(H1)。
      2. RUN_FILES 非空 ⇒ 按显式白名单;不在表内即禁止。
      3. RUN_FILES 为空 ⇒ 回落到 git:文件必须**当前处于未提交状态**才允许。
    不执行任何操作,只给判定。
    """
    committed = set(m.get("COMMITTED_FILES") or [])
    run_files = set(m.get("RUN_FILES") or [])
    uncommitted = _uncommitted_files() if not run_files else set()
    blocked = []
    for f in files:
        if f in committed:
            blocked.append(f)
            continue
        if run_files:
            if f not in run_files:
                blocked.append(f)
        elif f not in uncommitted:
            blocked.append(f)
    return {"allowed": not blocked, "blocked": blocked,
            "mode": ("explicit RUN_FILES" if run_files else "git-derived uncommitted"),
            "reason": ("全部属于本 Run 未提交改动 ⇒ 可安全回退" if not blocked else
                       "含已提交或非本 Run 文件 ⇒ 禁止自动回退,触发 H1")}


# =========================================================== Manifest IO
def runs_dir() -> Path:
    _RUNS_DIR.mkdir(parents=True, exist_ok=True)
    return _RUNS_DIR


def manifest_path(run_id: str) -> Path:
    return runs_dir() / f"{run_id}.yaml"


def load(run_id: Optional[str]) -> tuple[str, dict]:
    rid = run_id or _latest_run_id()
    p = manifest_path(rid)
    if not p.exists():
        raise SystemExit(f"[audit] run not found: {rid} ({p})")
    m = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return rid, m


def save(run_id: str, m: dict) -> Path:
    m["UPDATED_AT"] = _now()
    p = manifest_path(run_id)
    p.write_text(yaml.safe_dump(m, allow_unicode=True, sort_keys=False, width=100),
                 encoding="utf-8")
    return p


def _latest_run_id() -> str:
    files = sorted(runs_dir().glob("*.yaml"))
    if not files:
        raise SystemExit("[audit] no runs found; run `init` first")
    return files[-1].stem


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _git(*args: str) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=_ROOT, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=30)
        return (r.stdout or "").strip()
    except Exception:  # noqa: BLE001 只读探测失败不阻断
        return ""


def new_manifest(run_id: str, objective: str) -> dict:
    head = _git("rev-parse", "HEAD")
    return {
        "AUDIT_RUN_ID": run_id,
        "OBJECTIVE": objective,
        "PROTOCOL_REF": PROTOCOL_REF,
        "AUDIT_METHOD_REF": AUDIT_METHOD_REF,
        "AUTHORIZATION": "AUTO_WITH_ESCALATION",
        "START_HEAD": head,
        "CURRENT_HEAD": head,
        "CURRENT_STAGE": "M1",
        "STAGE_STATUS": {s: "PENDING" for s in STAGES},
        "SCOPE": {"files": [], "batches": [], "no_auto_expansion": True},
        "FINDINGS": [],
        "BATCHES": {},
        "GATES": {},
        "EVIDENCE": [],
        "RETRIES": 0,
        "RETRY_LIMIT": 2,
        "ESCALATIONS": [],
        "BLOCKERS": [],
        "OBSERVATIONS": [],
        "REOPENINGS": 0,
        "DRIFT": False,
        "SCOPE_OK": True,
        "UNSCOPED_EDITS": [],
        "UNQUALIFIED_OVERCLAIMS": 0,
        "REMAINING_MANDATORY": [],
        "C_VOTES": {},
        "C_ATTEST": {},
        "NEXT_ACTION": "M1 discovery",
        "STOP_DECISION": None,
        "CREATED_AT": _now(),
    }


# ===================================================================== CLI
def _print_rules() -> None:
    print("== Stages ==")
    for s in STAGES:
        print(f"  {s} {STAGE_NAME[s]:<16} gate = {' AND '.join(STAGE_GATES[s])}")
    print("\n== C1-C8 ==")
    for cid, (name, mech, desc) in C_CHECKS.items():
        print(f"  {cid} {name:<22} {'mech ' if mech else 'HUMAN'} {desc}")
    print("\n== Stop (all true => AUDIT COMPLETE) ==")
    for k, v in STOP_CHECKS.items():
        print(f"  {k} {v}")
    print("\n== Human Escalation ==")
    for k, v in ESCALATION.items():
        print(f"  {k} {v}")
    print("\n== Protocol Improvement Candidates (登记,不得自动写入) ==")
    for p in PIC:
        print(f"  {p['id']} {p['gap']}")
    print("\n== Reusable detectors (不重写) ==")
    for d in REUSABLE_DETECTORS:
        print(f"  {d['kind']:<14} {d['cmd']}")


def _cmd_init(a: argparse.Namespace) -> int:
    rid = a.run_id or f"FRA-{_dt.date.today().isoformat()}-01"
    p = manifest_path(rid)
    if p.exists() and not a.force:
        print(f"[audit] exists: {p} (use --force to overwrite)")
        return 1
    save(rid, new_manifest(rid, a.objective))
    print(f"[audit] initialized {rid} -> {p}")
    return 0


def _cmd_rules(_a: argparse.Namespace) -> int:
    _print_rules()
    return 0


def _cmd_status(a: argparse.Namespace) -> int:
    rid, m = load(a.run_id)
    print(f"RUN        : {rid}")
    print(f"OBJECTIVE  : {m.get('OBJECTIVE')}")
    print(f"AUTH       : {m.get('AUTHORIZATION')}")
    print(f"STAGE      : {m.get('CURRENT_STAGE')} ({STAGE_NAME.get(m.get('CURRENT_STAGE', ''), '?')})")
    print(f"HEAD       : {m.get('START_HEAD', '')[:12]} -> {m.get('CURRENT_HEAD', '')[:12]}")
    print(f"STAGE_STATUS: {m.get('STAGE_STATUS')}")
    print(f"STOP       : {m.get('STOP_DECISION')}")
    print(f"RETRIES    : {m.get('RETRIES')}/{m.get('RETRY_LIMIT')}")
    print(f"BLOCKERS   : {m.get('BLOCKERS')}")
    print(f"NEXT_ACTION: {m.get('NEXT_ACTION')}")
    print("-- stage gate --")
    stage = str(m.get("CURRENT_STAGE", "M1"))
    for name in STAGE_GATES.get(stage, ()):
        print(f"   {'PASS' if _GATES[name](m) else 'FAIL'}  {name}")
    lg = legacy_exemptions(m)
    if lg:
        print(f"-- legacy 豁免 --\n   {len(lg)} 条生效(无据闭合,显式承认): "
              f"{[f.get('id') for f in lg]}\n"
              f"   理由见各 finding 的 {_LEGACY_REASON_KEY}")
    d = decide(m)
    print(f"-- decide --\n   {d['verdict']:9} {d['reasons']}")
    if d["escalate_h"]:
        print(f"   ESCALATE {d['escalate_h']}: {ESCALATION[d['escalate_h']]}")
    return 0


def _cmd_decide(a: argparse.Namespace) -> int:
    rid, m = load(a.run_id)
    d = decide(m)
    print(json.dumps(d, ensure_ascii=False, indent=2))
    m["NEXT_ACTION"] = d["next_action"]
    save(rid, m)
    return 0


def _cmd_advance(a: argparse.Namespace) -> int:
    """Gate PASS ⇒ 自动进入下一阶段。失败时**必须持久化重试计数**(F-A3)。

    计数语义:RETRY 计入 `RETRIES` 并**落盘**;成功推进时清零。达到 `RETRY_LIMIT`
    后 `decide()` 稳定返回 ESCALATE ⇒ 不存在无限循环。计数在**任何返回路径**上都
    在 return 之前写入(异常也不丢,因为写盘在 try 之外先于 return 完成)。

    **终态守卫(CLOSE-02-F1)**:`decide()` 在 M5 未收敛且无未闭合 mandatory target 时
    会返回 `CONTINUE`(其 `next_action` 是"转入 close 处理")。但 M5 是终态,
    `_next_stage("M5") is None`。此时**不得推进** —— 否则会把 `CURRENT_STAGE` 写成
    `None`(破坏 manifest 可恢复契约)并返回假成功。守卫在**任何状态写入之前**判定。
    """
    rid, m = load(a.run_id)
    stage = str(m.get("CURRENT_STAGE", "M1"))
    d = decide(m)

    if d["verdict"] == "CONTINUE":
        nxt = _next_stage(stage)
        if nxt is None:
            # 终态:M5 已过 Gate 但 Stop 谓词未全真 ⇒ 正确动作是 close,不是 advance。
            # 不写 CURRENT_STAGE、不标 STAGE_STATUS[stage]=DONE。仅落 NEXT_ACTION 提示。
            m["NEXT_ACTION"] = "use `close` (terminal stage reached)"
            save(rid, m)
            print(f"[audit] NOT advancing: {stage} 是终态,无下一阶段。"
                  f"本 Run 尚未收敛 ⇒ 请用 `close` 评估 Stop 谓词。")
            return 1
        m["STAGE_STATUS"][stage] = "DONE"
        m["CURRENT_STAGE"] = nxt
        m["NEXT_ACTION"] = d["next_action"]
        m["RETRIES"] = 0                       # 推进成功 ⇒ 重试计数清零
        save(rid, m)
        print(f"[audit] {stage} -> {m['CURRENT_STAGE']}  ({AUDIT_METHOD_REF})")
        return 0

    if d["verdict"] == "RETRY":
        m["RETRIES"] = int(m.get("RETRIES", 0) or 0) + 1
        m["NEXT_ACTION"] = d["next_action"]
        save(rid, m)                            # ★ 持久化递增:RUN 中断也不丢
        lim = int(m.get("RETRY_LIMIT", 2) or 2)
        print(f"[audit] NOT advancing: RETRY {m['RETRIES']}/{lim} — {d['reasons']}")
        if m["RETRIES"] >= lim:
            print(f"[audit] retry limit reached ⇒ 下次 decide 将 ESCALATE")
        return 1

    # RECOVER / ESCALATE / STOP —— 不改计数,但仍持久化 NEXT_ACTION
    m["NEXT_ACTION"] = d["next_action"]
    save(rid, m)
    print(f"[audit] NOT advancing: {d['verdict']} — {d['reasons']}")
    if d["escalate_h"]:
        print(f"[audit] ESCALATE {d['escalate_h']}: {ESCALATION[d['escalate_h']]}")
    return 1


def _run_and_record(rid: str, m: dict, kind: str, cmd: str) -> int:
    """执行一条已声明的检测命令并记录证据(RECOVER / collect 共用)。

    `{PY}` 在此解析为真实解释器;证据记录的是**实际执行的命令**(可追溯)。
    """
    resolved = _resolve_cmd(cmd)
    r = subprocess.run(resolved, shell=True, cwd=_ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=900)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    m.setdefault("EVIDENCE", []).append(
        {"kind": kind, "cmd": resolved, "exit": int(r.returncode),
         "out": out[-2000:], "ts": _now()})
    return int(r.returncode)


def _cmd_recover(a: argparse.Namespace) -> int:
    """RECOVER:对"证据缺失型"Gate 失败自动重采证据后重验。

    仅在 `decide()` 返回 RECOVER 时执行 —— 即失败**仅**由 EVIDENCE_REFRESHABLE
    中的谓词造成(证据缺失,而非真实失败)。不涉人工、不扩 scope。
    """
    rid, m = load(a.run_id)
    d = decide(m)
    if d["verdict"] != "RECOVER":
        print(f"[audit] not in RECOVER state: {d['verdict']} — {d['reasons']}")
        return 1
    stage = d["stage"]
    fail = [n for n in STAGE_GATES[stage] if not _GATES[n](m)]
    kinds = sorted({EVIDENCE_REFRESHABLE[n] for n in fail if n in EVIDENCE_REFRESHABLE})
    for kind in kinds:
        det = next((x for x in REUSABLE_DETECTORS if x["kind"] == kind), None)
        if det is None:
            print(f"[audit] no declared detector for kind={kind} ⇒ 无法自动恢复")
            return 1
        print(f"[audit] recovering {stage}: running declared detector {kind}")
        print(f"        $ {det['cmd']}")
        rc = _run_and_record(rid, m, kind, det["cmd"])
        print(f"        exit={rc}")
    save(rid, m)
    d2 = decide(m)
    print(f"[audit] after RECOVER -> {d2['verdict']}: {d2['reasons'][0][:90]}")
    if d2["verdict"] == "RECOVER":
        # 重采后仍为 RECOVER ⇒ 恢复未生效(例如检测器仍未产出可信证据)。
        # 返回非 0 并登记 blocker,防止自动驱动者无限重试
        # —— 与 F-A3 已确立的"禁止无限循环"一致。
        m.setdefault("BLOCKERS", []).append({
            "id": "RECOVER-NO-EFFECT", "scope_expansion": False, "blocking": True,
            "what": f"{stage} 重采证据后仍未解除 RECOVER(证据不可信或无产出)"})
        save(rid, m)
        print("[audit] RECOVER 未生效 ⇒ 停止重试(避免无限循环)")
        return 1
    return 0


def _cmd_collect(a: argparse.Namespace) -> int:
    rid, m = load(a.run_id)
    denied = is_denied_command(a.cmd)
    if denied:
        print(f"[audit] REFUSED (H1-class fragment '{denied}'): {a.cmd}")
        return 2
    if not is_allowlisted(a.cmd):
        print(f"[audit] REFUSED (not in allowlist): {a.cmd}")
        return 2
    resolved = _resolve_cmd(a.cmd)          # VAL-B1:`{PY}` -> 真实解释器
    r = subprocess.run(resolved, shell=True, cwd=_ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=900)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    rec = {"kind": a.kind, "cmd": resolved, "exit": int(r.returncode),
           "out": out[-2000:], "ts": _now()}
    m.setdefault("EVIDENCE", []).append(rec)
    save(rid, m)
    trusted = "trusted" if _evidence_trusted(rec) else "NOT-TRUSTED"
    print(f"[audit] collected kind={a.kind} exit={r.returncode} [{trusted}] "
          f"(log {len(out)} chars)")
    print(out[-600:])
    return 0 if r.returncode == 0 else 1


def _cmd_escalate(a: argparse.Namespace) -> int:
    rid, m = load(a.run_id)
    if a.h not in ESCALATION:
        print(f"[audit] unknown escalation key: {a.h} (expect {list(ESCALATION)})")
        return 1
    m.setdefault("ESCALATIONS", []).append(
        {"h": a.h, "what": a.what, "ts": _now(), "resolved": False})
    save(rid, m)
    print(f"[audit] ESCALATE {a.h}: {ESCALATION[a.h]}\n        what: {a.what}")
    return 0


def _cmd_check_rollback(a: argparse.Namespace) -> int:
    rid, m = load(a.run_id)
    res = rollback_guard(a.files, m)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res["allowed"] else 1


def _cmd_close(a: argparse.Namespace) -> int:
    rid, m = load(a.run_id)
    sp = stop_predicates(m)
    for k, ok in sp.items():
        print(f"   {'PASS' if ok else 'FAIL'}  {k}  {STOP_CHECKS[k]}")
    lg = legacy_exemptions(m)
    if lg:
        print(f"   NOTE  {len(lg)} 条 legacy 豁免生效(无据闭合,显式承认): "
              f"{[f.get('id') for f in lg]}")
    if all(sp.values()):
        m["STOP_DECISION"] = "AUDIT COMPLETE"
        m["STAGE_STATUS"]["M5"] = "DONE"
        m["NEXT_ACTION"] = "none"
        save(rid, m)
        print("\n[a] AUDIT COMPLETE")
        return 0
    m["STOP_DECISION"] = "NOT COMPLETE"
    save(rid, m)
    print("\n[a] NOT COMPLETE — 剩余项按决定进入 CONTINUE 或 Deferred/Optional")
    return 1


def main(argv: Optional[list[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser(prog="audit_run",
                                description="PyHarness M1-M5 autonomous audit orchestrator")
    p.add_argument("--run-id", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="创建 run manifest")
    s.add_argument("--objective", required=True)
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=_cmd_init)

    s = sub.add_parser("rules", help="打印规则表")
    s.set_defaults(fn=_cmd_rules)

    s = sub.add_parser("status", help="当前状态 + 阶段 Gate + 决策")
    s.set_defaults(fn=_cmd_status)

    s = sub.add_parser("decide", help="Stage Decision Engine 输出")
    s.set_defaults(fn=_cmd_decide)

    s = sub.add_parser("advance", help="Gate PASS 则自动进入下一阶段；失败时持久化重试计数")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=_cmd_advance)

    s = sub.add_parser("recover", help="RECOVER：对证据缺失型 Gate 失败自动重采后重验")
    s.set_defaults(fn=_cmd_recover)

    s = sub.add_parser("collect", help="运行一条证据命令并记录")
    s.add_argument("--kind", required=True)
    s.add_argument("--cmd", required=True)
    s.set_defaults(fn=_cmd_collect)

    s = sub.add_parser("escalate", help="登记 H1-H5 升级")
    s.add_argument("--h", required=True)
    s.add_argument("--what", required=True)
    s.set_defaults(fn=_cmd_escalate)

    s = sub.add_parser("check-rollback", help="rollback 边界判定")
    s.add_argument("--files", nargs="+", required=True)
    s.set_defaults(fn=_cmd_check_rollback)

    s = sub.add_parser("close", help="评估 Stop 谓词")
    s.set_defaults(fn=_cmd_close)

    a = p.parse_args(argv)
    return int(a.fn(a))


if __name__ == "__main__":
    raise SystemExit(main())
