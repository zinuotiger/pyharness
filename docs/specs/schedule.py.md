# specs/schedule.py.md — 编码规格

> **模块文件**:`pyharness/core/schedule.py` | **功能编号**:F048(schedule 定时任务)· F043(到点入队)· F011(持久化联动)· F014/F015(危险模板深夜禁触发) | **权威口径**:PRD-Core §5.5 F048(功能与验收伪代码权威)、EVENT-SCHEMA §3.5.3(schedule.trigger 字段级权威)、EVENT-SCHEMA §7(词表外新增类型登记规则)/§8.1(落盘矩阵)、ERR.md §2.7(CFG-6xx:非法 cron 拒)/§2.4(BUSY)、DIS-CORE §0.2(INV-01/INV-07)
> **一句话**:进程内持久化定时器(F048)——cron 表达式(及固定间隔 interval/绝对时间 at 两种扩展)注册任务模板,到点把模板作为新任务入队(F043,复用 guard/预算/日志纪律);list/remove/pause/resume 全事件化;job 定义与状态随会话持久化(事件溯源),崩溃恢复后**继续未来触发**,宕机期错过的触发**只记 missed 不补跑**(PRD F048 边界);默认禁深夜(23:00-7:00)触发危险类模板;最小粒度 1 分钟,单进程协程(INV-07)。

## 模块职责

1. **三种触发方式**:`cron`(5 字段表达式,每分钟对齐检查——F048 最小粒度 1 分钟)、`interval`(固定间隔,expr=秒,≥60s,按 last_fired 推进)、`at`(绝对时间,expr=ISO8601,一次性,触发后自动移除);到点统一把 job.template{intent,meta} 作为新任务 submit 入队(F043)。
2. **注册/管理命令**:`register`(校验非法 → CFG-6xx 拒)、`list_jobs`、`remove`、`pause`/`resume`;job 定义事件化持久化:schedule.registered/updated/removed(词表外新增,按 EVENT-SCHEMA §7 登记;事件=唯一真源 INV-01),注册表内存态由回放重建。
3. **到点触发留痕(F048 I/O)**:每次触发写 schedule.trigger{job,cron,fired_at} 后入队;任务模板带 meta{source:"schedule:<name>"} 溯源;触发即入队,队列满载 QUE-001 时触发失败显式事件化(不静默丢)。
4. **深夜禁触发**:23:00≤hour 或 hour<7:00 且 job.is_risky → 不触发,写 schedule.blocked(F048 验收伪代码;is_risky 默认由模板内工具危险分级推导,可显式覆盖)。
5. **崩溃恢复**:`recover()` 回放 schedule.* 事件重建注册表与 next_fire_at;宕机窗口内错过的触发机会 → 记 schedule.missed(合并计数,不补跑不追账,PRD:错过触发不补跑只记 missed);恢复后重臂 next_fire_at 并继续正常触发(测试 F048:…/恢复)。
6. **持久化与并发边界**:job 状态(含 next_fire_at 快照)落事件,随会话 JSONL 持久(§8.1 普通落盘);调度 ticker 为会话内单协程(单写者 INV-07);触发入队与前台任务共享同一 FIFO 队列——同一时刻仅一个 running(F043 顺延)。

## 依赖

- **依赖方向**(§0.3 拓扑):schedule 位于编排层(agent-loop 之上);消费 `session.append/replay`(schedule.* 事件与恢复回放)、`task_queue.submit`(F043 到点入队)、危险分级表(F023,推导 is_risky);不依赖 plan/subagent。
- **消费方**:命令层(register/list/remove/pause 命令)、会话启动装配(recover 恢复 ticker)、compaction(状态由日志重建,无额外折叠负担)。
- **外部依赖**:errors(PyHError)、asyncio(分钟对齐 ticker,协程并发原则 5);时间一律本地时区(中国标准时间),cron 字段用本地时钟匹配;事件 schedule.registered/updated/removed/missed/blocked 为词表外新增(trigger 已收编 EVENT-SCHEMA §3.5.3),实现前按 §7 登记。

## 数据结构表

