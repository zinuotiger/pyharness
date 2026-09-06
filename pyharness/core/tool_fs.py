"""pyharness/core/tool_fs.py — 文件系统工具族 fs.* (specs/tool_fs.py.md 契约;阶段 3 模块)

功能编号:F034(fs.read_file 文件读取)· F035(fs.write_file 写入与覆盖保护)·
F036(fs.list_dir 目录列举)· fs.delete_file(critical 审计锚点);联动 F023
(g-fs-path/g-overwrite 兜底语义)/F039(超限 spill)/F055(workspace 单点几何语义)。
权威口径:specs/tool_fs.py.md(编码契约,伪代码权威)、PRD-Core §5.4 F034-F036、
SECURITY.md §4.2(危险分级)/§7.2(文件影响域)、CONSTRAINTS-03-ToolSafety
(T-05 五要素/T-09 输出有界)、ERR.md §2.9(TLB-8xx)/§2.11(POL-FS-*)、
PARAMETER-ANCHOR(写文件上限 1MB、spill 单文件 10MB);冲突以 PRD-Core 为准。

职责一句话:文件系统工具族——fs.read_file(UTF-8 自动 BOM、>64KB 转 spill 不进
上下文)、fs.write_file(自动建目录、临时文件+rename 原子写、覆写审批由管道 g7
负责、工具内不二判)、fs.list_dir(隐藏默认隐藏、≤500 条截断留痕)、fs.delete_file
(danger=critical 注册即拒的审计锚点);所有路径先过 resolve_in_workspace 单点归一
(管道 g3 已过之后的纵深第二道,PRD F034 明示"再兜一层")。

本文件是 Provider 侧纵深(阶段 3 自含,INV-08):不 import tools_guard/tools_executor/
approval 内部符号;路径几何判定与 guard 同码同判(POL-FS-1/2/3)但自含实现,阶段 5
若落 F055 共享实现则签名不变、内部改调。Provider 以 async handler(args, ctx) 裸函数
形态绑定(executor 关3 兼容两种 Provider 形态:async handle 直接协程、同步经线程池)。

偏离说明(契约=spec;以下为既有实现冲突/空白处的取舍,均列理由):
1. 配置读取面:spec 伪码写 ctx.cfg["loop.content.file_spill_bytes"] 等键,而
   config.Settings 实为 pydantic 属性链(L1 默认值权威)→ _cfg_get 同时兼容属性链
   与 dict 访问;ctx.cfg 未接线(阶段装配期)→ 回落 L1 默认值并记 debug 日志
   (运行面不因未接线崩溃,阈值有界默认)。
2. spill.put 调用形态与 tools_executor._spill_put(已落地消费者)对齐:
   put(text, kind=kind),不传 ctx——spill.py 尚未落地,executor 侧已是该形态的
   既有调用方,未来 spill 实现须同时服务两消费方;tool 侧按 F034 规格先
   ctx.redact 脱敏再落盘(INV-09,spill 文件为明文出口),不依赖 spill 内部脱敏。
3. workspace 根取数兼容双面:spec resolve 伪码用 ctx.scope.workspace,而 scope 落地
   实现(scope.py 偏离 1)与 guard(g3)统一存 scope.policy.workspace_root → 先读
   ctx.scope.workspace(未来门面挂载面),回落 scope.policy.workspace_root/裸 policy。
   根缺失/未接线 = 装配 bug → CYC-999 fail-closed,绝不回落进程 CWD 锚定。
4. fs.read_file 解码失败(非 UTF-8 文本、样本无 NUL)→ TLB-806 拒读(与"二进制
   拒读"同码族语义,提示换工具);spec 伪码未覆盖该分支(直 decode 会以裸
   UnicodeDecodeError 泄漏为 TLB-805 运行异常),按错误码语义归并。
5. fs.read_file/delete_file 对"目标是目录"显式 TLB-805(spec delete 异常表同款
   先例;read 伪码只查 exists 会让 IsADirectoryError 以 TLB-805 运行异常形态泄漏,
   显式化后提示更可行动,码不变)。
6. fs.list_dir 截断语义取"条目 >500 才 truncated"(常量段/职责 4 权威):伪码
   `len>=500 → truncated=True` 会在恰 500 条时误标截断,落地为追加前检查
   (>500 丢弃多余并留痕,恰 500 完整返回不误标)。
7. fs.list_dir recursive 取"1 层递归"(参数表与注册 description"1 层递归"权威,
   T-07 描述须与执行同谓词),弃用伪码 p.glob("**/*") 的全深度语义——description
   明示 1 层,全深度会与对外描述不符。
8. fs.list_dir glob 参数按伪码实现为**名称前缀匹配**(startswith,注释明示"前缀
   匹配由 agent 扩展");注册 description 的"通配"措辞为 spec 原文照录,不擅改。
9. 遍历中消失的条目(子项 stat 时已被并发删除/断链)→ FileNotFoundError 单条跳过,
   其余 OSError 上抛(TLB-805)——巨目录列举不被单条竞态整体打爆。
10. register() 在 spec 伪码逐条 register_tool(d) 之外补绑 Provider(register_tool
    的 provider 具名参数,同包已支持):Provider handle = 模块级 async handler 裸函数,
    与 executor 关3 契约一致;不补绑则 executor.lookup_provider 报"契约已注册但
    未绑定 Provider",工具不可执行。
"""

