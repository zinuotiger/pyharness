# specs/repair.py.md — 编码规格

> 目标代码:pyharness/repair.py,PRD F060(崩溃恢复,阶段 6 核心)+ F064(CLI repair 子命令桥)+ F011(事件溯源真源)。AI 编码 Agent 只读本文件即可写出崩溃恢复全量代码。权威源:PRD-Core.md F060(验收伪代码直译)、DEP.md §4.7A/§6(演示与排障台本)、DIS-CORE.md §8(原子写原语,冲突处 PRD 优先)、EVENT-SCHEMA.md(session.recovered 载荷)。全中文,仅 Python,禁 TS。禁止:修复前不备份、自动删除中部坏行(只能隔离,删留由用户定)、无声明 seq 空洞静默放行(告警→F031 深查)、修复非幂等(同日志重复 repair 结果一致)、把修复写成"抹除"(修复=显式化+可备份+可报告,不是悄悄删)。
> 与 core/persistence 的分工:persistence(核心)提供 JSONL 物理原语(原子写/严格行读/截断检测偏移);**本模块是 F060 修复策略与编排的唯一归属**(备份命名、隔离文件、空洞判定、报告、recovered 声明、交互决策)。core/persistence 早期草案中同名 repair 若已实现,应改为委托本模块,禁两套修复策略并存。备份命名冲突更正:persistence 草案的 `.jsonl.bak-{ts}` 与 PRD F060"原文件备份 `.corrupt-{ts}`"及 DEP §4.7A 输出示例 `{sid}.corrupt-<ts>.jsonl` 冲突——以 PRD(唯一权威)为准,统一 `{sid}.corrupt-{ts}.jsonl`。

## 模块职责
一句话:崩溃恢复管线(F060)——启动/打开会话时自动扫描全会话健康(尾部半行/中部坏行/seq 空洞/派生索引对账),对损坏会话执行"先备份 `.corrupt-{ts}` → 尾部半行截断 → 中部坏行隔离到 quarantine 文件(不删,主文件原子重写)→ seq 空洞定位(对照 compacted/recovered 声明)→ FTS/storage 派生视图整体重建 → 强同步追加 `session.recovered` 声明"的幂等修复,产出 RepairReport(fixed/quarantined/lost/backup);不可修复→明确报错且原文件备份留存;交互模式让用户决策"丢弃尾部 or 从最后完整点续跑 / 隔离行删留"。

## 依赖
| import | 用途 |
|---|---|
| pathlib、shutil、datetime | 路径操作、copy2 备份、`.corrupt-{ts}` 时间戳(UTC,毫秒,文件系统安全) |
| pyharness.core.persistence 原语 | readlines_strict(坏行记跳)、detect_truncation(尾部半行偏移)、原子重写(temp+fsync+rename)——仅物理原语,策略不在此 |
| pyharness.session(open_session、append) | 修复后打开/追加 session.recovered(强同步,actor=system) |
| pyharness.events(Envelope、check_seq_gap、SeqState) | 行解析校验、seq 空洞算法(声明区间合法化复用) |
| pyharness.errors(PyHError、raise_code) | PERS-201/PERS-202 唯一出口;禁现场造码 |
| pyharness.llm 无关 | 本模块零 LLM:崩溃恢复不依赖模型可用性(离线可修) |
| pyharness.session_query / FTS 重建器 | rebuild_derived_views:派生视图整体重建(原则 1) |
| pyharness.bus(EventBus) | recovered 声明后的总线广播(可选,审计以日志为准) |

## 数据结构表
| 结构 | 字段 | 说明 |
|---|---|---|
| `SessionHealth` | sid:str;path:Path;tail_truncated:bool;tail_offset:int\|None;bad_lines:list[int];holes:list[int];holes_declared:list[tuple[int,int]];index_stale:bool;healthy:bool | 只读健康报告(扫描产物,不修改文件) |
| `RepairReport` | sid:str;fixed:list[str];quarantined:list[QuarantineEntry];lost:int;backup_path:Path\|None;holes_alert:list[int];recovered_seq:int\|None | 修复产出;fixed 词条∈{tail-truncated,quarantine-N,views-rebuilt} |
| `QuarantineEntry` | line_no:int;raw:str;reason:str;archived:bool | 隔离记录(行号=主文件原行号;raw 截断 ≤8KB 防爆) |
| `RepairPolicy` | mode:"auto"\|"interactive";drop_tail:bool(默认 True=截断续跑);keep_quarantine:bool(默认 True);interactive 问询项 | 决策入参;auto=headless 用安全默认 |
| `RecoveredPayload` | fixed:list[str];lost:int;quarantined:int;backup:str;declared_holes:list[int] | session.recovered 事件载荷(PRD §3.2 表:fixed[]/lost/backup,扩展字段向后兼容) |

