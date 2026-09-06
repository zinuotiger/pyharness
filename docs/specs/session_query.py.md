# specs/session_query.py.md — 编码规格

> **模块文件**:`pyharness/core/session_query.py` | **功能编号**:F057(会话全文查询 FTS5 核心)· 联动 F009/F011(FTS 索引=JSONL 的**派生副本**,原则 1)/F042(session.renamed 标题入索引)/F063(user.message_edited 级联更新索引)/F060(repair 索引对账:落后即整体重建)/F058(compaction 不折叠 FTS——索引独立于压缩)/F064(CLI `search` 子命令) | **权威口径**:PRD-Core §5.6 F057(功能与验收伪代码权威:events_fts/攒批 200ms/查询超时 5s/中文 ≥2 字英文 ≥3/≤20 条)、EVENT-SCHEMA §4.2(派生视图纪律:FTS 是日志投影,可整体丢弃重建)、EVENT-SCHEMA §8.3(只读边界:视图只读日志投影)、CFG.md §3.7(storage.fts.index_batch_ms=200/query_timeout_s=5/result_limit=20)、ERR.md §2.3(PERS-2xx 索引存储故障语义)、SECURITY.md §8.3(日志含敏感→FTS 索引同权限 600、不进版本库);冲突以 PRD-Core 为准
> **一句话**:给全会话历史建 **SQLite FTS5 派生索引**(F057)实现\"上次让它整理过某文件夹\"式检索——索引**只做可整体重建的派生副本,永不写回、更不替代 JSONL 真源**(原则 1:任何一行索引可删了重放日志再建);订阅 append 事件异步攒批(200ms)增量索引,关键词/短语查询返回命中(带 **seq** 锚点,下游按 seq 回原会话 JSONL 取全事件);中文分词弱用\"二元切分+查询端连续子串补偿\"(unicode61 局限如实标注);查询超时 5s 软降级返回空+告警;索引损坏/落后由 rebuild() 或 repair(F060)整体重建。

## 模块职责

