# specs/desktop.py.md — 编码规格

> 目标代码:pyharness/desktop.py(console_scripts 入口 `pyharness-desktop`),PRD F065(Windows 桌面程序,阶段 6 核心/面试主演示)+ ADR-012(pywebview 壳 + FastAPI 同进程,不建 SPA 不开浏览器)。AI 编码 Agent 只读本文件即可写出桌面程序全量代码。权威源:PRD-Core.md F065、ADD.md ADR-012、DEP.md §4.7B(演示台本)、EVENT-SCHEMA.md §3(事件渲染字段)。全中文,仅 Python,禁 TS(前端为内嵌原生 JS,无 node 构建链)。禁止:页面直写存储(窗口=只读投影,写操作全走 session.append/task_queue/approval,ADR-001/012 不变式)、绑非回环地址、自造特权通道绕过 guard/预算、把瞬时事件当持久事件渲染进时间线(仅日志派生时间线)。
> 部署形态:PyInstaller 单 exe(前端资源内嵌);依赖 pywebview + WebView2(Win10/11 自带)+ fastapi/uvicorn 同进程单 worker。

## 模块职责
一句话:双击即用的 Windows 桌面壳——pywebview 主线程开窗(1280×800)加载 FastAPI 同进程本地服务(`127.0.0.1:随机端口`);API 只读投影(会话列表/对话流/SSE 事件流/**Agent 轨迹时间线回放**/预算仪表盘)或经引擎门面写(task_queue.submit 提问、approval.approve 审批、session.append 无特权);`DesktopBridge`(js_api)与 SSE 双通道驱动前端;窗口关闭=优雅停服(强同步事件已落盘,uvicorn 停止,进程退出)。CLI/桌面/ACP 同一事件流的不同投影。

## 依赖
| import | 用途 |
|---|---|
| threading | uvicorn 后台线程(daemon)+ webview 主线程共存(同进程,ADR-005) |
| socket | 随机端口:bind(("127.0.0.1", 0)) 取空闲口后释放交 uvicorn |
| fastapi(FastAPI)、uvicorn | 同进程本地 HTTP + SSE;单 worker 不 reload |
| sse-starlette(EventSourceResponse)或自研 ASGI 流 | SSE 事件流;禁轮询替代(PRD F065 边界:SSE 断连=重连续拉) |
| webview | pywebview 窗口壳;create_window(js_api=DesktopBridge);WebView2 运行时 |
| pyharness.session(events_after、derive_messages) | 时间线/对话数据源:视图永远从事件派生(原则 1) |
| pyharness.task_queue | 提问 submit→SSE 广播任务事件 |
| pyharness.approval | 审批桥:approve/deny、pending 查询(裁决入 approval.* 强同步) |
| pyharness.bus(EventBus.subscribe) | 总线订阅→SSE fan-out(含瞬时 llm.chunk 流式) |
| pyharness.repair(ensure_repaired) | 启动自检:损坏会话先 repair 再可开(F060 前置) |
| pyharness.attach_image / validation(F061) | 图片选择上传(可选,桌面界面入口) |
| pyharness.errors(PyHError、raise_code) | /api 错误体复用错误码(ADR-011):body={"code","advice"},禁吐 ctx 敏感值 |
| pyharness.budget(状态查询) | 预算仪表盘数据(limit/used/warn 标记) |

## 数据结构表
| 结构 | 字段 | 说明 |
|---|---|---|
| `DesktopApp` | api:FastAPI;url:str;bridge:DesktopBridge;hub:EventStreamHub;ctx:门面;stopping:Event | 桌面程序总装;url=http://127.0.0.1:{port} |
| `DesktopBridge` | app:DesktopApp;pending_calls:dict | pywebview js_api;webview 线程方法→loop.call_soon_threadsafe |
| `TimelineNode` | seq:int;ts:str;kind:str;actor:str;title:str;detail:dict;severity:"info"\|"warn"\|"danger"\|"success" | 轨迹面板一帧;kind∈{user,thought,message,tool,guard,approval,budget,recovery,error} |
| `StreamClient` | sid:str;queue:asyncio.Queue;last_seq:int;dead:bool | 一个 SSE 连接的订阅游标;重连后从 last_seq 续拉补发 |
| `EventStreamHub` | clients:set[StreamClient];seq_locks | 总线→SSE fan-out;写锁保证 seq 顺序广播 |
| `DialogTurn` | seq;role:"user"\|"assistant";content;tool_cards:list;status | 对话区消息气泡(derive_messages 派生) |
| `ApiErrorBody` | code:str;advice:str;detail:dict\|None | 统一错误体(ADR-011);HTTP 状态映射:4xx 客户端/5xx 引擎 |