## 类与函数清单

### `async def auto_scan(sessions_dir: Path) -> list[SessionHealth]` — 启动/CLI 全会话自检
功能:遍历 `*.jsonl`(跳过轮转 `*.n.jsonl`/备份 `*.corrupt-*`/隔离 `*.quarantine-*`),逐个 scan_session 汇总 unhealthy;bootstrap 与 `repair` 子命令(无 sid)共用;扫描只读零修改。参数表:sessions_dir。返回:list[SessionHealth](含 healthy 会话,调用方过滤)。
伪代码:
```python
async def auto_scan(sessions_dir):
    if not sessions_dir.exists(): return []                        # 首次运行无目录:零报告
    out = []
    for path in sorted(sessions_dir.glob("*.jsonl")):
        if is_aux_file(path): continue                             # 轮转/备份/隔离不扫
        h = await scan_session(path)
        if not h.healthy: out.append(h)                            # 只返回需修会话
    return out
```
异常表:PermissionError|目录不可读|本地异常(日志)|跳过该目录并告警,不崩启动;OSError|glob 竞态|本地异常|跳过单文件。
关联测试:test_f060_repair.py(auto_scan 命中截断会话)、DEP §4.7A(重启修复台本)。

### `async def scan_session(path: Path) -> SessionHealth` — 单会话健康扫描(截断/坏行/空洞/索引对账)
功能:读尾部 4KB 判半行(detect_truncation 偏移);严格行迭代:JSON 解析失败行记 bad_lines(不中断);收集 seq 序列查空洞(check_seq_gap,对照 compacted/recovered 声明区间=合法);索引落后标记(派生视图末 seq < 日志末 seq)。参数表:path。返回:SessionHealth。
伪代码:
```python
async def scan_session(path):
    tail = detect_truncation(path)                                 # 非空且末字节≠\n → 偏移;None=完整
    bad, seqs, declared, last = [], [], [], 0
    for no, line in readlines_strict(path):                        # 坏行记跳不抛(PERS-201 语义)
        if line is None: bad.append(no); continue
        env = parse_or_none(line)
        if env is None: bad.append(no); continue
        seqs.append(env.seq); last = max(last, env.seq)
        if env.type in {"context.compacted", "session.recovered"}: # 声明区间合法化(§3.4)
            declared.append(declared_range(env))
    holes = [s for s in check_seq_gap(seqs, declared) if s]        # 空洞:未被声明覆盖的缺失 seq
    return SessionHealth(sid=path.stem, path=path, tail_truncated=tail is not None,
                         tail_offset=tail, bad_lines=bad, holes=holes,
                         holes_declared=declared, index_stale=last > fts_last_seq(path.stem),
                         healthy=not (tail or bad or holes) and not index_stale)
```
异常表:无(扫描不抛,损坏全部落报告字段);UnicodeDecodeError|非 UTF-8 段|PERS-201 语义|按坏行记 no,健康=False。
关联测试:test_f060_repair.py(人工截断/注入坏行/制造空洞后各字段断言)。

