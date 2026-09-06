"""pyharness/core/spill.py — 输出溢出 spill 基础设施(specs/spill.py.md 契约;F039)

功能编号:F039(输出溢出)· 联动 F034(读文件超 64KB)/F038(抓取超 32KB)/
F052(子进程输出)/tools_executor 关4(结果 finalize)/F060(repair 读窗口)。
权威口径:specs/spill.py.md(编码契约)、DIS-SEAM §6.1(seam A Provider 接口/
Consumer/生命周期)、CFG.md §3.6(storage.spill_dir=~/.pyharness/spill、
storage.spill.max_per_file_bytes=10MB)、EVENT-SCHEMA §2/§3.4.6
(tool.result.spill_ref={ref,chars,lines,preview}),ERR.md §2.3(PERS-221/222/223)。

一句话:工具输出溢出基础设施(基础设施型 seam,Consumer=脊柱结果检查器,
LLM 不直调 put)——超限原始大文本落**会话私有 spill 区(workspace 外、
权限 600)**,上下文只放 ≤2KB 摘要 + spill_ref 引用;模型要全文经 LLM 可见的
读工具 storage.spill 按行取回,已读量计入 ctx.counters(spill_read_chars)
防循环烧预算;**截断不吞事实,溢出部分可查**。

核心语义(spec 逐条兑现):
1. put:Consumer(tools_executor 关4)输出 >2KB → put(text, kind) 写入会话
   私有区 spill_dir/<session_id>/spill-<8hex>.txt(临时文件+rename 原子
   落盘,坏行不落地);返回 {spilled, ref, chars, lines, preview(头500字符)}
   ——与 EVENT-SCHEMA spill_ref 字段级一致,大文本不进上下文/事件/日志。
2. 上限与摘要固定:spill 单文件 ≤10MB(storage.spill.max_per_file_bytes,
   默认 10485760),超限 PERS-223 拒写回喂;摘要 = 头 500 字符 + 行数 + 大小
   (固定三要素);chars=len(text)、lines=text.count("\\n")+1。
3. read:LLM 经读工具按行取回(ref/start/limit,默认 0/200),返回 content+
   more;ref 解析强制会话私有区 containment——绝对路径/`..`/符号链接逃逸/
   跨会话引用一律 PERS-222 **零读取**(同 call 无部分返回)。
4. 防循环烧预算:每次 read 的返回字符数累入 ctx.counters 的
   spill_read_chars 键,预算/轮数闸可据此终止"读全文-再读"死循环。
5. 生命周期:enter=会话首访惰性建目录(权限 600,Windows ACL 尽力而为,主防线
   =位置与路径 containment);detach=幂等摘除(摘读工具、摘定位器、摘订阅,
   **文件不删**——留 F060 归档/repair 读取窗口);purge_session 订阅
   session.finished 删除本会话 spill 目录(删除前 containment 校验、目录
   不存在幂等)。
6. 出口脱敏(INV-09):put 落盘前对 text 执行 ctx.redact(F016 全出口脱敏)
   ——spill 文件是 SECURITY §4 明文列出的出口之一。
7. LLM 可见面最小化:LLM 只见"读"工具(storage.spill);put 是 Provider 内部
   方法,不进工具表(防模型拿 put 当存储滥写)。

偏离说明(契约=spec 伪代码;以下为与既有实现冲突/空白处的取舍,均列理由):
1. ctx.cfg 兼容读取:spec 伪码用 ctx.cfg["storage.spill_dir"] 点分 dict 读法,
   而本项目 config.py 落地为 pydantic Settings(属性形态,agent.Ctx 挂
   ctx.config)→ 内部 _cfg() 双形态兼容:dict(点分键/嵌套)与属性链都收,
   契约面(键名/默认值)不变。
2. containment 补强为"会话级":spec 模块职责 3 明示"本会话 ref 只指本会话
   spill 文件(防跨会话越权)",但伪码 _resolve_ref 只校验"首段=所在目录名"
   (根内即可,未锁当前会话)→ 补当前会话 id 相等比较(ctx 无会话 id 的
   场景——如 F060 repair 直读——回落伪码口径,不误伤);行为多拒不误放。
3. read 行切分与 put 行数口径对齐:伪码 read 用 splitlines(),而 put 的
   lines=text.count("\\n")+1(尾随换行会产生第 N+1 个空行,splitlines 会丢
   尾空行致 roundtrip 行数不一致)→ read 用 split("\\n") 与 put 口径严格
   同源,取回的 content 以 "\\n" 拼回即原文。
4. Windows 路径与权限:containment 比对用 Path.relative_to(Windows 路径
   比较大小写不敏感,字符串 startswith 会误伤大小写归一);chmod(0o600) 在
   Windows 上尽力而为(底层 ACL 不由 Python 管),主防线=目录位置+ref 路径
   containment;ref 内 "\\" 与 "/" 统一由 Path 解析(WindowsPath 双分隔符)。
5. 删除残留 .tmp:purge 除 spill-*.txt 外顺带清 spill-*.tmp(中断写入残留),
   否则 rmdir 收口会因残留失败(伪码只删 spill-*.txt)。
6. register 落 ToolDefinition 实契约:伪码 defn 无 owner 字段,本项目
   tools_registry.py T-05 五要素要求 owner 必填(能力 id)→ 补 owner="spine"
   (DIS-SEAM §6.1 ② 同值);expose_to_llm 是能力层标志不属 Definition,
   由装配层 announce 消费(agent.py 先例)。
7. put 的 kind 参数按 spec 参数表保留为来源标注(入错误现场与审计扩展点),
   文件名暂不含 kind(spec 伪码文件名 = spill-<8hex>.txt 权威,"可扩展"不
   是必须)。
8. ctx.log 缺失容错:spec 伪码多处 ctx.log.warning,本项目 Ctx 无 log 属性
   → 内部统一 _log_warn()(有 ctx.log 用之,否则模块 logger),行为不变。
9. 会话总量 100MB(storage.spill.max_per_session_mb,N3)闸:spec 函数清单
   与伪码均未实现(只锁单文件 10MB),Consumer 无调用面 → 本文件同样不实现
   (装配层/会话级配额后续功能落地时补,列理由不欠账)。
10. put 返回 ref 用 d.name 而非伪码的 d.parent.name:spec 伪码 enter 返回的
    d = spill_dir/<session_id>,d.parent.name = spill 根目录名;按 spec 依赖
    节"目录布局"行(ref 恒为相对 `<session_id>/spill-xxxx.txt`)与 EVENT-
    SCHEMA 语义,ref 首段必须是会话 id → 取 d.name(伪码此处为笔误,布局表
    权威)。

依赖方向(INV-08):本文件 → errors(raise_code)、config(仅 F016 脱敏函数
兜底 import)、tools_registry(ToolDefinition 登记);不 import tools_executor
内部符号(Consumer 反向经 ctx.storage.spill 注入)。外部:pathlib/os/uuid。
"""
from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import Any, Optional