from __future__ import annotations

import inspect
import logging
import os
import stat as stat_mod
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from pyharness.core.tools_registry import ToolDefinition  # noqa: F401 五要素字段源
from pyharness.errors import PyHError, raise_code

if TYPE_CHECKING:  # 仅类型标注;registry 鸭子注入(不 import 内部符号)
    from pyharness.core.tools_registry import ToolRegistry

log = logging.getLogger("pyharness.tool_fs")

# ------------------------------------------------------------------ 常量
READ_SPILL_BYTES: int = 65536
"""读阈值默认(loop.content.file_spill_bytes L1,1024-1048576 可配)。"""

WRITE_MAX_BYTES: int = 1_048_576
"""写文件上限 1MB(schema maxLength 契约层拒,TLB-803 零执行,F035/PARAMETER-ANCHOR)。"""

LIST_MAX_ENTRIES: int = 500
"""目录列举条目上限(F036;>500 截断留痕,防巨目录打爆上下文)。"""

BINARY_SAMPLE: int = 8192
"""二进制探测样本字节数(NUL 字节命中即拒读 TLB-806)。"""

SPILL_MAX_FILE_BYTES: int = 10_485_760
"""spill 单文件上限默认(storage.spill.max_per_file_bytes L1 = 10MB,超限 PERS-223)。"""

_CRED_BASENAMES: tuple[str, ...] = ("credentials.yaml", "credentials.yml")
"""凭据型文件名(g4 同源纵深:任何位置命中该形态 = 凭据文件,拒读 POL-CRED-1)。"""

_DEFAULT_CRED_FILE: str = "~/.pyharness/credentials.yaml"
"""凭据文件兜底清单项(config security.credentials.file L1 同源)。"""

_DOT_HIDDEN: tuple[str, ...] = (".", "..")
"""目录列举隐藏过滤锚(点开头即隐藏;iterdir 不含 . / ..,防御性保留)。"""


# ---------------------------------------------------------------- 配置辅助
def _cfg_get(ctx: Any, dotted: str, default: Any) -> Any:
    """读 ctx.cfg 点分键(偏离 1:属性链与 dict 访问双兼容;未接线/缺键回落默认)。"""
    cfg = getattr(ctx, "cfg", None)
    if cfg is None:
        log.debug("ctx.cfg 未接线,配置键 %s 回落默认 %r", dotted, default)
        return default
    cur: Any = cfg
    for part in dotted.split("."):
        if isinstance(cur, dict):
            if part not in cur:
                return default
            cur = cur[part]
        else:
            nxt = getattr(cur, part, None)
            if nxt is None:
                return default
            cur = nxt
    return cur if cur is not None else default


