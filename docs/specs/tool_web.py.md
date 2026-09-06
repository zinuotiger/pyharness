# specs/tool_web.py.md — 编码规格

> **模块文件**:`pyharness/core/tool_web.py` | **功能编号**:F037(Web 搜索)· F038(Web 抓取)· 联动 F016(凭据 get_secret/出口脱敏)/F023(g-net-outbound 域闸)/F024(抓取内容指令视为数据)/F039(>32KB 转 spill) | **权威口径**:PRD-Core §5.4 F037/F038(验收伪代码权威)、SECURITY.md §7.3(网络影响域:allowlist 默认空=禁一切外发/搜索次数闸)、CFG.md §3.2(loop.content.search_result_chars=8000、fetch_max_chars=32768)/§3.3(security.network.web_search_per_session=20、allowed_domains=[])、ERR.md §2.9/§2.11(GRD-401/POL-NET-1/CRED-701/TLB-805/TLB-806);冲突以 PRD-Core 为准
> **一句话**:Web 工具族(命名空间 `web.*`)——`web.search`(关键词搜索,单会话次数闸 ≤20、结果合计 ≤8KB 截断、key 走 F016)与 `web.fetch`(URL 抓取 → 正文提取 Markdown、HTML 不入上下文、>32KB 转 spill、域名必须命中 allowed_domains);网络默认全禁:会话显式放行域名后才可外发,抓取是注入攻击主入口,三层限制 = allowlist + 正文提取 + 截断(F038 设计理由)。
> **代码目录**:`pyharness/core/tool_web.py`;经 tools_registry.register_tool 登记;HTTP 传输复用 httpx(DEP 依赖表,openai 传输层同库);测试零外发(注入 fake backend,对齐 T-SEC-07 mock 语义)。

## 模块职责

1. **网络策略边界(F037/F038/SECURITY §7.3)**:`web.fetch` 的目标 URL 由 LLM 提供,每次调用过域名 allowlist(`scope.policy.allowed_domains`,默认空 = 禁一切外发,N13)——管道 g5(g-net-outbound)先拒,工具内 F038 伪代码同判再兜一层(纵深第二道,域名归一比对使 IP 直连/编码混淆失效);`web.search` 的外发目标是**配置固定、用户经 F016 授权**的搜索服务端点(非 LLM 可指使的目标),不受逐调用 allowlist 约束,受单会话次数闸(≤20,0=禁用)与凭据闸约束——该区分与 SECURITY §7.3"web_fetch 域名不在白名单→POL-NET-1;web_search ≤20 次/会话"逐字一致。
2. **搜索防烧钱/防注入面(F037)**:`ctx.counters` 会话级计数 `search_calls`,超 `security.network.web_search_per_session`(默认 20)即拒(GRD-401,reason=search-quota,零外发);key 缺失即 CRED-701 拒(绝不空串继续,F016);命中结果经 `_summarize_search` 压到 ≤8KB(`loop.content.search_result_chars`),默认 top_k=5(1-10),超限逐条截断并置 truncated。
3. **抓取正文提取与体积闸(F038)**:HTTP 响应先判类型(二进制/非文本拒),原始 HTML 上限 2MB(超限 PERS-223 拒,防内存/带宽滥用);`html_to_markdown` 用标准库 html.parser 提取正文(去 script/style/nav/ad/footer 等噪声块,链接转 `[text](url)`),**HTML 原文永不进上下文**;Markdown >32KB(`fetch_max_chars`,默认 32768)转 spill 引用,只回摘要。
4. **重定向域重验**:跟随重定向但每次跳转对目标域名重新做 allowlist 校验(≤3 跳),防"白名单域名跳转到黑名单域名"绕过;DNS 重绑定列为残余风险(用户显式配置的域名可信,SECURITY §7.3)。
5. **抓取内容指令视为数据(F024/输出层双保险)**:web.fetch 返回的正文以工具结果身份进上下文(成不了 system 消息),内容中的指令由护栏段声明为数据;本工具不自行"执行"页面里任何指令形态,也不在结果里附加可执行语义(注入载荷最大代价 = 让模型多撞一次 guard,SECURITY §3.1)。
6. **超时与失败语义(F038 timeout=15s)**:单次 HTTP 请求墙钟 15s 超时、连接 10s;失败给明确原因(TLB-805 + http_status/阶段分类),回喂 LLM 自行判断重试——工具内不做自动重试(F028 只属模型调用通道)。

## 依赖

