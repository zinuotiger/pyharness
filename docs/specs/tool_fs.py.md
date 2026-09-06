# specs/tool_fs.py.md — 编码规格

> **模块文件**:`pyharness/core/tool_fs.py` | **功能编号**:F034(文件读取)· F035(文件写入与覆盖保护)· F036(目录列举)· 联动 F023(g-fs-path/g-overwrite 兜底语义)/F039(超限 spill)/F055(workspace 单点几何语义) | **权威口径**:PRD-Core §5.4 F034-F036(验收伪代码权威)、SECURITY.md §4.2(危险分级)/§7.2(文件影响域)、CONSTRAINTS-03-ToolSafety(T-05 五要素/T-09 输出有界)、ERR.md §2.9(TLB-8xx)/§2.11(POL-FS-*)、CFG.md §3.2(loop.content.file_spill_bytes)/§3.6(storage.spill.*);冲突以 PRD-Core 为准
> **一句话**:文件系统工具族(命名空间 `fs.*`)——`fs.read_file`(UTF-8 自动 BOM、>64KB 转 spill 不进历史)、`fs.write_file`(自动建目录、临时文件+rename 原子写、覆写审批由管道 g7 负责、工具内不二判)、`fs.list_dir`(隐藏文件默认隐藏、≤500 条截断留痕)、`fs.delete_file`(danger=critical 注册即拒的演示/审计锚点);所有路径先过 `resolve_in_workspace` 单点归一(g-fs-path 已过之后的纵深第二道,PRD F034 明示"再兜一层")。
> **代码目录**:`pyharness/core/tool_fs.py`;经 tools_registry.register_tool 登记(五要素),被 tools_executor 管道调用;Provider 内不重复校验/审批(ADR-002/003、CONSTRAINTS-03 T-01)。

## 模块职责

1. **路径几何第二道闸(F034/F055 语义)**:每个工具执行前对 `path` 做 `resolve_in_workspace` 归一——绝对路径越界(POL-FS-1)、`..` 规范化逃逸(POL-FS-2)、符号链接/junction 最终解析越界(POL-FS-3)全部在工具内再拒一次(管道 g3 已拒过,此处防 TOCTOU 与内层直调,INV-04);失败一律结构化 PyHError 按 ERR 规则映射 GRD-401(reason=POL-FS-x),零副作用。
2. **读文件防上下文爆炸(F034/T-09)**:先 stat 判大小再读,>64KB(`loop.content.file_spill_bytes`,默认 65536)不把全文进上下文,调 `ctx.storage.spill.put` 落盘、返回 `{content: 摘要, truncated: true, spill_ref}`;二进制(NUL 字节样本探测)拒读 TLB-806;不存在 TLB-802 附 hint 自查;超大文件(>spill 单文件上限 10MB)直接 PERS-223 拒并回喂,指引换 F052 分块——"禁止整文件内容进上下文"是硬线(CONSTRAINTS-03 T-09)。
3. **原子写与覆盖保护分工(F035)**:新建/append = 临时文件+`os.replace` 原子替换(崩溃不留半文件);**覆写审批不在工具内做**——管道 g7(g-overwrite)按动作形态在 Provider 执行前转审批(POL-OVW-1,timeout=denied),工具声明 danger=low 使普通新建直执行;工具体只负责"写对、写原子"。
4. **目录列举侦查上限(F036)**:名称/类型/大小/修改时间,通配过滤与 1 层递归,隐藏文件默认隐藏;条目 >500(`LIST_MAX_ENTRIES`)截断并置 `truncated: true` 留痕,防巨目录打爆上下文。
5. **五要素工具定义(硬约束 T-05)**:每个工具 = name/description/参数 schema(pydantic 编译)/执行函数(handler)/danger 五要素齐备才注册;description 与执行同谓词、不含"忽略之前指令"类文本(T-07)。
6. **零权限放大**:读工具(含 read_extra_dirs 只读例外,见 `resolve_in_workspace`)无写能力;写/删工具只认 workspace 内路径;Provider 不 import guard/executor/approval 内部符号(单向依赖,INV-08 阶段 3 自含,不依赖阶段 5 模块)。

## 依赖