from pyharness.errors import PyHError, raise_code

log = logging.getLogger("pyharness.spill")

# ------------------------------------------------------------------ 常量
SUMMARY_PREVIEW: int = 500          # 摘要/引用 preview = 头 500 字符(固定)
DEFAULT_MAX_PER_FILE: int = 10_485_760   # 默认 10MB(storage.spill.max_per_file_bytes)
DEFAULT_LIMIT: int = 200            # read 默认行数(1-1000)
MAX_READ_LIMIT: int = 1000          # read 单次上限行(schema 同步锁死)
MODE_PRIVATE: int = 0o600           # 私有目录/文件权限(spill 区 600)
TOOL_NAME: str = "storage.spill"    # LLM 可见读工具名(模块职责 7:只读面)
COUNTER_KEY: str = "spill_read_chars"  # 已读量计数键(防循环烧预算,F039 边界)
SPILL_PATTERN: str = "spill-*.txt"  # spill 文件 glob(清理/审计用)
FILE_PREFIX: str = "spill-"         # 文件名前缀(事件可 grep)

# PERS-222 回喂文案(spec 异常表;经 raise_code 的 advice 落 tool.error)
_REFUSED_ADVICE: str = "spill 引用越界,已拒绝读取;只允许本会话私有区"
# PERS-223 回喂文案(超限拒写)
_TOO_BIG_ADVICE: str = "输出超过 10MB spill 上限;上游截断或分块"
# PERS-221 回喂文案(写失败)
_WRITE_FAIL_ADVICE: str = "查 spill 目录权限(600)与磁盘空间(F039)"