- **单向依赖**:本文件 → httpx(唯一外发传输)、`errors`(PyHError)、`ctx.scope.policy`(allowed_domains)、`ctx.counters`(search_calls 会话计数)、`ctx.get_secret`(F016 凭据单一读取口,禁自行 os.environ)、`ctx.storage.spill`(F039,>32KB Markdown 落盘)、`ctx.redact`(F016 出口脱敏,INV-09)——不 import guard/executor 内部符号。
- **消费方**:tools_executor.execute(唯一执行入口);agent-loop 侦查阶段经 ctx.tools 调用;审计经 tool.call/result。
- **外部**:httpx、urllib.parse、html.parser(标准库正文提取)、pydantic v2(registry 编译)。
- **测试注入面**:SearchBackend/Fetcher 均以可替换 Provider 形态注册,验收/安全测试注入 fake(mock 零 HTTP 断言,T-SEC-07)。

## 数据结构表

**工具定义五要素总表(T-05)**:

| 工具名 | description(一句话) | 参数 schema 要点 | danger | handler |
|---|---|---|---|---|
| `web.search` | 关键词搜索(默认 5 条:标题/URL/摘要),供判断是否抓取 | `query: str`(1-500 字符,必填);`top_k: int`(1-10,默认 5) | none(SECURITY §4.2 白名单内只读) | web_search |
| `web.fetch` | 抓取 URL → 正文 Markdown(去导航/脚本/广告);HTML 不入上下文 | `url: str`(http/https,必填);`max: int`(默认 32768,1024-1048576) | none | web_fetch |

**其他结构**:

| 结构 | 字段 | 规则 |
|---|---|---|
| `SearchHit` | title/url/snippet | 后端原样返回,摘要时逐条截断 |
| `_SearchOut` | results/truncated/total | 合计字符 ≤8000;truncated=超限截断留痕 |
| `_FetchOut` | content/truncated/spill_ref/final_url | Markdown 或 spill 引用;final_url=重定向终址 |
| `FetchPolicy` | allowed_domains/raw_max/search_per_session | 快照自 scope.policy + cfg,一次调用内恒定(防运行期漂移) |

**常量**:`SEARCH_PER_SESSION` 读 `security.network.web_search_per_session`(默认 20,0=禁用);`SEARCH_RESULT_CHARS` 读 `loop.content.search_result_chars`(默认 8000);`FETCH_MAX_CHARS` 读 `loop.content.fetch_max_chars`(默认 32768);`RAW_HTML_MAX=2_097_152`(原始 HTML 上限 2MB);`HTTP_TIMEOUT=15`(F038);`REDIRECT_MAX=3`;`_DEFAULT_SEARCH_ENDPOINT`(内置搜索服务端点常量,可经 ctx 配置注入覆盖;CFG.md 未列实现级端点键,需用户可配时按 CFG 登记流程补入)。

## 类与函数清单

### `async def web_search(args: dict, ctx) -> dict` — web.search(F037)

**功能**:关键词搜索:会话次数闸(超限零外发拒绝)→ F016 取 SEARCH_API_KEY(缺失 CRED-701)→ 调 SearchBackend → 结果摘要压 ≤8KB 返回;域名目标固定为已授权搜索端点,不受逐调用 allowlist 约束(职责 1 的区分依据)。

```python
async def web_search(args, ctx):
    used = ctx.counters.bump("search_calls")                       # 会话计数 +1
    limit = ctx.cfg.get("security.network.web_search_per_session", 20)
    if limit == 0 or used > limit:                                 # 0=禁用;超闸零外发
        raise PyHError("GRD-401", ctx={"reason": "search-quota",
            "policy_ref": "PRD F037 / CFG security.network.web_search_per_session",
            "advice": f"本会话搜索次数已达上限({limit});改用 web.fetch 直抓已知 URL 或新开会话"})
    key = ctx.get_secret("SEARCH_API_KEY")                         # F016 单一读取口
    if key is None:                                                # 缺失即拒绝
        raise PyHError("CRED-701", ctx={"hint": "配置 SEARCH_API_KEY(F016)"})
    hits = await ctx.search_backend.search(                        # 可替换后端(测试注入 fake)
        query=args["query"], top_k=args.get("top_k", 5), key=key)
    out = _summarize_search(hits, max_chars=SEARCH_RESULT_CHARS)   # 合计 ≤8KB 截断
    return out
```