def _read_spill_bytes(ctx: Any) -> int:
    """fs.read_file 转 spill 阈值(loop.content.file_spill_bytes,默认 64KB)。"""
    return int(_cfg_get(ctx, "loop.content.file_spill_bytes", READ_SPILL_BYTES))


def _spill_max_bytes(ctx: Any) -> int:
    """spill 单文件上限(storage.spill.max_per_file_bytes,默认 10MB)。"""
    return int(_cfg_get(ctx, "storage.spill.max_per_file_bytes",
                        SPILL_MAX_FILE_BYTES))


def _extra_read_dirs(ctx: Any) -> list[str]:
    """只读例外目录清单(security.policy.read_extra_dirs;config+guard 留痕授权)。"""
    v = _cfg_get(ctx, "security.policy.read_extra_dirs", None)
    if v is None:
        return []
    if isinstance(v, str):                       # 宽松兼容:单串配置
        return [v] if v.strip() else []
    return [str(x) for x in v if str(x).strip()]


def _redact(ctx: Any, text: str) -> str:
    """出口脱敏(ctx.redact 注入面;未接线/失败 → 原样,读路径不因脱敏中断)。"""
    fn = getattr(ctx, "redact", None)
    if not callable(fn):
        return text
    try:
        return fn(text)
    except Exception as exc:                     # noqa: BLE001 脱敏故障不阻断读取
        log.warning("ctx.redact 执行失败,原样返回: %s", exc)
        return text


# ------------------------------------------------------------ 路径几何第二道闸
def _workspace_root(ctx: Any) -> Path:
    """workspace 唯一根(偏离 3:ctx.scope.workspace → policy.workspace_root 回落)。

    根缺失/空 = 装配 bug:fail-closed CYC-999(绝不回落进程 CWD 作锚定根)。
    """
    scope = getattr(ctx, "scope", None)
    if scope is None:
        raise_code("CYC-999", module="tool_fs",
                   hint="ctx.scope 未接线:workspace 根缺失,拒绝路径解析(fail-closed)")
    ws = getattr(scope, "workspace", None)
    if ws is None:
        policy = getattr(scope, "policy", None) or scope
        ws = getattr(policy, "workspace_root", None)
    if not ws:
        raise_code("CYC-999", module="tool_fs",
                   hint="workspace 根为空/未接线(ctx.scope.workspace 或 "
                        "scope.policy.workspace_root),拒绝路径解析(fail-closed)")
    return Path(os.path.expanduser(str(ws))).resolve()


def _under(p: Path, root: Path) -> bool:
    """p 是否几何落在 root 内(含根本身;normcase 后带分隔符边界前缀比较)。

    同 guard._inside_workspace 语义(大小写/分隔符归一,Windows 同源比较)。
    """
    rs = os.path.normcase(str(root))
    ps = os.path.normcase(str(p))
    if ps == rs:
        return True
    return ps.startswith(rs.rstrip("\\/") + os.sep)


def _in_extra_read(real: Path, ctx: Any) -> bool:
    """只读例外目录命中判定(real 最终落点 ∈ 任一 read_extra_dirs 解析根内)。"""
    for d in _extra_read_dirs(ctx):
        try:
            base = Path(os.path.expanduser(str(d))).resolve()
        except OSError:
            continue                            # 例外目录本身不可解析 → 跳过
        if _under(real, base):
            return True
    return False


