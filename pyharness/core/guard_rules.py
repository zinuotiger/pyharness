"""Guard rules: complete migrated implementation."""
from __future__ import annotations
import os
from typing import Any, Callable, Iterable, Optional
from urllib.parse import urlsplit
from pyharness.errors import PyHError

from pyharness.core.guard_models import DEFAULT_CREDENTIAL_FILES, FS_DOMAINS, NET_DOMAINS, _CRED_BASENAMES, _EXEC_PREFIXES, _READ_PREFIXES, _WRITE_PREFIXES, _danger_of, _is_domain, _policy_of
from pyharness.core.guard_paths import _fs_target, _inside_workspace, _normcase_path, _path_from_args, _resolve_geometry

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
    pol = _policy_of(scope)
    ws = getattr(pol, "workspace_root", None) or ""
    # A2:只读例外目录**仅对读类工具**生效(写类仍一律越界拒);与 provider 同判。
    target, policy = _resolve_geometry(
        _fs_target(call), ws, link_resolver=link_resolver,
        read_only=call.name.startswith(_READ_PREFIXES),
        extra_read_dirs=getattr(pol, "read_extra_dirs", None) or ())
    if policy:
        return ("reject", policy)
    return ("allow", None)

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
    if not raw and call.name == "web.search":
        # web.search 无 url 参数,g5 无法从 args 取目标 → 取装配期记录的后端 host
        # (operator 配置的搜索端点;P3:web.search 恒拒)。空=未配后端 → 仍拒。
        raw = getattr(_policy_of(scope), "search_host", "") or ""
    host = _normalize_domain(raw)
    if not host or host not in {str(d).strip().lower().rstrip(".")
                                for d in allowed}:
        return ("reject", "POL-NET-1")
    return ("allow", None)

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

def g_overwrite_match(call: Any) -> bool:
    """g7 match:写类工具(fs.write_file 等;按动作形态,不信任描述)。"""
    return call.name.startswith(_WRITE_PREFIXES)

async def g_overwrite_check(
        call: Any, scope: Any,
        *, path_exists: Optional[Callable[[str], bool]] = None
) -> tuple[str, Optional[str]]:
    """g7 check:覆写既有文件(含 append 读改写)→ approval(POL-OVW-1)。

    规则:目标不存在(新建)→ allow;目标已存在 → approval(无论 mode=write 整覆
    还是 mode=append 读改写——append 是 read_text()+整体覆写,同为声明外后门,
    SECURITY §4.2 A3);目标探测走注入 path_exists(缺省 os.path.exists,测试可
    替身化),探测失败视同不存在(新建路径)。路径先过 g3 几何,越界轮不到 g7
    (链序保证)。
    """
    args = getattr(call, "safe_args", {}) or {}
    ws = getattr(_policy_of(scope), "workspace_root", None) or ""
    target, policy = _resolve_geometry(_path_from_args(args), ws)
    if policy:
        return ("reject", policy)
    probe = path_exists or os.path.exists
    try:
        exists = bool(probe(target))
    except OSError:
        exists = False                              # 探测异常 → 视同不存在
    if not exists:
        return ("allow", None)                      # 新建路径:无覆写面
    return ("approval", "POL-OVW-1")                # 已存在:write/append 均转审批