**参数表**:`query` str 必填(1-500 字符);`top_k` int 可选(1-10,默认 5)。**返回**:`_SearchOut` {results:[{title,url,snippet}],truncated,total}。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 会话搜索次数超限/被禁用 | GRD-401(reason=search-quota,零外发) | 回喂;换 web.fetch 或新会话 |
| `PyHError` | SEARCH_API_KEY 缺失 | CRED-701 | 配置凭据;绝不空串继续(F016) |
| `PyHError` | 后端 HTTP 失败/超时 | TLB-805(ctx 含后端与状态) | tool.error 回喂,LLM 判重试 |
| `PyHError` | query 超长/类型错 | TLB-803 | 契约层拒,零执行(INV-06) |

**关联测试**:test_f037_search.py(次数闸/摘要/凭据)、T-SEC-07 变体(次数闸超限零 HTTP,mock 计数 0)、SECURITY D-1(防烧钱闸在代码里)。

### `async def web_fetch(args: dict, ctx) -> dict` — web.fetch(F038)

**功能**:抓取 URL → 正文 Markdown:域名 allowlist 自检(第二道)→ httpx 抓取(15s 超时、2MB 原始上限、重定向 ≤3 跳逐跳重验域)→ 类型判二进制/非文本 → html_to_markdown 正文提取 → Markdown >32KB 转 spill 引用;HTML 永不进上下文。

```python
async def web_fetch(args, ctx):
    url = args["url"]
    domain = _domain_allowed(url, ctx)                             # 域名归一 + allowlist 自检
    if domain is None:                                             # 默认空=禁一切外发
        raise PyHError("GRD-401", ctx={"reason": "POL-NET-1",
            "advice": "目标域不在 allowed_domains;需会话显式放行(默认全禁)"})
    raw, final_url = await http_get(url, max_bytes=RAW_HTML_MAX,   # 15s 超时/2MB 上限
                                    ctx=ctx)
    if looks_binary(raw):                                          # 二进制/非文本拒
        raise PyHError("TLB-806", ctx={"url": url,
            "advice": "目标为二进制/非文本内容,换其它手段"})
    md = html_to_markdown(raw.decode("utf-8", errors="replace"),   # 正文提取,HTML 丢弃
                          base_url=final_url)
    md = ctx.redact(md)                                            # 出口脱敏(INV-09)
    if len(md) <= args.get("max", FETCH_MAX_CHARS):                # 小正文直接返回
        return {"content": md, "truncated": False, "final_url": final_url}
    meta = await ctx.storage.spill.put(md, kind="fetch", ctx=ctx)  # >32KB 转 spill
    return {"content": meta["preview"], "truncated": True,         # 上下文只放摘要+引用
            "spill_ref": meta, "final_url": final_url}
```

**参数表**:`url` str 必填(http/https);`max` int 可选(截断阈值,默认 32768)。**返回**:`_FetchOut`。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | 域名不在 allowlist(默认全禁) | GRD-401(reason=POL-NET-1) | 回喂;改已放行域或请用户放行 |
| `PyHError` | 原始 HTML >2MB | PERS-223 | 回喂换更小页面/搜索缓存 |
| `PyHError` | 二进制/非文本内容 | TLB-806 | 提示换手段 |
| `PyHError` | HTTP 4xx/5xx/连接失败/超时(15s) | TLB-805(ctx 含 http_status) | tool.error 明确原因,LLM 判重试 |
| `PyHError` | 重定向目标域逃逸 allowlist | GRD-401(reason=POL-NET-1) | 回喂;拒跟随逃逸跳转 |
| `PyHError` | url 非法(非 http/https/解析失败) | TLB-803 | 契约层拒,零执行 |

**关联测试**:test_f038_fetch.py(allowlist/正文提取/截断)、T-SEC-07(空 allowlist 抓 evil 域被拒、mock 零 HTTP)、SECURITY D-2(>32KB 转 spill,HTML 不入上下文)、注入样本回归(F038 抓取载荷)。

### `def _domain_allowed(url: str, ctx) -> str | None` — 域名归一比对(工具内第二道,F038)

**功能**:解析 URL host,做归一化(小写、去 userinfo/port、IDNA 编码)后查 allowlist;命中返回归一域名,未命中返回 None;IP 直连与编码混淆同样归一比对(SECURITY §7.3)。

```python
def _domain_allowed(url, ctx):
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()                      # 去 port/userinfo
    except ValueError:
        return None
    if not host or parts.scheme not in ("http", "https"):
        return None                                                # 非 http(s) 一律拒
    host = host.encode("idna").decode("ascii")                     # 国际化域名归一
    allowed = ctx.scope.policy.allowed_domains or []               # 默认空 = 全禁
    if host in allowed or any(host == d.lower() for d in allowed): # 精确匹配
        return host
    return None                                                    # 未命中 → POL-NET-1
```

