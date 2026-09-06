# specs/acp.py.md — 编码规格

> 目标代码:pyharness/acp.py(入口:`pyharness acp`),PRD F066(ACP 自动化桥,DSH ACP 的 Python 等价)+ F064(CLI acp 子命令)+ ADR-005(单进程不破坏)。AI 编码 Agent 只读本文件即可写出 ACP 桥全量代码。权威源:PRD-Core.md F066、DEP.md §4.7C(管道喂 JSON-RPC 演示)、JSON-RPC 2.0 规范(协议错→标准码)、ERR.md §1.4(引擎错以 code 进 error.data)。全中文,仅 Python,禁 TS。禁止:一次并发处理多请求(串行协议)、把引擎日志/调试输出写 stdout(stdio 双工:协议独占 stdout,日志走 stderr)、桥自持特权绕过 guard/预算/审批、对无 id 通知回响应、阻塞期吞第二请求不报错。
> 形态:外部程序用 stdin 喂 JSON-RPC 请求行、从 stdout 读响应行的长驻进程;零依赖跨语言,引擎可编程化(脚本驱动跑任务全过审批流=引擎化最佳证明)。

## 模块职责
一句话:JSON-RPC 2.0 over stdio 自动化桥——逐行读 stdin 解析请求(非法行→-32700),按 method 分派 initialize/chat/approve/read_events(附 shutdown),一次一请求串行(处理完才读下一条;忙时新 chat→-32000 BUSY),响应带 id 回 stdout;approve 允许远程人类经桥下发裁决(通道="acp:<id>",仍过 guard/预算/防重放);read_events 提供游标式事件订阅(断线重连从 last_seq 续拉);引擎错误以 F019 码进 error.data,协议错误用 JSON-RPC 标准码;日志一律 stderr。

## 依赖
| import | 用途 |
|---|---|
| sys、json | stdin/stdout 逐行双工;json.loads/dumps(禁第三方 RPC 库,协议极薄) |
| pyharness.task_queue | chat 方法:submit(阻塞 wait=True 默认)→wait_for 终态;wait=False 秒回 task_id |
| pyharness.session(open_session、events_after) | read_events 游标读、会话绑定/新建 |
| pyharness.approval(approve/deny、on_verdict) | approve 方法:远程人类裁决入口(by="acp:<client>") |
| pyharness.bus(EventBus.subscribe) | 事件游标缓冲(桥进程内 last_seq 推进) |
| pyharness.errors(PyHError、raise_code、to_user_message) | 引擎错→error.data.code(F019);禁现场造 JSON-RPC 码外的码 |
| pyharness.commands / plan_mode | chat 外可选扩展 method(本规格只落 PRD 四法+shutdown,扩展注册点保留) |
| pyharness.repair | 启动/打开会话遇损坏的修复前置(F060) |

## 数据结构表
| 结构 | 字段 | 说明 |
|---|---|---|
| `JsonRpcRequest` | jsonrpc:"2.0";id:int\|str\|None(空=通知);method:str;params:dict\|None | 入站请求;id 缺失=通知不回响应 |
| `JsonRpcError` | code:int;message:str;data:dict\|None | 标准码:-32700 解析/-32600 非法/-32601 无此法/-32602 参数错/-32603 内部/-32000 忙;data.code 放 F019 引擎码 |
| `AcpState` | client_id:str;session_id:str\|None;cursor:int;busy:bool;bridge_ref | 连接会话态;cursor=已投递事件最大 seq(游标订阅) |
| `InitializeResult` | protocolVersion:"1.0";methods:list[str];events:{"cursor": true};session:{"id": str, "created": bool};engine:{"model": str, "headless_channel": bool} | initialize 返回能力清单 |
| `ChatResult` | task_id:str;reason:str;summary:dict;seq_range:[int,int]\|None | chat 阻塞式终态摘要;wait=False 时 reason="queued" |
| `EventBatch` | from_seq:int;to_seq:int;events:list[dict];has_more:bool | read_events 增量批;空批 from_seq=to_seq=当前游标 |

## 类与函数清单