## 类与函数清单

### `def main(argv: list[str] | None = None) -> int` — 进程入口(pyharness-desktop)
功能:入口点:装配 ctx→启动 FastAPI 后台线程→webview 开窗→阻塞至关窗→优雅停服;双击 exe 即 main。返回:int 退出码。
参数表:argv 未用(桌面无 CLI 参数,保留签名对称)。返回:int(0 正常/1 启动失败)。
伪代码:
```python
def main(argv=None):
    ctx = assemble_desktop_ctx()                        # config→guard→budget→storage(repair 自检前置)
    app = DesktopApp(ctx=ctx)
    port = pick_free_port()                              # 127.0.0.1 随机端口(F065 边界)
    thread = threading.Thread(target=run_uvicorn, args=(app, port), daemon=True)
    thread.start(); wait_until_listening(app, port)      # 就绪探测,失败→退 1 不弹空窗
    bridge = DesktopBridge(app)
    window = webview.create_window("PyHarness", app.url, js_api=bridge,
                                   width=1280, height=800)
    window.events.closing += lambda: app.request_shutdown()   # 关窗=优雅停服(F065 边界)
    webview.start(debug=False)                           # 阻塞至全部窗口关闭
    app.shutdown_gracefully()                            # flush→停 uvicorn→落 recovered 无(正常路径)
    return 0
```
异常表:PyHError|装配失败(CFG-601)|CFG-601|错误框提示后退 1;OSError|端口/WebView2 不可用|本地异常(日志)|提示装 WebView2 后退 1;Exception|窗口生命周期未预期|CYC-999|stderr 堆栈。
关联测试:test_f065_desktop.py(壳拉起/同进程/无特权)、DEP §4.7B(演示台本:双击→会话→提问→轨迹)。

### `async def run_desktop(ctx) -> int` — CLI desktop 子命令桥(cli.py 调用)
功能:`uv run pyharness desktop` 等价入口:复用已装配 ctx 走同一 DesktopApp 生命周期;保证 CLI/桌面同内核入口(PRD F065 输入输出)。返回:int。
参数表:ctx=CLI 已装配门面。返回:int 0。
伪代码:
```python
async def run_desktop(ctx):
    app = DesktopApp(ctx=ctx); port = pick_free_port()
    threading.Thread(target=run_uvicorn, args=(app, port), daemon=True).start()
    await wait_listening_async(app, port)
    bridge = DesktopBridge(app)
    webview.create_window("PyHarness", app.url, js_api=bridge, width=1280, height=800)
    webview.start(); app.shutdown_gracefully(); return 0
```
异常表:同 main;另:RuntimeError|非主线程调 webview|本地异常|文档注明桌面必须主线程启动。
关联测试:test_f064_cli.py(desktop 子命令解析)、test_f065_desktop.py。

### `def pick_free_port() -> int` / `def run_uvicorn(app, port) -> None` — 端口与后台服务
功能:bind 127.0.0.1:0 拿系统分配随机端口→关 socket 交 uvicorn(仅回环,PRD F065 边界);后台线程 serve api 单 worker,禁 reload/禁多 worker(seq 单调前提 ADR-005)。
参数表:app=DesktopApp;port。返回:int / None。
伪代码:
```python
def pick_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0)); return s.getsockname()[1]   # 竞窗极小:随即交 uvicorn,失败重试≤3

def run_uvicorn(app, port):
    config = uvicorn.Config(app.api, host="127.0.0.1", port=port, log_level="warning", workers=1)
    server = uvicorn.Server(config)
    app.server = server
    server.run()                                          # 阻塞;shutdown_gracefully 时 server.should_exit=True
```
异常表:OSError|端口占用|本地异常(日志 CYC-999 域外)|重试 pick_free_port;RuntimeError|uvicorn 已停|无码|线程内吞并置 app.server=None。
关联测试:test_f065_desktop.py(服务 127.0.0.1 可连且非 0.0.0.0)。

