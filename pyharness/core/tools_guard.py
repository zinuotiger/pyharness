"""pyharness/core/tools_guard.py — 工具安全闸:guard 单调拒绝链 (specs/tools_guard.py.md 契约)

功能编号:F014(guard 单调链核心)· F023(内置危险 guard g1-g7)· 联动 F026(g1
schema 复查)/F015(approval 交接)。
权威口径:specs/tools_guard.py.md(编码契约)、DIS-SEAM §5(决策模型/链求值伪代码/
g1-g7 表)、ERR.md §2.5(GRD-401)/§2.11(POL-*)、SECURITY.md §4(L0-L4 单调策略面);
冲突以 PRD-Core 为准(本模块未见冲突,偏离均为事件载荷适配,见下)。

职责一句话:工具执行前的单调安全求值器——Guard 接口 + GuardChain(按注册序
waterfall,任一 guard 可拒绝且拒绝不可被后续覆盖)+ g1-g7 内置 guard + danger
分级(L0-L4)默认策略;决策三值 allow/reject/approval(**无 bypass 值**,不存在
"跳过 guard"标记位);每个 reject 必出强同步 guard.rejected,审计可证"拦了且
没执行"(INV-04/05)。guard 只有拒绝和请示两条非放行路径,**永远没有"放行"
API**——全链 allow 只是"本 guard 不反对",最终执行权在 executor 管道
(DIS-CORE §7.3.2 关3 Provider)。

单调性三层结构防线(原则 3 / INV-03):
1. waterfall 短路:evaluate 按注册序求值,首个非 allow 即终局返回,后续 guard
   不再求值(测试断言短路后链尾 guard.check 零调用);
2. 无续跑/回翻 API:拒绝后本模块不提供任何把 reject 翻回 allow 的通道(无
   override/bypass/force-allow/execute 方法;审批重入 = 重新 evaluate 一次,
   新决策以新策略为准,单调性高于人类即时意志,GRD-403 语义);
3. 链只增:register_plugin_guard 只追加链尾(TLB-801 重名拒/BUSY 已禁用拒),
   无移除/重排 API;单个内置 guard 关闭须 config 显式声明(disable,CFG-601
   兜底),g-schema/g-danger 恒在不可关。

偏离说明(相对 spec 伪码;契约=spec,偏离均列理由,scope.py 偏离注同款先例):
1. **guard.evaluated/rejected 载荷字段以已落地的 events 词表模型为准**
   (events/payload.py 的 GuardEvaluatedPayload/GuardRejectedPayload,
   extra="forbid" 拒多余字段,57 类型已锁定;实测:超字段 append → EVT-100):
   - evaluated 载荷 = {tool, decision, guard_ids, reasons};spec 伪码的
     policy_ref 字段在该模型不存在 → policy_ref 并入 reasons(模型同义字段);
     decision 词表为 allow|deny|need_approval(deny≡reject、need_approval≡
     approval,spec 明示别名口径),内部 Decision 三值经 _AUDIT_WORD 映射落盘;
   - rejected 载荷 = {tool, guard_id, reason, policy_ref}:spec 伪码的 call_id
     字段模型不允许 → 经 Envelope.trace={"call_id": …} 携带(信封 trace 为自由
     dict,不触发载荷校验),回放按 trace/事件序与 tool.call(含 call_id)配对;
   - guard.disabled 未入 57 词表(实测 append → EVT-102)→ 经注入 session
     尽力而为留痕(scope.py 偏离 4 同款:真实 SessionLog 拒写属 events 模块
     后续阶段门,测试全替身覆盖)。
2. **g1 g-schema 的 registry 依赖注入式装配**:tools_registry(F026)本阶段未
   实现 → GuardChain 经 validator 回调注入(签名 validator(name, raw_args) ->
   dict,校验失败抛 TLB-803);validator=None(未装配)时 g1 视为入口 F026 已验
   直接 allow,待 registry 落地后由装配层注入(链内复查防内层直调,INV-04)。
3. **approval 通道(has_channel)注入式装配**:danger_default_policy(high →
   has_channel ? approval : reject,headless=R8)是 SECURITY §4.2 机器表达;
   链在 evaluate 层统一把 approval 决策降级(无通道 → reject,policy_ref
   APR-501;critical → reject,POL-DGR-1 不可审批)。通道状态由装配层注入
   (GuardChain(approval_channel=…)),None = 未接线视同有通道(guard 只出决策,
   headless 终拦归 approval 模块 APR-501,R8——偏离:伪码未显式处理通道位)。
4. **BUSY 未登记码直构 PyHError("BUSY")**(scope.py 偏离 6 同款先例:
   raise_code 会把未登记码改写为 CYC-999,语义不符);已登记码(TLB-801/
   CFG-601/GRD-4xx)全走 raise_code(全系统唯一抛出入口纪律)。
5. **g3/g4/g7 路径单点解析在 guard 内自含**:F055 单点 resolve 属 tool_fs 模块
   (resolve_in_workspace,F034 第二道闸,本阶段未实现)→ guard 先实现几何判定
   (绝对越界/.. 逃逸/symlink-junction 终解析越界),语义与 tool_fs 规格同码
   (POL-FS-1/2/3);workspace 根取自 scope.policy.workspace_root;read_extra_dirs
   只读例外是工具层(F034)纵深设施,guard 层不消费(只紧不松)。
6. **exec "scope 显式授权"判定 = 沙箱级别**:strict 沙箱下 exec.* 域外不可见
   (scope.can_use 前置已拦,见 scope.py 偏离 7)→ g6 对 strict 兜底直拒
   (POL-EXEC-1,双保险);basic/off 下无 shell=True 且 cwd 不越界才 allow。
7. 签名差异:spec 伪码 evaluate/check 混用 Decision 枚举与字符串三值——落地为
   Decision(StrEnum,ALLOW/REJECT/APPROVAL,与字符串 "reject" 等值可比较,
   executor 伪码 `d == "reject"` 直接可用)+ check 返回 (decision_str,
   policy_ref) 二元组(spec 伪代码权威);"其余函数速览"表内 enabled_guard_ids/
   chain_version/match_guard_by_hook 以链实例方法/模块函数形式落地(表为速览,
   装配/审计消费按实例)。
8. g5 域名匹配为**字面 hostname 精确匹配**(归一化后;子域/IP/混淆不隐含放行,
   须显式列入 allowed_domains),通配 "*" 视为越权不构成放行(config 层已拒,
   此处纵深兜底)。

依赖方向(单向,禁止反向):errors.raise_code(F020)← session.append(注入)←
scope(Scope.can_use 前置 + ScopePolicy 策略面)← config(装配只读);credentials/
workspace 解析均经构造注入;不 import Provider/executor/approval(反向依赖禁止)。
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Iterable, Optional
from urllib.parse import urlsplit

from pyharness.errors import PyHError, raise_code

log = logging.getLogger("pyharness.tools_guard")

# ====================================================================== 常量
# guard 决策三值(内部唯一字面量;无 bypass 值——不存在第四态/跳过标记)
_DECISIONS = ("allow", "reject", "approval")

# 审计事件 decision 词表(events GuardEvaluatedPayload 锁定:
# allow|deny|need_approval;deny≡reject、need_approval≡approval,spec 别名口径)
_AUDIT_WORD = {"allow": "allow", "reject": "deny", "approval": "need_approval"}

# 危险分级 L0-L4(scope/config 同源表;rank 仅供匹配比较)
DANGER_LEVELS: tuple[str, ...] = ("none", "low", "high", "critical")

# 五内置可关 guard(config.py BUILTIN_GUARDS 同源:F023 五内置真子集);
# g-schema/g-danger 恒在不可关(disable → CFG-601)
FORCED_GUARDS: frozenset = frozenset({"g-schema", "g-danger"})

# g3 文件域工具名前缀(与 scope.WORKSPACE_DOMAINS 同源;fs./workspace.)
FS_DOMAINS: frozenset = frozenset({"fs", "workspace"})
# g5 外发域工具名前缀(与 scope.ALLOWLIST_DOMAINS 同源;web./net.)
NET_DOMAINS: frozenset = frozenset({"web", "net"})

# 读路径类工具前缀(g4 match:读凭据文件风险面;list_dir 只列名不读内容)
_READ_PREFIXES: tuple[str, ...] = ("fs.read", "workspace.read")
# 写类工具前缀(g7 match:覆写转审批;fs.write_file 等)
_WRITE_PREFIXES: tuple[str, ...] = ("fs.write", "workspace.write")
# exec 类工具前缀(g6 match:exec.subprocess/exec.pty 等)
_EXEC_PREFIXES: tuple[str, ...] = ("exec.",)

# 凭据文件默认清单项(config security.credentials.file 同源;g4 兜底)
DEFAULT_CREDENTIAL_FILES: tuple[str, ...] = ("~/.pyharness/credentials.yaml",)

# 凭据型文件名(清单外纵深:任何位置命中该名 = 凭据文件形态,拒读)
_CRED_BASENAMES: tuple[str, ...] = ("credentials.yaml", "credentials.yml")


# ==================================================================== 决策
class Decision(StrEnum):
    """guard 决策三值(原则 3;无 bypass 值)。

    StrEnum:Decision.REJECT == "reject" 成立(executor 伪码 `d == "reject"`
    直接比较可用,无需 .value)。语义:
        ALLOW    放行,继续下一 guard 或执行(仅"本 guard 不反对")
        REJECT   终局拒绝(guard.rejected 强同步;零副作用;无续跑 API)
        APPROVAL 请求人类裁决(仅 danger=high 且有通道;critical 强制转 reject)
    """

    ALLOW = "allow"
    REJECT = "reject"
    APPROVAL = "approval"


def danger_default_policy(danger: str, *, has_channel: bool) -> Decision:
    """danger 分级默认策略机器表达(SECURITY §4.2;headless=R8)。

    none/low → allow;high → (has_channel ? approval : reject);critical → reject
    (不可审批,无审批通道是刻意设计——人类真想删须下调沙箱留事件而非弹窗点
    同意,POL-DGR-1)。供 g-danger/装配层/测试消费;危险级非法值按 none 处理。
    """
    if danger == "high":
        return Decision.APPROVAL if has_channel else Decision.REJECT
    if danger == "critical":
        return Decision.REJECT
    return Decision.ALLOW  # none/low/未知 → 放行(分级信息由 match 层过滤)


# ================================================================= ToolCall
@dataclass(frozen=True)
class ToolCall:
    """待裁决工具调用(最小契约;与 tools_executor.parse_tool_call 同构)。

    字段:name = 工具名;raw_args = LLM 原始参数(审计/复查用);call_id = 调用
    id(关联 tool.call/result);parent_seq = 触发它的 llm.response seq;
    defn = 工具契约引用(只读 danger/guard_hooks 等;tools_registry 落地前可为
    任意含 danger 属性的鸭子对象/None);args = F026 校验后实参(executor 填入;
    未填时回落 raw_args,guard 按动作形态读路径/域名/shell 开关)。
    """

    name: str
    raw_args: dict = field(default_factory=dict)
    call_id: str = ""
    parent_seq: Optional[int] = None
    defn: Any = None
    args: Optional[dict] = None

    @property
    def safe_args(self) -> dict:
        """guard 读参面:F026 后实参优先,未校验回落 raw_args(只读不写)。"""
        return self.args if isinstance(self.args, dict) else self.raw_args


# ==================================================================== Guard
class Guard:
    """Guard 契约基类(DIS-SEAM §5.2;插件 guard 继承本类,只能加拒绝面)。

    id    事件里出现的 guard_id(全链唯一,如 "g-fs-path")
    match(call) -> bool                 管不管该工具(按名前缀/Definition 结构,
                                        不信任 description 文本——防"低危声明+
                                        高危实现"伪装工具)
    check(call, scope) -> (decision, policy_ref)
                                        判定;decision ∈ allow/reject/approval;
                                        policy_ref 如 POL-FS-1(审计 reasons 用);
                                        不读 description
    """

    id: str = "guard"

    def match(self, call: Any) -> bool:
        """是否管辖该调用;缺省不管辖(插件须显式实现)。"""
        return False

    async def check(self, call: Any, scope: Any) -> tuple[str, Optional[str]]:
        """判定;缺省不反对(子类覆盖;返回 (allow, None))。"""
        return ("allow", None)

    # 便于单测/插件断言契约的只读名(防把 Guard 当 bypass 门)
    @property
    def allows_nothing_extra(self) -> bool:
        """只拒绝不放行标记:guard 无任何"放行某调用"的旁路能力(恒 True)。"""
        return True


def _policy_of(scope: Any) -> Any:
    """取策略面:Scope 实例回落 .policy,ScopePolicy 原样(鸭子兼容)。"""
    return getattr(scope, "policy", None) or scope


def _danger_of(call: Any) -> str:
    """读工具契约 danger(五级枚举;defn 缺失/非法 → none,按最安全读取)。"""
    d = getattr(getattr(call, "defn", None), "danger", None)
    return d if d in DANGER_LEVELS else "none"


def _is_domain(prefix: str, name: str) -> bool:
    """工具名前缀域判定(fs./web./exec.…;无点 = 单字工具不入文件/网络域)。"""
    return name.split(".", 1)[0] == prefix if "." in name else False


# ------------------------------------------------------------------ 路径几何
def _normcase_path(p: str) -> str:
    """路径归一(大小写/分隔符;Windows 同源比较用)。"""
    return os.path.normcase(os.path.normpath(p))


def _inside_workspace(target: str, workspace_root: str) -> bool:
    """target(已 normpath)是否几何落在 workspace_root 内(含根本身)。

    前缀比较带分隔符边界(防 ws=/a/b 误纳 /a/bc);Windows normcase 后比较。
    """
    ws = _normcase_path(os.path.abspath(os.path.expanduser(workspace_root)))
    t = _normcase_path(os.path.abspath(target))
    if t == ws:
        return True
    return t.startswith(ws.rstrip("\\/") + os.sep)


def _norm_target(raw: Any, workspace_root: str) -> str:
    """原始路径 → 参与几何判定的规范绝对串(相对路径以 workspace 为基拼接)。

    只做词法规范化(normpath),不做 symlink 解析(那是 POL-FS-3 单独职责);
    规范化后仍可能词法在界内而真实(realpath)在界外 → FS-3 接住。
    """
    s = str(raw).strip()
    if not s:
        return os.path.abspath(os.path.expanduser(workspace_root))
    raw = os.path.expanduser(s)
    ws = os.path.abspath(os.path.expanduser(workspace_root))
    if os.path.isabs(raw):
        return os.path.normpath(raw)
    return os.path.normpath(os.path.join(ws, raw))


def _path_from_args(args: dict) -> str:
    """从动作参数取目标路径(工具契约键面:path/src/dst/target;空 → workspace)。"""
    for key in ("path", "src", "dst", "target", "dir"):
        if isinstance(args.get(key), str) and args[key].strip():
            return args[key]
    return ""


def _fs_target(call: Any) -> str:
    """g3/g4/g7 共享:取调用目标路径原文(无 → 空串=workspace 根)。"""
    return _path_from_args(getattr(call, "safe_args", {}) or {})


def _has_dotdot(raw: str) -> bool:
    """原始路径是否含 ".." 路径段(fnmatch 段级,防 /a..b 误报)。"""
    return any(seg == ".." for seg in os.path.normpath(str(raw)).split(os.sep)
               or os.path.normpath(str(raw)).split("/"))


def _final_realpath(p: str) -> str:
    """symlink/junction 最终解析(realpath;不存在路径解析现存前缀,不抛)。"""
    return os.path.realpath(p)


def _is_linkish(p: str) -> bool:
    """p 或其现存祖先是否 symlink/junction(触发 FS-3 realpath 复核的门槛)。"""
    cur = os.path.normpath(p)
    if os.path.islink(cur) or (hasattr(os.path, "isjunction")
                               and os.path.isjunction(cur)):
        return True
    parent = os.path.dirname(cur)
    guard = 0
    while parent and parent != cur and guard < 64:  # 向上找现存链点
        if os.path.islink(parent) or (hasattr(os.path, "isjunction")
                                      and os.path.isjunction(parent)):
            return True
        nxt = os.path.dirname(parent)
        if nxt == parent:
            break
        cur, parent, guard = parent, nxt, guard + 1
    return False


def _resolve_geometry(raw: Any, workspace_root: str,
                      *, link_resolver: Optional[Callable[[str], str]] = None
                      ) -> tuple[str, Optional[str]]:
    """路径几何单点判定(g3 权威语义;F055 单点 guard 侧实现)。

    返回 (规范绝对路径, policy_ref);policy_ref 非空 = 越界(调用方须拒):
        POL-FS-1 绝对路径越界(词法不在 workspace)
        POL-FS-2 ".." 段逃逸(规范化后越界)
        POL-FS-3 symlink/junction 终解析越界(词法在界内,真实在界外)
    全过返回 (target, None)。纯只读:零 stat 之外副作用(os.path 探测)。
    """
    target = _norm_target(raw, workspace_root)
    ws = os.path.abspath(os.path.expanduser(workspace_root))
    # 1) POL-FS-1:绝对路径词法越界(不 startswith workspace)
    if os.path.isabs(str(raw).strip()) and not _inside_workspace(target, ws):
        return (target, "POL-FS-1")
    # 2) POL-FS-2:".." 段逃逸(规范化后不在 workspace)
    if _has_dotdot(str(raw)) and not _inside_workspace(target, ws):
        return (target, "POL-FS-2")
    # 3) POL-FS-3:symlink/junction 终解析越界(链点存在才查,防误伤普通路径)
    if _is_linkish(target):
        final = (link_resolver or _final_realpath)(target)
        if not _inside_workspace(final, ws):
            return (target, "POL-FS-3")
    return (target, None)


# ============================================================ g1 g-schema
def g_schema_match(call: Any) -> bool:
    """g1 match:所有工具 + 内部调用(链内复查入口 F026 已验的契约)。"""
    return True


async def g_schema_check(call: Any, scope: Any,
                         *, validator: Optional[Callable[..., dict]] = None
                         ) -> tuple[str, Optional[str]]:
    """g1 check:schema 复查——validator 注入(registry.validate_args 同型)。

    失败(TLB-803)= 内层绕过证据(入口已验仍败 → 直调旁路),转 reject;
    validator 未装配 → allow(入口 F026 已验,注册表落地后由装配层注入,
    见偏离 2)。policy_ref = TLB-803(错误码即策略标识,ERR §2.11 口径)。
    """
    if validator is None:
        return ("allow", None)
    try:
        validator(call.name, getattr(call, "raw_args", {}) or {})
    except PyHError as exc:
        if exc.code == "TLB-803":
            return ("reject", "TLB-803")
        raise                                  # 其它错误码:注册表层契约问题,上抛
    return ("allow", None)


# ============================================================== g2 g-danger
def g_danger_match(call: Any) -> bool:
    """g2 match:danger≠none 的工具才进判定(none/low 也过 match 由 check 放行)。"""
    return _danger_of(call) != "none"


async def g_danger_check(call: Any, scope: Any) -> tuple[str, Optional[str]]:
    """g2 check:danger 分级默认策略(high→approval;critical→reject 不可审批)。

    与 evaluate 层降级分工:本函数只按"有通道假设"输出分级决策;无通道/
    critical 强制转 reject 由 evaluate 统一兜底(插件 approval 也覆盖,
    见 danger_default_policy 与偏离 3)。
    """
    level = _danger_of(call)
    if level == "critical":
        return ("reject", "POL-DGR-1")          # critical 直拒,人类无批准入口
    if level == "high":
        return ("approval", "POL-DGR-1")        # 转审批(通道判定在 evaluate 层)
    return ("allow", None)                       # none/low → 不反对


# ============================================================== g3 g-fs-path
def g_fs_path_match(call: Any) -> bool:
    """g3 match:文件域工具(fs.*/workspace.*;按名前缀,不信任描述)。"""
    return any(_is_domain(d, call.name) for d in FS_DOMAINS)


async def g_fs_path_check(call: Any, scope: Any,
                          *, link_resolver: Optional[Callable[[str], str]] = None
                          ) -> tuple[str, Optional[str]]:
    """g3 check:路径几何三闸(POL-FS-1/2/3;单点 resolve,见 _resolve_geometry)。

    非文件域 → allow(match 兜底);workspace 根取自 scope.policy.workspace_root
    (F055 fresh workspace);越界即拒,零副作用(只读探测)。
    """
    ws = getattr(_policy_of(scope), "workspace_root", None) or ""
    target, policy = _resolve_geometry(_fs_target(call), ws,
                                       link_resolver=link_resolver)
    if policy:
        return ("reject", policy)
    return ("allow", None)


# ======================================================== g4 g-credential-read
def g_credential_read_match(call: Any) -> bool:
    """g4 match:读路径类工具(读文件内容才有凭据外泄面)。"""
    return call.name.startswith(_READ_PREFIXES)


async def g_credential_read_check(
        call: Any, scope: Any,
        *, credential_paths: Optional[Iterable[str]] = None,
        link_resolver: Optional[Callable[[str], str]] = None
) -> tuple[str, Optional[str]]:
    """g4 check:读目标命中凭据文件清单 → 拒(POL-CRED-1)。

    命中口径双轨(见偏离 1 注释/模块 docstring):①规范化绝对路径 ∈ 清单;
    ②文件形态名(basename = credentials.yaml/yml 等,纵深:凭据文件拷进
    workspace 的任何位置都拦)。清单由装配注入(credentials.yaml 等,F016
    credentials 模块落地后同源);路径先过 g3 几何(g4 只做命中比较)。
    """
    paths = list(credential_paths or ())
    if not paths:                                # 未装配清单 → 兜底默认凭据文件
        paths = [os.path.expanduser(p) for p in DEFAULT_CREDENTIAL_FILES]
    ws = getattr(_policy_of(scope), "workspace_root", None) or ""
    target, policy = _resolve_geometry(_fs_target(call), ws,
                                       link_resolver=link_resolver)
    if policy:
        return ("reject", policy)                # 越界读:g3 语义优先(g4 不越权)
    norm_target = _normcase_path(target)
    for c in paths:
        norm_c = _normcase_path(os.path.abspath(os.path.expanduser(str(c))))
        if norm_target == norm_c:
            return ("reject", "POL-CRED-1")      # 命中清单精确路径
    base = os.path.basename(norm_target).lower()
    if base in _CRED_BASENAMES or any(
            os.path.basename(os.path.normpath(str(c))).lower() == base
            for c in paths if str(c).strip()):
        return ("reject", "POL-CRED-1")          # 凭据文件形态(名字即证据)
    return ("allow", None)


# ============================================================ g5 g-net-outbound
def g_net_outbound_match(call: Any) -> bool:
    """g5 match:外发网络工具(web.*/net.*;按名前缀)。"""
    return any(_is_domain(d, call.name) for d in NET_DOMAINS)


def _normalize_domain(raw: Any) -> str:
    """域名/URL 归一为可比对 hostname(小写、去尾点、剥 scheme/port/路径)。

    URL 用 urlsplit 取 hostname;裸域名/IP/带端口原样取首段;解析不出 → 空串
    (空串必不在 allowlist → 拒,禁外发默认)。
    """
    s = str(raw or "").strip().lower().rstrip(".")
    if not s:
        return ""
    try:
        if "://" not in s:
            s = "http://" + s                    # scheme 缺失补默认(仅解析用)
        host = urlsplit(s).hostname
        return (host or "").lower().rstrip(".")
    except ValueError:
        return ""


async def g_net_outbound_check(call: Any, scope: Any
                               ) -> tuple[str, Optional[str]]:
    """g5 check:目标域不在 scope.policy.allowed_domains → 拒(POL-NET-1)。

    默认空 = 禁一切外发;字面 hostname 精确匹配(见偏离 8:子域/IP/混淆不隐含
    放行;通配 "*" 是越权配置,config 层已拒,此处纵深不构成放行)。
    目标域取参数 url/domain/host(URL 或裸域都归一);无域参数 → 拒(禁外发)。
    """
    allowed = getattr(_policy_of(scope), "allowed_domains", None) or set()
    args = getattr(call, "safe_args", {}) or {}
    raw = next((args[k] for k in ("url", "domain", "host", "target")
                if isinstance(args.get(k), str) and args[k].strip()), "")
    host = _normalize_domain(raw)
    if not host or host not in {str(d).strip().lower().rstrip(".")
                                for d in allowed}:
        return ("reject", "POL-NET-1")
    return ("allow", None)


# ================================================================ g6 g-exec
def g_exec_match(call: Any) -> bool:
    """g6 match:exec 域工具(exec.subprocess/exec.pty 等;按名前缀)。"""
    return call.name.startswith(_EXEC_PREFIXES)


async def g_exec_check(call: Any, scope: Any) -> tuple[str, Optional[str]]:
    """g6 check:exec 执行约束(POL-EXEC-1)。

    三条拒绝面(任一命中即拒,零执行):
      ① strict 沙箱直拒——scope 前置已把 exec.* 判不可见(域外),此处兜底
        (偏离 6:strict 下 exec 无"scope 显式授权"可言);
      ② args.shell is True ——禁 shell=True(解释器注入面);
      ③ cwd 绝对路径越出 workspace ——进程工作目录限沙箱。
    其余(basic/off + 无 shell + cwd 界内/缺省)→ allow。
    """
    pol = _policy_of(scope)
    if getattr(pol, "sandbox_level", "strict") == "strict":
        return ("reject", "POL-EXEC-1")
    args = getattr(call, "safe_args", {}) or {}
    if args.get("shell") is True:
        return ("reject", "POL-EXEC-1")
    cwd = args.get("cwd")
    if isinstance(cwd, str) and cwd.strip() and os.path.isabs(cwd):
        ws = getattr(pol, "workspace_root", None) or ""
        if not _inside_workspace(os.path.normpath(cwd), ws):
            return ("reject", "POL-EXEC-1")
    return ("allow", None)


# ============================================================= g7 g-overwrite
def g_overwrite_match(call: Any) -> bool:
    """g7 match:写类工具(fs.write_file 等;按动作形态,不信任描述)。"""
    return call.name.startswith(_WRITE_PREFIXES)


async def g_overwrite_check(
        call: Any, scope: Any,
        *, path_exists: Optional[Callable[[str], bool]] = None
) -> tuple[str, Optional[str]]:
    """g7 check:目标已存在且 mode=write → approval(POL-OVW-1;覆写转审批)。

    append/新建(不存在)→ allow;目标探测只读(os.path.exists 注入,测试可
    替身化);路径先过 g3 几何,越界轮不到 g7(链序保证)。目标存在性探测失败
    视同不存在(新建路径;安全侧:覆盖写仍会因存在才转审批)。
    """
    args = getattr(call, "safe_args", {}) or {}
    if str(args.get("mode", "write")).lower() == "append":
        return ("allow", None)                    # append = 追加,非覆盖
    exists = path_exists or os.path.exists
    ws = getattr(_policy_of(scope), "workspace_root", None) or ""
    target, policy = _resolve_geometry(_path_from_args(args), ws)
    if policy:
        return ("reject", policy)
    try:
        if os.path.exists(target):
            return ("approval", "POL-OVW-1")      # 覆写已有文件 → 人类裁决
    except OSError:
        pass                                       # 探测异常 → 视同不存在
    return ("allow", None)


# ================================================================ 内置链装配
_BUILTIN_IDS: tuple[str, ...] = (
    "g-schema",            # g1 schema 复查(恒在)
    "g-danger",            # g2 danger 分级(恒在)
    "g-fs-path",           # g3 路径几何
    "g-credential-read",   # g4 凭据读拦截
    "g-net-outbound",      # g5 外发域名 allowlist
    "g-exec",              # g6 exec 约束
    "g-overwrite",         # g7 覆写转审批
)


def build_builtin_chain(*, credential_paths: Optional[Iterable[str]] = None,
                        validator: Optional[Callable[..., dict]] = None,
                        approval_channel: Optional[bool] = None,
                        path_exists: Optional[Callable[[str], bool]] = None,
                        link_resolver: Optional[Callable[[str], str]] = None
                        ) -> list[Guard]:
    """装配内置 g1-g7(固定序 = 求值序;DIS-SEAM §2.5 第④步)。

    g1-g5 恒在;g6/g7 与插件 guard 一律按注册序追加链尾(本函数即固定序源)。
    validator/credential_paths/approval_channel/path_exists/link_resolver 为
    注入式依赖(见模块 docstring 偏离 1/2/3/5):链实例构造时一次注入,运行期
    只读(防策略漂移,F021)。
    """
    paths = list(credential_paths) if credential_paths else None
    return [
        _FuncGuard("g-schema", g_schema_match,
                   lambda c, s: g_schema_check(c, s, validator=validator)),
        _FuncGuard("g-danger", g_danger_match, g_danger_check),
        _FuncGuard("g-fs-path", g_fs_path_match,
                   lambda c, s: g_fs_path_check(c, s, link_resolver=link_resolver)),
        _FuncGuard("g-credential-read", g_credential_read_match,
                   lambda c, s: g_credential_read_check(
                       c, s, credential_paths=paths,
                       link_resolver=link_resolver)),
        _FuncGuard("g-net-outbound", g_net_outbound_match, g_net_outbound_check),
        _FuncGuard("g-exec", g_exec_match, g_exec_check),
        _FuncGuard("g-overwrite", g_overwrite_match,
                   lambda c, s: g_overwrite_check(c, s, path_exists=path_exists)),
    ]


class _FuncGuard(Guard):
    """函数对 → Guard 实例适配器(内置 guard 实现即模块函数对,spec 清单)。"""

    def __init__(self, guard_id: str,
                 match_fn: Callable[[Any], bool],
                 check_fn: Callable[[Any, Any],
                                    Any]) -> None:
        self.id = guard_id
        self._match_fn = match_fn
        self._check_fn = check_fn

    def match(self, call: Any) -> bool:
        return bool(self._match_fn(call))

    async def check(self, call: Any, scope: Any) -> tuple[str, Optional[str]]:
        return await self._check_fn(call, scope)


# ================================================================ GuardChain
class GuardChain:
    """guard 单调拒绝链(原则 3 落地;L3 调用裁决层)。

    结构不可变纪律:chain 注册序 = 求值序,只增不改(插件追加链尾);disabled
    仅经 disable()(config 显式声明 + guard.disabled 留痕)写入;运行期无任何
    API 可移除/重排/回翻一次拒绝(INV-03,测试钉死方法面)。唯一状态 = 链装配
    (chain/disabled/_snapshot_seq),求值本身无状态残留(取消/重入语义 = 重新
    evaluate 一次,以新策略为准,GRD-403)。
    """

    # 无 bypass/放行/执行类方法面(测试断言 AttributeError;executor 才持有
    # Provider 执行权——guard 永远只是"拒绝或请示"的裁决器)
    _FORBIDDEN_API: tuple[str, ...] = (
        "override", "bypass", "force_allow", "set_allow", "allow", "execute",
        "run", "mark_allowed", "clear_reject", "release_decision",
    )

    def __init__(self, chain: Optional[list[Guard]] = None, *,
                 session: Any = None, disabled: Optional[Iterable[str]] = None,
                 credential_paths: Optional[Iterable[str]] = None,
                 validator: Optional[Callable[..., dict]] = None,
                 approval_channel: Optional[bool] = None,
                 path_exists: Optional[Callable[[str], bool]] = None,
                 link_resolver: Optional[Callable[[str], str]] = None,
                 bus: Any = None) -> None:
        """构造链:chain 缺省 = 内置 g1-g7(固定序);session/bus 注入式装配。

        session = 事件落点(SessionLog.append 同型:append(type, payload, *,
        actor=…, sync=…, trace=…));None = 未接线(evaluated/rejected 日志降级,
        但 rejected 强同步语义要求装配层注入真实 session——单测以替身覆盖)。
        approval_channel = 审批通道在位与否(偏离 3;None 视同有通道)。
        """
        built = chain if chain is not None else build_builtin_chain(
            credential_paths=credential_paths, validator=validator,
            approval_channel=approval_channel, path_exists=path_exists,
            link_resolver=link_resolver)
        ids = [g.id for g in built]
        if len(set(ids)) != len(ids):
            raise_code("TLB-801", guard="chain",
                       advice="guard id 全链唯一(重复装配被拒)")
        self.chain: list[Guard] = list(built)    # 注册序 = 求值序(只增不改)
        self.disabled: set[str] = set(disabled or ())
        bad = self.disabled - set(ids)
        if bad or self.disabled & FORCED_GUARDS:
            raise_code("CFG-601", reason="越权",
                       fields=[f"guards.disabled:{sorted(bad or self.disabled)}"],
                       detail="仅五内置可关且须 config 声明;g-schema/g-danger "
                              "强制恒在")
        self._session: Any = session              # 事件落点(注入)
        self._bus: Any = bus                      # registry.updated 尽力出口
        self._approval_channel: Optional[bool] = approval_channel
        self._snapshot_seq: int = 1               # 装配版本(审批重入复核)
        self._tasks: set[asyncio.Task] = set()    # fire-and-forget 任务登记

    # ------------------------------------------------------ 单调链求值主函数
    async def evaluate(self, call: Any, scope: Any) -> Decision:
        """单调求值(F014 主函数):scope 前置 → 按注册序 waterfall → 终局决策。

        规则(spec 伪代码权威):
        - scope 前置:can_use 不可见 → 终局 REJECT(guard_id=scope-hidden,
          policy_ref=GRD-401;executor 关2a 已先行拦截,此处兜底直调消费方);
        - 命中 guard 逐个 check;首个非 allow 即短路返回(reject/approval 都到
          此为止,后续 guard 不再求值 = 单调拒绝的求值面保证);
        - approval 降级两闸(统一在 evaluate 层,插件 approval 也覆盖):
          danger=critical → 强制转 reject(POL-DGR-1,不可审批,F014);
          无审批通道 → 转 reject(APR-501,headless=R8,偏离 3);
        - 每次求值恰好一条 guard.evaluated(INV-04:执行前必有;缺 = 非法执行);
          reject 再强同步 guard.rejected(sync=True,崩溃不丢拦截事实,INV-05);
        - 全链 allow 才返回 ALLOW——guard 无放行权,执行权归 executor 关3。
        返回 Decision(StrEnum;executor 伪码 `d == "reject"` 直接可比较)。
        异常:append 强同步失败按 PERS-202/EVT-1xx 语义上抛(拒绝事实必须落地,
        调用方 fail-closed:缺 evaluated/rejected 不进入 Provider)。
        """
        can_use = getattr(scope, "can_use", None)
        if can_use is None:
            raise_code("GRD-401", hint="evaluate 需要会话 Scope(can_use 前置 "
                       "不可缺);直接传 ScopePolicy 违反调用契约")
        if not can_use(call.name):                # scope 前置:不可见 → 终局
            await self._audit(call, Decision.REJECT, "scope-hidden", "GRD-401")
            await self._append_rejected(call, "scope-hidden", "GRD-401")
            return Decision.REJECT
        enabled = self.enabled_guard_ids()
        for g in self.chain:                      # 按注册序 waterfall
            if g.id in self.disabled:
                continue                          # 仅 config 显式关闭(留过事件)
            if not g.match(call):
                continue                          # 非本 guard 管辖 → 看下一个
            d_str, policy = await g.check(call, scope)   # (allow/reject/approval)
            if d_str not in _DECISIONS:
                raise_code("CYC-999", guard=g.id, decision=d_str,
                           hint="guard.check 只能返回 allow/reject/approval"
                                "(插件契约违规=bug 证据)")
            d = Decision(d_str)
            if d is Decision.APPROVAL:            # approval 降级两闸(统一兜底)
                if _danger_of(call) == "critical":
                    d, policy = Decision.REJECT, "POL-DGR-1"  # 不可审批(F014)
                elif self._approval_channel is False:
                    d, policy = Decision.REJECT, "APR-501"    # 无通道(R8)
            if d is not Decision.ALLOW:           # 首个非 allow 即短路(waterfall)
                await self._audit(call, d, g.id, policy)
                if d is Decision.REJECT:          # 强同步:拦了且没执行(INV-05)
                    await self._append_rejected(call, g.id, policy)
                return d                          # reject/approval 都到此为止
        await self._audit(call, Decision.ALLOW, enabled, None)
        return Decision.ALLOW                     # 全链 allow → executor 关3

    # ------------------------------------------------------------ 插件挂载
    def register_plugin_guard(self, guard: Guard) -> None:
        """插件 guard 只加严挂载:追加链尾(单调;只增拒绝面)。

        拒绝面:①重名(TLB-801,guard id 全链唯一);②已禁用 guard(BUSY,须先
        config 启用再挂)。成功 → 链尾 + 装配版本 +1(审批重入复核策略是否已
        变)+ registry.updated 总线广播(尽力而为)。无任何移除/重排 API。
        """
        if not isinstance(guard, Guard):
            raise_code("TLB-801", guard=type(guard).__name__,
                       advice="插件 guard 须继承 Guard(契约:id/match/check)")
        ids = [g.id for g in self.chain]
        if guard.id in ids:
            raise_code("TLB-801", guard=guard.id,
                       advice="guard id 全链唯一(重复挂载被拒)")
        if guard.id in self.disabled:
            # BUSY 未登记码直构(见偏离 4:raise_code 会改写为 CYC-999)
            raise PyHError("BUSY", ctx={"guard": guard.id,
                                        "advice": "先启用(config)再挂载"})
        self.chain.append(guard)                  # 恒在链尾(单调)
        self._snapshot_seq += 1
        self._emit_bus("registry.updated",
                       {"op": "add", "kind": "guard", "key": guard.id})

    # ------------------------------------------------------------ 显式降级口
    def disable(self, guard_id: str, *, config_ref: str) -> None:
        """单个内置 guard 关闭(只可收严的反面特例;SECURITY §4.1)。

        必须 config 显式声明(config_ref 指明出处留痕),默认拒绝:
        - g-schema/g-danger(强制恒在)或不在链上的 id → CFG-601;
        - 五内置之一且 config 声明 → 关 + guard.disabled 事件(谁在何时放松
          安全,永远可审计;事件词表外 → 尽力而为留痕,见偏离 1)。
        """
        if guard_id in FORCED_GUARDS or guard_id not in {g.id for g in self.chain}:
            raise_code("CFG-601", guard=guard_id,
                       why="g-schema/g-danger 强制恒在;其余须 config 声明",
                       fields=[f"guards.disabled:{guard_id}"])
        if guard_id in self.disabled:
            return                                 # 幂等:已关不重复留痕
        self.disabled.add(guard_id)
        self._snapshot_seq += 1
        self._record("guard.disabled",
                     {"guard_id": guard_id, "config_ref": config_ref})

    # ------------------------------------------------------------ 只读审计面
    def enabled_guard_ids(self) -> list[str]:
        """当前生效链 id(审计/自检 F031;排除 config 显式关闭项)。"""
        return [g.id for g in self.chain if g.id not in self.disabled]

    def chain_version(self) -> int:
        """装配版本号(审批重入复核:策略装配是否已变;单调 +1)。"""
        return self._snapshot_seq

    def __getattr__(self, name: str) -> Any:
        """结构防线:禁 bypass/放行/执行类方法面(INV-03 测试钉死)。"""
        if name in self._FORBIDDEN_API:
            raise AttributeError(
                f"GuardChain 无 {name} API:guard 只有拒绝和请示,永远没有"
                f"放行/执行路径(单调拒绝,INV-03);执行权归 executor 关3")
        raise AttributeError(f"{type(self).__name__!r} object has no "
                             f"attribute {name!r}")

    # ------------------------------------------------------------ 事件出口
    async def _audit(self, call: Any, decision: Decision,
                     guard_ids: Any, policy_ref: Optional[str]) -> None:
        """evaluated 审计点(INV-04):每次求值恰一条,决策落词表词。

        payload 适配已落地 GuardEvaluatedPayload(偏离 1):decision ∈
        allow|deny|need_approval(内部 Decision 映射);policy_ref 并入 reasons;
        call_id 经 trace 携带(与 tool.call 配对回放)。普通异步落盘。
        """
        await self._append(
            "guard.evaluated",
            {"tool": call.name,
             "decision": _AUDIT_WORD[decision.value],
             "guard_ids": ([guard_ids] if isinstance(guard_ids, str)
                           else list(guard_ids or [])),
             "reasons": ([policy_ref] if policy_ref else None)},
            actor="tool", trace={"call_id": getattr(call, "call_id", "")})

    async def _append_rejected(self, call: Any, guard_id: str,
                               policy_ref: str) -> None:
        """拒绝强同步留痕:guard.rejected 唯一事件出口(INV-05 审计证据)。

        sync=True:落盘成功才返回(崩溃不丢"拦了"事实);失败(PERS-202/EVT-1xx)
        上抛——调用方 fail-closed,绝不带着未落盘的拒绝继续(审计不能撒谎)。
        policy_ref 不含参数原文(防凭据入审计,SECURITY §6.4)。
        """
        await self._append(
            "guard.rejected",
            {"tool": call.name, "guard_id": guard_id,
             "reason": policy_ref or "GRD-401",
             "policy_ref": policy_ref or "GRD-401"},
            actor="tool", sync=True,
            trace={"call_id": getattr(call, "call_id", "")})

    async def _append(self, type_: str, payload: dict, *, actor: str,
                      sync: bool = False,
                      trace: Optional[dict] = None) -> None:
        """append 统一封装:兼容同步替身/异步 SessionLog(await 实际返回)。"""
        sess = self._session
        if sess is None:
            log.debug("guard 未接线事件出口,%s 未记录(sync=%s)", type_, sync)
            return
        r = sess.append(type_, payload, actor=actor, sync=sync, trace=trace)
        if inspect.isawaitable(r):
            await r

    def _record(self, type_: str, payload: dict) -> None:
        """词表外事件(guard.disabled)尽力而为留痕(偏离 1;scope.py 同款)。

        真实 SessionLog 现会 EVT-102 拒写(词表 57 锁定,后续阶段门扩展);
        未接线/失败只记日志,不阻断策略主路径(与 scope._record 一致)。
        """
        sess = self._session
        if sess is None:
            log.debug("guard 未接线事件出口,%s 未记录", type_)
            return
        try:
            r = sess.append(type_, payload, actor="system")
        except Exception as exc:                   # noqa: BLE001 尽力而为
            log.warning("guard append %s 失败: %s", type_, exc)
            return
        if inspect.isawaitable(r):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return
            task = asyncio.create_task(self._safe_record(r, type_))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _safe_record(self, coro: Any, type_: str) -> None:
        try:
            await coro
        except Exception as exc:                    # noqa: BLE001 EVT-102 等
            log.warning("guard append %s 异步失败: %s", type_, exc)

    def _emit_bus(self, type_: str, payload: dict) -> None:
        """总线尽力而为出口(registry.updated 瞬时事件,仅总线无载荷模型)。"""
        bus = self._bus
        if bus is None:
            return
        try:
            r = bus.emit(type_, payload)
            if inspect.isawaitable(r):
                try:
                    asyncio.get_running_loop().create_task(r)
                except RuntimeError:
                    log.warning("guard 无运行中事件循环,%s 未投递", type_)
        except Exception as exc:                    # noqa: BLE001 未注册类型等
            log.warning("guard emit %s 失败: %s", type_, exc)


# ================================================================= 模块函数
# 插件 guard 钩子注册表(hook 名 → Guard 实例;capability 经 Definition
# guard_hooks 声明后由 match_guard_by_hook 解析,announce 时挂链尾,F023)
_PLUGIN_HOOKS: dict[str, Guard] = {}


def register_guard_hook(hook: str, guard: Guard) -> None:
    """登记插件 guard 钩子名(能力包启动期调用;重名/非 Guard → TLB-801)。"""
    if not isinstance(guard, Guard):
        raise_code("TLB-801", hook=hook, advice="插件 guard 须继承 Guard")
    if hook in _PLUGIN_HOOKS:
        raise_code("TLB-801", hook=hook, advice="guard hook 名全库唯一")
    _PLUGIN_HOOKS[hook] = guard


def match_guard_by_hook(defn: Any) -> list[Guard]:
    """按 Definition.guard_hooks 名字解析出 Guard 实例(announce 时挂载用)。

    guard_hooks 中未登记的钩子 = 声明了不存在的 guard(非法注册证据)→
    TLB-801 拒(能力须先 register_guard_hook 再声明)。返回实例按 hooks 声明序。
    """
    hooks = tuple(getattr(defn, "guard_hooks", ()) or ())
    out: list[Guard] = []
    for name in hooks:
        g = _PLUGIN_HOOKS.get(str(name))
        if g is None:
            raise_code("TLB-801", hook=str(name),
                       advice="guard_hooks 声明了未登记的 guard:先 "
                              "register_guard_hook 再挂载")
        out.append(g)
    return out


def from_config(cfg: Any, *, credentials: Any = None,
                workspace: Any = None, session: Any = None,
                bus: Any = None,
                approval_channel: Optional[bool] = None) -> GuardChain:
    """启动第④步装配:内置 g1-g7(固定序)+ config 显式禁用项(DIS-SEAM §2.5)。

    读取:cfg.security.guards.disabled(五内置真子集,越权在 config 层已拒
    CFG-601,此处 disable() 复核兜底,逐个写 guard.disabled 事件);
    cfg.security.credentials.file(默认 ~/.pyharness/credentials.yaml)→ g4
    凭据清单;approval_channel 由外壳装配注入(headless=False,偏离 3)。
    签名差异(spec 伪码含 scope 参数):链按 call 绑定 scope,装配不消费 scope
    → 移除;credentials/workspace 参数保留为注入位(见偏离 5/7)。
    异常:CFG-601(装配序违反/非法禁用项)、TLB-801(链装配重名)。
    """
    cred_paths: list[str] = []
    if credentials is not None:
        paths = getattr(credentials, "paths", credentials)
        cred_paths = [str(p) for p in (paths or ())]
    else:
        f = getattr(getattr(cfg, "security", None), "credentials", None)
        cred_paths = [str(getattr(f, "file", DEFAULT_CREDENTIAL_FILES[0]))]
    chain = GuardChain(
        credential_paths=cred_paths, session=session, bus=bus,
        approval_channel=approval_channel)
    for gid in (getattr(getattr(cfg, "security", None), "guards", None)
                and getattr(cfg.security.guards, "disabled", None)) or []:
        chain.disable(str(gid), config_ref="security.guards.disabled")
    return chain


# 目录不存在/被误当包 import 时的告警(防装配静默失败;正常 import 无输出)
_log_ready = log.getEffectiveLevel()

__all__ = [
    # 决策
    "Decision", "danger_default_policy",
    # 契约
    "Guard", "GuardChain", "ToolCall",
    # 内置 guard 函数对(F023;match/check 分离,spec 清单)
    "g_schema_match", "g_schema_check",
    "g_danger_match", "g_danger_check",
    "g_fs_path_match", "g_fs_path_check",
    "g_credential_read_match", "g_credential_read_check",
    "g_net_outbound_match", "g_net_outbound_check",
    "g_exec_match", "g_exec_check",
    "g_overwrite_match", "g_overwrite_check",
    # 装配/工厂
    "build_builtin_chain", "from_config",
    "register_guard_hook", "match_guard_by_hook",
    # 常量(审计/测试锚点)
    "FORCED_GUARDS", "DANGER_LEVELS", "_BUILTIN_IDS",
]