**参数表**:`url`=LLM 提供的目标 URL;`ctx`=会话门面(scope.policy.allowed_domains)。**返回**:归一域名或 None。**异常表**:无(失败以 None 表达,由调用方映射 POL-NET-1)。

**关联测试**:T-SEC-07、tools_guard g5 同判一致性(IP/混淆用例)。

### `async def http_get(url: str, *, max_bytes: int, ctx, redirects: int = REDIRECT_MAX) -> tuple[bytes, str]` — 受限 HTTP 抓取(F038 传输层)

**功能**:httpx 抓取原始字节:连接 10s/总 15s 超时,读满 max_bytes 即停(超限由调用方按 PERS-223 处置);跟随重定向但逐跳重验域名,超跳数/逃逸即拒;非 2xx 状态抛结构化错误带 http_status。

```python
async def http_get(url, *, max_bytes, ctx, redirects=REDIRECT_MAX):
    if redirects < 0:
        raise PyHError("TLB-805", ctx={"url": url, "advice": "重定向超过 3 跳,已停止"})
    if _domain_allowed(url, ctx) is None:                          # 每跳重验域
        raise PyHError("GRD-401", ctx={"reason": "POL-NET-1",
            "advice": "重定向目标域未放行,已停止"})
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10, total=15),
                                     follow_redirects=False) as c:  # 手动控跳
            async with c.stream("GET", url) as r:
                if r.status_code >= 300 and r.status_code < 400:   # 重定向:手动跟随
                    loc = r.headers.get("location")
                    if not loc:
                        raise PyHError("TLB-805", ctx={"url": url, "advice": "重定向无 Location"})
                    return await http_get(urljoin(url, loc), max_bytes=max_bytes,
                                          ctx=ctx, redirects=redirects - 1)
                if r.status_code >= 400:                           # 4xx/5xx 明确原因
                    raise PyHError("TLB-805", ctx={"url": url, "http_status": r.status_code,
                        "advice": f"HTTP {r.status_code},可稍后重试或换源"})
                raw = b""
                async for chunk in r.aiter_bytes():                # 限量读流
                    raw += chunk
                    if len(raw) > max_bytes:                       # 超原始上限
                        raise PyHError("PERS-223", ctx={"url": url,
                            "advice": "页面超过 2MB 抓取上限,换更小目标"})
                return raw, str(r.url)
    except httpx.TimeoutException:
        raise PyHError("TLB-805", ctx={"url": url, "advice": "抓取超时(15s)"}) from None
    except httpx.HTTPError as e:
        raise PyHError("TLB-805", ctx={"url": url, "advice": f"网络失败:{type(e).__name__}"}) from None
```

**参数表**:`url` 目标;`max_bytes` 原始字节上限(2MB);`ctx` 门面;`redirects` 剩余跳数。**返回**:`(raw_bytes, final_url)`。**异常表**:如上(GRD-401 POL-NET-1 / PERS-223 / TLB-805 三族,均零落盘零污染)。

**关联测试**:test_f038_fetch.py(超时/4xx 明确原因)、T-SEC-07(mock 断言无 HTTP 发出)、TO-301 类超时语义对照。

### `def html_to_markdown(html: str, base_url: str) -> str` — 正文提取转 Markdown(F038,标准库自含)

**功能**:用 html.parser 流式提取正文:丢弃 script/style/noscript/svg/head 与 class 命中导航/广告黑名单(nav/menu/ad/footer/sidebar)的块;块级标签换行、标题加 #、链接转 `[text](url)`、图片取 alt、空白折叠——输出是纯文本 Markdown,**HTML 标签/脚本永不泄漏进结果**。

```python
def html_to_markdown(html, base_url):
    out, skip, _ = [], 0, _Extractor()                             # 手写 HTMLParser 子类
    parser = _TextExtractor(out)                                   # 见下:_Extractor 状态机
    parser.feed(html)                                              # 流式,不建 DOM
    lines = [ln.strip() for ln in "".join(out).splitlines()]       # 逐行清理
    md = "\n".join(ln for ln in lines if ln)                       # 去空行
    return md[: _MAX_MD]                                           # 防御性上限(超限由调用方 spill)
```