### `class DesktopApp.__init__(ctx)` / `def mount_api(self) -> None` — API 装配(同进程内核入口)
功能:建 FastAPI;注册全部只读投影端点 + 引擎门面端点 + SSE;错误处理器把 PyHError 转 ApiErrorBody(ADR-011);挂事件订阅把总线事件推 EventStreamHub。
参数表:ctx。返回:None。
伪代码:
```python
class DesktopApp:
    def __init__(self, ctx):
        self.ctx = ctx; self.hub = EventStreamHub()
        self.bridge = None; self.server = None; self.stopping = threading.Event()
        self.api = FastAPI(title="PyHarness Desktop", docs_url=None, redoc_url=None)
        self.mount_api()
        self._sub = ctx.bus.subscribe("#", self.hub.forward, owner="desktop")   # 全事件 fan-out
    def mount_api(self):
        a = self.api
        a.add_api_route("/api/sessions", list_sessions, methods=["GET"])            # 投影
        a.add_api_route("/api/sessions/{sid}/messages", session_messages, methods=["GET"])
        a.add_api_route("/api/sessions/{sid}/timeline", session_timeline, methods=["GET"])  # 轨迹(核心)
        a.add_api_route("/api/sessions/{sid}/event/{seq}", event_detail, methods=["GET"])
        a.add_api_route("/api/stream", stream_sse, methods=["GET"])                 # SSE 事件流
        a.add_api_route("/api/sessions", create_message, methods=["POST"])          # 门面写:task_queue
        a.add_api_route("/api/approvals/pending", pending_approvals, methods=["GET"])
        a.add_api_route("/api/approvals/{sid}/{aid}", decide_approval_for, methods=["POST"])
        a.add_api_route("/api/approvals/{aid}", decide_approval, methods=["POST"])  # 兼容单会话
        a.add_api_route("/api/budget/{sid}", budget_dashboard, methods=["GET"])     # 预算仪表盘
        a.add_api_route("/api/attachments", upload_attachment, methods=["POST"])    # F061 图片(可选)
        a.add_exception_handler(PyHError, api_error_handler)
```
异常表:EVT-102|订阅未注册类型|EVT-102|注册器兜底跳过;PyHError|端点内引擎错|原码|统一错误体 4xx/5xx。
关联测试:test_f065_desktop.py(API 全端点可路由)。

### `async def session_timeline(sid: str, after_seq: int = 0, kinds: str = "") -> list[TimelineNode]` — Agent 轨迹时间线(F065 核心,面试展示主场景)
功能:读 JSONL 真源(session.events_after)派生渲染"Agent 干活过程"节点序列:用户说了啥/LLM 想与做(工具调用带参数)/guard 拦截(高亮+策略引用)/审批/结果/预算/恢复声明;可 after_seq 增量续拉与 kinds 过滤;非聊天消息视图(逐帧回放数据源)。
参数表:sid;after_seq=增量游标(断连续拉);kinds=逗号过滤(空=全部)。返回:list[TimelineNode]。
伪代码:
```python
async def session_timeline(sid, after_seq=0, kinds=""):
    log = await ensure_open_session(sid)                 # 未 open→EVT-106 守卫提示先建会话
    want = set(kinds.split(",")) if kinds else None
    nodes = []
    for ev in log.events_after(after_seq=after_seq):     # 只读派生,绝不直写(ADR-012)
        node = render_timeline_node(ev)                  # 类型映射见该函数
        if want is None or node.kind in want: nodes.append(node)
    return {"sid": sid, "base_seq": after_seq, "nodes": nodes}
```
异常表:PyHError|会话不存在/未创建|EVT-106|404+code 提示;PERS-201|坏行(已被 repair 隔离)|PERS-201|跳过坏行并标注节点;EVT-103|订阅异常|EVT-103|隔离。
关联测试:test_f065_desktop.py(时间线=事件派生、逐帧字段齐全)、test_f060_repair.py(损坏会话先 repair 后时间线完整)。

