# specs/persistence.py.md — 编码规格

> **模块文件**:`pyharness/core/persistence.py` | **功能编号**:F011(核心)· F060 · PRD §3.6/§2.5.3 | **权威口径**:PRD-Core §3.6(JSONL 物理格式与落盘)、DIS-CORE §8(伪代码级)、EVENT-SCHEMA §5(物理格式)
> **一句话**:会话真源的物理形态——事件逐行 JSONL append(UTF-8 单行信封+payload)、攒批/强同步双速 flush、原子写(临时文件 + rename)、轮转(>50MB)、截断检测与 repair(备份 → 修复 → recovered 事件);崩溃恢复入口,唯一真源持久化是回放与审计的前提。

## 模块职责

1. **JSONL 唯一真源落盘**:一行一事件(Envelope + payload 拍平单行,`model_dump_json()`),路径 `{sessions_dir}/{session_id}.jsonl`(可配);只追加物理格式,无就地改写。
2. **双速 flush(§3.6)**:普通事件内存即对订阅者可见、persistence 攒批(≤0.5s 或 ≥64 条)flush;**强同步三类**(`user.message` / `guard.rejected` / `approval.*`)立即 write + flush,成功才返回——崩溃最多丢强同步点之后 ≤0.5s 事件,由 repair 的 recovered 事件声明。
3. **原子写**(DIS 细化,重写路径专用):凡"重写文件"操作(repair 截断/隔离重写、轮转合并)一律走 **同目录临时文件 + fsync + rename** 原子替换——rename 原子性保证任何时刻磁盘上要么是旧完整文件、要么是新完整文件,杜绝半写;正常 append 路径仍是纯追加句柄,不经临时文件。
4. **损坏检测与读取隔离**:`replay()` 严格行解析,坏行记 `PERS-201` 跳过并进隔离区,**绝不中断回放**(§3.8);`detect_truncation()` 定位崩溃遗留的尾部半行。
5. **repair 崩溃恢复入口(F060)**:备份 → 截断检测 → 中部坏行隔离 → seq 空洞定位 → 派生视图重建 → `session.recovered` 声明;幂等(重复执行结果一致);修复前强制备份。
6. **写通道状态机**:攒批/强同步/重试/暂停四级;3 次写失败 → PERS-202 + 会话暂停(拒新不丢旧,重试队列不丢事件)。

## 依赖

- **依赖方向**(单向,INV-08):`persistence` 被 `session` 依赖;**自身不反向 import session**——向上写 `system.error` / `session.recovered` 经注入的 `on_system_event` 回调(由装配层/bus 日志订阅者注入),保拓扑无回边。
- 外部依赖:`events.Envelope`(`model_validate_json` / `model_dump_json`)、`errors.raise_code`(F020)、`config`(sessions_dir/rotate_bytes=50MB/flush_batch=64/flush_interval=0.5s)、`log`。
- 单写者纪律(INV-07):文件句柄 append 模式,单进程单写者;轮转文件名带序号,重放按序合并。

## 类与函数清单

### 关键数据结构(字段级)

| 字段 | 类型 | 规则 |
|---|---|---|
| `session_id` | str | 本存储绑定的会话;path = `{dir}/{session_id}.jsonl` |
| `path` | Path | 主文件;轮转 → `{sid}.{n}.jsonl`(n 从 1 递增) |
| `_fh` | TextIO | 追加句柄(append 模式,UTF-8);单写者 |
| `_pending` | `deque[(seq, line)]` | 攒批缓冲(≤0.5s 或 ≥64 条触发 _flush_batch) |
| `_retry_q` | `deque[(seq, line)]` | 写失败重试队列;拒新不丢旧 |
| `_fail_streak` | int | 连续失败计数;≥3 → PERS-202 暂停会话 |
| `_quarantine` | `set[int]` | 坏行行号隔离区(repair 决策/用户查看) |
| `SYNC_TYPES` | frozenset | {user.message, guard.rejected, approval.requested/granted/denied/timeout} |
| `RepairReport` | fixed[]/quarantined[]/backup_path | repair 产出;由 session.recovered 事件承载(F060) |

### 写通道状态机(ASCII + 转移表)