| 字段 | 类型 | 规则 |
|---|---|---|
| `JobKind` | Literal | `cron / interval / at`(cron=F048 核心,后两者为固定间隔/绝对时间扩展) |
| `ScheduleJob` | dataclass | `name: str`(NAME_RE 合法,全库唯一)/ `kind` / `expr`(cron 5 字段或秒数或 ISO8601)/ `template: dict`({intent 必填,meta 可选})/ `is_risky: bool` / `paused: bool` / `next_fire_at: datetime\|None` / `last_fired_at: datetime\|None` / `created_seq: int` |
| `JobInfo` | dataclass | list 输出:name/kind/expr/paused/next_fire_at/last_fired_at/triggered/missed(计数) |
| `_jobs` | dict[str, ScheduleJob] | name → job;由事件重建(INV-01) |
| 事件(词表外新增) | — | schedule.registered / updated / removed / trigger(已收编)/ blocked / missed;全部普通落盘(§8.1) |
| 常量 | — | 最小粒度 60s(interval 下限)/ cron 每分钟匹配;深夜窗 23≤h<7(可配 schedule.night_window);is_risky 推导用 F023 分级表 |

## 类与函数清单

### `async def register(self, name: str, kind: str, expr: str, template: dict, *, is_risky: bool | None = None, ctx) -> None` — 注册定时任务(F048)

**功能一句话**:注册/更新调度 job——cron 表达式非法或 interval<60s 或 at 时间非法 → CFG-6xx 拒(EVENT-SCHEMA:cron 非法 → CFG-6xx);重名拒(BUSY,提示先 remove,防隐式覆盖);写 schedule.registered 持久化定义。

```python
async def register(self, name, kind, expr, template, *, is_risky=None, ctx):
    if not NAME_RE.match(name):                      # 名字合法性(与工具注册同规)
        raise PyHError("CFG-601", ctx={"hint": f"job 名非法: {name}",
                                       "advice": "字母/数字/下划线/连字符"})
    if name in self._jobs:                           # 重名:显式拒,不隐式覆盖
        raise PyHError("BUSY", ctx={"hint": f"job {name} 已存在",
                                    "advice": "先 remove 再注册,或用 update 语义"})
    spec = self._validate_spec(kind, expr)           # 非法 → CFG-6xx(见异常表)
    if not template or not template.get("intent"):   # 任务模板必须含意图
        raise PyHError("EVT-100", ctx={"hint": "template 缺 intent 字段"})
    risky = self._derive_risky(template) if is_risky is None else is_risky
    job = ScheduleJob(name=name, kind=kind, expr=expr, template=template,
                      is_risky=risky, paused=False,
                      next_fire_at=self._first_fire(kind, expr, now()),
                      created_seq=ctx.session.next_seq())
    self._jobs[name] = job                           # 先入内存
    await session.append("schedule.registered",     # 后落事件(崩溃重建源,INV-01)
        {"name": name, "kind": kind, "expr": expr,
         "template": {"intent": template["intent"]},
         "is_risky": risky, "paused": False,
         "next_fire_at": iso(job.next_fire_at)}, actor="system")
```

**参数表**:`name`=job 名(唯一);`kind`=cron/interval/at;`expr`=对应表达式;`template`={intent,meta}(meta 里禁放凭据,INV-09);`is_risky`=显式覆盖(默认模板推导)。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | name 非法 | CFG-601 | 按 NAME_RE 修正 |
| `PyHError` | cron 语法错/interval<60s/at 非未来 | CFG-601 | 修表达式;拒绝启动该 job |
| `PyHError` | 重名注册 | BUSY | remove 后重注(防隐式覆盖) |
| `PyHError` | template 缺 intent | EVT-100 | 补意图文本 |

**关联测试**:test_f048_schedule(cron/入队/非法拒)、GWT-F048-01(非法 cron → CFG-601 且零事件)、GWT-F048-02(重名注册 → BUSY)。

### `def _validate_spec(self, kind: str, expr: str) -> CronSpec | IntervalSpec | AtSpec` — 表达式校验与解析(内部)