### `def render_timeline_node(ev: Envelope) -> TimelineNode` — 事件→时间线一帧(纯函数)
功能:按事件类型映射 kind/title/detail/severity:user.message→(user,原文);agent.message→(message,正文);tool.call→(tool,工具名+脱敏参数);guard.rejected→(guard,policy_ref,severity=danger);approval.requested/granted/denied/timeout→(approval,裁决态);llm.usage→(budget, token/费用);session.recovered→(recovery, fixed/lost/backup);llm.chunk 等瞬时事件不入时间线(非日志派生,禁混入)。参数表:ev。返回:TimelineNode。
伪代码:
```python
def render_timeline_node(ev):
    p = ev.payload
    base = {"seq": ev.seq, "ts": ev.ts, "actor": ev.actor}
    if ev.type == "user.message":      return TimelineNode(kind="user", title="用户", detail={"text": p["content"][:400]}, **base)
    if ev.type == "agent.message":     return TimelineNode(kind="message", title="AI 回复", detail={"text": p["content"]}, severity="success", **base)
    if ev.type == "tool.call":         return TimelineNode(kind="tool", title=f"调用 {p['tool']}", detail=redact_args(p), **base)
    if ev.type == "guard.rejected":    return TimelineNode(kind="guard", title="guard 拦截", severity="danger",
                                            detail={"guard": p["guard"], "reason": p.get("reason"), "policy_ref": p.get("policy_ref")}, **base)
    if ev.type.startswith("approval."):return approval_node(ev, base)          # granted/denied/timeout 同构
    if ev.type == "llm.usage":         return TimelineNode(kind="budget", title="用量", severity="warn" if p["out_tokens"] else "info",
                                            detail={"model": p["model"], "in_tokens": p["in_tokens"], "out_tokens": p["out_tokens"], "cost_est": p.get("cost_est")}, **base)
    if ev.type == "session.recovered": return TimelineNode(kind="recovery", title="崩溃修复声明", severity="warn",
                                            detail={"fixed": p.get("fixed", []), "lost": p.get("lost", 0), "backup": p.get("backup")}, **base)
    if ev.type in {"session.created", "session.finished"}: return TimelineNode(kind="recovery", title=ev.type, **base)
    return TimelineNode(kind="info", title=ev.type, detail=redact(p), **base)   # 其余持久事件兜底渲染
```
异常表:无(纯函数;payload 字段缺失按 get 兜底,不抛——渲染永不因单事件异常中断整条时间线)。关联测试:test_f065_desktop.py(六类核心节点字段断言,轨迹=日志投影 INV-01)。

### `async def stream_sse(request, sid: str = "", after_seq: int = 0)` — SSE 事件流
功能:前端实时通道:连接即注册 StreamClient(游标 after_seq),总线事件按 seq 序 fan-out(瞬时 llm.chunk 直推,持久事件同推);断连→客户端带 last_seq 重连续拉(F065 边界:断连=视图重连,事件仍完整);心跳注释行保活。
参数表:request;sid=目标会话(空=全部);after_seq=初始游标。返回:EventSourceResponse。
伪代码:
```python
async def stream_sse(request, sid="", after_seq=0):
    me = StreamClient(sid=sid, last_seq=after_seq)
    async with app.hub.register(me):                    # 注册;closing 时注销
        async def gen():
            while True:
                if await request.is_disconnected(): break      # 前端断开→回收游标
                try:
                    ev = await asyncio.wait_for(me.queue.get(), timeout=15)
                    yield f"event: {ev.type}\ndata: {ev.json()}\n\n"      # 带 event: 名,前端按类型路由
                except asyncio.TimeoutError:
                    yield ": ping\n\n"                   # SSE 心跳注释行
        return EventSourceResponse(gen())
```
异常表:无(断开即净退);EVT-103|单订阅者异常|EVT-103|hub 注销该 client 不断其他;RuntimeError|hub 已停|无码|关闭连接。
关联测试:test_f065_desktop.py(SSE 派生流/断连续拉)。