```text
 NORMAL(攒批)──(满64/0.5s)──► FLUSHING ──成功──► NORMAL
   │                            │失败
   │ 强同步点(sync 三类)         ▼
   │   │                    ERROR_BACKOFF(重试≤3)──成功──► NORMAL
   │   ▼                        │3 败
   └─► SYNC_FLUSH ──失败──► SUSPENDED(PERS-202,会话暂停)──repair──► RECOVERING──► NORMAL
```

| 当前→目标 | 触发 | 动作/事件 |
|---|---|---|
| normal→flushing | 攒批满 64 条/0.5s 定时器 | 批量 write + flush |
| normal→sync_flush | 强同步三类事件 | 立即 write + flush,成功才返回 |
| flushing/sync→error_backoff | OSError | 入 _retry_q;重试 ≤3 次 |
| error_backoff→suspended | 3 次失败 | PERS-202 事件 + 暂停会话(拒新不丢旧) |
| suspended→recovering | repair(F060) | 备份/截断/隔离/原子重写/重建 |
| recovering→normal | 修复完成 | session.recovered 声明 |
| normal→normal | 文件 >50MB | _rotate → {sid}.{n}.jsonl |

### `def open_store(session_id: str, *, dir: Path | None = None) -> SessionStore` — 工厂/构造

**功能**:创建/打开会话存储:目录 `mkdir(mode=700)`;主文件 append 模式开句柄;文件已存在(重启恢复)则 `_seq` 基线从 replay 最后完整点取(由 session.open_session 重放);检测到尾部半行 → 告警提示先跑 repair(不自动截断——修复前强制备份)。

```python
def open_store(session_id, *, dir=None):
    d = dir or default_sessions_dir(); d.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = d / f"{session_id}.jsonl"
    trunc = detect_truncation(path)               # 只读检测:崩溃遗留尾部半行?
    if trunc is not None:
        log.warning("PERS-201 域", hint="尾部半行待 repair",
                    file=path, offset=trunc)      # open 不修,repair 先备份再修(F060)
    return SessionStore(session_id=session_id, path=path,
                        fh=open(path, "a", encoding="utf-8"))
```

**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `OSError` | 目录/文件不可写、权限不足 | PERS-202 | 查磁盘/权限;repair 后恢复 |

### `async def append(self, env: Envelope, sync: bool = False) -> None` — 事件落盘(F011 双速写)

**功能**:Envelope → 单行;`sync=True`(强同步三类)立即 write + flush,成功才返回;否则入 `_pending` 攒批(满 64 条即刷,0.5s 定时器兜底);写入后检查文件大小触发轮转。调用方:session.append 内部(强同步点)、总线日志订阅者。

```python
async def append(self, env, sync=False):
    line = env.model_dump_json() + "\n"           # 信封+payload 拍平单行(§3.6)
    if sync:                                      # 强同步点:user.message / guard.rejected /
        try:                                      #   approval.*(§3.6,EVENT-SCHEMA §1.2)
            self._fh.write(line); self._fh.flush()    # 成功才返回 → 崩溃一致性锚点
        except OSError as e:
            raise PyHError("PERS-202", ctx={"seq": env.seq,
                "why": str(e), "advice": "落盘通道故障;跑 repair(F060)"})
    else:
        self._pending.append((env.seq, line))     # 普通事件:攒批(内存即对订阅者可见)
        if len(self._pending) >= self.flush_batch:    # ≥64 条 → 立即批量 flush
            await self._flush_batch()
        # 0.5s 定时器:事件循环空闲也保证不积压过久(§2.5.3)——append 内不 sleep,
        # 由独立定时任务调 flush()(见 flush)
    if self._fh.tell() > self.rotate_bytes:       # >50MB → 轮转(§8.3.4)
        self._rotate()
```

**参数表**:`env` = 已校验 Envelope(本层不再校验,校验在 session.append);`sync` = 强同步开关。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 强同步路径 write/flush OSError | PERS-202 | 直接抛给调用方;repair 后重试 |
| `PyHError` | 异步路径 3 次重试仍败 | PERS-202 | system.error + 会话暂停(见 _flush_batch) |

### `async def flush(self, up_to_seq: int | None = None) -> None` — 公开 flush(session 强同步点调用)