- **单向依赖**:本文件 → `tools_registry.register_tool`(F008 登记)、`errors`(PyHError 结构化错误)、`ctx.scope.workspace`(workspace 根,只读例外 `security.policy.read_extra_dirs`)、`ctx.storage.spill`(F039 put,超限输出落盘)、`ctx.redact`(F016,输出出口脱敏)——不 import tools_guard/tools_executor/approval 内部符号;阶段 3 自含路径解析(阶段 5 若落 F055 共享实现,签名不变、内部改调,INV-08)。
- **消费方**:tools_executor.execute(唯一执行入口,INV-04);agent-loop 经 ctx.tools;审计经 tool.call/result 事件。
- **外部**:pathlib、os、uuid、re、pydantic v2(schema 编译由 registry 完成)。

## 数据结构表

**工具定义五要素总表(T-05;schema 均为 JSON-Schema dict,注册时编译 pydantic 强类型,F026)**:

| 工具名 | description(一句话,与执行同谓词) | 参数 schema 要点 | danger | handler | 管道自动挂载 guard |
|---|---|---|---|---|---|
| `fs.read_file` | 读 workspace 内文本文件(UTF-8 自动 BOM);超 64KB 自动转 spill 引用 | `path: str`(必填);`max_lines: int?`(预留行读,默认全量) | low(SECURITY §4.2:workspace 内读) | read_file | g3 路径 + g4 凭据文件 |
| `fs.write_file` | 新建/追加文本到 workspace 内,自动建目录,原子写 | `path: str`;`content: str`(maxLength=1048576,超限在契约层拒);`mode: "write"|"append"` 默认 write | low | write_file | g3 + g7(目标已存在且 mode=write → 审批 POL-OVW-1) |
| `fs.list_dir` | 列 workspace 内目录条目(≤500 条),支持通配与 1 层递归 | `path: str`(可选默认 ".");`glob: str?`;`recursive: bool`(1 层);`all: bool`(含隐藏) | none | list_dir | g3 |
| `fs.delete_file` | 删除 workspace 内文件(critical,默认恒拒,仅审计/演示锚点) | `path: str` | **critical**(不可审批 POL-DGR-1) | delete_file | g3;strict 下 scope.deny_tools 追加 |

**其他结构**:

| 结构 | 字段 | 规则 |
|---|---|---|
| `_ReadResult` | content/truncated/spill_ref/bytes | ≤64KB 全量;>64KB 只带摘要+引用;bytes=实际读入字节 |
| `_WriteResult` | bytes/path | bytes=写入 UTF-8 编码字节数;path=归一后绝对路径 |
| `_ListResult` | entries/truncated | entries ≤500;truncated=True 表示被截断留痕 |
| `Entry` | name/type/size/modified | type ∈ {file,dir};size 仅文件(字节);modified=ISO 8601 mtime |
| `_SpillMeta` | ref/chars/lines/preview | 由 storage.spill.put 返回,原样透传(F039) |

**常量**:`READ_SPILL_BYTES` 读配置 `loop.content.file_spill_bytes`(默认 65536,1024-1048576);`WRITE_MAX_BYTES=1048576`(schema maxLength,>1MB 契约层拒 TLB-803 零执行,F035);`LIST_MAX_ENTRIES=500`(F036);`BINARY_SAMPLE=8192`(二进制探测样本)。

## 类与函数清单

### `def resolve_in_workspace(raw: str, ctx, *, writable: bool = False) -> Path` — 路径单点归一(第二道几何闸,F055 语义,模块内私有不注册)

**功能**:把 LLM 给的路径字符串解析为 workspace 内绝对 Path——绝对路径越界拒(POL-FS-1)、`..` 规范化逃逸拒(POL-FS-2)、symlink/junction 最终解析越界拒(POL-FS-3);`writable=True` 时只认 workspace 根,`writable=False`(读)额外允许 `security.policy.read_extra_dirs` 只读例外(config+guard 留痕的显式授权)。

```python
def resolve_in_workspace(raw, ctx, *, writable=False):
    root = ctx.scope.workspace.resolve()                # workspace 唯一根
    p = Path(raw)
    if p.is_absolute():
        if not _under(p, root):                          # POL-FS-1:绝对路径越界
            raise PyHError("GRD-401", reason="POL-FS-1",
                ctx={"path": raw, "advice": "只允许 workspace 内路径"})
        cand = p
    else:
        cand = root / p                                   # 相对路径锚定 workspace
    norm = os.path.normpath(cand)                         # 先规范化
    if not _under(Path(norm), root):                      # POL-FS-2:".." 逃逸
        raise PyHError("GRD-401", reason="POL-FS-2",
            ctx={"path": raw, "advice": "路径含 .. 越界"})
    real = Path(norm).resolve()                           # 解开 symlink/junction
    inside = _under(real, root)
    if not inside and not (not writable and _in_extra_read(real, ctx)):
        raise PyHError("GRD-401", reason="POL-FS-3",      # 链接解析后越界
            ctx={"path": raw, "advice": "符号链接指向 workspace 外"})
    return real
```