# ------------------------------------------------------------------ 配置/上下文辅助
def _cfg_holder(ctx: Any) -> Any:
    """取配置持有者:spec 伪码经 ctx.cfg;本项目装配(Ctx)为 ctx.config(偏离 1)。

    两种命名都收;都没有 → None(调用方按各自错误路径处理)。
    """
    for name in ("cfg", "config"):
        holder = getattr(ctx, name, None)
        if holder is not None:
            return holder
    return None


def _cfg(holder: Any, dotted: str, default: Any = None) -> Any:
    """点分键取值,双形态兼容:dict(点分整键或逐层嵌套)与属性链(pydantic)。"""
    if holder is None:
        return default
    parts = dotted.split(".")
    if isinstance(holder, dict):
        if dotted in holder:                    # 平铺点分键形态
            return holder[dotted]
        cur: Any = holder
        for p in parts:                         # 嵌套 dict 形态
            if not isinstance(cur, dict) or p not in cur:
                return default
            cur = cur[p]
        return cur
    cur = holder                                # pydantic/属性形态
    for p in parts:
        cur = getattr(cur, p, None)
        if cur is None:
            return default
    return cur


def _spill_root(ctx: Any) -> Optional[Path]:
    """spill 根目录(expanduser 后 Path);配置缺失 → None(调用方定错误码)。"""
    raw = _cfg(_cfg_holder(ctx), "storage.spill_dir", None)
    if raw is None or not str(raw).strip():
        return None
    return Path(str(raw)).expanduser()


def _session_id(ctx: Any) -> Optional[str]:
    """会话 id 获取:ctx.session_id 直挂 → ctx.session.{session_id|sid}。"""
    sid = getattr(ctx, "session_id", None)
    if sid:
        return str(sid)
    sess = getattr(ctx, "session", None)
    if sess is not None:
        for attr in ("session_id", "sid"):
            v = getattr(sess, attr, None)
            if v:
                return str(v)
    return None


def _max_per_file(ctx: Any) -> int:
    """单文件上限:storage.spill.max_per_file_bytes(默认 10485760,CFG §3.6)。"""
    v = _cfg(_cfg_holder(ctx), "storage.spill.max_per_file_bytes",
             DEFAULT_MAX_PER_FILE)
    try:
        return int(v)
    except (TypeError, ValueError):
        return DEFAULT_MAX_PER_FILE


def _redact(ctx: Any, text: str) -> str:
    """出口脱敏(INV-09):优先 ctx.redact(F016 单口);否则回落 config.redact。

    ctx.redact 为装配层注入的脱敏单口(已含 log.redact_enabled 开关语义);
    回落路径尊重 log.redact_enabled(默认 true),关则原文直写(配置层承诺的
    出口策略,见 SECURITY §6)。
    """
    fn = getattr(ctx, "redact", None)
    if callable(fn):
        try:
            return fn(text)
        except Exception:                       # noqa: BLE001 脱敏自身失败不吞事实
            log.warning("spill ctx.redact 异常,回落 config.redact")
    holder = _cfg_holder(ctx)
    if _cfg(holder, "log.redact_enabled", True) is False:
        return text                             # 显式关闭全出口脱敏
    try:
        from pyharness.config import redact as _module_redact
        return _module_redact(text)
    except Exception:                           # noqa: BLE001 兜底:原文直写
        log.warning("spill 脱敏不可用,原文落盘(检查 config.redact)")
        return text