**功能**:把 `_pending` 中 `seq ≤ up_to_seq` 的全部行写盘 + flush,成功才返回;`up_to_seq=None` = 全量(定时器/关闭前调用);session.append 在强同步三类时以此保证"该事件已物理落盘"。

```python
async def flush(self, up_to_seq=None):
    if not self._pending: return                  # 无积压:空操作
    take, keep = [], []
    for item in self._pending:
        (keep if (up_to_seq is not None and item[0] > up_to_seq) else take).append(item)
    self._pending = deque(keep)                   # 仅刷 up_to_seq 之前;之后的保留积压
    if not take: return
    try:
        for seq, line in take: self._fh.write(line)
        self._fh.flush()                          # 成功才返回(强同步契约)
    except OSError as e:
        self._retry_q.extend(take)                # 拒新不丢旧:回重试队列
        raise PyHError("PERS-202", ctx={"n": len(take), "why": str(e)})
```

**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | write/flush OSError | PERS-202 | 行已入重试队列不丢;repair 后恢复 |

### `async def _flush_batch(self) -> None` — 内部:攒批写 + 失败重试

**功能**:把 `_pending` 整体写出;OSError → 全部入 `_retry_q`,连续失败计数 +1;`_fail_streak ≥ 3` 或重试队列 >192 → `on_system_event(system.error, PERS-202)` + 置 suspended(暂停会话,拒新不丢旧)。

```python
async def _flush_batch(self):
    batch, self._pending = self._pending, deque()     # 先摘批:并发 append 不阻塞
    try:
        for seq, line in batch: self._fh.write(line)
        self._fh.flush()
        self._fail_streak = 0                          # 成功:复位失败计数
    except OSError:
        self._retry_q.extend(batch); self._fail_streak += 1
        if self._fail_streak >= 3 or len(self._retry_q) > 192:
            await self._on_system_event("system.error",   # 注入回调(不反向 import session)
                {"code": "PERS-202", "advice": "落盘通道故障,会话暂停;跑 repair(F060)"},
                sync=True)
            self._suspended = True
            raise PyHError("PERS-202", ctx={"advice": "会话暂停,repair 后恢复"})
        # 未达阈值:保留重试队列,等下一次 flush 定时器重试(拒新不丢旧)
```

### `def replay(self) -> Iterator[Envelope]` — 读取(坏行隔离,不中断)

**功能**:行迭代器(含轮转文件按序号合并):空行跳过;坏行记 PERS-201 + 进 `_quarantine` 隔离区,继续下一行;**绝不中断回放**(§3.8 版本演进:未知类型行跳过 + 警告,不中断);中部坏行不自动删(人类决策,repair 只隔离)。

```python
def replay(self):
    for p in self._rotated_paths() + [self.path]: # 轮转合并:先 {sid}.1..n 后主文件,按序
        with open(p, encoding="utf-8") as f:
            for no, line in enumerate(f, 1):
                line = line.rstrip("\n")
                if not line: continue             # 空行跳过(轮转残留容忍)
                try:
                    yield Envelope.model_validate_json(line)   # 信封+payload 一次校验
                except Exception as e:
                    self._quarantine.add(no)      # 中部坏行隔离(记跳不中断)
                    log.warning("PERS-201", file=p, line_no=no)
```

**异常表**:不抛——坏行 PERS-201 记跳暴露给 repair;回放永不因单行损坏中断(回放 = 恢复 = 审计同一路径)。

### `def detect_truncation(path: Path) -> int | None` — 截断检测(只读)

**功能**:检测崩溃遗留的尾部半行:文件非空且末字节不是 `\n` → 返回半行起始字节偏移(供 repair/repair 决策);完整结尾 → None。

```python
def detect_truncation(path):
    if not path.exists() or path.stat().st_size == 0: return None
    with open(path, "rb") as f:
        f.seek(0, 2); size = f.tell()
        tail = f.read(min(size, 4096))            # 读尾部窗口
        if tail.endswith(b"\n"): return None      # 完整行结尾:无截断
    # 半行定位:从尾部窗口找最后一个 b"\n",偏移 = size - (窗口内最后换行后字节数)
    last_nl = tail.rfind(b"\n")
    return size - (len(tail) - last_nl - 1) if last_nl >= 0 else 0
```