### `async def create_message(sid: str, body: dict) -> dict` — 提问(经门面写,无特权路径)
功能:前端发消息→session.append("user.message", 强同步)→task_queue.submit 跑任务→返回 task_id;页面永不直写存储;写结果经 SSE 回流渲染。参数表:sid;body={text, attachments?:[ref]}。返回:{task_id, user_seq}。
伪代码:
```python
async def create_message(sid, body):
    text = (body.get("text") or "").strip()
    if not text: raise_code("EVT-100", ctx={"advice": "消息不能为空"})        # 空消息拒写
    seq = await ctx.session.append("user.message", content=text, actor="user",
                                   origin="desktop", sync=True)               # 强同步三类(F009)
    for ref in body.get("attachments", []):                                   # F061 图片附件寻址
        await ctx.session.append("user.attachment.image", ref=ref, actor="user")
    task_id = await ctx.task_queue.submit(text, meta={"channel": "desktop", "session_id": sid})
    return {"task_id": task_id, "user_seq": seq}
```
异常表:EVT-100|空消息|EVT-100|400 拒写;EVT-104|会话已 finished|EVT-104|409 提示 /new;EVT-106|先于 created|EVT-106|先建会话;QUE-001|队列满(≥32)|QUE-001|503 稍后再试;PERS-202|强同步落盘失败|PERS-202|500+跑 repair。
关联测试:test_f065_desktop.py(写操作无特权路径:仅 append/submit)、DEP §4.7B。

### `async def decide_approval(aid: int, body: dict) -> dict` — 审批弹窗裁决桥
功能:弹窗按钮批准/拒绝→approval.approve/deny(by="desktop",写 approval.granted/denied 强同步);裁决经桥无特权:重入 guard 链仍生效(GRD-403 批准后被新拒以新决策为准)。参数表:aid=approval.requested 的 seq;body={decision:"approve"\|"deny"}。返回:{ok, approval_id}。
伪代码:
```python
async def decide_approval(aid, body):
    decision = body.get("decision")
    if decision not in {"approve", "deny"}:
        raise_code("EVT-100", ctx={"field": "decision", "advice": "取值 approve/deny"})   # 400
    if decision == "approve": await ctx.approval.approve(aid, by="desktop")
    else:                      await ctx.approval.deny(aid, by="desktop")   # 拒绝终局,零副作用(R2)
    return {"ok": True, "approval_id": aid}
```
异常表:APR-503|裁决重放/未知 id|APR-503|404+忽略;APR-502|等待已取消|APR-502|按拒绝语义回 200(状态=denied);GRD-403|批准后新拒|GRD-403|回 409,执行未发生。
关联测试:test_f015_approval.py 联动、test_f065_desktop.py(弹窗桥裁决入日志)。

### `async def budget_dashboard(sid: str) -> dict` — 预算仪表盘 API
功能:返回当前任务/会话预算态:limit_cny/used_cny/max tokens(出入)/warn 80% 标记/最近 llm.usage 序列;只读派生(llm.usage 事件折叠),无写;离线可用数据实时(与 /cost 同源 F032)。参数表:sid。返回:dict。
伪代码:
```python
async def budget_dashboard(sid):
    st = ctx.budget.snapshot(sid)                        # 装配预算闸状态(F032)
    usages = [e for e in ctx.session.events_after() if e.type == "llm.usage"]
    used_in = sum(u.payload["in_tokens"] for u in usages); used_out = sum(u.payload["out_tokens"] for u in usages)
    ratio = (st.used_cny / st.limit_cny) if st.limit_cny else 0.0
    return {"sid": sid, "limit_cny": st.limit_cny, "used_cny": st.used_cny,
            "used_in_tokens": used_in, "used_out_tokens": used_out,
            "warned": ratio >= 0.8, "ratio": round(ratio, 4),           # warn 80% 阈值(config)
            "recent": [u.payload for u in usages[-20:]]}
```
异常表:PyHError|无预算闸装配|CFG-601|返回 disabled 标志(前端隐藏面板);无码异常|折叠 bug|CYC-999|空面板+日志。
关联测试:test_f032_budget.py 联动(warn 80%)、test_f065_desktop.py(仪表盘=usage 聚合一致)。