**参数表**:`raw`=LLM 原始路径字符串;`ctx`=会话门面(scope.workspace/policy.read_extra_dirs);`writable`=是否写操作(True 禁只读例外)。**返回**:解析后绝对 Path。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 绝对路径在 workspace 外 | GRD-401(reason=POL-FS-1) | 回喂 LLM 自查,改 workspace 相对路径 |
| `PyHError` | 规范化后含 `..` 逃逸 | GRD-401(reason=POL-FS-2) | 回喂;先 list_dir 确认真实路径 |
| `PyHError` | symlink/junction 最终解析越界 | GRD-401(reason=POL-FS-3) | 回喂;链接文件本身在 workspace 内才可读 |

**关联测试**:test_f034_read.py(越界拒)、T-SEC-01/05(POL-FS-1 主场景)、tools_guard 配套 GWT(g3 与工具内第二道同码同判)。

### `async def read_file(args: dict, ctx) -> dict` — fs.read_file(F034)

**功能**:读文本文件供分析:先 stat 防超大、二进制探测拒读、UTF-8 自动 BOM 解码;内容 >64KB 调 storage.spill.put 只返回摘要+引用,全文不进历史/上下文。

```python
async def read_file(args, ctx):
    p = resolve_in_workspace(args["path"], ctx, writable=False)   # 第二道几何闸
    if not p.exists():
        raise PyHError("TLB-802", ctx={"path": str(p),
            "advice": "文件不存在,先 list_dir 自查路径拼写"})
    if p.stat().st_size > spill_max(ctx):                          # 预检:超 spill 上限
        raise PyHError("PERS-223", ctx={"path": str(p), "size": p.stat().st_size,
            "advice": "文件超过 spill 上限,换 F052 分块或缩小目标"})
    data = p.read_bytes()                                          # ≤10MB 才整读
    if looks_binary(data):
        raise PyHError("TLB-806", ctx={"path": str(p),
            "advice": "二进制文件,不能用文本读取"})
    text = data.decode("utf-8-sig")                                # 自动 BOM
    if len(data) <= ctx.cfg["loop.content.file_spill_bytes"]:      # 小文件全量返回
        return {"content": ctx.redact(text), "truncated": False,
                "bytes": len(data), "path": str(p)}
    meta = await ctx.storage.spill.put(ctx.redact(text), kind="read", ctx=ctx)
    return {"content": meta["preview"], "truncated": True,          # 大文件只给引用
            "spill_ref": meta, "bytes": len(data), "path": str(p)}
```

**参数表**:`path` str 必填(workspace 相对或 workspace 内绝对)。**返回**:见 `_ReadResult`。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 路径不存在(幻觉/拼写错) | TLB-802 | 回喂 hint"先 list_dir";不静默 |
| `PyHError` | 二进制内容(NUL 样本命中) | TLB-806 | 结构化错误提示换工具 |
| `PyHError` | 文件 >10MB(超 spill 上限) | PERS-223 | 回喂:换 F052 或缩小读取目标 |
| `PyHError` | 越权读凭据文件 | GRD-401(reason=POL-CRED-1) | 管道 g4 已拒;工具内同码兜底 |
| `OSError` | 权限/IO 失败 | TLB-805 | tool.error 回喂,LLM 判重试 |

**关联测试**:test_f034_read.py(边界/二进制/超大 spill)、T-SEC-04(读凭据文件被拦)、CONSTRAINTS-03 §5(超长输出上下文 token 有界)。

### `async def write_file(args: dict, ctx) -> dict` — fs.write_file(F035)

**功能**:写/追加文本到 workspace 内:自动建父目录;临时文件+`os.replace` 原子替换防半写;目标已存在且 mode=write 时的审批由管道 g7 在 Provider 前完成(工具内不二判、不做任何降级);content >1MB 在契约层(pydantic maxLength)被拒,零执行。