**功能一句话**:按 kind 校验并解析表达式——cron 5 字段(分 时 日 月 周,支持 `* , - /`;周 0-6,0=周日;值域越界/段数错 → CFG-601);interval 秒数 ≥60;at 为 ISO8601 且须未来;返回内部 spec。

```python
def _validate_spec(self, kind, expr):
    if kind == "cron":                               # F048 核心:5 字段 cron
        parts = expr.split()
        if len(parts) != 5:                          # 段数错误
            raise PyHError("CFG-601", ctx={"hint": f"cron 须 5 字段: {expr}"})
        _check_fields(parts)                         # 值域/步长语法(越界抛 CFG-601)
        return CronSpec(fields=parts)
    if kind == "interval":
        secs = _as_int(expr)                         # 固定间隔(秒)
        if secs is None or secs < 60:                # 最小粒度 1 分钟(F048 边界)
            raise PyHError("CFG-601", ctx={"hint": f"interval 须 ≥60 秒: {expr}"})
        return IntervalSpec(seconds=secs)
    if kind == "at":                                 # 绝对时间(一次性)
        dt = _parse_iso(expr)
        if dt is None or dt <= now():
            raise PyHError("CFG-601", ctx={"hint": "at 时间须为未来的 ISO8601"})
        return AtSpec(fire_at=dt)
    raise PyHError("EVT-100", ctx={"hint": f"kind 非法: {kind}",
                                   "advice": "kind ∈ cron/interval/at"})
```

**参数表**:`kind/expr`=注册入参。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | cron 段数/值域/语法错 | CFG-601 | 修表达式 |
| `PyHError` | interval<60 / at 已过去 | CFG-601 | 改表达式 |
| `PyHError` | kind 未知 | EVT-100 | 用三值 |

**关联测试**:test_f048_schedule(表达式边界表)、GWT-F048-03(interval 59s 拒)。

### `def _cron_match(self, spec: CronSpec, dt: datetime) -> bool` — cron 分钟匹配(内部)

**功能一句话**:5 字段逐一匹配给定时刻(分/时/日/月/周,本地时钟);支持 `*`、`a-b`、`a,b`、`*/n`、`a-b/n`;周 0=周日;F048 每 60s tick 一次即以分钟粒度判定。

```python
def _cron_match(self, spec, dt):
    vals = [dt.minute, dt.hour, dt.day, dt.month, dt.weekday()]  # 本地时区字段序
    for field, v in zip(spec.fields, vals):
        if not _field_match(field, v):               # 单字段:*,列表,区间,步长任一
            return False
    return True                                      # 五字段全中 → 到点(F048 tick)
```

**参数表**:`spec`=解析后 CronSpec;`dt`=当前时刻。**异常表**:无(注册期已校验)。**关联测试**:test_f048_schedule(cron 表达式表驱动)、GWT-F048-04(`0 2 * * *` 02:00 命中/02:01 不命中)。

### `def next_fire(self, job: ScheduleJob, now_dt: datetime) -> datetime | None` — 下次触发时刻(内部)

**功能一句话**:按 kind 计算下次触发——cron:向后扫到下一匹配分钟(60s 粒度);interval:last_fired+interval(无则 now+interval);at:fire_at(已过 → None,一次性耗尽)。

```python
def next_fire(self, job, now_dt):
    if job.kind == "cron":                           # 逐分钟后扫(上限 1440 次防死循环)
        t = now_dt.replace(second=0, microsecond=0) + timedelta(minutes=1)
        for _ in range(1440):
            if self._cron_match(job.spec, t): return t
            t += timedelta(minutes=1)
        return None                                  # 年内无匹配(如 2/30):不触发
    if job.kind == "interval":
        base = job.last_fired_at or now_dt           # 固定间隔推进(≥60s)
        return base + timedelta(seconds=job.spec.seconds)
    return job.spec.fire_at if job.spec.fire_at > now_dt else None   # at 一次性:已过即 None
```

**参数表**:`job`/`now_dt`=当前时刻。**异常表**:无。**关联测试**:test_f048_schedule(next_fire 边界)、GWT-F048-05(interval 触发后推进)。