### `async def repair_session(ctx, sid: str, *, interactive: bool = False, policy: RepairPolicy | None = None) -> RepairReport` — F060 主入口(PRD 验收伪代码直译)
功能:管线编排:扫描→无损坏空报告(幂等:不动文件不追加事件)→**先备份** copy2 `.corrupt-{ts}.jsonl`→尾部截断→中部坏行隔离(交互确认删留)→空洞定位告警→派生视图重建→强同步追加 session.recovered→返回报告。参数表:ctx;sid;interactive=tty 问询开关;policy 覆盖(默认 auto+截断续跑)。返回:RepairReport。
伪代码:
```python
async def repair_session(ctx, sid, interactive=False, policy=None):
    path = ctx.storage.sessions_dir / f"{sid}.jsonl"
    if not path.exists(): return RepairReport(sid=sid, fixed=[], lost=0)   # 不存在=空报告(幂等)
    health = await scan_session(path)
    if health.healthy: return RepairReport(sid=sid, fixed=[], lost=0)      # 二次 repair:无动作(幂等)
    pol = policy or (interactive_confirm(health) if interactive else RepairPolicy(mode="auto"))
    backup = backup_file(path)                                      # ① 修复前强制备份 .corrupt-{ts}
    fixed, lost, kept = [], 0, []
    if health.tail_truncated and pol.drop_tail:                     # ② 尾部半行:截断(未完成不假装发生)
        lost = count_unfinished(path, health.tail_offset)           # 半行含未完成事件数(0/1)
        truncate_tail(path, health.tail_offset); fixed.append("tail-truncated")
    if health.bad_lines:                                            # ③ 中部坏行:隔离不自动删
        kept = await quarantine_lines(path, health.bad_lines, pol)  # 原子重写剔除+归档隔离文件
        fixed += [f"quarantine-{e.line_no}" for e in kept]
    if health.holes and not interactive:                            # ④ seq 空洞:无声明→告警 F031 深查
        fixed.append(f"seq-holes:{health.holes}")
    if health.index_stale or fixed: rebuild_derived_views(ctx, sid) # ⑤ FTS/storage 整体重建(原则 1)
    seq = await declare_recovered(ctx, sid, fixed, lost, kept, backup)  # ⑥ recovered 强同步声明
    return RepairReport(sid=sid, fixed=fixed, lost=lost, quarantined=kept,
                        backup_path=backup, recovered_seq=seq)
```
异常表:PyHError|备份/重写落盘失败|PERS-202|原文件未动,修复中止,报错建议查磁盘权限;PyHError|格式版本不符等不可修复|PERS-201|原文件已备 `.corrupt-{ts}`,明确报错;PermissionError|文件被占用(禁双开同会话,DEP §6)|PERS-202|提示关闭其他会话进程。
关联测试:test_f060_repair.py(截断/隔离/幂等/备份四断言)、DEP G6(备份覆盖后二次 repair 无 lost)。

### `def backup_file(path: Path) -> Path` — 修复前强制备份
功能:copy2 整文件到 `{sid}.corrupt-{utc_ms}.jsonl`(PRD F060 命名,覆盖 persistence 草案旧命名);已存在同 ts 备份(重试/并发)→不重复备份(幂等);备份权限 600。参数表:path。返回:Path 备份路径。
伪代码:
```python
def backup_file(path):
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")[:-3]   # 毫秒级 UTC
    dst = path.with_name(f"{path.stem}.corrupt-{ts}.jsonl")
    if dst.exists(): return dst                                   # 幂等:同 ts 已备不重备
    shutil.copy2(path, dst)                                       # 元数据一并保留
    os.chmod(dst, 0o600)                                          # 会话日志同权限纪律
    return dst
```
异常表:OSError|复制失败/空间不足|PERS-202 包装|修复中止(备份优先于一切修复动作)。
关联测试:test_f060_repair.py(备份存在且字节一致/命名规范)。

### `def truncate_tail(path: Path, offset: int) -> None` — 尾部半行截断(原子)
功能:把文件截断到 offset(最后完整行末尾);走同目录临时文件+fsync+rename 原子替换或 os.truncate(fd, offset)+fsync(单文件截断可原子);半行内未完成事件按 lost 声明,绝不尝试补全。参数表:path;offset=detect_truncation 返回。返回:None。
伪代码:
```python
def truncate_tail(path, offset):
    if offset is None or offset <= 0: return                     # 无半行/空文件不动
    with open(path, "r+b") as f:
        f.truncate(offset); f.flush(); os.fsync(f.fileno())      # 截断点=最后完整 \n 后
    log.warning("repair: 尾部半行截断于 offset=%d", offset)
```
异常表:OSError|truncate/fsync 失败|PERS-202|已备份存在,报错中止(文件可能半截,禁止继续)。关联测试:test_f060_repair.py(截断后逐事件可解析/续跑 seq 正确)。

