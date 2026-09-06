# specs/spill.py.md — 编码规格

> **模块文件**:`pyharness/core/spill.py` | **功能编号**:F039(输出溢出 spill)· 联动 F034(读文件超 64KB)/F038(抓取超 32KB)/F052(子进程输出)/tools_executor 关4(结果 finalize)/F060(repair 读窗口) | **权威口径**:PRD-Core §5.4 F039(验收伪代码权威)、DIS-SEAM §6.1(seam A 九段式:Provider 接口/Consumer/生命周期权威)、CFG.md §3.6(storage.spill_dir=~/.pyharness/spill、spill.max_per_file_bytes=10MB)、EVENT-SCHEMA §2(tool.result.spill_ref={ref,chars,lines,preview(头500)} 字段级)、ERR.md §2.3(PERS-221/222/223);冲突以 PRD-Core 为准
> **一句话**:工具输出溢出基础设施(基础设施型 seam,Consumer=脊柱结果检查器,LLM 不直调 put)——超限原始大文本落**会话私有 spill 区(workspace 外、权限 600)**,上下文只放 ≤2KB 摘要 + `spill_ref` 引用;模型要全文经 LLM 可见的读工具按行取回,已读量计入防循环烧预算;**截断不吞事实,溢出部分可查**(MAP §3.5)。
> **代码目录**:`pyharness/core/spill.py`(DIS-SEAM §6.1 ⑨ 落点 core/tools.py + capabilities/storage/ 的 storage 侧实现;specs 统一落 core/,模块路径以本文件为准)。

## 模块职责

1. **put:超限输出落盘(F039)**:Consumer(tools_executor 关4)输出 >2KB → `put(text, kind)` 写入会话私有区 `spill_dir/<session_id>/spill-<8hex>.txt`(临时文件+rename 原子落盘,坏行不落地);返回 `{spilled, ref, chars, lines, preview(头500字符)}`——EVENT-SCHEMA 字段级一致,tool.result 只带该引用与 ≤2KB summary,大文本不进上下文/事件/日志。
2. **上限与摘要固定(CFG §3.6)**:spill 单文件 ≤10MB(`storage.spill.max_per_file_bytes`,默认 10485760),超限 PERS-223 拒写并回喂(上游截断/分块);摘要=头 500 字符 + 行数 + 大小(固定三要素,不随实现漂移);chars=len(text)、lines=text.count("\n")+1。
3. **read:按行取回 + 越权零读取(PERS-222)**:LLM 经暴露工具按行读回(`ref`,`start`,`limit` 默认 0/200),返回 content + more;ref 解析强制会话私有区 containment——绝对路径/`..`/符号链接逃逸一律 PERS-222 **零读取**(同 call 无部分返回),防跨会话越权(本会话 ref 只指本会话 spill 文件)。
4. **防循环烧预算**:每次 read 的返回字符数累入 `ctx.counters`(`spill_read_chars`),预算/轮数闸可据此终止"读全文-再读"死循环(PRD F039 边界条件;DIS-SEAM §6.1 ④)。
5. **生命周期与清理策略**:enter=会话首访惰性建目录(workspace 外,权限 600,Windows 上 ACL 尽力而为、主防线=位置与路径 containment);detach=幂等摘除(摘读工具、释放句柄,**文件不删**——留 F060 归档/repair 读取窗口);**purge_session 订阅 session.finished 删除本会话 spill 目录**(PRD F039"spill 随会话生命周期";SECURITY I-3"随会话清理"),删除前再做 containment 校验、目录不存在幂等。
6. **出口脱敏(INV-09)**:put 落盘前对 text 执行 `ctx.redact`(F016 全出口脱敏)——spill 文件是 SECURITY §4 明文列出的出口之一,含疑似 key 原文即 INV-09 违规(构建阻断 check_secrets.py 会 grep spill 样本)。
7. **LLM 可见面最小化**:LLM 只见"读"工具(`storage.spill`,schema=ref/start/limit);put 是 Provider 内部方法,不进工具表(DIS-SEAM §6.1 ②,防模型拿 put 当存储滥写)。

## 依赖

- **单向依赖**:本文件 → `errors`(PyHError)、`ctx.redact`(F016)、`ctx.counters`(已读量)、`ctx.cfg`(storage.spill_dir/max_per_file_bytes)、`bus.subscribe`(session.finished → purge)、`tools_registry.register_tool`(storage.spill 读工具登记)、`ctx.session`(session_id 获取);不 import tools_executor 内部符号(Consumer 反向经 ctx.storage.spill 注入)。
- **消费方**:tools_executor `_finalize`/`_check_output`(唯一 put 调用方,F039);LLM 侧经 tool `storage.spill` 调 read;F060 repair 读窗口(read 直读文件,不依赖会话存活)。
- **外部**:pathlib、os、uuid、stat。
- **目录布局**:`<spill_dir>/<session_id>/spill-<uuid4().hex[:8]>.txt`;ref 恒为相对 `<session_id>/spill-xxxx.txt`(会话内引用,事件可 grep)。