```python
async def write_file(args, ctx):
    p = resolve_in_workspace(args["path"], ctx, writable=True)     # 写:只认 workspace
    content = args["content"]                                      # 契约已验 ≤1MB(TLB-803 超限零执行)
    p.parent.mkdir(parents=True, exist_ok=True)                    # 自动建目录
    if args.get("mode", "write") == "append" and p.exists():
        content = p.read_text(encoding="utf-8") + content          # append=复制+追加
    tmp = p.with_name(p.name + f".tmp-{uuid4().hex[:8]}")          # 同目录临时文件
    try:
        tmp.write_text(content, encoding="utf-8", newline="")      # 防换行改写
        os.replace(tmp, p)                                         # 原子替换
    except BaseException:
        tmp.unlink(missing_ok=True)                                # 失败清临时文件
        raise
    return {"bytes": len(content.encode("utf-8")), "path": str(p)}
```

**参数表**:`path` str 必填;`content` str 必填(maxLength=1048576,超限 → TLB-803 未执行);`mode` 枚举 write/append,默认 write。**返回**:`_WriteResult`。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | content >1MB(schema maxLength) | TLB-803 | 契约层拒,零执行(INV-06) |
| `PyHError` | 覆写审批被拒/超时 | GRD-401/APR-5xx | 管道处置:审批 denied/timeout=不执行 |
| `PyHError` | 路径越界 | GRD-401(reason=POL-FS-x) | 回喂自查 |
| `OSError` | 磁盘/权限失败 | TLB-805 | tool.error 回喂;原子写保证无半文件 |

**关联测试**:test_f035_write.py(覆写审批/原子写/越界拒)、T-SEC-03(审批超时=denied、文件原样、Provider 零调用)、INV-05(拒绝后零副作用)。

### `async def list_dir(args: dict, ctx) -> dict` — fs.list_dir(F036)

**功能**:列目录条目(名称/类型/大小/修改时间),支持 glob 通配与 1 层递归;隐藏文件默认隐藏(`all=True` 显示);条目数超 500 截断并置 truncated 留痕,供 agent 规划前侦查。

```python
async def list_dir(args, ctx):
    p = resolve_in_workspace(args.get("path", "."), ctx, writable=False)
    if not p.is_dir():
        raise PyHError("TLB-802", ctx={"path": str(p),
            "advice": "目录不存在,先 list_dir 父目录"})
    entries, truncated = [], False
    it = p.iterdir() if not args.get("recursive") else p.glob("**/*")
    for child in sorted(it, key=lambda x: x.name):                 # 稳定排序
        if child.name.startswith(".") and not args.get("all"):
            continue                                               # 隐藏文件默认隐藏
        if args.get("glob") and not child.name.startswith(args["glob"]):
            continue                                               # 通配过滤(前缀匹配由 agent 扩展)
        st = child.stat()
        entries.append({"name": child.name, "type": "dir" if child.is_dir() else "file",
                        "size": st.st_size if child.is_file() else None,
                        "modified": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")})
        if len(entries) >= LIST_MAX_ENTRIES:                       # 500 条上限
            truncated = True
            break
    return {"entries": entries, "truncated": truncated}
```

**参数表**:`path` str 可选(默认当前目录 ".");`glob` str 可选(通配过滤);`recursive` bool 默认 False(1 层递归);`all` bool 默认 False(含隐藏文件)。**返回**:`_ListResult`。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 目录不存在/路径是文件 | TLB-802 | 回喂 hint 先列父目录 |
| `PyHError` | 路径越界 | GRD-401(reason=POL-FS-x) | 回喂自查 |
| `OSError` | 权限/IO | TLB-805 | tool.error 回喂 |

**关联测试**:test_f036_listdir.py(过滤/上限截断)、SECURITY D-2(巨目录不爆上下文)、F034 主场景前置侦查(录屏素材)。

### `async def delete_file(args: dict, ctx) -> dict` — fs.delete_file(critical 锚点,PRD §6.3/T-SEC)

**功能**:注册为 danger=critical 的删除工具——管道 g-danger 对 critical **直接拒绝、不可审批**(POL-DGR-1),故正常路径 Provider 恒零执行(INV-05 演示"拦了且没执行"的审计样本);本函数体是真实删除实现,仅当策略被显式下调(不鼓励,须留 sandbox.opened 事件)或测试直调 Provider mock 时才可达。

```python
async def delete_file(args, ctx):
    # 正常管道下不可达:g-danger(critical)在 Provider 前已 REJECT,
    # 审计特征 = guard.rejected(POL-DGR-1) 且本 call_id 无 tool.result。
    p = resolve_in_workspace(args["path"], ctx, writable=True)     # 即便下调也守几何
    if not p.exists():
        raise PyHError("TLB-802", ctx={"path": str(p),
            "advice": "文件不存在,先 list_dir 确认"})
    if p.is_dir():
        raise PyHError("TLB-805", ctx={"path": str(p),
            "advice": "本工具只删文件;目录删除不在工具面"})
    p.unlink()                                                     # 删除(真实副作用仅策略下调后发生)
    return {"deleted": str(p)}
```