### `async def _tick(self, ctx, *, now_dt: datetime | None = None) -> list[str]` — 每分钟检查(F048 核心)

**功能一句话**:F048 验收伪代码全分支落地——遍历全部 job:暂停跳过 → 未到期跳过 → 深夜+危险 blocked 留痕不触发 → at 一次性触发后移除 → cron/interval 触发并重算 next_fire;返回本次触发名单。

```python
async def _tick(self, ctx, *, now_dt=None):
    now_dt = now_dt or datetime.now()
    fired = []
    for job in list(self._jobs.values()):            # 快照遍历(tick 中允许 remove/注册)
        if job.paused: continue                      # 暂停:不触发(F048 list/remove/pause)
        if not self._due(job, now_dt): continue      # 未到触发窗(cron 不匹配/未到 next_fire)
        if self._is_night(now_dt) and job.is_risky:  # 默认禁深夜(23-7)触发危险类模板
            await session.append("schedule.blocked",
                {"job": job.name, "reason": "night-window"}, actor="system")
            job.next_fire_at = self.next_fire(job, now_dt); continue   # 顺延下次
        if job.kind == "at":                         # 一次性:触发后移除(不重复)
            await self._fire(ctx, job, now_dt); fired.append(job.name)
            await self.remove(job.name); continue
        job.last_fired_at = now_dt                   # 周期型:推进窗口
        job.next_fire_at = self.next_fire(job, now_dt)
        await self._fire(ctx, job, now_dt); fired.append(job.name)
    return fired                                     # 触发名单(测试断言/告警用)
```

**参数表**:`ctx`=会话实体;`now_dt`=测试注入时钟。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| 单 job 异常隔离 | _fire 入队失败(如 QUE-001) | 原码入 system.error | 不中断其余 job;记日志 |

**关联测试**:test_f048_schedule(cron/入队/深夜禁/暂停跳过)、GWT-F048-06(深夜 02:00 危险 job → schedule.blocked 零入队;非危险照常触发)、GWT-F048-07(at 触发后自动 remove)。

### `async def _fire(self, ctx, job: ScheduleJob, fired_at: datetime) -> None` — 触发执行(入队,F048)

**功能一句话**:触发留痕 + 入队任务模板——先写 schedule.trigger{job,cron,fired_at} 再 submit(F043);模板意图经队列执行,**复用 guard/预算/日志纪律**(F048 设计理由);入队失败不静默,事件化上报。

```python
async def _fire(self, ctx, job, fired_at):
    await session.append("schedule.trigger",         # 触发留痕(EVENT-SCHEMA §3.5.3)
        {"job": job.name, "cron": job.expr,
         "fired_at": fired_at.isoformat()}, actor="system")
    try:
        await task_queue.submit(job.template["intent"],
            meta={"source": f"schedule:{job.name}"}) # 任务模板 → 新任务(F043 FIFO 顺延)
    except PyHError as e:                            # QUE-001 队满等:显式事件化不吞
        await session.append("system.error", {"code": e.code,
            "hint": f"schedule:{job.name} 入队失败"}, actor="system")
```

**参数表**:`job`=到点 job;`fired_at`=本次触发时刻(ISO 入事件)。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError`(捕获) | 队列满载等 | QUE-001 | system.error 留痕;下一触发窗重试 |

**关联测试**:GWT-F048-08(schedule.trigger 先于 task.enqueued,meta 带 source)、test_f048_schedule。

### `async def remove(self, name: str, ctx) -> None` — 移除定时任务(F048)

**功能一句话**:按名移除 job 并写 schedule.removed;不存在显式拒(BUSY);tick 快照遍历中移除安全;at 一次性任务触发后自动走此路径。

```python
async def remove(self, name, ctx):
    if name not in self._jobs:                       # 不存在:显式拒(幂等不静默)
        raise PyHError("BUSY", ctx={"hint": f"job {name} 不存在",
                                    "advice": "用 list_jobs 查现有 job"})
    del self._jobs[name]                             # 内存摘除(tick 用快照,安全)
    await session.append("schedule.removed",
        {"name": name}, actor="system")              # 落事件:重建时不再出现