## 数据结构表

| 结构 | 字段 | 规则 |
|---|---|---|
| `SpillMeta`(put 返回,EVENT-SCHEMA spill_ref 同形) | spilled=True/ref/chars/lines/preview | preview=头 500 字符(固定);chars=字符数;lines=\n 计数+1 |
| `SpillReadArgs`(LLM 工具 schema) | ref:str 必填;start:int=0(≥0);limit:int=200(1-1000) | 行区间 [start, start+limit) |
| `SpillReadOut` | content/more/start/returned | content=命中的行拼接;more=还有后续行 |
| `_SessionDir` | root/spill_root/contained(realpath) | containment 单点判定,所有 ref 必经 |
| 事件承载 | tool.result.spill_ref | {ref,chars,lines,preview};事件 payload ≤64KB(N3),spill 全文不进事件 |

**常量**:`SUMMARY_PREVIEW=500`(头 500 字符);`MAX_PER_FILE` 读 `storage.spill.max_per_file_bytes`(默认 10485760);`DEFAULT_LIMIT=200`;`MODE_PRIVATE=0o600`。

## 类与函数清单

### `async def enter(ctx) -> Path` — 会话首访建私有目录(F039,幂等)

**功能**:惰性创建 `spill_dir/<session_id>`(workspace 外),目录权限 600;已存在则幂等返回;创建失败(PERS-221)以结构化错误上抛,由 Consumer 转 tool.error 回喂("输出归档写入失败,请重试")。

```python
async def enter(ctx):
    root = Path(ctx.cfg["storage.spill_dir"]).expanduser()         # ~/.pyharness/spill
    sid = ctx.session.session_id                                   # 会话隔离键
    d = root / sid
    try:
        d.mkdir(parents=True, exist_ok=True)                       # 幂等
        os.chmod(d, MODE_PRIVATE)                                  # 600(Windows ACL 尽力而为)
    except OSError as e:
        raise PyHError("PERS-221", ctx={"dir": str(d),
            "advice": "查 spill 目录权限(600)与磁盘空间(F039)"}) from e
    return d.resolve()                                             # 归一根,后续 containment 基准
```

**参数表**:`ctx`=会话门面(cfg.storage.spill_dir/session.session_id)。**返回**:归一后的会话 spill 目录 Path。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 目录创建/权限失败 | PERS-221 | 查目录权限与空间;重试 |

**关联测试**:test_f039_spill.py(目录惰性创建/会话隔离)、ERR 排障"查 spill 目录权限(600)与空间"。

### `async def put(text: str, kind: str, ctx) -> dict` — 超限输出落盘(F039,Consumer 唯一入口)

**功能**:把超限原始文本写入本会话 spill 文件(先 redact 脱敏,INV-09);单文件超 10MB 拒写 PERS-223;原子落盘(临时文件+rename);返回 EVENT-SCHEMA 同形 SpillMeta。

```python
async def put(text, kind, ctx):
    if len(text) > MAX_PER_FILE(ctx):                             # 单文件 ≤10MB
        raise PyHError("PERS-223", ctx={"chars": len(text), "max": MAX_PER_FILE(ctx),
            "advice": "输出超过 10MB spill 上限;上游截断或分块"})
    d = await enter(ctx)                                           # 惰性建目录(幂等)
    text = ctx.redact(text)                                        # 出口脱敏 INV-09
    name = f"spill-{uuid4().hex[:8]}.txt"
    tmp = d / (name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="")             # 临时文件
    os.replace(tmp, d / name)                                      # 原子替换,坏行不落地
    return {"spilled": True, "ref": f"{d.parent.name}/{name}",     # 会话内相对引用
            "chars": len(text), "lines": text.count("\n") + 1,
            "preview": text[:SUMMARY_PREVIEW]}                     # 头 500 字符固定
```

**参数表**:`text`=超限原始文本(已由 Consumer 判定 >2KB);`kind`=来源标注(read/fetch/exec/pty…,入文件名审计前缀可扩展);`ctx`=门面。**返回**:`SpillMeta`。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | text >10MB | PERS-223 | 拒写回喂;上游截断/分块 |
| `PyHError` | 目录写失败 | PERS-221 | 查权限与空间,重试 |
| `OSError` | 磁盘 IO | PERS-221 | 同上(统一映射) |