**参数表**:`path` str 必填。**返回**:`{"deleted": path}`。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | critical 调用(critical 不可审批) | GRD-401(reason=POL-DGR-1) | 恒拒;人类想删须下调沙箱并留事件 |
| `PyHError` | 路径不存在 | TLB-802 | 回喂自查 |
| `PyHError` | 目标是目录 | TLB-805 | 回喂提示 |

**关联测试**:T-SEC-01/02/05(删除全路径被拦、Provider 计数 0、目录仍在)、PRD §6.3 主场景三条 guard.rejected、scope.py.md(strict 下 deny_tools 追加)。

### `def register(registry) -> list[str]` — 五要素注册入口(F008/T-05)

**功能**:按工具定义五要素表装配 4 个 ToolDefinition(name/description/schema/danger/handler)并逐个 `register_tool`,返回注册名列表;重复注册由 registry 拒(TLB-801);description 防注入检查(T-07)在 registry 层。

```python
def register(registry):
    defs = [ToolDefinition(name="fs.read_file", danger="low",
        description="读 workspace 内文本文件(UTF-8 自动 BOM);超过 64KB 自动转 spill 引用",
        schema={"type": "object", "properties": {"path": {"type": "string"}},
                "required": ["path"], "additionalProperties": False},
        handler=read_file, timeout_s=30),
        ToolDefinition(name="fs.write_file", danger="low",
        description="新建或追加文本到 workspace 内文件;自动建目录;原子写入",
        schema={"type": "object",
                "properties": {"path": {"type": "string"},
                    "content": {"type": "string", "maxLength": 1048576},
                    "mode": {"enum": ["write", "append"]}},
                "required": ["path", "content"], "additionalProperties": False},
        handler=write_file, timeout_s=30),
        ToolDefinition(name="fs.list_dir", danger="none",
        description="列 workspace 内目录条目(名称/类型/大小/修改时间),支持通配与 1 层递归",
        schema={"type": "object",
                "properties": {"path": {"type": "string"},
                    "glob": {"type": "string"},
                    "recursive": {"type": "boolean"},
                    "all": {"type": "boolean"}},
                "required": [], "additionalProperties": False},
        handler=list_dir, timeout_s=15),
        ToolDefinition(name="fs.delete_file", danger="critical",
        description="删除 workspace 内文件(默认恒拒,仅审计演示锚点)",
        schema={"type": "object", "properties": {"path": {"type": "string"}},
                "required": ["path"], "additionalProperties": False},
        handler=delete_file, timeout_s=15)]
    for d in defs:                                                  # 逐个登记
        registry.register_tool(d)
    return [d.name for d in defs]                                   # 注册名清单
```

**参数表**:`registry`=tools_registry.ToolRegistry 实例。**返回**:注册名 list[str]。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 重名/保留名/NAME_RE 不匹配 | TLB-801 | 注销旧定义重注册 |
| `PyHError` | schema 不可编译 | TLB-803 | 注册期早失败,修 schema |

**关联测试**:test_f008 注册族(TLB-801 重名拒)、CONSTRAINTS-03 §5 验收(五要素/同名拒绝)。

## 关联文档

| 文档 | 章节 | 关系 |
|---|---|---|
| PRD-Core.md | §5.4 F034/F035/F036、§6.3 主场景 | 验收伪代码与功能权威;删除被拦审计样本 |
| SECURITY.md | §4.2 危险分级、§7.2 文件影响域 | danger 声明与 workspace 几何规则来源 |
| CONSTRAINTS-03-ToolSafety.md | T-01~T-10、§7 分级表 | 四关管道纪律、五要素、输出有界硬约束 |
| ERR.md | §2.9 TLB-8xx、§2.11 POL-FS-* | 错误码与拒绝原因映射 |
| CFG.md | §3.2 loop.content.file_spill_bytes、§3.6 storage.spill | 读阈值与 spill 上限配置键 |
| tools_guard.py.md | g3/g7 | 管道侧路径/覆写 guard,本文件为其工具内第二道 |
| tools_registry.py.md | ToolDefinition/DangerLevel | 登记契约与五要素字段定义 |