### `async def quarantine_lines(path: Path, bad: list[int], pol: RepairPolicy) -> list[QuarantineEntry]` — 中部坏行隔离(不删,删留用户定)
功能:逐行读主文件,坏行行抽到 `{sid}.quarantine-{ts}.jsonl`(行号/原文/原因,≤8KB/条),好行写入临时文件,fsync+rename 原子替换主文件;pol.interactive 时先问"隔离 N 行(默认)还是保留原地报警";隔离≠删除:数据始终可查可恢复。参数表:path;bad;pol。返回:list[QuarantineEntry]。
伪代码:
```python
async def quarantine_lines(path, bad, pol):
    badset, kept, tmp, no = set(bad), [], path.with_suffix(".jsonl.tmp"), 0
    if pol.interactive and not confirm(f"隔离 {len(bad)} 行坏数据? [Y/n]"):   # 用户选保留原地
        return [QuarantineEntry(line_no=n, raw=read_short(path, n), reason="user-keep") for n in bad]
    qpath = path.with_name(f"{path.stem}.quarantine-{now_ts()}.jsonl")
    with open(tmp, "w", encoding="utf-8") as out:                 # 原子重写(好行全保留)
        for no, line in readlines_raw(path):
            if no in badset:
                raw = line[:8192]; kept.append(QuarantineEntry(line_no=no, raw=raw, reason="parse-fail", archived=True))
                qpath.write_text(qpath.read_text() + json.dumps({"line_no": no, "raw": raw}) + "\n" if not qpath.exists()
                                 else "", encoding="utf-8")       # 防爆:逐条追加写隔离档
            else: out.write(line)
    os.replace(tmp, path); fsync_dir(path.parent)                 # rename 原子替换,无半写(DIS-CORE §8 原语)
    return kept
```
异常表:OSError|临时文件/rename 失败|PERS-202|已备份存在;隔离档写失败不阻断主流程(记日志,报告 quarantined 标 archived=False)。关联测试:test_f060_repair.py(坏行隔离可查/主文件其余事件完整)。

### `def locate_holes(health: SessionHealth) -> list[int]` — seq 空洞定位(声明合法化)
功能:对扫描 seq 序列求缺失值;被 context.compacted(压缩区间)或 session.recovered(修复区间)声明覆盖的缺失=合法空洞不告警;未声明空洞→告警并触发 F031 深查线索(不自动补 seq,append 永远 max+1)。参数表:health。返回:list[int] 未声明空洞。
伪代码:
```python
def locate_holes(health):
    if not health.holes: return []                               # 无缺失:干净
    undeclared = []
    for h in health.holes:
        if any(lo <= h <= hi for lo, hi in health.holes_declared): continue   # 声明覆盖:合法(compaction/repair)
        undeclared.append(h)
    if undeclared: log.warning("repair: 未声明 seq 空洞 %s → F031 深查线索", undeclared)
    return undeclared
```
异常表:无(纯函数)。关联测试:test_f060_repair.py(compacted 声明空洞不告警/无声明告警)、test_f031 联动。

### `def rebuild_derived_views(ctx, sid: str) -> None` — 派生视图整体重建(原则 1)
功能:FTS 全文索引与 storage KV 全部丢弃重建:从 JSONL 重放逐事件重建(非增量修补);派生视图可整体重建=索引对账失败的终极手段;重建期间该会话禁写(open 前完成)。参数表:ctx;sid。返回:None。
伪代码:
```python
def rebuild_derived_views(ctx, sid):
    log.info("repair: 重建派生视图 sid=%s (FTS/storage)", sid)
    ctx.session_query.rebuild(sid)                               # 丢 FTS 表→重放填充(单点入口)
    ctx.storage.kv_rebuild(sid)                                  # KV 派生缓存重建
    verify_rebuild(ctx, sid)                                     # 对账:日志末 seq == 视图末 seq,不一致→PERS-201
```
异常表:PyHError|重建后对账不一致|PERS-201|视图落后不静默,报告 index 异常让用户决策;OSError|索引写失败|PERS-202 包装|原日志不受影响(索引=派生,可再建)。
关联测试:test_f060_repair.py(索引落后→重建一致)、FTS 对账 GWT。

### `async def declare_recovered(ctx, sid, fixed, lost, kept, backup) -> int` — recovered 事件声明(修复是事件不是抹除)
功能:构造 session.recovered 载荷(fixed/lost/quarantined/backup/declared_holes),actor=system 强同步 append;声明是修复的审计落点与"空洞合法化"依据(后续 replay 不再告警);fixed 为空且无动作→不追加(幂等,避免无意义事件)。参数表:ctx;sid;fixed;lost;kept;backup。返回:int recovered 事件 seq。
伪代码:
```python
async def declare_recovered(ctx, sid, fixed, lost, kept, backup):
    payload = {"fixed": fixed, "lost": lost,
               "quarantined": len(kept), "backup": str(backup)}
    if not fixed and not kept:                                   # 无修复动作:不声明(幂等)
        return None
    return await ctx.session.append("session.recovered", payload=payload,
                                    actor="system", sync=True)   # 强同步:声明先于一切续写
```
异常表:PyHError|append 落盘失败|PERS-202|修复动作已完成但声明失败:报错(文件已修,声明可下次 repair 补——幂等保证不重复动作)。关联测试:test_f060_repair.py(recovered 事件含 fixed/lost/backup)、EVENT-SCHEMA 载荷断言。