def resolve_in_workspace(raw: str, ctx: Any, *, writable: bool = False) -> Path:
    """路径单点归一(第二道几何闸,F034/F055 语义;模块内私有不注册为工具)。

    三道拒(与管道 g3 同码同判,防 TOCTOU 与内层直调,INV-04):
      POL-FS-1 绝对路径词法越界(不在 workspace 内);
      POL-FS-2 规范化后含 ".." 逃逸(相对路径归一后越界);
      POL-FS-3 symlink/junction 最终解析越界(writable=True 无只读例外;
              writable=False 允许 security.policy.read_extra_dirs 只读例外)。
    失败一律 GRD-401(reason=POL-FS-x)结构化上抛,零副作用。
    """
    root = _workspace_root(ctx)
    p = Path(str(raw)).expanduser()
    if p.is_absolute():
        if not _under(p, root):                # POL-FS-1:绝对路径越界
            raise_code("GRD-401", reason="POL-FS-1", path=str(raw),
                       advice="只允许 workspace 内路径(绝对路径越界)")
        cand = p
    else:
        cand = root / p                          # 相对路径锚定 workspace 根
    norm = Path(os.path.normpath(cand))
    if not _under(norm, root):                   # POL-FS-2:".." 规范化逃逸
        raise_code("GRD-401", reason="POL-FS-2", path=str(raw),
                   advice="路径含 .. 越界")
    try:
        real = norm.resolve()                    # 解开 symlink/junction
    except OSError:
        raise_code("GRD-401", reason="POL-FS-3", path=str(raw),
                   advice="符号链接解析失败,按越界拒绝(无法安全归一)")
    inside = _under(real, root)
    if not inside and not (not writable and _in_extra_read(real, ctx)):
        raise_code("GRD-401", reason="POL-FS-3", path=str(raw),
                   advice="符号链接指向 workspace 外(只读例外目录亦未命中)")
    return real


# ------------------------------------------------------------ 凭据纵深兜底
def _reject_credential(p: Path, ctx: Any) -> None:
    """读凭据文件纵深拦截(与 g4 同码 POL-CRED-1;防内层直调绕过管道)。

    命中口径双轨(g4 同源):①规范化绝对路径 ∈ 凭据文件清单(默认
    ~/.pyharness/credentials.yaml);②文件形态名(credentials.yaml/yml,
    凭据文件拷进 workspace 任何位置都拦)。
    """
    raw_paths = _cfg_get(ctx, "security.credentials.file", None)
    if isinstance(raw_paths, str):
        cred_paths: list[str] = [raw_paths] if raw_paths.strip() else []
    elif raw_paths:
        cred_paths = [str(x) for x in raw_paths if str(x).strip()]
    else:
        cred_paths = [_DEFAULT_CRED_FILE]
    np_target = os.path.normcase(str(p))
    for c in cred_paths:
        norm_c = os.path.normcase(os.path.abspath(os.path.expanduser(str(c))))
        if np_target == norm_c:                  # 命中清单精确路径
            raise_code("GRD-401", reason="POL-CRED-1", path=str(p),
                       advice="凭据文件禁止经工具读取(F016 单口)")
    base = os.path.basename(np_target).lower()
    if base in _CRED_BASENAMES:                  # 凭据文件形态(名字即证据)
        raise_code("GRD-401", reason="POL-CRED-1", path=str(p),
                   advice="凭据文件禁止经工具读取(形态名命中,含 workspace 内副本)")


# ------------------------------------------------------------ 溢出落盘依赖
async def _spill_put(ctx: Any, text: str, kind: str) -> dict:
    """ctx.storage.spill.put 封装(偏离 2:与 executor._spill_put 调用形态一致)。

    storage.spill 未接线 → PERS-221 结构化上抛(超长输出无法归档,不静默);
    put 返回须为 SpillMeta dict(F039 契约),否则 PERS-221。
    """
    spill = getattr(getattr(ctx, "storage", None), "spill", None)
    if spill is None:
        raise_code("PERS-221", module="tool_fs",
                   hint="storage.spill 未接线(能力未激活),超限内容无法归档")
    try:
        r = spill.put(text, kind=kind)
        if inspect.isawaitable(r):
            r = await r
    except PyHError:
        raise
    except Exception as exc:                     # noqa: BLE001 落盘故障 → PERS-221
        raise_code("PERS-221", module="tool_fs", cause=exc,
                   advice="spill 写失败,查 spill 目录权限与磁盘空间")
    if not isinstance(r, dict):
        raise_code("PERS-221", module="tool_fs",
                   hint="storage.spill.put 返回非 dict(契约违约)")
    return r