### `class DesktopBridge` — pywebview js_api(审批/提问/系统集成桥)
功能:webview 侧同步方法→asyncio 线程安全投递(run_coroutine_threadsafe);暴露 window.submit(text)/approve(aid,decision)/replay(sid,from_seq)/sessions();方法薄壳无业务,返回 Promise 字符串(json),错误=code 体(ADR-011)。参数表:app。方法:submit/approve/list_sessions/system_info。
伪代码:
```python
class DesktopBridge:
    def __init__(self, app): self.app = app; self._futs = {}
    def _run(self, coro):
        fut = asyncio.run_coroutine_threadsafe(coro, self.app.loop)   # webview 线程→引擎事件循环
        return json.dumps(fut.result(timeout=60))                     # 超时→本地错误体
    def submit(self, sid, text):            # 前端回车/发送按钮
        return self._run(create_message(sid, {"text": text}))
    def approve(self, aid, decision):       # 审批弹窗按钮(桥无特权:仍过 guard/预算)
        return self._run(decide_approval(int(aid), {"decision": decision}))
    def replay_frame(self, sid, from_seq):  # 轨迹面板逐帧回放取帧
        return self._run(session_timeline(sid, after_seq=int(from_seq)))
    def list_sessions(self):
        return self._run(list_sessions_api())
    def on_close(self):                     # 窗口关闭事件:优雅停服
        self.app.request_shutdown()
```
异常表:PyHError|引擎错|原码入 json body|前端 toast code+advice;asyncio.TimeoutError|引擎忙|本地错误体{"code":"BUSY"}|提示稍后再试(不悬挂)。
关联测试:test_f065_desktop.py(bridge 方法可达性/无特权)。

### `def shutdown_gracefully(self) -> None` — 窗口关闭=优雅停服(F065 边界)
功能:stopping 置位→停新订阅→hub 关闭→server.should_exit=True 停 uvicorn→webview.destroy→进程退出;强同步三类事件已在 append 时落盘(此处不依赖未 flush 数据,再兜底 flush 一次 persistence);SSE 客户端收到 close 事件。参数表:无。返回:None。
伪代码:
```python
def shutdown_gracefully(self):
    if self.stopping.is_set(): return                      # 幂等:双击关闭只走一次
    self.stopping.set()
    self.ctx.bus.unsubscribe_all("desktop")                # 停事件 fan-out
    self.hub.close_all()                                   # SSE 客户端收 close,前端可重开
    if self.ctx.persistence: self.ctx.persistence.flush_sync()   # 兜底 flush(正常路径事件已落盘)
    if self.server: self.server.should_exit = True         # uvicorn 线程自然退出
    try: webview.destroy()                                 # Windows 关窗后清理
    except Exception: pass                                 # 已销毁则忽略(本地异常不跨边界)
```
异常表:无(幂等保护);Exception|webview 已销毁|本地吞并|不阻止进程退出。
关联测试:test_f065_desktop.py(关窗后端口释放/进程退出/事件完整)、DEP §4.7B(关窗=优雅停服)。

## 关联文档
1. PRD-Core.md §5.7 F065(桌面程序:pywebview+FastAPI 同进程、SSE、轨迹面板、审批弹窗、预算仪表盘、127.0.0.1、关窗优雅停服、PyInstaller 单 exe)。
2. ADD.md ADR-012(决策全文:窗口=只读投影、不建 SPA、桥无特权、WebView2 依赖)+ ADR-005(单进程不被外壳破坏)+ ADR-001(页面永不直写存储)。
3. DEP.md §4.7B(演示台本与预期输出)、DEP §1.2(依赖表 fastapi+uvicorn+pywebview)。
4. EVENT-SCHEMA.md §1.3(瞬时 llm.chunk 与持久事件分流,轨迹只取持久)、§3(tool.call/guard.rejected/approval.*/llm.usage/session.recovered 渲染字段)。
5. specs/session.py.md(events_after/derive_messages 投影 API)、specs/repair.py.md(启动自检 F060 前置)、specs/cli.py.md(desktop 子命令桥)。
6. ERR.md §1.4(远端只回 code+advice,禁 ctx 敏感值)+ ADR-011(/api 响应复用错误码)。