def _bump_counter(ctx: Any, key: str, delta: int) -> None:
    """已读量入计数器(防循环烧预算):支持 dict / bump(key, n) / 属性三形态。

    ctx.counters 未接线时静默跳过(log debug)——读数不受影响,仅防循环闸
    少一个数据源(预算/轮数闸另有兜底)。
    """
    c = getattr(ctx, "counters", None)
    if c is None:
        return
    if isinstance(c, dict):                     # dict 形态(单测/装配可直挂)
        c[key] = c.get(key, 0) + delta
        return
    bump = getattr(c, "bump", None)
    if callable(bump):
        try:
            bump(key, delta)
            return
        except Exception:                       # noqa: BLE001 降级到属性形态
            pass
    try:                                        # 属性形态(UsageCounters 风格)
        setattr(c, key, int(getattr(c, key, 0)) + delta)
    except Exception:                           # noqa: BLE001 均不可用:忽略
        log.debug("spill 计数器形态不支持 bump,key=%s", key)


def _log_warn(ctx: Any, msg: str, *args: Any) -> None:
    """告警出口:ctx.log.warning 优先,缺失回落模块 logger(偏离 8)。"""
    clog = getattr(ctx, "log", None)
    if clog is not None and callable(getattr(clog, "warning", None)):
        try:
            clog.warning(msg, *args)
            return
        except Exception:                       # noqa: BLE001
            pass
    log.warning(msg, *args)


async def _maybe_await(r: Any) -> Any:
    """异步上下文里的 await-if-awaitable(executor._spill_put 同款处理)。"""
    import inspect
    if inspect.isawaitable(r):
        return await r
    return r


# ------------------------------------------------------------------ enter
async def enter(ctx: Any) -> Path:
    """会话首访建私有目录(F039,幂等):spill_dir/<session_id>,权限 600。

    已存在则幂等返回;创建/权限失败 → PERS-221 结构化上抛(Consumer 转
    tool.error 回喂"输出归档写入失败,请重试")。返回归一后的会话 spill
    目录 Path(后续 containment 基准)。
    """
    root = _spill_root(ctx)
    if root is None:
        raise_code("PERS-221", hint="storage.spill_dir 未配置(ctx.cfg/config 缺失)",
                   advice=_WRITE_FAIL_ADVICE)
    sid = _session_id(ctx)
    if not sid:
        raise_code("PERS-221", hint="ctx.session.session_id 缺失(spill 装配错误)",
                   advice=_WRITE_FAIL_ADVICE)
    d = root / sid
    try:
        d.mkdir(parents=True, exist_ok=True)     # 幂等(会话首访惰性建)
        try:
            os.chmod(d, MODE_PRIVATE)            # 600;Windows ACL 尽力而为(偏离 4)
        except OSError as e:                     # 权限设置失败同样上抛(spec 伪码)
            raise_code("PERS-221", dir=str(d), cause=e, advice=_WRITE_FAIL_ADVICE)
    except OSError as e:
        raise_code("PERS-221", dir=str(d), cause=e, advice=_WRITE_FAIL_ADVICE)
    return d.resolve()                           # 归一根,后续 containment 基准