```

**参数表**:`name`=job 名。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | job 不存在 | BUSY | list_jobs 核对名字 |

**关联测试**:test_f048_schedule(remove 后不再触发)、GWT-F048-09(remove 未知 → BUSY)。

### `async def pause(self, name: str, ctx) -> None` / `async def resume(self, name: str, ctx) -> None` — 暂停/恢复(F048)

**功能一句话**:暂停=到点不触发但保留定义与窗口(_tick 首行跳过);恢复=继续按 next_fire_at 触发;状态变更写 schedule.updated 持久化;不存在 → BUSY。

```python
async def pause(self, name, ctx):
    j = self._require(name)                          # 不存在 → BUSY
    if j.paused: return                              # 幂等(重复暂停无事件)
    j.paused = True
    await session.append("schedule.updated",
        {"name": name, "paused": True}, actor="system")

async def resume(self, name, ctx):
    j = self._require(name)
    if not j.paused: return                          # 幂等
    j.paused = False
    j.next_fire_at = self.next_fire(j, datetime.now())   # 恢复后重臂下次触发
    await session.append("schedule.updated",
        {"name": name, "paused": False,
         "next_fire_at": iso(j.next_fire_at)}, actor="system")
```

**参数表**:`name`=job 名。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | job 不存在 | BUSY | list_jobs 核对 |

**关联测试**:GWT-F048-10(暂停期零触发零入队;恢复后续触发;重复暂停幂等)、test_f048_schedule。

### `async def list_jobs(self, ctx) -> list[JobInfo]` — 查询(F048)

**功能一句话**:列出全部 job 快照(name/kind/expr/paused/下次触发/最近触发/累计触发与 missed 计数)——计数由事件回放聚合(triggered=schedule.trigger 计数,missed=schedule.missed 计数),内存不存第二份(INV-01)。

```python
async def list_jobs(self, ctx):
    counts = self._count_from_events()               # 回放聚合 triggered/missed(INV-01)
    return [JobInfo(name=j.name, kind=j.kind, expr=j.expr, paused=j.paused,
            next_fire_at=j.next_fire_at, last_fired_at=j.last_fired_at,
            triggered=counts[j.name]["triggered"],
            missed=counts[j.name]["missed"]) for j in self._jobs.values()]
```

**参数表**:无。**异常表**:无。**关联测试**:GWT-F048-11(list 与事件计数一致)、UI/命令层查询。

### `async def recover(self, ctx, *, now_dt: datetime | None = None) -> int` — 崩溃恢复(F048:…/恢复)

**功能一句话**:会话启动装配时调用——回放 schedule.registered/updated/removed 重建注册表;宕机窗口错过的触发机会**只记 schedule.missed 不补跑**(PRD F048 边界),at 一次性过期同样记 missed;全部 job 重臂 next_fire_at 后启动分钟 ticker;返回 missed 总数。

```python
async def recover(self, ctx, *, now_dt=None):
    now_dt = now_dt or datetime.now()
    self._rebuild_from(session.replay())             # 事件重建注册表(INV-01)
    missed_total = 0
    for j in self._jobs.values():
        if j.paused:                                 # 暂停中:不核算错过
            continue
        if self._missed_window(j, now_dt):           # 持久化 next_fire_at 已过(宕机期)
            await session.append("schedule.missed",  # 只留痕不补跑(PRD:错过不补跑)
                {"job": j.name, "missed": self._missed_count(j, now_dt),
                 "since": iso(j.next_fire_at), "until": iso(now_dt)}, actor="system")
            missed_total += 1
        j.next_fire_at = self.next_fire(j, now_dt)   # 重臂:未来触发继续(恢复=补触发能力)
        if j.kind == "at" and j.next_fire_at is None:
            continue                                 # 一次性 at 已彻底过期:由 remove 清理
    asyncio.create_task(self._ticker(ctx))           # 启动分钟对齐泵(单协程,INV-07)
    return missed_total