# ================================================================ fs.read_file
def _looks_binary(data: bytes) -> bool:
    """二进制探测:NUL 字节样本命中(BINARY_SAMPLE=8192 头样本)。"""
    return b"\x00" in data[:BINARY_SAMPLE]


async def read_file(args: dict, ctx: Any) -> dict:
    """fs.read_file(F034):读 workspace 内文本文件(UTF-8 自动 BOM)。

    流程:第二道几何闸 → 凭据纵深 → stat 判存在/目录/大小(>10MB spill 上限
    PERS-223 拒)→ 整读(≤10MB)→ 二进制探测拒读 TLB-806 → utf-8-sig 解码;
    内容 ≤64KB(loop.content.file_spill_bytes)全量返回;>64KB 经 ctx.storage.
    spill.put 落盘只返回摘要+引用,全文不进上下文/历史(输出有界 T-09)。
    """
    p = resolve_in_workspace(args["path"], ctx, writable=False)
    _reject_credential(p, ctx)                   # g4 同码兜底(POL-CRED-1)
    try:
        st = p.stat()
    except FileNotFoundError:
        raise_code("TLB-802", path=str(p),
                   advice="文件不存在,先 list_dir 自查路径拼写")
    except OSError as e:
        raise_code("TLB-805", path=str(p), cause=e,
                   advice="文件不可访问(权限/IO),请重试")
    if stat_mod.S_ISDIR(st.st_mode):
        raise_code("TLB-805", path=str(p),
                   advice="目标是目录,本工具只读文件,先 list_dir 侦查")
    if st.st_size > _spill_max_bytes(ctx):       # 预检:超 spill 单文件上限
        raise_code("PERS-223", path=str(p), size=st.st_size,
                   advice="文件超过 spill 上限,换 F052 分块或缩小目标")
    try:
        data = p.read_bytes()                    # ≤10MB 才整读
    except OSError as e:
        raise_code("TLB-805", path=str(p), cause=e,
                   advice="文件读取失败(权限/IO),请重试")
    if _looks_binary(data):
        raise_code("TLB-806", path=str(p),
                   advice="二进制文件,不能用文本读取(换专门工具)")
    try:
        text = data.decode("utf-8-sig")          # 自动 BOM
    except UnicodeDecodeError:
        raise_code("TLB-806", path=str(p),       # 偏离 4:非 UTF-8 拒读
                   advice="非 UTF-8 文本(解码失败),不能用文本读取")
    if len(data) <= _read_spill_bytes(ctx):      # 小文件全量返回
        return {"content": _redact(ctx, text), "truncated": False,
                "bytes": len(data), "path": str(p)}
    redacted = _redact(ctx, text)                # 出口脱敏后再落盘(INV-09)
    meta = await _spill_put(ctx, redacted, kind="read")
    return {"content": str(meta.get("preview", ""))[:500],
            "truncated": True, "spill_ref": meta,
            "bytes": len(data), "path": str(p)}   # 大文件只给摘要+引用