# ------------------------------------------------------------------ put
async def put(text: str, kind: str = "output", ctx: Any = None) -> dict:
    """超限输出落盘(F039,Consumer 唯一入口):原文 → 会话私有 spill 文件。

    顺序(spec 伪码权威):单文件超 10MB 先拒(PERS-223)→ 惰性建目录
    (enter,PERS-221)→ ctx.redact 出口脱敏(INV-09)→ 临时文件+rename 原子
    落盘(坏行不落地)→ 返回 EVENT-SCHEMA 同形 SpillMeta
    {spilled, ref, chars, lines, preview(头500)}。

    kind = 来源标注(read/fetch/exec/pty…,入错误现场与审计扩展点,偏离 7)。
    """
    if ctx is None:
        raise_code("PERS-221", hint="ctx 缺失:put 必须带会话门面", advice=_WRITE_FAIL_ADVICE)
    if not isinstance(text, str):                # 契约:Consumer 已 render 成 str
        raise_code("PERS-221", hint="put 仅接受 str(Consumer 须先 render_result_text)",
                   advice=_WRITE_FAIL_ADVICE)
    limit = _max_per_file(ctx)
    if len(text) > limit:                        # 单文件 ≤10MB,超限拒写
        raise_code("PERS-223", chars=len(text), max=limit,
                   advice=_TOO_BIG_ADVICE)
    d = await enter(ctx)                         # 惰性建目录(幂等;失败 PERS-221)
    text = _redact(ctx, text)                    # 出口脱敏(INV-09,spill=明文出口)
    name = f"{FILE_PREFIX}{uuid.uuid4().hex[:8]}.txt"
    tmp = d / (name + ".tmp")                    # 临时文件:坏行不落地(rename 前不可见)
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp, d / name)                # 原子替换(Windows os.replace 同 rename)
    except OSError as e:
        try:                                    # 清理残留临时文件(尽力而为)
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise_code("PERS-221", dir=str(d), kind=kind, cause=e,
                   advice=_WRITE_FAIL_ADVICE)
    return {"spilled": True,
            "ref": f"{d.name}/{name}",         # 会话内相对引用:ref=<sid>/spill-*.txt
            "chars": len(text),
            "lines": text.count("\n") + 1,
            "preview": text[:SUMMARY_PREVIEW]}   # 头 500 字符固定


# ------------------------------------------------------------------ read
async def read(ref: str, start: int = 0, limit: int = DEFAULT_LIMIT,
               ctx: Any = None) -> dict:
    """按行取回(F039/LLM 可见工具 storage.spill):行区间 [start, start+limit)。

    ref 必须先过 _resolve_ref containment(PERS-222 零读取);文件不存在(已
    清理/会话结束)→ TLB-802;start<0/limit 超界 → TLB-803 零执行(schema 已
    拦,Provider 直调路径兜底)。返回 {content, more, start, returned} 并把
    已读字符数累入 counters.spill_read_chars(防循环烧预算)。
    """
    if ctx is None:
        raise_code("PERS-222", ref=ref, advice=_REFUSED_ADVICE)
    try:
        start = int(start)
        limit = int(limit)
    except (TypeError, ValueError):
        raise_code("TLB-803", tool=TOOL_NAME, field="start/limit",
                   advice="start/limit 须为整数;未执行任何操作")
    if start < 0 or limit < 1 or limit > MAX_READ_LIMIT:
        raise_code("TLB-803", tool=TOOL_NAME, start=start, limit=limit,
                   advice=f"start≥0 且 1≤limit≤{MAX_READ_LIMIT};未执行任何操作")
    f = _resolve_ref(ref, ctx)                   # containment 单点判定
    if f is None:
        raise_code("PERS-222", ref=ref, advice=_REFUSED_ADVICE)   # 零读取
    if not f.is_file():                          # ref 失效(已清理/会话结束/非文件)
        raise_code("TLB-802", tool=TOOL_NAME, ref=ref,
                   advice="spill 引用已失效;重跑源头工具重新生成输出")
    try:
        raw = f.read_text(encoding="utf-8", newline="")
    except OSError as e:                         # 读 IO 失败 → 同样按失效回喂
        raise_code("TLB-802", tool=TOOL_NAME, ref=ref, cause=e,
                   advice="spill 文件读取失败;重跑源头工具重新生成输出")
    lines = raw.split("\n")                      # 与 put 行数口径严格同源(偏离 3)
    chunk = lines[start:start + limit]
    more = (start + limit) < len(lines)          # 还有后续行
    body = "\n".join(chunk)
    _bump_counter(ctx, COUNTER_KEY, len(body))   # 已读量防循环
    return {"content": body, "more": more, "start": start,
            "returned": len(chunk)}