**关联测试**:test_f039_spill.py(引用/预览/上限)、DIS-SEAM §6.1 G1(5KB 输出 → tool.result 带 truncated+spill_ref,文件=原文,上下文 ≤2KB)、G3 变体(PERS-223 超限拒写)。

### `async def read(ref: str, start: int, limit: int, ctx) -> dict` — 按行取回(F039/LLM 可见工具 `storage.spill`)

**功能**:按行区间 [start, start+limit) 读回 spill 内容;ref 必须先过 containment(会话私有区内、无 `..`/绝对/符号链接逃逸),越权即 PERS-222 **零读取**;返回 content+more,并把已读字符数累入 counters(防循环烧预算)。

```python
async def read(ref, start, limit, ctx):
    f = _resolve_ref(ref, ctx)                                     # containment 单点(见下)
    if f is None:
        raise PyHError("PERS-222", ctx={"ref": ref,
            "advice": "spill 引用越界,已拒绝读取;只允许本会话私有区"})   # 零读取
    if not f.exists():                                             # ref 失效(已清理/会话结束)
        raise PyHError("TLB-802", ctx={"ref": ref,
            "advice": "spill 引用已失效;重跑源头工具重新生成输出"})
    lines = f.read_text(encoding="utf-8").splitlines()             # 整读后按行切片
    chunk = lines[start:start + limit]
    more = (start + limit) < len(lines)                            # 还有后续行
    body = "\n".join(chunk)
    ctx.counters.bump("spill_read_chars", len(body))               # 已读量防循环
    return {"content": body, "more": more, "start": start,
            "returned": len(chunk)}
```

**参数表**:`ref`=SpillMeta.ref(会话内相对路径);`start`=起始行(默认 0);`limit`=最多行数(默认 200,1-1000);`ctx`=门面。**返回**:`SpillReadOut`。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | ref 逃逸(`../`/绝对路径/符号链接) | PERS-222 | 零读取;tool.error 回喂,查 ref 来源 |
| `PyHError` | spill 文件不存在(已清理) | TLB-802 | 回喂:重跑产生该输出的工具 |
| `PyHError` | start<0/limit>1000(schema) | TLB-803 | 契约层拒,零执行 |

**关联测试**:test_f039_spill.py(行读/预览)、DIS-SEAM §6.1 G2(read(start=0,limit=100) → 前 100 行+more=true;已读量递增)、G3(ref="../../s-other/x.jsonl" → PERS-222 零读取)。

### `async def detach(ctx) -> None` — 幂等摘除(DIS-SEAM §6.1 ⑤,不删文件)

**功能**:会话关闭(ctx.close)时摘除 LLM 可见读工具、卸载 ctx.storage.spill 定位器、释放句柄;幂等(重复调用无副作用);**文件保留**——留给 F060 归档/repair 在会话结束后仍可读的窗口,删除统一由 purge_session 在 session.finished 后执行。

```python
async def detach(ctx):
    if ctx.storage._unmounted("spill"):                            # 幂等闸:已摘除即返回
        return
    await ctx.tools.unregister("storage.spill")                    # 摘读工具
    ctx.storage.unmount("spill")                                   # 摘定位器
    # 注意:不删除 <spill_dir>/<sid>/ 任何文件——repair/归档(F060)
    # 在 session.finished 之后仍需读窗口;删除只发生在 purge_session。
```

**参数表**:`ctx`=会话门面。**返回**:None。**异常表**:无(摘除失败仅记本地日志告警,不阻断会话关闭——文件系统无副作用残留)。

**关联测试**:DIS-SEAM §6.1 ⑤ 生命周期 GWT(enter→announce→detach 事件序;detach 后文件仍在)。

### `async def purge_session(session_id: str, ctx) -> None` — 清理策略主体(F039 随会话生命周期/I-3)

**功能**:订阅 `session.finished`,清理该会话 spill 目录:先 containment 校验(只删本会话子目录,路径逃逸即拒),目录不存在幂等;删除失败记日志+告警不阻断(文件残留由 F060 repair 兜底);实现"spill 随会话生命周期清理"的收口。

```python
async def purge_session(session_id, ctx):
    root = Path(ctx.cfg["storage.spill_dir"]).expanduser().resolve()
    target = (root / session_id).resolve()                         # 归一后比对
    if not str(target).startswith(str(root)) or target == root:    # containment 单点
        raise PyHError("PERS-222", ctx={"session": session_id,
            "advice": "清理目标逃逸 spill 根,已拒绝"})
    try:
        if target.exists():
            for f in target.glob("spill-*.txt"):                   # 逐文件删(可留审计计数)
                f.unlink(missing_ok=True)
            target.rmdir()                                         # 空目录收口
    except OSError as e:                                           # 残留不致命
        ctx.log.warning("spill purge failed: %s", e)               # 本地日志,不阻断
```