1. **派生索引,不碰真源(F057+原则 1)**:索引表 `events_fts` 的每一行都从 JSONL 事件派生——行键=(session_id, seq);**FTS 模块对 JSONL 只读**;索引可随时 DELETE 全表重建(rebuild),JSONL 一行不改;禁止任何\"索引写回日志/日志补索引\"的双写路径(INV-01)。索引物理位置 `~/.pyharness/index.db`(SQLite WAL,与 storage KV 同库或分库均可,权限 600)。
2. **增量订阅与攒批**:`on_event` 订阅总线持久事件→按**索引白名单**抽取文本(user.message.content/agent.message.content/llm.response.content/tool.result.summary/session.renamed.new_title/user.message_edited.new_content(更新 target 行)/context.compacted.summary)——攒批:≥200ms(index_batch_ms)或 ≥64 条批量 INSERT OR IGNORE;瞬时事件(llm.chunk)永不索引。
3. **查询**:`query(q, limit=20, session_id?, actor?, after_ts?)`——查询词解析:`_cjk_terms`(每连续中文字符窗滑 2-gram)+`_latin_terms`(≥3 字符英文/数字词),短于下限的中文单字/英文 2 字符 → EVT-100 拒;词组以 OR 合并并做**连续子串补偿**(把整串再作为短语条件 OR 上,补 unicode61 对中文只按标点分词之不足);`snippet()` 取上下文;按 FTS5 rank 排序截断 ≤limit;**5s 超时软降级**:返回 {\"hits\":[], \"timed_out\":True} + WARNING 日志,不抛不挂(检索属 UX 场景,降级优于报错)。
4. **结果关联原事件 seq**:每条 Hit 必带 `(session_id, seq, type, ts, snippet, rank)`——seq 即 JSONL 行号锚;CLI/UI 拿到 Hit 后经 `session.events_between(seq, seq)` 或打开该会话回放,可跳到原事件全量上下文(含未索引字段);seq 单调保证\"搜到就能回放\"。
5. **索引重建与对账(F060 联动)**:`rebuild(session_id=None)` 全量/单会话重建:DELETE 该会话索引行→重放 JSONL→逐事件重插;崩溃后半索引/版本升级/repair 对账发现落后(F031 syscheck.fail 或 F060)时调用;FTS 与日志不一致永远以**重放日志**为准(派生服从真源)。
6. **生命周期与级联删除**:enter 开库建表挂订阅;detach flush+退订+关库(幂等);`delete_session(sid)` 联级删该会话索引行(会话删除 F064/存储清理时调用;PRD F057 边界\"删会话级联删索引\");中文局限与查询端补偿策略在 docstring 与 UI 提示中如实标注(PRD 要求\"中文分词局限查询端补偿并如实标注\")。

## 依赖

- **依赖方向**:session_query 位于会话周边层(ctx.session 命名空间,F057-F060 组);消费 `session.append` 事件流(总线订阅)、`persistence.replay`(rebuild 读真源)、`session.events_between`(Hit→原事件跳转);被 CLI `search`/桌面 UI 搜索框/repair(F060)/F031 自检消费;只读投影,禁止反向写 session。
- **消费方**:CLI `pyharness search \"备份\"`(F064)、桌面轨迹搜索、repair 索引对账(F060)、测试(test_f057_fts)。
- **外部依赖**:sqlite3(stdlib,FTS5 需 SQLite ≥3.9,Python 3.11 自带)、errors(PyHError);无 LLM 依赖(纯文本索引,禁调模型——搜索不该花钱,INV-02 也禁)。

## 数据结构表

| 字段 | 类型 | 规则 |
|---|---|---|
| `events_fts` | FTS5 虚表 | `session_id UNINDEXED, seq UNINDEXED, type UNINDEXED, ts UNINDEXED, title, content`;行键 (session_id, seq) 唯一;content=白名单事件文本 |
| `_WHITELIST` | dict[str, extractor] | 索引事件类型→文本抽取器:user.message/agent.message/llm.response(content 非空)/tool.result(summary)/session.renamed(new_title)/user.message_edited(更新 target 行)/context.compacted(summary) |
| `Hit` | dataclass | `session_id: str / seq: int / type: str / ts: str / snippet: str / rank: float`(seq=JSONL 锚点,跳原事件用) |
| `_pending` | list[tuple] | 攒批缓冲;≥64 条或 200ms 定时器触发 flush |
| `_db_path` | Path | `~/.pyharness/index.db`;WAL 模式;线程池访问串行写(§2.5.4) |
| `_MIN_CJK` / `_MIN_LATIN` | int | 2 / 3(F057 边界:中文≥2 字、英文≥3 字符) |
| `_QUERY_TIMEOUT_S` / `_RESULT_LIMIT` | int | 5 / 20(读 config storage.fts.*) |

## 类与函数清单

### `async def enter(self, ctx) -> None` — 装载(开库+建表+挂订阅)

**功能一句话**:开 SQLite(WAL/权限 600),`CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(...)`;挂总线订阅持久事件→`on_event`;enter 失败回 detached。

```python
async def enter(self, ctx):
    if self._db is not None:
        return                                     # 幂等
    self._db = sqlite3.connect(self._db_path)      # 权限 600,不进版本库
    self._db.execute("PRAGMA journal_mode=WAL")
    self._db.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5("
        "session_id UNINDEXED, seq UNINDEXED, type UNINDEXED, "
        "ts UNINDEXED, title, content)")
    bus.subscribe("*", self.on_event,               # 持久事件流(瞬时被过滤)
                  owner="cap:session_fts", when=self._is_indexable)
```

**参数表**:`ctx`=会话上下文。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 开库/建表失败(磁盘/权限/旧版 SQLite 无 FTS5) | PERS-202 | 修存储或换 SQLite;重试 enter |

**关联测试**:test_f057_fts(建表成功/幂等 enter)。

### `async def announce(self, ctx) -> None` — 对外宣布

**功能一句话**:注册 Definition `session.fts_query`(expose_to_llm=True,LLM 可自查旧会话记忆,danger=none)+ mount `ctx.session.fts`;失败回滚回 detached。

```python
async def announce(self, ctx):
    defn = Definition(name="session.fts_query", ns="ctx.session",
            schema=FtsQuerySchema, danger="none",
            desc="全历史全文搜索(关键词/短语),返回带 seq 的命中与摘要片段")
    tools.register_tool(defn)                       # LLM 可见(F049 同类五步)
    locator.mount("ctx.session.fts", self)
    await session.append("registry.updated", op="attach", kind="capability",
                         key="ctx.session.fts", actor="plugin")
```

**参数表**:`ctx`。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 重名/保留名 | TLB-801/BUS-002 | 换名或先 detach |

**关联测试**:DIS-SEAM 生命周期(announce 失败回滚无残留)。

### `async def detach(self, ctx) -> None` — 摘除

**功能一句话**:flush 未落批→退订→关库置 None;幂等(已摘直接返回);半卸优先。

```python
async def detach(self, ctx):
    if self._db is None:
        return                                     # 幂等
    await self.flush()                             # ① 清空攒批
    bus.unsubscribe_all(owner="cap:session_fts")   # ② 摘订阅
    tools.unregister("session.fts_query")
    locator.unmount("ctx.session.fts")
    self._db.close(); self._db = None              # ③ 关库
```

**参数表**:`ctx`。**异常表**:无(逐步骤容错,失败记日志继续摘)。**关联测试**:detach 幂等/重复调用安全。

### `async def on_event(self, type_, env) -> None` — 事件→索引攒批

**功能一句话**:白名单事件的订阅回调:抽取可索引文本入 `_pending`;user.message_edited 走\"修正 target 行\"(派生视图允许更新,真源不动);攒满 64 条或到 200ms 定时 flush。

```python
async def on_event(self, type_, env):
    if type_ == "user.message_edited":             # F063 级联:索引行换新版
        self._pending_edits.append(
            (env.session_id, env.payload["target_seq"], env.payload["new_content"]))
        return
    text = _WHITELIST.get(type_)
    if text is None:
        return                                     # 白名单外不索引(含 guard.*)
    row = (env.session_id, env.seq, type_, env.ts,
           env.payload.get("new_title", ""), text(env))
    self._pending.append(row)
    if len(self._pending) + len(self._pending_edits) >= 64:
        await self.flush()                         # 条数闸立即刷
```

**参数表**:`type_/env`=总线事件。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | flush 落库失败 | PERS-202 | 记 system.error;索引稍后 rebuild 自愈(派生可弃) |

**关联测试**:test_f057_fts(白名单外事件不进索引、edited 更新行)、test_f063(编辑后搜索命中新版)。

### `async def flush(self) -> int` — 批量落库

**功能一句话**:把 `_pending` 批量 `INSERT OR IGNORE`(重复 seq 幂等)、`_pending_edits` 更新对应行,提交;返回写入数;SQLite WAL 串行写(§2.5.4)。

```python
async def flush(self):
    rows, edits = self._pending, self._pending_edits
    self._pending, self._pending_edits = [], []    # 先取走(失败可回填)
    n = 0
    try:
        with self._db:                             # WAL 事务
            cur = self._db.executemany(
                "INSERT OR IGNORE INTO events_fts VALUES(?,?,?,?,?,?)", rows)
            n += cur.rowcount
            for sid, tseq, newc in edits:          # 派生视图更新(真源不动)
                self._db.execute(
                    "UPDATE events_fts SET content=? WHERE session_id=? AND seq=?",
                    (newc, sid, tseq))
    except sqlite3.Error as e:
        self._pending += rows; self._pending_edits += edits   # 回填拒新不丢
        raise PyHError("PERS-202", ctx={"hint": f"FTS 索引落库失败: {e}",
            "advice": "运行 repair(F060) 重建索引"})
    return n
```

**参数表**:无。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | SQLite 写失败(磁盘/锁) | PERS-202 | 回填缓冲;repair 重建;不中断主流程 |

**关联测试**:test_f057_fts(攒批/幂等 INSERT/edited 更新)。

### `async def query(self, q: str, *, limit: int = 20, session_id: str | None = None, actor: str | None = None, after_ts: str | None = None) -> QueryResult` — 全文查询(F057 验收直译)

**功能一句话**:解析查询词(中文 2-gram+英文 ≥3)→拼 MATCH→SQLite 查询带 `snippet()`→按 rank 取 ≤limit 条 Hit;5s 超时**软降级**返回 {\"hits\":[],\"timed_out\":True} 不抛;词长低于下限 → EVT-100。

```python
async def query(self, q, *, limit=20, session_id=None, actor=None, after_ts=None):
    terms = self._cjk_terms(q) + self._latin_terms(q)
    if not terms:                                  # 中文<2 且英文<3 → 拒
        raise PyHError("EVT-100", ctx={"hint": f"查询词过短: {q!r}",
            "advice": "中文≥2 字、英文≥3 字符;或直接给整句短语"})
    cond = " OR ".join(f'"{t}"' for t in terms)
    if len(q) >= 4: cond += f' OR "{q}"'           # 连续子串补偿(整串短语)
    sql = ("SELECT session_id, seq, type, ts, snippet(events_fts, 5, '[', ']', '…', 24), "
           "rank FROM events_fts WHERE events_fts MATCH ? AND seq>0")
    args = [cond]
    if session_id: sql += " AND session_id=?"; args.append(session_id)
    if actor:      sql += " AND actor=?";          args.append(actor)   # actor 需冗余列(见注)
    if after_ts:   sql += " AND ts>=?";            args.append(after_ts)
    sql += f" ORDER BY rank LIMIT {min(limit, self._result_limit)}"
    def _run():                                    # 同步查询扔线程池+5s 闸
        try:
            return [Hit(*r) for r in self._db.execute(sql, args)]
        except sqlite3.OperationalError:            # MATCH 语法错 → 空集
            return []
    try:
        hits = await asyncio.wait_for(asyncio.to_thread(_run),
                                      timeout=self._query_timeout_s)
        return QueryResult(hits=hits, timed_out=False)
    except asyncio.TimeoutError:                    # 超时软降级:不抛不挂
        log.warning("fts query timeout q=%r", q)
        return QueryResult(hits=[], timed_out=True)
```

**参数表**:`q`=查询词/短语;`limit`≤20(超限钳到 config result_limit);`session_id/actor/after_ts`=过滤(会话/actor/时间,PRD F057 输出要求)。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 词长低于下限 | EVT-100 | 加字/给整句 |
| `PyHError` | 库通道故障(非超时) | PERS-202 | repair 重建索引 |
| `asyncio.TimeoutError` | >5s | (软降级) | 返回 timed_out=True,缩小查询面 |

**注**:actor 过滤需在 events_fts 冗余 actor 列(UNINDEXED),enter 建表时一并声明。**关联测试**:test_f057_fts(命中/中文 2-gram/对账)、GWT-F057-01(搜\"备份\"命中旧会话且带 seq)、GWT-F057-02(超短词拒)。

### `async def rebuild(self, *, session_id: str | None = None) -> int` — 索引重建(F060/repair 对账)

**功能一句话**:按会话 DELETE 索引行→`persistence.replay` 重放该会话 JSONL→逐事件重插(与 on_event 同抽取逻辑);返回重建行数;单会话/全量两种粒度;派生视图整体重建的唯一权威入口。

```python
async def rebuild(self, *, session_id=None):
    await self.flush()                             # 先清缓冲防新旧混写
    with self._db:
        if session_id:
            self._db.execute("DELETE FROM events_fts WHERE session_id=?",
                             (session_id,))
        else:
            self._db.execute("DELETE FROM events_fts")     # 全量清空(派生可弃)
    count = 0
    for env in persistence.replay(session_id=session_id):  # 只读真源
        text = _WHITELIST.get(env.type)
        if text is None: continue                  # 与 on_event 同一白名单
        self._db.execute("INSERT OR IGNORE INTO events_fts VALUES(?,?,?,?,?,?)",
            (env.session_id, env.seq, env.type, env.ts,
             env.payload.get("new_title", ""), text(env)))
        count += 1
    with self._db: pass                            # 提交
    return count
```

**参数表**:`session_id`=None 全量/指定单会话。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 回放遇坏行(真源损坏) | PERS-201 | 坏行隔离不中断(与 replay 同纪律);repair 定删留 |
| `PyHError` | 落库失败 | PERS-202 | 修存储后重试 rebuild |

**关联测试**:test_f057_fts(重建后与增量索引逐行一致=INV-03 派生比对)、test_f060_repair(索引对账落后→重建)。

### `async def delete_session(self, session_id: str) -> None` — 联级删索引

**功能一句话**:会话删除时联级删除其全部索引行(PRD F057 边界\"删会话级联删索引\";JSONL 归档/删除由存储层负责,FTS 只清自己的派生行)。

```python
async def delete_session(self, session_id):
    with self._db:
        self._db.execute("DELETE FROM events_fts WHERE session_id=?",
                         (session_id,))            # 派生行可删;真源不在本模块管
```

**参数表**:`session_id`。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 删除失败 | PERS-202 | 重试;索引可整库重建兜底 |

**关联测试**:test_f057_fts(删除会话后搜不到该会话命中)。

### `def _cjk_terms(self, q: str) -> list[str]` / `def _latin_terms(self, q: str) -> list[str]` — 查询词解析(中文 2-gram 补偿)

**功能一句话**:纯函数分词器:CJK 连续段滑窗产 2-gram(每段 ≥2 字),拉丁段按 `\\w+` 取 ≥3 字符词;unicode61 中文弱分词的查询端补偿核心,单测独立覆盖。

```python
_CJK = re.compile(r"[\u4e00-\u9fff]+")
_WORD = re.compile(r"[A-Za-z0-9_]{3,}")
def _cjk_terms(self, q):
    out = []
    for seg in _CJK.findall(q):                    # 每段中文连续串
        for i in range(len(seg) - 1):
            out.append(seg[i:i+2])                 # 2-gram 滑窗
    return out                                     # 例:"整理文件夹"→整理/理文/文件/夹?→ 补整串短语
def _latin_terms(self, q):
    return _WORD.findall(q)                        # ≥3 字符才入词(英文下限)
```

**参数表**:`q`=原始查询。**异常表**:无(纯函数)。**关联测试**:GWT-F057-03(分词器边界:单字中文/2 字符英文产出空→上层 EVT-100;混合串正确拆 2-gram+拉丁词)。

## 关联文档

- PRD-Core.md §5.6 F057(功能与验收伪代码权威:events_fts/二元切分/查询端补偿/攒批/级联删/5s 超时/≤20 条)、§4.1(原则 1:索引=派生视图,无第二真源)
- EVENT-SCHEMA.md §4.2(reducer 与视图纪律:FTS 是日志投影可整体重建)、§8.3(只读边界:视图只读日志投影)
- CFG.md §3.7(`storage.fts.index_batch_ms=200 / query_timeout_s=5 / result_limit=20`)、N4 基准(FTS ≤100ms @10 万事件)
- ERR.md §2.3(PERS-201/202 索引存储故障语义)、§2.2(EVT-100)
- SECURITY.md §8.3(日志含敏感文本→索引权限 600、不进版本库、按策略清理)、INV-09(索引内容同样过脱敏出口)
- specs/session.py.md(事件写入口/seq 语义,索引的喂料源)、specs/persistence.py.md(replay 重建源)、specs/commands.py.md(CLI `search` F064 消费方)