# ------------------------------------------------------------------ containment 单点
def _resolve_ref(ref: Any, ctx: Any) -> Optional[Path]:
    """ref → 私有区内绝对 Path;任一逃逸形态返回 None(PERS-222 唯一判定)。

    拒绝:空/非 str、绝对路径(含盘符)、`..` 段、符号链接解析后越出 spill
    根、跨会话引用(当前会话 id 已知时,偏离 2);全部零读取语义由调用方兑现。
    """
    if not isinstance(ref, str) or not ref:
        return None
    if os.path.isabs(ref) or ".." in Path(ref).parts:
        return None
    root = _spill_root(ctx)
    if root is None:
        return None
    root = root.resolve()                        # 根归一(符号链接在根级先解)
    cand = (root / ref).resolve()                # 解符号链接后归一(逃逸即越界)
    try:
        cand.relative_to(root)                   # 最终落点必须在根内(Windows
    except ValueError:                           # 路径比较大小写不敏感,偏离 4)
        return None
    parts = Path(ref).parts
    if not parts:
        return None
    if cand.parent.name != parts[0]:             # 首段必须是实际所在目录
        return None
    sid = _session_id(ctx)                       # 会话级隔离(偏离 2):本会话
    if sid is not None and parts[0] != sid:      # ref 只指本会话 spill 文件
        return None
    return cand


# ------------------------------------------------------------------ detach
async def detach(ctx: Any) -> None:
    """幂等摘除(DIS-SEAM §6.1 ⑤):摘读工具/摘定位器/摘订阅,文件不删。

    会话关闭(ctx.close)时调用;**文件保留**——留给 F060 归档/repair 在会话
    结束后仍可读的窗口,删除统一由 purge_session 在 session.finished 后执行。
    摘除失败仅本地日志告警,不阻断会话关闭(文件系统无副作用残留)。
    """
    storage = getattr(ctx, "storage", None)
    # ---- 1) 幂等闸:locator 自报未挂载即已摘;无 locator 面按挂载痕迹判
    if storage is not None:
        gate = getattr(storage, "_unmounted", None)
        if callable(gate):
            try:
                if await _maybe_await(gate("spill")):
                    return                        # 已摘除(幂等早退)
            except Exception:                     # noqa: BLE001 locator 异常:
                pass                              # 继续尽力摘除,不阻断
        else:
            services = getattr(storage, "services", None) or {}
            if not (hasattr(storage, "spill") or "spill" in services):
                return                            # 无 spill 挂载痕迹 → 已摘
    # ---- 2) 摘 LLM 可见读工具(TLB-802 = 已摘,静默幂等)
    tools = getattr(ctx, "tools", None)
    if tools is not None:
        unreg = getattr(tools, "unregister", None)
        if not callable(unreg):
            unreg = getattr(tools, "unregister_definition", None)
        if callable(unreg):
            try:
                await _maybe_await(unreg(TOOL_NAME))
            except PyHError as e:
                if e.code != "TLB-802":           # 已摘(重入/双摘)→ 静默
                    _log_warn(ctx, "spill detach unregister 失败 code=%s", e.code)
            except Exception as e:                # noqa: BLE001
                _log_warn(ctx, "spill detach unregister 异常: %s", e)
    # ---- 3) 摘定位器(ctx.storage.spill;真实 locator 走 unmount)
    if storage is not None:
        unmount = getattr(storage, "unmount", None)
        if callable(unmount):
            try:
                await _maybe_await(unmount("spill"))
            except Exception as e:                # noqa: BLE001
                _log_warn(ctx, "spill detach unmount 失败: %s", e)
        elif hasattr(storage, "spill"):           # 无 locator API:直接摘属性
            try:
                delattr(storage, "spill")
            except Exception as e:                # noqa: BLE001
                _log_warn(ctx, "spill detach 摘除属性失败: %s", e)
    # ---- 4) 摘 session.finished 清理订阅(按会话属主)
    _unsubscribe_purge(ctx)
    log.info("spill detached(文件保留,F060 读窗口)")


def _unsubscribe_purge(ctx: Any) -> None:
    """按会话属主摘除 purge 订阅(owner=f"spill:{sid}";幂等)。"""
    sid = _session_id(ctx)
    if not sid:
        return
    bus = getattr(ctx, "bus", None)
    drop = getattr(bus, "unsubscribe_all", None)
    if callable(drop):
        try:
            drop(f"spill:{sid}")
        except Exception as e:                    # noqa: BLE001
            _log_warn(ctx, "spill purge 订阅摘除失败: %s", e)