**异常表**:无(文件不存在/空 → None)。**关联测试**:GWT-P8-03(尾部半行 fixture → detect_truncation 返回非 None)。

### `async def repair(self, session_id: str | None = None) -> RepairReport` — 崩溃恢复入口(F060)

**功能**:校验 + 修复流水线,幂等:①**备份**(copy2 → `{sid}.jsonl.bak-{ts}`,修复前强制);②尾部半行截断;③中部坏行隔离(不自动删);④seq 空洞定位(对照 compacted/recovered 声明);⑤派生视图整体重建(FTS/KV,原则 1);⑥`on_system_event` 强同步写 `session.recovered(fixed/quarantined/backup)`;返回 RepairReport。

```python
async def repair(self, session_id=None):
    sid = session_id or self.session_id
    path = self.path
    if not path.exists():
        return RepairReport(fixed=[], quarantined=[], backup_path=None)   # 空会话:空报告
    backup = shutil.copy2(path, path.with_name(f"{sid}.jsonl.bak-{now_ts()}"))  # ① 先备份
    fixed = []
    off = detect_truncation(path)                 # ② 尾部半行(崩溃未完成事实不假装发生)
    if off is not None:
        self._rewrite_without_tail(off)           # 原子重写:temp+fsync+rename(§原子写)
        fixed.append("tail-truncated")
    for no, line in enumerate(readlines_strict(path), 1):   # ③ 中部坏行隔离(不删)
        try: Envelope.model_validate_json(line)
        except Exception: self._quarantine.add(no)
    if self._quarantine:
        fixed.append(f"quarantined:{sorted(self._quarantine)}")
    holes = self.seq_holes(path)                  # ④ 空洞:有 compacted/recovered 声明→合法
    if holes and not self._declared_by_compaction(holes):
        fixed.append(f"seq-holes:{holes}")        #   未声明 → 告警(F031 深查),不回填
    rebuild_derived_views(sid)                    # ⑤ 派生视图整体重建(原则 1,可弃重建)
    await self._on_system_event("session.recovered",   # ⑥ 声明(注入回调,强同步)
        {"fixed": fixed, "quarantined": sorted(self._quarantine),
         "backup": str(backup)}, sync=True)
    return RepairReport(fixed=fixed, quarantined=sorted(self._quarantine),
                        backup_path=backup)       # 幂等:重复 repair 结果一致
```

**参数表**:`session_id` 缺省用本存储绑定会话。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| 无 | 文件不存在 | — | 返回空报告(新会话) |
| `PyHError` | 不可修复损坏(如编码级坏块) | PERS-202 | 原文件保留为 `.corrupt-{ts}`,明确报错,不覆盖 |

**关联测试**:GWT-P8-03(先备份、半行截断、session.recovered(fixed=[tail-truncated])、重复 repair 幂等)、GWT-P8-02(中部坏行 → repair 后隔离区可查,坏行不删)。

### `def _rewrite_without_tail(self, cut_offset: int) -> None` — 原子截断重写(内部)

**功能**:repair 专用:把主文件截至 `cut_offset` 的内容写进**同目录临时文件** → fsync → `os.replace`(原子 rename)替换原文件 → 重开追加句柄;rename 原子性保证无半写窗口。

```python
def _rewrite_without_tail(self, cut_offset):
    tmp = self.path.with_name(self.path.name + f".tmp-{os.getpid()}")
    try:
        with open(self.path, "rb") as src, open(tmp, "wb") as dst:
            src.seek(0); dst.write(src.read(cut_offset))   # 保留全部完整行
            dst.flush(); os.fsync(dst.fileno())            # 数据落盘再换名
        os.replace(tmp, self.path)                         # 原子 rename:旧完整或新完整
        self._fh.close()
        self._fh = open(self.path, "a", encoding="utf-8")  # 重开追加句柄续写
    finally:
        if tmp.exists(): tmp.unlink(missing_ok=True)       # 失败清理,不留临时残留
```

### `def _rotate(self) -> None` — 轮转(内部)