### `async def serve(ctx, *, client_id: str | None = None) -> int` — 桥主循环(F066 验收直译)
功能:逐行读 stdin;空行/EOF→净退 0;每行一个请求串行处理(await 完成才读下一行);日志仅 stderr;stdout 只写协议行。返回:int 退出码。
参数表:ctx=装配门面(由 cli bootstrap 注入,channel 按需设 None);client_id 缺省 "acp:pid"。返回:int 0/1。
伪代码:
```python
async def serve(ctx, client_id=None):
    st = AcpState(client_id=client_id or f"acp:{os.getpid()}", cursor=0, busy=False)
    while True:
        line = await stdin_readline()                    # EOF→None→退出 0(上游关闭管道)
        if line is None: return 0
        line = line.strip()
        if not line: continue                            # 空行容忍(编辑器/echo 尾随 \n)
        req = parse_request(line)                        # 非法 JSON→已回 -32700;非对象→-32600
        if req is None: continue
        if req.id is None:                               # 通知:处理不响应(仍消耗,推进游标)
            await notify(ctx, st, req); continue
        resp = await dispatch(ctx, st, req)              # 一次一请求串行:busy 内新 chat 被 -32000 拒
        stdout_write(json.dumps(resp) + "\n"); await stdout_flush()
```
异常表:json.JSONDecodeError|请求行非 JSON|-32700 已回|continue;ValueError(信封非对象)|结构非法|-32600 已回|continue;OSError|stdin/stdout 断|本地异常|日志 stderr,退 1;Exception|未预期|-32603(data.code=CYC-999)|回错误响应不崩桥。
关联测试:test_f066_acp.py(喂 10 条请求串行响应顺序一致)、DEP §4.7C(initialize 管道演示)。

### `def parse_request(line: str) -> JsonRpcRequest | None` — 行解析(协议错→标准码)
功能:json.loads→校验对象/jsonrpc=="2.0"/method 为 str/id 类型合法;失败即把标准码错误响应写 stdout 并返回 None(调用方 continue)。参数表:line。返回:JsonRpcRequest 或 None。
伪代码:
```python
def parse_request(line):
    try:
        raw = json.loads(line)
    except json.JSONDecodeError as e:
        stdout_write(error_resp(None, -32700, "Parse error", {"detail": str(e)}) + "\n"); return None
    if not isinstance(raw, dict) or raw.get("jsonrpc") != "2.0" \
       or not isinstance(raw.get("method", ""), str) or "id" in raw and not is_id_type(raw["id"]):
        stdout_write(error_resp(raw.get("id"), -32600, "Invalid Request") + "\n"); return None
    return JsonRpcRequest(jsonrpc="2.0", id=raw.get("id"), method=raw["method"],
                          params=raw.get("params") or {})
```
异常表:无(全部就地转标准码响应);TypeError|loads 输入非 str|内部防御|返回 -32600(理论上不可达)。
关联测试:test_f066_acp.py(坏行→-32700、非对象→-32600、id 类型校验)。

### `async def dispatch(ctx, st, req) -> dict` — 方法分派(method not found→-32601)
功能:按 method 分发到四法+shutdown;未知→-32601;dispatch 内忙锁:仅 chat/approve 需要互斥且只有 chat 长驻——chat 进行中再来 chat→-32000 BUSY(串行协议);其余读方法不互斥。参数表:ctx;st;req。返回:完整响应 dict。
伪代码:
```python
async def dispatch(ctx, st, req):
    m, p, rid = req.method, req.params, req.id
    try:
        if m == "initialize":  r = await cmd_initialize(ctx, st, p)
        elif m == "chat":      r = await cmd_chat(ctx, st, p)          # 唯一长驻;锁下执行
        elif m == "approve":   r = await cmd_approve(ctx, st, p)
        elif m == "read_events": r = await cmd_read_events(ctx, st, p)
        elif m == "shutdown":  r = {"shutdown": True}; raise _Shutdown(r)   # 内部信号:净退 0
        else: return error_resp(rid, -32601, "Method not found", {"method": m})
        return {"jsonrpc": "2.0", "id": rid, "result": r}
    except _Shutdown as s:      return {"jsonrpc": "2.0", "id": rid, "result": s.result}
    except PyHError as e:       return engine_error(rid, e)           # F019 码进 data.code
    except asyncio.TimeoutError: return error_resp(rid, -32000, "Busy/Timeout", {"code": "BUSY"})
    except Exception as e:      return error_resp(rid, -32603, "Internal error", {"code": "CYC-999", "detail": str(e)})
```
异常表:见伪代码各 except(标准码映射);禁裸 raise str 跨出本函数(桥边界=协议边界)。
关联测试:test_f066_acp.py(未知法 -32601、引擎错 data.code 透传)。