# ------------------------------------------------------------------ purge
async def purge_session(session_id: str, ctx: Any = None) -> None:
    """清理策略主体(F039 随会话生命周期/I-3):删除该会话 spill 目录全部文件。

    先 containment 校验(只删 spill 根下本会话子目录,逃逸即 PERS-222 拒);
    目录不存在幂等;删除失败记日志+告警不阻断(F060 repair 兜底残留)。
    订阅 session.finished 后由本函数收口"spill 随会话生命周期清理"。
    """
    root = _spill_root(ctx)
    if root is None:
        raise_code("PERS-222", session=session_id,
                   advice="spill 根未配置,无法校验清理目标,已拒绝")
    root = root.resolve()
    target = (root / session_id).resolve()       # 归一后比对
    try:
        target.relative_to(root)                 # containment 单点(Windows
    except ValueError:                           # 大小写不敏感,偏离 4)
        raise_code("PERS-222", session=session_id,
                   advice="清理目标逃逸 spill 根,已拒绝")
    if target == root:                           # 目标即根(空/`.` session)→ 拒
        raise_code("PERS-222", session=session_id,
                   advice="清理目标逃逸 spill 根,已拒绝")
    try:
        if target.exists():
            for f in target.glob(SPILL_PATTERN):  # 逐文件删(可留审计计数)
                f.unlink(missing_ok=True)
            for f in target.glob(f"{SPILL_PATTERN}.tmp"):   # 残留 tmp(偏离 5)
                f.unlink(missing_ok=True)
            target.rmdir()                        # 空目录收口
        log.info("spill purge done session=%s dir=%s", session_id, target)
    except OSError as e:                          # 残留不致命(F060 兜底)
        _log_warn(ctx, "spill purge failed session=%s: %s", session_id, e)


def subscribe_purge(ctx: Any, session_id: Optional[str] = None) -> Optional[str]:
    """订阅本会话 session.finished → purge_session(返回 owner 标记,可摘除)。

    装配于 Provider.enter(生命周期 enter→announce→detach);bus 未接线/无
    subscribe 面 → 返回 None(清理留待装配层兜底,文件由 F060 repair 覆盖)。
    """
    sid = session_id or _session_id(ctx)
    if not sid:
        return None
    bus = getattr(ctx, "bus", None)
    subscribe = getattr(bus, "subscribe", None)
    if not callable(subscribe):
        return None
    owner = f"spill:{sid}"

    async def _on_finished(type_: str, payload: Any) -> None:  # noqa: ARG001
        """session.finished 回调:仅清本会话(总线跨会话共享,按 payload 过滤)。

        声明 async:总线 _dispatch 对协程函数 handler 会 await(同步函数返回
        协程则不会被等待,产生 never-awaited 告警)。
        """
        p_sid = getattr(payload, "session_id", None)
        if p_sid is None and isinstance(payload, dict):
            p_sid = payload.get("session_id")
        if p_sid is not None and str(p_sid) != sid:
            return                                # 其他会话的 finished:不动
        await purge_session(sid, ctx)             # 删除本会话 spill 目录

    try:
        bus.subscribe("session.finished", _on_finished, owner=owner)
    except Exception as e:                        # noqa: BLE001 订阅失败不致命
        _log_warn(ctx, "spill purge 订阅失败: %s", e)
        return None
    return owner


# ------------------------------------------------------------------ register
READ_SCHEMA: dict = {"type": "object",
                     "properties": {"ref": {"type": "string"},
                                    "start": {"type": "integer", "minimum": 0,
                                              "default": 0},
                                    "limit": {"type": "integer", "minimum": 1,
                                              "maximum": MAX_READ_LIMIT,
                                              "default": DEFAULT_LIMIT}},
                     "required": ["ref"],
                     "additionalProperties": False}