**功能**:主文件 >50MB → 原子 rename 为 `{sid}.{n}.jsonl`(n 递增)→ 开新主句柄;重放按 `{sid}.1.jsonl … {sid}.n.jsonl → {sid}.jsonl` 序号合并;rename 失败同 PERS-202 通道。

```python
def _rotate(self):
    self._fh.flush()
    n = len(self._rotated_paths()) + 1
    target = self.path.with_name(f"{self.session_id}.{n}.jsonl")
    os.replace(self.path, target)                 # 原子 rename(同文件系统)
    self._fh.close()
    self._fh = open(self.path, "a", encoding="utf-8")
    log.info("jsonl rotated", file=str(target), size_mb=self.rotate_bytes // 1048576)
```

### `def seq_holes(path: Path) -> list[int]` — 空洞定位

**功能**:对照 seq 连续序列找缺失号;返回空洞列表;调用方(replay/repair)再对照 `context.compacted.ranges` / `session.recovered.fixed` 声明判定合法性——未声明空洞 → 告警 + F031 深查;seq 只前进,空洞只解释不回填(§3.4)。

```python
def seq_holes(self, path):
    seqs = []
    for env in self.replay(): seqs.append(env.seq)      # 复用坏行隔离读取
    holes = []
    for expect, got in zip(seqs, seqs[1:]):             # 相邻差 >1 = 空洞
        if got != expect + 1: holes.extend(range(expect + 1, got))
    return holes                                        # 声明判定在 repair 内做
```

### `def quarantine_info(self) -> dict` — 隔离区查询

**功能**:暴露坏行隔离区 `{path, line_nos, count}`(repair 决策/用户查看);只读。

## 边界与限制

1. **崩溃一致性**:强同步三类之后的事件最多丢 ≤0.5s,由 repair 截断 + recovered 声明;已落盘事实永不回滚。
2. **只追加物理格式**:正常写路径纯 append 无就地改写;一切重写(repair 截断/隔离、轮转)走**临时文件 + fsync + 原子 rename**,且修复前强制备份。
3. **单进程写**(INV-07):文件句柄 append 模式单写者;轮转文件名带序号,重放按序合并。
4. **敏感性**:事件 payload 全序列化(工具大结果只进 spill_ref,F039);日志全出口脱敏(INV-09:日志无凭据)。
5. **不反向依赖**:system.error / session.recovered 一律经注入回调写出,persistence 不 import session(拓扑无回边,INV-08)。

## 错误路径(汇总)

| 故障 | 处置 | 码 |
|---|---|---|
| 写失败(磁盘满/IO) | 强同步抛错;异步重试 3 次 → system.error + 会话暂停 | PERS-202 |
| 读遇坏行 | 记跳 + 隔离,暴露给 repair;中部不自动删 | PERS-201 |
| 尾部半行(崩溃) | repair 先备份 → 原子截断 → recovered 声明 | session.recovered |
| seq 空洞无声明 | 告警 + F031 深查(不回填) | PERS-201 域日志 |
| 派生索引落后 | repair 整体重建(原则 1,可弃重建) | rebuild |

## 关联测试(汇总)

GWT-P8-01 强弱同步分级 · GWT-P8-02 坏行隔离(repair 后隔离区可查) · GWT-P8-03 崩溃恢复(备份→截断→recovered,重复 repair 幂等) · GWT-P8-04 写失败暂停(OSError×3 → PERS-202 + 会话暂停,修复后重试队列不丢事件)——`tests/acceptance/test_f011_persistence.py`;里程碑:阶段1 CLI 单轮对话 → JSONL 落盘 → 重启回放一致。

## 关联文档

- PRD-Core.md §3.6(JSONL 物理格式/强同步三类/损坏行)、§2.5(启动/强同步三类)、§5.2 F011、§5.7 F060(repair)
- DIS-CORE.md §8(本模块伪代码级唯一权威,写通道状态机/repair 流水线)
- EVENT-SCHEMA.md §1.2(强同步三类语义)、§5(JSONL 物理格式)、§3.1(session.recovered payload)
- ERR.md §2.3(PERS-201/202 处置与可重试性)、§3(用户可见消息基准);ADD.md ADR-001/ADR-006