### `async def cmd_initialize(ctx, st, params) -> InitializeResult` — 能力协商
功能:记录 client_name/version(仅审计日志,不进事件);参数带 sessionId→绑定既有会话(先 ensure_repair 再 open,EVT-106 守卫);否则新建会话(session.created);返回能力清单:methods=[initialize,chat,approve,read_events,shutdown]、events.cursor=true、engine.model、headless 语义=桥即通道(channel="acp:<client>",非 headless,审批可经桥下发)。
参数表:params={clientName?,clientVersion?,sessionId?}。返回:InitializeResult。
伪代码:
```python
async def cmd_initialize(ctx, st, params):
    sid = params.get("sessionId")
    if sid:
        await ensure_repaired(ctx, sid)                  # 损坏先 repair(F060 前置),失败→PERS-201
        try: await ctx.session.open_session(sid)         # 已存在:绑定(重放重建)
        except PyHError as e: raise e                    # EVT-106 等原码透传
        created = False
    else:
        sid = await ctx.session.create()                 # 新会话:session.created seq=1
        created = True
    st.session_id = sid; st.cursor = 0
    ctx.channel = f"acp:{st.client_id}"                  # 桥=人类审批通道(非 headless):approve 可下发
    return InitializeResult(protocolVersion="1.0", methods=BRIDGE_METHODS,
                            events={"cursor": True}, session={"id": sid, "created": created},
                            engine={"model": ctx.config.llm.model})
```
异常表:PERS-201|会话损坏不可自动修复|PERS-201|error.data.code 透传,客户端可再调 chat?否——先 repair;EVT-104|绑定 finished 会话|EVT-104|返回 error 建议新 sessionId。
关联测试:test_f066_acp.py(initialize 能力清单/会话绑定/损坏前置)。

### `async def cmd_chat(ctx, st, params) -> ChatResult` — 结构化驱动主方法(F066)
功能:params={text, sessionId?, wait?(默认 true), meta?}:wait=true 阻塞:append user.message(强同步)→task_queue.submit→wait_for 终态→聚合 summary(含 guard.rejected/approval.* 清单);wait=false 秒回 task_id,结果由 read_events 消费。忙锁:上一条 chat 未终→-32000。参数表:params。返回:ChatResult。
伪代码:
```python
async def cmd_chat(ctx, st, params):
    if st.busy: raise_code("BUSY", ctx={"advice": "上一条 chat 未完成;等 read_events 就绪或 wait=false"})
    text = (params.get("text") or "").strip()
    if not text: raise_code("EVT-100", ctx={"field": "text", "advice": "text 必填"})
    sid = params.get("sessionId") or st.session_id or (await cmd_initialize(ctx, st, {})).session.id
    st.busy = True
    try:
        await ctx.session.append("user.message", content=text, actor="user",
                                 origin=f"acp:{st.client_id}", sync=True)     # 强同步:审计同 CLI
        task_id = await ctx.task_queue.submit(text, meta={"channel": f"acp:{st.client_id}", "session_id": sid})
        if not params.get("wait", True):
            return ChatResult(task_id=task_id, reason="queued", summary={})  # 异步:read_events 跟进
        res = await ctx.task_queue.wait_for(task_id)                          # 阻塞至终态(串行语义)
        return ChatResult(task_id=task_id, reason=res.reason,
                          summary={"final": res.summary(),
                                   "rejected": [e.payload for e in res.events_of("guard.rejected")],
                                   "approvals": [e.payload for e in res.events_of("approval.*")]})
    finally:
        st.busy = False
```
异常表:PyHError("BUSY")|并发 chat|-32000(data.code=BUSY)|客户端 wait 或串行化;EVT-100|空 text|EVT-100 进 error|补 text;LLM-310|降级链全败|LLM-310|reason=error 终态;GRD-401|critical 直拒|GRD-401|计入 rejected 清单,任务正常终(非桥错);PERS-202|落盘失败|PERS-202|建议 repair。
关联测试:test_f066_acp.py(阻塞 chat 串行/拒绝入清单/并发 chat 拒)、DEP §4.7C。

### `async def cmd_approve(ctx, st, params) -> dict` — 远程人类审批(F066:approve 可经桥下发)
功能:params={approval_id, decision:"approve"|"deny", by?:str}:approve/deny 交裁决入口(by="acp:<client>" 或显式 by);裁决后 approval.granted/denied 强同步落盘;桥无特权:执行前 guard 重入链仍校验(GRD-403)。返回:{ok, approval_id, decision}。
伪代码:
```python
async def cmd_approve(ctx, st, params):
    aid = params.get("approval_id"); decision = params.get("decision")
    if not isinstance(aid, int): raise_code("EVT-100", ctx={"field": "approval_id"})
    if decision not in {"approve", "deny"}: raise_code("EVT-100", ctx={"field": "decision"})
    by = params.get("by") or f"acp:{st.client_id}"
    if decision == "approve": await ctx.approval.approve(aid, by=by)   # granted 强同步
    else:                      await ctx.approval.deny(aid, by=by)     # 拒绝终局零副作用(R2)
    return {"ok": True, "approval_id": aid, "decision": decision}
```
异常表:APR-503|裁决重放/未知 id|APR-503 error|忽略不重执行;APR-502|等待取消|APR-502|按 denied 语义回;GRD-403|批准后被新拒|GRD-403|error,执行未发生;EVT-100|参数非法|EVT-100|400 语义。
关联测试:test_f066_acp.py(approve 驱动审批流全过/无特权断言)、test_f015_approval.py 联动。