class SpillReadHandle:
    """storage.spill 读工具的 Provider 实现(executor 契约 handle(args, ctx))。

    只读面(模块职责 7):把 LLM 参数转发到模块 read;put 不在此类,也不登记
    为工具。schema 已带默认值(start=0/limit=200),args 恒含两键(INV-06)。
    """

    name: str = TOOL_NAME

    async def handle(self, args: dict, ctx: Any) -> dict:
        """executor 关3 Provider 调用面:经模块 read 返回 SpillReadOut。"""
        return await read(args.get("ref", ""),
                          args.get("start", 0),
                          args.get("limit", DEFAULT_LIMIT),
                          ctx=ctx)


def register(registry: Any) -> list[str]:
    """读工具登记(DIS-SEAM §6.1 ⑥ announce):storage.spill 挂 tools 表。

    五要素齐备,danger=none(owner="spine",偏离 6),expose_to_llm=True 由
    装配层 announce 消费;put 不注册为工具(职责 7)。登记后装配层执行
    locator.mount("storage.spill")。返回注册名 list[str]。
    """
    defn = _read_definition()
    registry.register_tool(defn, provider=SpillReadHandle())
    return [defn.name]


def _read_definition() -> Any:
    """读工具 ToolDefinition(spec register 伪码 defn 的实契约形态)。"""
    from pyharness.core.tools_registry import ToolDefinition
    return ToolDefinition(
        name=TOOL_NAME,
        danger="none",
        description="按行读回 spill 溢出文件内容(ref/start/limit);工具输出超限时,"
                    "由结果中的 spill_ref 指引使用本工具取回全文",
        schema=READ_SCHEMA,
        timeout_s=15,                            # 只读文件切片,15s 足够
        owner="spine",                           # 脊柱支撑服务(DIS-SEAM §6.1 ②)
        ctx_path="storage.spill",                # 定位器键(消费者寻址面)
        version="1.0.0",
    )


# ------------------------------------------------------------------ SpillProvider
class SpillProvider:
    """DIS-SEAM §6.1 ③ Provider 接口:绑定会话 ctx 的能力实例。

    装配层 activate 后挂 ctx.storage.spill(定位器),Consumer 经
    ctx.storage.spill.put(text, kind) 调用(executor 关4,唯一 put 消费方);
    read 的 LLM 可见面由 register() 登记的 storage.spill 工具经 handle 承担。
    enter = 建目录 + 订阅 session.finished → purge;detach = 幂等摘除
    (工具/定位器/订阅),文件不删(F060 窗口),随 ctx.close() 收尾。
    """

    def __init__(self, ctx: Any = None) -> None:
        self._ctx = ctx

    # ---- Provider 生命周期(seam A ③⑤)
    async def enter(self, ctx: Any = None) -> Path:
        """首访:建会话私有目录(幂等)+ 订阅清理。返回归一目录 Path。"""
        c = ctx or self._ctx
        d = await enter(c)                       # 模块 enter(建目录,PERS-221)
        subscribe_purge(c)                       # session.finished → purge
        return d

    async def put(self, text: str, kind: str = "output") -> dict:
        """Provider 内部方法(不进工具表):落盘 + 返回 SpillMeta。"""
        return await put(text, kind=kind, ctx=self._ctx)

    async def read(self, ref: str, start: int = 0,
                   limit: int = DEFAULT_LIMIT) -> dict:
        """Provider 方法面(供内部/repair 直读;LLM 面走工具 handle)。"""
        return await read(ref, start, limit, ctx=self._ctx)

    async def detach(self, ctx: Any = None) -> None:
        """幂等摘除(文件保留,spec 职责 5)。"""
        await detach(ctx or self._ctx)


__all__ = [
    # 模块函数(spec 函数清单,签名一致)
    "enter", "put", "read", "detach", "purge_session", "register",
    # 内部/装配件
    "_resolve_ref", "subscribe_purge", "SpillProvider", "SpillReadHandle",
    # 常量
    "SUMMARY_PREVIEW", "DEFAULT_MAX_PER_FILE", "DEFAULT_LIMIT",
    "MAX_READ_LIMIT", "MODE_PRIVATE", "TOOL_NAME", "COUNTER_KEY",
    "READ_SCHEMA",
]
