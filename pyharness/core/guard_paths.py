"""Guard paths: complete migrated implementation."""
from __future__ import annotations
import os
import stat
from typing import Any, Callable, Optional


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
    def is_link(path: str) -> bool:
        if os.path.islink(path):
            return True
        junction = getattr(os.path, "isjunction", None)
        if junction is not None:
            return junction(path)
        if os.name == "nt":
            # isjunction was added in Python 3.12. Windows 3.11 still exposes
            # the exact junction reparse tag through lstat; do not skip FS-3.
            try:
                return os.lstat(path).st_reparse_tag == stat.IO_REPARSE_TAG_MOUNT_POINT
            except (OSError, ValueError):
                return False
        return False

    cur = os.path.normpath(p)
    if is_link(cur):
        return True
    parent = os.path.dirname(cur)
    guard = 0
    while parent and parent != cur and guard < 64:  # 向上找现存链点
        if is_link(parent):
            return True
        nxt = os.path.dirname(parent)
        if nxt == parent:
            break
        cur, parent, guard = parent, nxt, guard + 1
    return False

def _in_read_extra(target: str, extra_read_dirs: Any) -> bool:
    """target 是否落在**只读例外目录**内(A2;两层同判口径)。

    例外目录条目按 `expanduser` + `abspath`(+ 尽力 `realpath`)归一后比较;
    不可解析的条目跳过(不放大)。**只判包含**:是否"允许"由调用方按**读写**决定。
    """
    for entry in (extra_read_dirs or ()):
        raw = str(entry or "").strip()
        if not raw:
            continue
        try:
            base = os.path.abspath(os.path.expanduser(raw))
            real = os.path.realpath(base)
        except (OSError, ValueError):
            continue
        if _inside_workspace(target, base) or _inside_workspace(target, real):
            return True
    return False

def _resolve_geometry(raw: Any, workspace_root: str,
                      *, link_resolver: Optional[Callable[[str], str]] = None,
                      read_only: bool = False,
                      extra_read_dirs: Any = ()
                      ) -> tuple[str, Optional[str]]:
    """路径几何单点判定(g3 权威语义;F055 单点 guard 侧实现)。

    返回 (规范绝对路径, policy_ref);policy_ref 非空 = 越界(调用方须拒):
        POL-FS-1 绝对路径越界(词法不在 workspace)
        POL-FS-2 ".." 段逃逸(规范化后越界)
        POL-FS-3 symlink/junction 终解析越界(词法在界内,真实在界外)
    全过返回 (target, None)。纯只读:零 stat 之外副作用(os.path 探测)。

    ``read_only`` + ``extra_read_dirs``(**A2/R24**):``security.policy.read_extra_dirs``
    声明的例外目录**只对读类工具**放行(写类一律仍越界拒)。此前例外在两层都被忽略
    ⇒ 配置了仍被拒(L-24)。此处与 provider ``resolve_in_workspace`` **同判**。
    """
    target = _norm_target(raw, workspace_root)
    ws = os.path.abspath(os.path.expanduser(workspace_root))
    exempt = bool(read_only) and _in_read_extra(target, extra_read_dirs)
    # 1) POL-FS-1:绝对路径词法越界(不 startswith workspace)
    if os.path.isabs(str(raw).strip()) and not _inside_workspace(target, ws)             and not exempt:
        return (target, "POL-FS-1")
    # 2) POL-FS-2:".." 段逃逸(规范化后不在 workspace)
    if _has_dotdot(str(raw)) and not _inside_workspace(target, ws)             and not exempt:
        return (target, "POL-FS-2")
    # 3) POL-FS-3:symlink/junction 终解析越界(链点存在才查,防误伤普通路径)
    if _is_linkish(target):
        final = (link_resolver or _final_realpath)(target)
        if not _inside_workspace(final, ws) and not exempt:
            return (target, "POL-FS-3")
    return (target, None)