### `async def cmd_read_events(ctx, st, params) -> EventBatch` — 事件游标订阅(F066 read_events)
功能:params={after_seq?|cursor?:int, sessionId?, limit?:int≤500}:从游标读持久事件(瞬时 llm.chunk 不给——非日志派生,桥客户端要打字机可另议);返回批+has_more;游标推进到 to_seq;断线客户端可用返回的 to_seq 续拉(等价 SSE 重连语义)。参数表:params。返回:EventBatch。
伪代码:
```python
async def cmd_read_events(ctx, st, params):
    sid = params.get("sessionId") or st.session_id
    if not sid: raise_code("EVT-106", ctx={"advice": "先 initialize 或传 sessionId"})
    after = params.get("after_seq", params.get("cursor", st.cursor))
    log = await ctx.session.open_session(sid)             # 只读投影(重放);不写
    limit = min(int(params.get("limit", 200)), 500)
    batch, last = [], after
    for ev in log.events_after(after_seq=after):          # 真源派生:只追加流按 seq 序
        batch.append(ev.envelope_dict()); last = ev.seq
        if len(batch) >= limit: break
    st.cursor = max(st.cursor, last)                      # 游标只进不退(单连接单调)
    return EventBatch(from_seq=after, to_seq=last, events=batch, has_more=len(batch) == limit)
```
异常表:EVT-106|未初始化|EVT-106 error|先 initialize;PERS-201|坏行已隔离|PERS-201|批内跳过并标注;EVT-103|订阅者异常|EVT-103|隔离不影响批返回。
关联测试:test_f066_acp.py(游标推进/断线重连续拉/limit 分页)。

### `async def notify(ctx, st, req) -> None` — 通知处理
功能:无 id 请求不响应;`shutdown` 通知→置净退标志(serve 循环尾检查退出);其余方法通知仅执行副作用(如 chat 通知=丢结果,记录日志告警不响应)。参数表:req。返回:None。
伪代码:
```python
async def notify(ctx, st, req):
    log.warning("acp 收到通知(无 id),不响应: %s", req.method)
    if req.method == "shutdown": st.notify_shutdown = True    # serve 循环检查后 return 0
```
异常表:无。关联测试:test_f066_acp.py(通知无响应行输出)。

### `def engine_error(rid, e: PyHError) -> dict` / `def error_resp(rid, code, msg, data) -> dict` — 响应构造
功能:引擎错→-32603 壳+data={code:F019,message,advice}(ADR-011:远端只回 code+advice,禁 ctx 敏感值/密钥);协议错→标准码直构。参数表:rid;e / code/msg/data。返回:dict。
伪代码:
```python
def engine_error(rid, e):
    body = {"code": e.code, "message": e.message, "advice": e.advice}   # to_user_message 语义:无 ctx 原文
    if e.code not in {"GRD-401", "GRD-403", "LLM-310", "PERS-202"}:     # 热路径码只留 advice,其余加 detail
        body["detail"] = redact(e.ctx or {})
    return error_resp(rid, -32603, e.message, body)

def error_resp(rid, code, message, data=None):
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": code, "message": message, **({"data": data} if data else {})}}
```
异常表:无(纯构造;redact 保证 INV-09 不泄密)。关联测试:test_f066_acp.py(错误形状断言:远端无敏感 ctx)、ERR.md §1.4 对齐。

## 关联文档
1. PRD-Core.md §5.7 F066(JSON-RPC over stdio:initialize/chat/approve/read_events;一次一请求串行;桥无特权仍过 guard/预算;协议错→标准 JSON-RPC 码)。
2. DEP.md §4.7C(管道喂 initialize 演示台本与预期 stdout/stderr 分流)。
3. ERR.md §1.4/§2(引擎错误进 error.data.code,禁现场造码)+ 标准 JSON-RPC 2.0 错误码(-32700/-32600/-32601/-32602/-32603/-32000 保留段)。
4. EVENT-SCHEMA.md §1.3(瞬时 vs 持久:read_events 只给持久事件)、§3(approval.*/guard.rejected 事件字段)。
5. specs/cli.py.md(acp 子命令装配桥:AcpBridge(ctx, channel="acp:cli").serve())、specs/approval.py.md(裁决入口契约)、specs/session.py.md(events_after 游标读)。
6. ADD.md ADR-005(单进程:桥=进程内协程,无跨进程 RPC 出口)、ADR-011(错误码对外契约)。
