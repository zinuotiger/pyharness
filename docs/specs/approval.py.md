# specs/approval.py.md — 编码规格

> **模块文件**:`pyharness/core/approval.py` | **功能编号**:F015(人类审批核心)· 联动 F014(重入)/F043(队列暂停)/F064(headless)/F066(ACP approve) | **权威口径**:PRD-Core §5.2 F015/§6.7(审批流安全细节)、SECURITY §5(审批流完整策略:摘要化/60s 合并/信任名单/通道身份)、DIS-SEAM §6.2(seam B 伪代码级);冲突以 PRD-Core 为准
> **一句话**:danger≥high 调用的人类裁决服务(脊柱子系统,owner=spine 不可卸载)——请求摘要化 → 人类裁决(granted/denied/**timeout=denied 安全默认**,TTL 默认 120s)→ 事件强同步留痕;granted **不等于放行**(executor 重入 guard 链);支持 60s 同工具同参合并防轰炸、会话级信任名单(默认关、仅交互、headless 永不生效)、APR-501/502/503 错误域。
> **代码目录**:`pyharness/core/approval.py`(DIS-SEAM §1.4 落点);经 ctx.approval 注入,全会话共享同一对象(裁决按 approval_id=请求 seq 配对,防跨会话串扰)。

## 模块职责

1. **裁决流程(F015/SECURITY §5)**:`tool.call(danger≥high)` → `approval.requested`(approval_id=**请求事件 seq**、args_summary 摘要化、ttl_ms=120_000,强同步)→ 等人类裁决(CLI 键入/Web 弹窗/ACP approve)→ granted/denied/timeout 事件(强同步,含 by)→ 结果返回 executor;**三结果都不直接执行**——granted 由 executor 重入 guard 链起点(单调性高于人类即时意志,PRD §6.7)。
2. **安全默认(headless/超时/无悬挂)**:R8——headless/后台 job 无交互通道 → **APR-501 直接拒**(不发 approval.requested、不等待、不自动同意);TTL 120s 无人 → denied + `approval.timeout`(无人值守不执行不挂起);取消/会话关闭 → 未决请求全部 denied(APR-502,无悬挂 Future)。
3. **60s 合并防轰炸(F015/R13)**:同工具同参数(规范化指纹)在 60s 窗口内合并为一条 approval.requested——批量裁决一次,防"每秒一次"请求制造盲批;每个等待者各自配对裁决结果,granted 后各自独立重入 guard 链。
4. **会话级信任名单(SECURITY §5.4)**:记住 = "某工具+参数签名本会话内已被批准",后续同类**跳过再次询问人类**(不代表跳过 guard——每次调用仍重入链起点,策略收紧自动失效);默认关、仅交互会话可开、headless 永不生效、不随 fork 继承(F059)、critical 类绝不在名单;加入/命中/失效/清除全留 `approval.trust_*` 事件。只限**低变异签名**(同工具同参数归一),参数变化须重新审批。
5. **事件与强同步(§3.6)**:approval.requested/granted/denied/timeout 五类事件全部强同步落盘(sync=True)——人类裁决是审计核心事实,崩溃不丢;by 由框架按通道打(cli:`<user>`/web:`<会话>`/acp:`<client_id>`),LLM/工具/插件无权自报"我是人类批准的"(S-2 假冒审批的结构防线)。
6. **队列联动(F043)**:有未决请求时通知 agent-loop 进 PAUSED、任务队列暂停(不发新 run);最后一个请求裁决完恢复;后台 job 的审批挂起等主会话(不超时饿死)。
7. **防重放(APR-503)**:approval_id=请求 seq 一次性消费;同一 id 二次裁决 → system.error 忽略,不重复执行(防重放攻击 §6.7)。

## 依赖

- **单向依赖**:本文件 → `session.append`(五类强同步事件)、`errors.raise_code`(APR-5xx)、`bus.subscribe`(approval.granted/denied/timeout 裁决事件驱动等待者,DIS-SEAM §6.2 订阅声明);经 ctx 注入 channel 标识(外壳装配:交互 CLI/Web/ACP)。
- **消费方**:tools_executor.execute 关2.5(唯一 request 调用方);CLI/Web/ACP 裁决入口(F064/F065/F066)经 `approve/deny` 调用;agent-loop(F007 PAUSED 状态联动)。
- **不依赖**:guard 链(重入由 executor 编排);无 Provider 概念(横切脊柱子系统,Definition: namespace=system, owner=spine, ctx_path=None, expose_to_llm=False)。

## 数据结构表

### ApprovalRequest(等待者记录)

| 字段 | 类型 | 规则 |
|---|---|---|
| `approval_id` | int | **= 请求事件 seq**(防重放唯一键) |
| `tool` / `call_id` | str | 目标工具与调用 |
| `args_summary` | str | 摘要化:工具名+动作类型+目标路径/域名+风险一句话+policy_ref;**由 Consumer 层 summarize 生成,不由 Provider/LLM 提供**(防注入操纵摘要) |
| `danger` | Literal[high,…] | 只有 high 能进审批(critical 在 guard 层已转 reject) |
| `ttl_ms` | int=120_000 | 超时=denied(安全默认);进配置 |
| `channel` / `by` | str? / str? | 通道类型(cli/web/acp);by 在裁决时由框架打 |
| `state` | Literal[pending,granted,denied,timeout] | 一次性迁移;终态后不再接受裁决 |
| `waiter` | Future[verdict] | asyncio Future;超时/取消/裁决任一先到者解决 |

### 内部索引

`_pending: dict[int, ApprovalRequest]`(approval_id→请求)、`_merge: dict[str, list[ApprovalRequest]]`(指纹→批,60s 窗口)、`_trust: dict[str, TrustEntry]`(指纹→信任记录:tool/fingerprint/by/added_seq/hits)、`_ttl_ms`(配置默认 120s)、`_merge_window_ms = 60_000`、`_enabled: bool`(信任名单开关,默认 False,仅交互可开)。

### 指纹与合并规约

指纹 = `sha1(tool + canonical_json(args))`(canonical = 键排序、值类型归一);60s 窗口内同指纹新请求**不新增事件**、挂到既有批等待同一裁决(anti-bombing);窗口外新建批。

## 类与函数清单

### `async def request(call, args_summary: str, ctx, *, ttl_ms: int | None = None) -> str` — 审批主入口(F015;executor 关2.5 唯一调用方)

**功能**:通道检查(headless → APR-501 直接拒,R8)→ 信任名单命中则跳过询问返回 granted(仍由 executor 重入链)→ 60s 合并或新建请求 → 强同步 approval.requested → 等裁决/超时 → 结果事件强同步 → 返回 verdict;denied/timeout 由 executor 解读为不执行。

```python
async def request(self, call, args_summary, ctx, *, ttl_ms=None):
    ch = self._ensure_channel(ctx)                      # 无通道→APR-501 直接拒
    if ch is None:
        return self._deny_no_channel(call)              # 安全默认:不请求不等待
    fp = self._fingerprint(call.name, call.args)        # 规范化指纹(同工具同参)
    if self._trust_hit(fp, ctx):                        # 信任名单命中(仅交互+开)
        return "granted"                                # ← executor 仍重入 guard 链
    reqs = self._merge.get(fp)                          # 60s 合并防轰炸(R13)
    if reqs and not reqs[0].is_terminal():
        reqs.append(self._new_waiter(call, args_summary, ch, ttl_ms))
        return await self._wait_any(reqs[-1])           # 挂到既有批,不新增请求事件
    ev = await session.append("approval.requested",     # 强同步三类之一(§3.6)
        {"approval_id": self._next_seq_hint(),          # 实际=append 返回 seq
         "tool": call.name, "args_summary": args_summary,
         "ttl_ms": ttl_ms or self._ttl_ms, "channel": ch}, actor="tool", sync=True)
    req = self._new_waiter_at(ev.seq, call, args_summary, ch, ttl_ms)
    self._merge[fp] = [req]                             # 开新批(60s 窗口)
    self._start_ttl(req)                                # 定时器:超时→denied
    bus.emit("queue.suspended", {"reason": "approval"}) # 队列暂停(F043 联动)
    return await self._wait_any(req)                    # 等 on_verdict/超时解决
```

**参数表**:`call` = ToolCall(executor 传入);`args_summary` = Consumer 层摘要;`ctx` = 会话(取 channel);`ttl_ms` = 覆盖默认。**返回**:`"granted"/"denied"/"timeout"`(executor 对非 granted 一律不执行)。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | headless/无交互通道 | APR-501 | 直接拒(不发请求,无等待);接通道或不用 high 工具 |
| `PyHError` | 等待被取消/会话 detach | APR-502 | 未决全置 denied;重试需重发请求 |
| `PyHError` | 裁决重放/未知 approval_id | APR-503 | system.error 忽略;不重复执行 |
| `PyHError` | 强同步落盘失败 | PERS-202 | 请求失败;repair 后重试 |

**关联测试**:GWT-T7-04(审批重入:granted 后策略收紧仍拒)、T-SEC-03(覆写审批超时=denied:approval.requested→approval.timeout、文件原样、Provider 零调用)、T-SEC-09(headless 直接 denied 含 headless 标记,不挂起不自动同意)、DIS-SEAM §6.2 G1/G2。

### `async def on_verdict(type_: str, payload: dict) -> None` — 裁决事件订阅(总线驱动)

**功能**:订阅 approval.granted/denied/timeout;按 approval_id 找等待者并解决;**一次性消费**——已终态 id 再收裁决 → APR-503(system.error,不重复执行);timeout 由本服务定时器先触发(TTL 优先于外部迟到裁决)。

```python
async def on_verdict(self, type_, payload):
    aid = payload.get("approval_id")
    req = self._pending.get(aid)
    if req is None or req.state != "pending":           # 未知/已消费:防重放
        await session.append("system.error",
            {"code": "APR-503", "message": f"未知或已处理的裁决 id={aid},已忽略"},
            actor="system")
        return
    verdict = type_.split(".", 1)[1]                    # granted/denied/timeout
    req.state = verdict                                  # 终态,不再接受裁决
    req.by = payload.get("by")
    req.waiter.set_result(verdict)                       # 唤醒 request() 等待者
    self._pending.pop(aid, None)
    if not self._pending:                                # 全部清空 → 恢复队列
        bus.emit("queue.resumed", {"reason": "approval"})
    if verdict == "granted" and self._enabled and self._channel_is_interactive():
        self._remember(req)                              # 会话级信任:granted 后记录
```

**参数表**:`type_/payload` = 总线裁决事件。**异常表**:APR-503(重放/未知——事件化而非抛错)。**关联测试**:DIS-SEAM §6.2 G3(approval_id=42 已消费后再投 granted → APR-503,无第二个 tool.result)。

### `def approve(approval_id: int, *, by: str) -> None` / `def deny(approval_id: int, *, by: str) -> None` — 人类裁决入口(CLI/Web/ACP)

**功能**:外壳层唯一裁决通道:校验裁决者身份来源(by 由框架从通道上下文打,LLM 无权调用本函数)→ 强同步落 approval.granted/denied → 总线分发唤醒等待者。ACP 桥(F066)approve RPC 与 CLI/Web 同路径、零特权差。

```python
def approve(self, approval_id, *, by):
    if by.startswith(("llm:", "tool:", "plugin:")):     # 假冒审批结构防线(S-2)
        raise PyHError("APR-503", ctx={"approval_id": approval_id,
            "why": "裁决者身份非法:仅 cli/web/acp 通道"})
    asyncio.create_task(session.append("approval.granted",
        {"approval_id": approval_id, "by": by}, actor="user", sync=True))
    # 总线分发 → on_verdict 解决等待者;granted 只对同 seq 生效一次(防重放)

def deny(self, approval_id, *, by):                     # 同型:approval.denied
    ...                                                 # 判 id 非法 → APR-503
```

**异常表**:APR-503(非法裁决者/未知 id)。**关联测试**:T-SEC-10(事件序 approval.granted→guard.evaluated(reject))、S-2 场景(伪造裁决被拒)。

### 其余函数速览

| 函数 | 功能一句话 | 备注 |
|---|---|---|
| `def _ensure_channel(ctx) -> str \| None` | 通道判定:交互 CLI/web/acp 返回通道名;headless/管道/后台 job → None | R8;APR-501 入口 |
| `def _deny_no_channel(call) -> str` | 无通道直接拒:落 approval.denied(by=system,headless 标记),零等待 | T-SEC-09 断言 headless 标记 |
| `def _fingerprint(tool, args) -> str` | sha1(tool + canonical_json(args));键排序/值归一 | 合并与信任共用的唯一键 |
| `def _start_ttl(req) -> None` | 起 asyncio 定时器(ttl_ms);到点无人裁决 → timeout | 超时=denied 安全默认 |
| `def _on_timeout(req) -> None` | 超时路径:state=timeout + `approval.timeout` 强同步 + 解决 waiter | 与外部迟到 granted 竞争:先到者胜 |
| `def _wait_any(req) -> Future` | 等 waiter;可被取消(F025)→ APR-502 denied 无悬挂 | detach 时全部取消 |
| `def _new_waiter_at(seq, call, summary, ch, ttl) -> ApprovalRequest` | 构造等待者并登记 _pending | approval_id=seq |
| `def _trust_hit(fp, ctx) -> bool` | 信任命中:开关开+交互通道+指纹在表内;命中计数+1 并留 `approval.trust_hit` 事件 | 命中 ≠ 跳过 guard(executor 重入) |
| `def _remember(req) -> None` | granted 后写信任表(≤N 条,FIFO 淘汰)+ `approval.trust_added` 事件 | critical 类永不记录;低变异签名 |
| `def clear_trust(tool=None) -> int` | 清除信任(全会话或单工具)+ `approval.trust_cleared`;用户可手动撤销 | 沙箱下调/安全事件后建议清除 |
| `def cancel_all(reason="detach") -> None` | 未决请求全置 denied(APR-502)+ queue.resumed;detach/会话关闭调用 | 不留悬挂 Future |
| `def enable_trust(on: bool, *, by: str) -> None` | 信任名单开关(默认关;仅交互会话可开;headless 调用直接拒绝) | 下调留痕:approval.trust_* |
| `def pending_count() -> int` | 未决请求数(队列暂停/自检用) | 0 → queue.resumed |
| `def detach(ctx) -> None` | 摘订阅+取消全部等待 | 幂等;随 ctx.close() |

**模块级测试**:T-SEC-03/09/10、DIS-SEAM §6.2 G1-G4(超时 denied/headless APR-501/裁决重放 APR-503/批准期间策略收紧重入拒)、test_f015_approval.py(三结果/合并/headless 拒)、R13(60s 合并防盲批:同参每秒一请求 → 仅一条 approval.requested)。

## 关联文档

1. PRD-Core.md §5.2 F015(人类审批:TTL 120s/三结果/合并/headless 拒)、§6.7(审批流安全细节:摘要化/防重放/身份/策略可变)。
2. SECURITY.md §5(审批流完整策略:事件序列/摘要化/60s 合并与轰炸防护/信任名单边界语义/通道身份审计)。
3. DIS-CORE.md §1.4(agent-loop PAUSED 子状态:审批等待挂起)、§7.3.2(executor 关2.5 编排)。
4. DIS-SEAM.md §6.2(seam B:Definition/Provider 接口/on_verdict/错误码 APR-501/502/503)。
5. ERR.md §2.6(APR-5xx)、EVENT-SCHEMA.md(approval.requested/granted/denied/timeout 五事件强同步)、验收:tests/acceptance/test_f015_approval.py、tests/security/test_sec_*.py(T-SEC-03/09/10)。