```

**参数表**:`ctx`=会话实体;`now_dt`=测试注入时钟。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| 孤儿事件防御跳过 | registered 前 updated(坏日志) | EVT-101 已拒于写入层 | repair 查隔离;恢复不中断 |

**关联测试**:test_f048_schedule(…/恢复:GWT——宕机错过仅 schedule.missed 无入队;恢复后到点正常触发)、GWT-F048-12(重启后 list_jobs 与崩溃前一致)。

### `async def _ticker(self, ctx) -> None` — 分钟对齐泵(内部)

**功能一句话**:常驻协程——睡到下一分钟边界(最小粒度 1 分钟,F048)后调 _tick;醒来先校正时钟(防长睡漂移),异常不退出(记 system.error 后继续下一轮)。

```python
async def _ticker(self, ctx):
    while not self._stopped:
        nxt = (datetime.now() + timedelta(minutes=1)).replace(second=0, microsecond=0)
        await asyncio.sleep(max(1.0, (nxt - datetime.now()).total_seconds()))
        try:
            await self._tick(ctx)                    # 每分钟检查一次(F048)
        except Exception as e:                       # 单轮异常不杀泵
            log.exception("schedule tick failed: %s", e)
            await session.append("system.error", {"code": "CYC-999",
                "hint": "schedule tick 异常已隔离"}, actor="system")
```

**参数表**:`ctx`=会话实体。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| 捕获隔离 | tick 未预期异常 | CYC-999 | 记 system.error;下一轮继续 |

**关联测试**:GWT-F048-13(tick 异常后泵存活继续触发)。

### `@classmethod def _rebuild_from(cls, events: list[Envelope]) -> None` — 事件回放重建(内部,INV-01)

**功能一句话**:顺序重放 schedule.registered/updated/removed 重建 _jobs(含 next_fire_at 快照与 paused)——内存态 ≡ 日志派生态;孤儿 updated/removed 防御跳过。

```python
@classmethod
def _rebuild_from(cls, events):
    m = cls._EMPTY                                   # 清空后按事件重建
    for e in events:
        p = e.payload
        if e.type == "schedule.registered":          # 定义+首次窗口快照
            m._jobs[p["name"]] = ScheduleJob(name=p["name"], kind=p["kind"],
                expr=p["expr"], template={"intent": p["template"]["intent"]},
                is_risky=p["is_risky"], paused=p["paused"],
                next_fire_at=parse_iso(p["next_fire_at"]), created_seq=e.seq)
        elif e.type == "schedule.updated":           # 暂停/恢复状态迁移
            j = m._jobs.get(p["name"])
            if j is None: continue                   # 孤儿(坏日志):防御跳过
            j.paused = p.get("paused", j.paused)
            if p.get("next_fire_at"): j.next_fire_at = parse_iso(p["next_fire_at"])
        elif e.type == "schedule.removed":           # 移除留痕
            m._jobs.pop(p["name"], None)
    # triggered/missed 计数同样只由 schedule.trigger/missed 事件回放聚合
```

**参数表**:`events`=session.replay() 全量。**异常表**:无(防御式)。**关联测试**:GWT-F048-14(事件流→注册表一致)、崩溃恢复配套。

## 关联文档

| 文档 | 章节 | 关系 |
|---|---|---|
| PRD-Core.md | §5.5 F048(定时任务:功能/边界/验收伪代码) | 功能与验收权威 |
| EVENT-SCHEMA.md | §3.5.3(schedule.trigger 字段)、§7(词表外新增登记:registered/updated/removed/missed/blocked)、§8.1 落盘矩阵 | 事件权威与登记规则 |
| ERR.md | §2.7 CFG-6xx(非法 cron 拒)、§2.4 BUSY、§2.11 QUE-001 | 错误码契约 |
| task_queue.py.md | 本规格同族 | 到点入队(F043 FIFO 顺延)、QUE-001 边界 |
| DIS-CORE.md | §0.2(INV-01 事件重建/INV-07 单进程协程) | 回放纪律与并发边界 |
| goal.py.md / plan_mode.py.md | 本规格同族 | 任务模板共用队列纪律;周期任务可与目标核对联动 |