# =============================================================== fs.write_file
async def write_file(args: dict, ctx: Any) -> dict:
    """fs.write_file(F035):新建/追加文本到 workspace 内,自动建目录,原子写。

    原子写:同目录临时文件写毕 → os.replace 原子替换(崩溃不留半文件);
    失败清临时文件后原样上抛(executor 收敛 TLB-805)。覆写审批由管道 g7
    在 Provider 前完成(工具内不二判、不降级);content >1MB 已在契约层
    (schema maxLength)拒,零执行(TLB-803,INV-06)。
    """
    p = resolve_in_workspace(args["path"], ctx, writable=True)  # 写:只认 workspace
    content = args["content"]                    # 契约已验 ≤1MB
    if args.get("mode", "write") == "append" and p.exists():
        try:
            content = p.read_text(encoding="utf-8") + content  # append=复制+追加
        except OSError as e:
            raise_code("TLB-805", path=str(p), cause=e,
                       advice="追加读取原文件失败(权限/IO),请重试")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)   # 自动建目录
    except OSError as e:
        raise_code("TLB-805", path=str(p.parent), cause=e,
                   advice="父目录创建失败(权限/IO),请重试")
    tmp = p.with_name(p.name + f".tmp-{uuid.uuid4().hex[:8]}")  # 同目录临时文件
    try:
        tmp.write_text(content, encoding="utf-8", newline="")   # 防换行改写
        os.replace(tmp, p)                       # 原子替换
    except BaseException:
        tmp.unlink(missing_ok=True)              # 失败清临时文件
        raise
    return {"bytes": len(content.encode("utf-8")), "path": str(p)}


# =============================================================== fs.list_dir
async def list_dir(args: dict, ctx: Any) -> dict:
    """fs.list_dir(F036):列 workspace 内目录条目(名称/类型/大小/修改时间)。

    隐藏文件默认隐藏(all=True 显示);glob 为名称前缀过滤(伪码 startswith,偏离 8);
    recursive=1 层递归(偏离 7);条目 >500 截断留痕 truncated=True(偏离 6),
    防巨目录打爆上下文(输出有界 T-09)。
    """
    p = resolve_in_workspace(args.get("path") or ".", ctx, writable=False)
    if not p.is_dir():
        raise_code("TLB-802", path=str(p),
                   advice="目录不存在或路径不是目录,先 list_dir 父目录侦查")
    show_all = bool(args.get("all"))
    recursive = bool(args.get("recursive"))
    gpat = args.get("glob")

    def _visible(name: str) -> bool:
        """隐藏过滤(点开头即隐藏;all=True 显示)。"""
        return show_all or not name.startswith(".")

    cands: list[Path] = []
    try:
        top = [c for c in p.iterdir() if _visible(c.name)]
        cands = list(top)
        if recursive:                            # 1 层递归:仅直接子目录的条目
            for d in top:
                if d.is_dir():
                    cands.extend(c for c in d.iterdir() if _visible(c.name))
    except OSError as e:
        raise_code("TLB-805", path=str(p), cause=e,
                   advice="目录列举失败(权限/IO),请重试")
    cands.sort(key=lambda c: c.name)             # 稳定排序(确定性输出)

    entries: list[dict] = []
    truncated = False
    for child in cands:
        if gpat and not child.name.startswith(gpat):
            continue                             # 通配过滤(前缀匹配,偏离 8)
        if len(entries) >= LIST_MAX_ENTRIES:     # >500 截断留痕(偏离 6)
            truncated = True
            break
        try:
            st = child.stat()
        except FileNotFoundError:
            continue                             # 竞态消失/断链:单条跳过(偏离 9)
        except OSError as e:
            raise_code("TLB-805", path=str(p), cause=e,
                       advice="目录条目读取失败(权限/IO),请重试")
        entries.append({
            "name": child.name,
            "type": "dir" if child.is_dir() else "file",
            "size": st.st_size if child.is_file() else None,   # size 仅文件
            "modified": datetime.fromtimestamp(st.st_mtime)
            .isoformat(timespec="seconds"),                    # ISO 8601 mtime
        })
    return {"entries": entries, "truncated": truncated}