### `def interactive_confirm(health: SessionHealth) -> RepairPolicy` — 交互决策(丢弃尾部 or 从最后完整点续跑)
功能:tty 下问询:尾部半行→[T]截断并续跑(默认,从最后完整 seq+1 续写)/[K]保留原样退出(不修,留待人工);中部坏行→隔离默认;空洞→提示 F031 深查选项。headless(auto)=全部安全默认:截断+隔离,不悬挂等待。参数表:health。返回:RepairPolicy。
伪代码:
```python
def interactive_confirm(health):
    print(f"[repair] {health.sid} 检测:尾部截断={health.tail_truncated} "
          f"坏行={len(health.bad_lines)} 空洞={health.holes}")
    if health.tail_truncated:
        ans = input("尾部半行未完成事实,已备份。[T]截断续跑(默认) [K]保留退出: ").strip().lower()
        if ans.startswith("k"): return RepairPolicy(mode="interactive", drop_tail=False)   # 用户保留,会话不可开
    q = input(f"隔离 {len(health.bad_lines)} 行坏数据? [Y/n] ").strip().lower() if health.bad_lines else "y"
    return RepairPolicy(mode="interactive", drop_tail=True, keep_quarantine=not q.startswith("n"))
```
异常表:EOFError|无输入(tty 半开)|无码|回退 auto 默认(安全侧:截断+隔离)。关联测试:test_f060_repair.py(交互分支 monkeypatch 输入)、DEP §4.7A 台本输出。

### `def view_quarantine(sid: str, sessions_dir: Path) -> list[QuarantineEntry]` — 隔离区查看(删留决策数据源)
功能:列出 `{sid}.quarantine-*.jsonl` 全部隔离条目(行号/原文/原因/归档);用户据此决定删(彻底删=人工 rm 或 CLI 确认后删文件,事件不留痕不适用——隔离文件非日志);repair 本身永不删除。参数表:sid;sessions_dir。返回:list[QuarantineEntry]。
伪代码:
```python
def view_quarantine(sid, sessions_dir):
    out = []
    for q in sorted(sessions_dir.glob(f"{sid}.quarantine-*.jsonl")):
        for line in q.read_text(encoding="utf-8").splitlines():
            if not line.strip(): continue
            e = json.loads(line)                                 # 隔离档结构自持(非 Envelope,不强校验)
            out.append(QuarantineEntry(line_no=e["line_no"], raw=e["raw"], reason="parse-fail", archived=True))
    return out
```
异常表:json.JSONDecodeError|隔离档自身损坏|本地异常|跳过该行并日志(隔离档非真源,损坏无害);FileNotFoundError|无隔离文件|无码|返回空表。
关联测试:test_f060_repair.py(隔离后 view_quarantine 可查/删留决策演示)。

## 关联文档
1. PRD-Core.md §5.7 F060(功能/边界表/验收伪代码;备份 `.corrupt-{ts}` 权威命名)+ F011(真源只追加语义)+ §3.4(空洞合法化:compacted/recovered 声明)。
2. DEP.md §4.7A(repair 演示台本:截断隔离 lost/backup/recovered 输出)、§6 排障表(会话损坏修复路径)、DEP G6(幂等验收:备份覆盖二次 repair 无 lost)。
3. DIS-CORE.md §8.3.3/§8.4(原子写原语与 REPAIR 状态机;修复策略归属本模块,persistence 仅留原语)。
4. EVENT-SCHEMA.md §3(session.recovered 载荷 fixed[]/lost/backup 字段级定义与示例)。
5. specs/persistence.py.md(原语契约:readlines_strict/detect_truncation/原子重写)、specs/session.py.md(open_session 先 repair 后执行、append 强同步)、specs/cli.py.md(repair 子命令桥)、specs/desktop.py.md(启动自检复用 auto_scan)。
6. ERR.md §2.3(PERS-201 隔离/PERS-202 落盘语义)、§1.4(错误跨边界只回 code+advice)。
7. ADD.md ADR-001(真源纪律:修复走声明不改写历史)、ADR-006(原子写/只追加闭环)。