**参数表**:`html`=原始 HTML 字符串;`base_url`=用于补全相对链接。**返回**:正文 Markdown 字符串。**异常表**:`html.parser.HTMLParseError` 容错(坏 HTML 不中断,parser 默认容错模式,无异常上抛)。**实现要点**(对编码 Agent 的约束):提取器维护 `skip_depth`(命中丢弃标签则跳过其后代)、`block_stack`(p/div/li/tr/h1-h6/pre 决定换行与缩进)、`link_buf`(a 标签内收 text、出标签时拼 `[text](urljoin(base, href))`);class/id 黑名单正则 `(^|[\s_-])(nav|menu|ad|ads?|footer|sidebar|banner)([\s_-]|$)`;本函数禁止引入任何第三方解析依赖(自含,DEP 依赖表只加 httpx)。

**关联测试**:test_f038_fetch.py(正文提取:脚本/导航剥离、链接转写)、注入样本回归(载荷经提取后体积受限)。

### `def _summarize_search(hits: list[SearchHit], *, max_chars: int) -> dict` — 搜索结果摘要截断(F037)

**功能**:把后端命中的标题/URL/摘要逐条压进 max_chars(8000)预算,超限截断并置 truncated;单条字段各自截断防止单条长文独占预算;总条数 ≤ top_k 恒成立。

```python
def _summarize_search(hits, *, max_chars):
    out, budget, truncated = [], max_chars, False
    for h in hits:                                                 # 逐条累计
        item = {"title": h["title"][:200], "url": h["url"][:500],
                "snippet": h.get("snippet", "")[:600]}
        cost = sum(len(v) for v in item.values())                  # 本条约占预算
        if budget - cost < 0:                                      # 装不下:截断留痕
            truncated = True
            break
        out.append(item)
        budget -= cost                                             # 扣减剩余预算
    return {"results": out, "truncated": truncated, "total": len(hits)}
```

**参数表**:`hits`=后端搜索结果(list[dict]);`max_chars`=合计字符预算(8000)。**返回**:`_SearchOut`。**异常表**:无(纯函数;hits 字段缺失按空串容错)。

**关联测试**:test_f037_search.py(摘要 ≤8KB 断言、truncated 语义)、SECURITY §2.1 体积闸(搜索摘要超限不入模)。

### `def register(registry) -> list[str]` — 五要素注册入口(F008/T-05)

**功能**:装配 `web.search`/`web.fetch` 两个 ToolDefinition 并登记;danger 均为 none(白名单内只读,SECURITY §4.2),域名约束由 g5 + 工具内第二道承担,不依赖 danger 审批。

```python
def register(registry):
    defs = [
        ToolDefinition(name="web.search", danger="none",
            description="关键词搜索(默认 5 条:标题/URL/摘要),供判断是否值得抓取;受会话次数闸限制",
            schema={"type": "object",
                    "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 500},
                                   "top_k": {"type": "integer", "minimum": 1, "maximum": 10}},
                    "required": ["query"], "additionalProperties": False},
            handler=web_search, timeout_s=30),
        ToolDefinition(name="web.fetch", danger="none",
            description="抓取 URL 并提取正文 Markdown(去导航/脚本/广告);HTML 不入上下文;需域名已放行",
            schema={"type": "object",
                    "properties": {"url": {"type": "string", "format": "uri",
                                           "pattern": "^https?://"},
                                   "max": {"type": "integer", "minimum": 1024,
                                           "maximum": 1048576}},
                    "required": ["url"], "additionalProperties": False},
            handler=web_fetch, timeout_s=60)]
    for d in defs:
        registry.register_tool(d)
    return [d.name for d in defs]
```

**参数表**:`registry`=ToolRegistry。**返回**:注册名 list[str]。**异常表**:TLB-801(重名)/TLB-803(schema 不可编译),同 tool_fs.register。

**关联测试**:test_f008 注册族、tools_guard g5 匹配例(web.* 名前缀)。

## 关联文档

| 文档 | 章节 | 关系 |
|---|---|---|
| PRD-Core.md | §5.4 F037/F038 | 功能/验收伪代码权威;三层限制设计理由 |
| SECURITY.md | §3.1(注入专题)/§4.2/§7.3(网络影响域) | allowlist 默认空、次数闸、域名归一、抓取注入防线 |
| CFG.md | §3.2(8000/32768)/§3.3(allowed_domains/20 次) | 全部体积闸与次数闸配置键 |
| ERR.md | §2.9(TLB-805/806)/§2.11(POL-NET-1)/§2.7(CRED-701) | 错误码映射与拒绝原因 |
| CONSTRAINTS-03-ToolSafety.md | T-05/T-09 | 五要素与输出有界 |
| tools_guard.py.md | g5 g-net-outbound | 管道侧域名闸,本文件为其工具内第二道 |
| tools_executor.py.md | 关4 结果 finalize | >32KB 后 spill 引用进 tool.result 的事件语义 |