# ============================================================= fs.delete_file
async def delete_file(args: dict, ctx: Any) -> dict:
    """fs.delete_file(critical 锚点,PRD §6.3/T-SEC)。

    注册为 danger=critical——管道 g-danger 对 critical 直接拒绝、不可审批
    (POL-DGR-1),正常路径 Provider 恒零执行(审计样本:"拦了且没执行")。
    本函数体是真实删除实现,仅策略被显式下调或测试直调 Provider 时才可达;
    即便下调也守第二道几何闸(writable=True 只认 workspace 根)。
    """
    p = resolve_in_workspace(args["path"], ctx, writable=True)
    if not p.exists():
        raise_code("TLB-802", path=str(p),
                   advice="文件不存在,先 list_dir 确认")
    if p.is_dir():
        raise_code("TLB-805", path=str(p),
                   advice="本工具只删文件;目录删除不在工具面")
    try:
        p.unlink()                               # 真实副作用仅策略下调后发生
    except OSError as e:
        raise_code("TLB-805", path=str(p), cause=e,
                   advice="删除失败(权限/IO),请重试")
    return {"deleted": str(p)}


# ============================================================ 五要素注册入口
PROVIDERS: dict[str, Any] = {
    "fs.read_file": read_file,
    "fs.write_file": write_file,
    "fs.list_dir": list_dir,
    "fs.delete_file": delete_file,
}
"""Provider handle 映射(裸 async handler,executor 关3 契约:handle(args, ctx))。"""


def register(registry: "ToolRegistry") -> list[str]:
    """五要素注册入口(F008/T-05):装配 4 个 ToolDefinition 并逐个登记。

    ToolDefinition(name/description/schema/danger/handler 五要素齐备,owner=
    "builtin");schema 编译与 description 防注入检查(T-07)由 registry 层完成;
    重复注册由 registry 拒(TLB-801)。偏离 10:register_tool 补绑 Provider
    (register_tool(defn, provider=…)),否则 executor 查不到实现。
    """
    defs = [
        ToolDefinition(name="fs.read_file", danger="low",
                       description="读 workspace 内文本文件(UTF-8 自动 BOM);"
                                   "超过 64KB 自动转 spill 引用",
                       schema={"type": "object",
                               "properties": {"path": {"type": "string"}},
                               "required": ["path"],
                               "additionalProperties": False},
                       owner="builtin", timeout_s=30),
        ToolDefinition(name="fs.write_file", danger="low",
                       description="新建或追加文本到 workspace 内文件;"
                                   "自动建目录;原子写入",
                       schema={"type": "object",
                               "properties": {
                                   "path": {"type": "string"},
                                   "content": {"type": "string",
                                               "maxLength": WRITE_MAX_BYTES},
                                   "mode": {"enum": ["write", "append"]}},
                               "required": ["path", "content"],
                               "additionalProperties": False},
                       owner="builtin", timeout_s=30),
        ToolDefinition(name="fs.list_dir", danger="none",
                       description="列 workspace 内目录条目(名称/类型/大小/"
                                   "修改时间),支持通配与 1 层递归",
                       schema={"type": "object",
                               "properties": {"path": {"type": "string"},
                                              "glob": {"type": "string"},
                                              "recursive": {"type": "boolean"},
                                              "all": {"type": "boolean"}},
                               "required": [], "additionalProperties": False},
                       owner="builtin", timeout_s=15),
        ToolDefinition(name="fs.delete_file", danger="critical",
                       description="删除 workspace 内文件(默认恒拒,"
                                   "仅审计演示锚点)",
                       schema={"type": "object",
                               "properties": {"path": {"type": "string"}},
                               "required": ["path"],
                               "additionalProperties": False},
                       owner="builtin", timeout_s=15),
    ]
    for d in defs:                               # 逐个登记并绑定 Provider
        registry.register_tool(d, provider=PROVIDERS.get(d.name))
    return [d.name for d in defs]                # 注册名清单


__all__ = [
    "resolve_in_workspace", "read_file", "write_file", "list_dir",
    "delete_file", "register", "PROVIDERS",
    "READ_SPILL_BYTES", "WRITE_MAX_BYTES", "LIST_MAX_ENTRIES", "BINARY_SAMPLE",
]