**参数表**:`session_id`=已结束会话 id;`ctx`=门面(log 注入)。**返回**:None。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | session_id 逃逸 spill 根 | PERS-222 | 拒清理(防御纵深,正常不可达) |
| `OSError` | 删除失败(占用/权限) | —(本地告警) | 残留文件由 F060 repair/归档兜底 |

**关联测试**:test_f039_spill.py(生命周期清理:finished 后目录消失;ref 再读 → TLB-802)、SECURITY I-3(私有区权限 600+随会话清理)、EVENT-SCHEMA session.finished 订阅序。

### `def _resolve_ref(ref: str, ctx) -> Path | None` — ref containment 单点(PERS-222 唯一判定)

**功能**:把 ref(形如 `<sid>/spill-xxxx.txt`)解析为私有区内绝对 Path;含绝对路径、`..`、盘符、符号链接解析越界任一情形返回 None(调用方据此零读取拒绝)。

```python
def _resolve_ref(ref, ctx):
    if not ref or os.path.isabs(ref) or ".." in Path(ref).parts:   # 绝对/`..` 即拒
        return None
    root = Path(ctx.cfg["storage.spill_dir"]).expanduser().resolve()
    cand = (root / ref).resolve()                                  # 解符号链接后归一
    if not str(cand).startswith(str(root)):                        # 最终落点必须在根内
        return None
    if cand.parent.name != Path(ref).parts[0]:                     # 首段必须是会话目录
        return None
    return cand
```

**参数表**:`ref`=SpillMeta.ref;`ctx`=门面。**返回**:containment 通过的绝对 Path,否则 None。**异常表**:无(失败以 None 表达)。

**关联测试**:DIS-SEAM §6.1 G3 全变体(`../../s-other/x.jsonl`/绝对路径/链接)、test_f039_spill.py 越权零读取断言。

### `def register(registry) -> list[str]` — 读工具登记(DIS-SEAM §6.1 ⑥ announce)

**功能**:把 LLM 可见的 `storage.spill`(读)工具挂 tools 表——五要素齐备,danger=none,expose_to_llm=True;put 不注册为工具(职责 7);登记后 `locator.mount("storage.spill")`。

```python
def register(registry):
    defn = ToolDefinition(name="storage.spill", danger="none",
        description="按行读回 spill 溢出文件内容(ref/start/limit);输出超限时由工具结果中的 spill_ref 指引使用",
        schema={"type": "object",
                "properties": {"ref": {"type": "string"},
                               "start": {"type": "integer", "minimum": 0},
                               "limit": {"type": "integer", "minimum": 1, "maximum": 1000}},
                "required": ["ref"], "additionalProperties": False},
        handler=read, timeout_s=15, expose_to_llm=True)            # 只读面;put 不进工具表
    registry.register_tool(defn)
    return [defn.name]
```

**参数表**:`registry`=ToolRegistry。**返回**:注册名 list[str]。**异常表**:TLB-801(重名/保留名)/TLB-803(schema 不可编译),同族。

**关联测试**:test_f039_spill.py(工具表只有 read 无 put)、tools_registry schemas_for(storage.spill 对 LLM 可见)。

## 关联文档

| 文档 | 章节 | 关系 |
|---|---|---|
| PRD-Core.md | §5.4 F039、§2.4 步 8b | 验收伪代码(摘要含头 500+行数+大小)与管道消费位 |
| DIS-SEAM.md | §6.1 seam A 全九段 | Provider 接口/生命周期/错误码/GWT 权威 |
| EVENT-SCHEMA.md | §2 tool.result spill_ref、§6.3 PERS | spill_ref 字段级形状;错误码关联域 |
| ERR.md | §2.3 PERS-221/222/223、§6 排障 | 写失败/越权读/超限拒写处置与排查 |
| CFG.md | §3.6 storage.spill_dir / spill.max_per_file_bytes | 目录与单文件上限配置键 |
| SECURITY.md | §4 出口脱敏(INV-09)、I-3 泄露面 | spill 文件为明文出口;600+随会话清理 |
| tools_executor.py.md | 关4 `_finalize` | 唯一 put 消费方(>2KB summary+truncated+spill_ref) |
| tools_guard.py.md | g4 凭据读 | 越权读 spill 的管道侧兜底(同 PERS-222 防线族) |
