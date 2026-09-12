r"""pyharness/core/tool_web.py — Web 工具族 web.* (specs/tool_web.py.md 契约;阶段 3 模块)

功能编号:F037(Web 搜索)· F038(Web 抓取);联动 F016(凭据 get_secret/出口脱敏)/
F023(g-net-outbound 域闸)/F024(抓取内容指令视为数据)/F039(>32KB 转 spill)。
权威口径:specs/tool_web.py.md(编码契约,伪代码权威)、PRD-Core §5.4 F037/F038、
SECURITY.md §7.3(网络影响域:allowlist 默认空 = 禁一切外发/搜索次数闸)、
CFG.md §3.2(loop.content.search_result_chars=8000、fetch_max_chars=32768)/§3.3
(security.network.web_search_per_session=20、allowed_domains=[])、ERR.md §2.9/§2.11
(TLB-805/TLB-806/GRD-401 POL-NET-1/CRED-701);冲突以 PRD-Core 为准。

职责一句话:Web 工具族——web.search(关键词搜索,单会话次数闸 ≤20、结果合计
≤8KB 截断、key 走 F016)与 web.fetch(URL 抓取 → 正文提取 Markdown、HTML 不入
上下文、>32KB 转 spill、域名必须命中 allowed_domains);网络默认全禁,抓取是注入
攻击主入口,三层限制 = allowlist + 正文提取(HTML 不进上下文)+ 截断。

偏离说明(契约=spec 伪码;以下为既有实现冲突/空白处的取舍,均列理由):
1. 单跳传输抽为 seam _http_once:伪码把 httpx 调用内联在 http_get,而依赖节/任务
   要求『HTTP 客户端注入式、测试零外发(mock transport,T-SEC-07)』→ 把『单跳
   GET(不跟随重定向,限量读流,超时/错误映射)』抽为模块私有 _http_once,http_get
   的递归重定向 + 逐跳域重验 + 状态分支保持伪码原样;真实路径与伪码逐位等价
   (httpx.AsyncClient 每次递归新建,与伪码 async with 同语义),单测注入 fake 零触网。
2. 重定向预算判改为 redirects <= 0:伪码 `redirects < 0` 在剩余 0 时仍会跟随第 4 个
   重定向响应才报错(实际放行 4 跳),与模块职责『重定向 ≤3 跳』、SECURITY §7.3
   『≤3 跳』及错误文案『重定向超过 3 跳』自相矛盾 → 落地 ≤0 即拒(允许恰 3 跳),
   伪码此处为笔误。
3. counters 未接线回落零读数:spec 伪码直用 ctx.counters.bump,而装配期 counters
   可能缺位(本仓库计数对象未定型,spill 只写不入读)→ _bump_search_calls 支持
   dict/带 bump 对象/属性三形态,均不可用则 debug 日志 + 回落 0(沿用 scope.py
   『未接线 → 零读数=ok』先例;正常会话装配必有 counters,次数闸真实生效)。
4. register 补 owner 与 Provider 绑定:本项目 ToolDefinition 无 handler 字段且
   extra=forbid(spec register 伪码的 handler= 参数在此注册表不可编译)→ 按
   tool_fs 偏离 10 同款:owner="builtin" + register_tool(d, provider=PROVIDERS[...]),
   否则 executor 关3 lookup_provider 报『契约已注册但未绑定 Provider』。
5. _summarize_search 字段容错:伪码 h["title"] 直取与自身异常表『hits 字段缺失按
   空串容错』矛盾 → 落地 h.get(title/url/snippet, 默认空串);total 恒 = len(hits)。
6. 噪声正则加 re.I:spec 正则 `(^|[\s_-])(nav|menu|ad|ads?|footer|sidebar|banner)
   ([\s_-]|$)` 原样保留但忽略大小写(HTML class/id 大小写不敏感,误伤面收敛);
   li/tr 等块只换行不加列表符/缩进(spec 只承诺『决定换行与缩进』的功能面,列表
   符不在功能清单;正文丢失不扩大,格式不加戏)。
7. html_to_markdown 命名与防御上限:_Extractor/_TextExtractor 伪码两处并存,取
   _TextExtractor;_MAX_MD 伪码引用未列值,与 RAW_HTML_MAX 同为 2MB(HTML 原文本
   就 ≤2MB,Markdown 为其子集,超限由调用方 spill 兜底)。
8. 搜索后端:默认装配 BingRssBackend(无 key,稳定 XML),保留 DuckDuckGoBackend
   作为可选 HTML 后端;端点由 security.network.search_backend/search_endpoint
   控制。web.* 仍受 scope 的 allowed_domains 显隐约束,未配置时 fail-closed。

依赖方向(INV-08):本文件 → errors(raise_code)、tools_registry(ToolDefinition 五
要素)、httpx(唯一外发传输,openai 传输层同库)、urllib.parse/html.parser(标准库
正文提取);不 import guard/executor/approval 内部符号(ctx 注入面全部鸭子读取)。
"""
from __future__ import annotations

import html.parser
import inspect
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import (parse_qs, urlencode, urljoin, urlsplit,
                           unquote)

import httpx

from pyharness.core.tools_registry import ToolDefinition
from pyharness.errors import PyHError, raise_code

log = logging.getLogger("pyharness.tool_web")

# ------------------------------------------------------------------ 常量
SEARCH_PER_SESSION: int = 20
"""web.search 单会话次数闸默认(security.network.web_search_per_session;0=禁用)。"""

SEARCH_RESULT_CHARS: int = 8000
"""搜索结果合计字符预算默认(loop.content.search_result_chars)。"""

FETCH_MAX_CHARS: int = 32768
"""web.fetch 正文转 spill 阈值默认(loop.content.fetch_max_chars)。"""

RAW_HTML_MAX: int = 2_097_152
"""原始 HTML 抓取上限 2MB(防内存/带宽滥用,超限 PERS-223)。"""

HTTP_TIMEOUT: int = 15
"""F038 单次 HTTP 请求墙钟总超时(秒)。"""

CONNECT_TIMEOUT: int = 10
"""F038 连接阶段超时(秒)。"""

REDIRECT_MAX: int = 3
"""重定向跟随上限(≤3 跳,逐跳重验域名;SECURITY §7.3)。"""

BINARY_SAMPLE: int = 8192
"""二进制探测样本字节数(NUL 命中即 TLB-806 拒读,与 tool_fs 同口径)。"""

_MAX_MD: int = 2_097_152
"""html_to_markdown 防御性输出上限(= RAW_HTML_MAX;超限由调用方 spill,偏离 7)。"""

_MAX_REDIRECT_ADVICE: str = "重定向超过 3 跳,已停止"
"""超跳数回喂文案(spec 伪码 advice)。"""

_POL_NET_ADVICE: str = "目标域不在 allowed_domains;需会话显式放行(默认全禁)"
"""allowlist 未命中回喂文案(spec 伪码 advice;重定向域逃逸复用)。"""

# 标签级丢弃(后代全跳):script/style 是 CDATA 指令载体,noscript/svg/head 非正文,
# nav/footer 属导航/页脚噪声块(职责 3);iframe 同族噪声(内容不可信渲染面)
_SKIP_TAGS: frozenset = frozenset({
    "script", "style", "noscript", "svg", "head", "iframe", "nav", "footer",
})

# 块级换行集(spec 实现要点权威:p/div/li/tr/h1-h6/pre 决定换行;br 单断另行处理)
_BLOCK_TAGS: frozenset = frozenset({
    "p", "div", "li", "tr", "pre", "h1", "h2", "h3", "h4", "h5", "h6",
})

_HEADING_LEVELS: dict[str, int] = {f"h{i}": i for i in range(1, 7)}
"""标题级别映射(开标签时写入 '#'*level + ' ' 前缀)。"""

# class/id 噪声黑名单(spec 正则权威 + re.I,偏离 6):命中即跳过该块及其后代
_NOISE_RE: re.Pattern = re.compile(
    r"(^|[\s_-])(nav|menu|ad|ads?|footer|sidebar|banner)([\s_-]|$)", re.I)

_WS_RE: re.Pattern = re.compile(r"\s+")
"""行内空白折叠(HTML 空白 → 单空格)。"""


# ---------------------------------------------------------------- 配置辅助
def _cfg_holder(ctx: Any) -> Any:
    """配置持有者:ctx.cfg 优先,ctx.config 兼容(装配形态两收)。"""
    for name in ("cfg", "config"):
        holder = getattr(ctx, name, None)
        if holder is not None:
            return holder
    return None


def _cfg_read(ctx: Any, dotted: str, default: Any) -> Any:
    """点分键取值,双形态兼容(tool_fs/spill 同款):dict(平铺点分或嵌套)与
    pydantic 属性链;未接线/缺键回落默认(运行面不因未接线崩溃,阈值有界默认)。"""
    holder = _cfg_holder(ctx)
    if holder is None:
        log.debug("ctx.cfg/config 未接线,配置键 %s 回落默认 %r", dotted, default)
        return default
    if isinstance(holder, dict):
        if dotted in holder:                    # 平铺点分键形态
            return holder[dotted]
        cur: Any = holder
        for part in dotted.split("."):          # 嵌套 dict 形态
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur
    cur = holder                                # pydantic/属性形态
    for part in dotted.split("."):
        nxt = getattr(cur, part, None)
        if nxt is None:
            return default
        cur = nxt
    return cur


def _redact(ctx: Any, text: str) -> str:
    """出口脱敏(INV-09):ctx.redact 注入面调用;未接线/失败 → 原样(读路径
    不因脱敏中断,与 tool_fs._redact 同款;spill 落盘侧还有一层脱敏兜底)。"""
    fn = getattr(ctx, "redact", None)
    if not callable(fn):
        return text
    try:
        return fn(text)
    except Exception as exc:                    # noqa: BLE001 脱敏故障不阻断抓取
        log.warning("ctx.redact 执行失败,原样返回: %s", exc)
        return text


def _get_secret(ctx: Any, name: str) -> Optional[str]:
    """F016 凭据单一读取口:ctx.get_secret(name) → 缺失/空串一律 None(CRED-701
    由调用方抛,绝不空串继续);读取口未接线/异常同样按缺失 fail-closed。"""
    fn = getattr(ctx, "get_secret", None)
    if not callable(fn):
        return None
    try:
        v = fn(name)
    except Exception:                           # noqa: BLE001 底层故障 = 缺失
        log.warning("ctx.get_secret(%s) 异常,按凭据缺失处理", name)
        return None
    return v if isinstance(v, str) and v else None


def _allowed_domains(ctx: Any) -> list[str]:
    """scope.policy.allowed_domains 读取(默认空 = 禁一切外发,spec 伪码权威)。

    ctx.scope 可能直挂 ScopePolicy(裸 policy 形态)或挂 policy 属性(Scope 形态),
    两形态同收;scope 未接线 → [] 全禁(fail-closed,与 allowlist 默认一致)。
    """
    scope = getattr(ctx, "scope", None)
    if scope is None:
        return []
    pol = getattr(scope, "policy", None)
    if pol is None:
        pol = scope                            # 裸 policy 直挂 ctx.scope
    allowed = getattr(pol, "allowed_domains", None)
    if not allowed:
        return []
    return [str(d) for d in allowed]


# ---------------------------------------------------------------- 域名归一闸
def _normalize_hostname(raw: Any) -> Optional[str]:
    """URL/域名 → 可比对归一 hostname(spec 伪码口径,与 guard g5 同语义)。

    归一化:小写 → 去尾点 → 去 userinfo/port/路径(urlsplit hostname)→ IDNA
    编码(国际化域名/编码混淆失效);非 http(s) scheme/解析失败 → None(调用方拒)。
    """
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        if "://" not in s:                      # 裸域名/带 port 域名补 scheme(仅解析)
            s = "http://" + s
        parts = urlsplit(s)
        if parts.scheme.lower() not in ("http", "https"):
            return None
        host = parts.hostname
    except ValueError:
        return None
    if not host:
        return None
    host = host.lower().rstrip(".")
    if not host:
        return None
    try:
        host = host.encode("idna").decode("ascii")   # IDNA 归一(逐标签)
    except UnicodeError:
        return None                            # 编码无法归一 → 不可比对 → 拒
    return host


def _domain_allowed(url: str, ctx: Any) -> Optional[str]:
    """域名归一比对(工具内第二道,F038;spec 伪码权威)。

    命中 allowlist(精确匹配,归一后比较;子域/IP/编码混淆不隐含放行)返回归一
    域名;未命中/非法 URL 返回 None,由调用方映射 POL-NET-1。
    """
    host = _normalize_hostname(url)
    if host is None:
        return None
    for raw in _allowed_domains(ctx):
        norm = _normalize_hostname(raw)         # allow 条目同规归一(宽松收 URL 形态)
        if norm is not None and host == norm:
            return host
    return None


# ------------------------------------------------------------ 会话计数(次数闸)
def _bump_search_calls(ctx: Any) -> int:
    """web.search 会话计数 +1,返回新计数值(spec: ctx.counters.bump("search_calls"))。

    counters 三形态兼容(dict / 带 bump 的对象 / 属性形态);均不可用 → debug 日志
    回落 0 读数(偏离 3,零读数=未超限放行,沿用 scope 先例)。计数键 search_calls
    会话级,新会话清零(次数闸 ≤20 次/会话)。
    """
    c = getattr(ctx, "counters", None)
    if c is None:
        log.debug("ctx.counters 未接线:搜索次数闸失读(回落 0)")
        return 0
    if isinstance(c, dict):
        nxt = int(c.get("search_calls", 0)) + 1
        c["search_calls"] = nxt
        return nxt
    bump = getattr(c, "bump", None)
    if callable(bump):
        try:
            out = bump("search_calls", 1)       # (key, delta) 形态
        except TypeError:
            out = bump("search_calls")          # (key) 形态(内部自增)
        if isinstance(out, int):
            return out
        cur = getattr(c, "search_calls", None)
        return int(cur) if cur is not None else 1
    cur = int(getattr(c, "search_calls", 0) or 0) + 1   # 属性形态
    try:
        setattr(c, "search_calls", cur)
    except Exception:                           # noqa: BLE001 只读对象:降级
        log.debug("counters 属性形态不可写 search_calls")
    return cur


# ================================================================ web.search
async def web_search(args: dict, ctx: Any) -> dict:
    """web.search(F037):次数闸 → F016 取 key → SearchBackend → ≤8KB 摘要返回。

    目标域固定为已授权搜索服务端点(ctx.search_backend 装配注入),不受逐调用
    allowlist 约束(职责 1 区分依据);受次数闸与凭据闸约束。异常全走 raise_code。
    """
    used = _bump_search_calls(ctx)              # 会话计数 +1(先计数后判断,伪码序)
    limit = _cfg_read(ctx, "security.network.web_search_per_session",
                      SEARCH_PER_SESSION)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = SEARCH_PER_SESSION
    if limit == 0 or used > limit:              # 0=禁用;超闸零外发拒绝
        raise_code("GRD-401", reason="search-quota", used=used, limit=limit,
                   policy_ref="PRD F037 / CFG security.network.web_search_per_session",
                   advice=f"本会话搜索次数已达上限({limit});"
                          "改用 web.fetch 直抓已知 URL 或新开会话")
    backend = getattr(ctx, "search_backend", None)
    key = _get_secret(ctx, "SEARCH_API_KEY")    # F016 单一读取口
    needs_key = True if backend is None else bool(
        getattr(backend, "requires_key", True))
    if needs_key and key is None:
        raise_code("CRED-701", hint="配置 SEARCH_API_KEY(F016)")
    if backend is None or not callable(getattr(backend, "search", None)):
        raise_code("CYC-999", module="tool_web",
                   hint="ctx.search_backend 未接线:搜索服务后端缺失"
                        "(装配 bug),fail-closed 拒绝外发")
    top_k = args.get("top_k")
    try:
        top_k = int(top_k) if top_k is not None else 5
    except (TypeError, ValueError):
        top_k = 5                               # 契约层已拦;直调路径兜底
    hits = await backend.search(query=args["query"], top_k=top_k, key=key)
    max_chars = _cfg_read(ctx, "loop.content.search_result_chars",
                          SEARCH_RESULT_CHARS)
    try:
        max_chars = int(max_chars)
    except (TypeError, ValueError):
        max_chars = SEARCH_RESULT_CHARS
    return _summarize_search(hits, max_chars=max_chars)


def _summarize_search(hits: list, *, max_chars: int) -> dict:
    """搜索结果摘要截断(spec 伪码权威,偏离 5 字段容错)。

    逐条标题/URL/摘要压进 max_chars(8000)预算,单条字段各自截断(title 200 /
    url 500 / snippet 600)防单条长文独占预算;装不下整条即停并置 truncated;
    total 恒 = 后端命中总数(含被预算截掉者)。返回 _SearchOut 字典。
    """
    out: list[dict] = []
    budget = int(max_chars)
    truncated = False
    for h in hits or []:
        if not isinstance(h, dict):             # 脏数据防御:非 dict 单条跳过
            continue
        item = {"title": str(h.get("title", ""))[:200],
                "url": str(h.get("url", ""))[:500],
                "snippet": str(h.get("snippet", ""))[:600]}
        cost = sum(len(v) for v in item.values())
        if budget - cost < 0:                   # 装不下:截断留痕
            truncated = True
            break
        out.append(item)
        budget -= cost
    return {"results": out, "truncated": truncated, "total": len(hits or [])}


# ------------------------------------------------------------ 默认搜索后端
class _DuckDuckGoParser(html.parser.HTMLParser):
    """Small parser for DuckDuckGo's no-JS HTML result page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hits: list[dict] = []
        self._mode: Optional[str] = None
        self._buf: list[str] = []
        self._href = ""

    @staticmethod
    def _is(attrs: list, needle: str) -> bool:
        return needle in " ".join(
            str(v) for k, v in attrs if k in ("class", "id") and v)

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag != "a":
            return
        if self._is(attrs, "result__a"):
            href = next((str(v) for k, v in attrs if k == "href" and v), "")
            parsed = urlsplit(href)
            qs = parse_qs(parsed.query)
            href = unquote((qs.get("uddg") or [href])[0])
            self._mode, self._buf, self._href = "title", [], href
        elif self._is(attrs, "result__snippet"):
            self._mode, self._buf = "snippet", []

    def handle_data(self, data: str) -> None:
        if self._mode:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._mode:
            return
        text = " ".join("".join(self._buf).split())
        if self._mode == "title" and text and self._href:
            self.hits.append({"title": text, "url": self._href,
                              "snippet": ""})
        elif self._mode == "snippet" and text and self.hits:
            if not self.hits[-1]["snippet"]:
                self.hits[-1]["snippet"] = text
        self._mode, self._buf, self._href = None, [], ""


class DuckDuckGoBackend:
    """No-key HTML search backend for local/agent use.

    ``web.search`` still requires a non-empty domain allowlist at scope level so
    the default security posture remains fail-closed.  Once a caller explicitly
    enables the network domain, this backend provides a working search without
    an API key.
    """

    requires_key = False

    def __init__(self, endpoint: str = "https://html.duckduckgo.com/html/") -> None:
        self.endpoint = endpoint

    async def search(self, *, query: str, top_k: int, key: Optional[str] = None) -> list:
        url = f"{self.endpoint}?{urlencode({'q': query})}"
        resp = await _http_once(None, url, max_bytes=RAW_HTML_MAX)
        if resp.status >= 400:
            raise_code("TLB-805", url=url, http_status=resp.status,
                       advice=f"DuckDuckGo HTML 搜索返回 HTTP {resp.status}")
        parser = _DuckDuckGoParser()
        parser.feed(resp.raw.decode("utf-8", errors="replace"))
        parser.close()
        return parser.hits[:max(1, int(top_k))]




class BingRssBackend:
    """No-key Bing RSS backend (stable XML, no HTML scraping)."""

    requires_key = False

    def __init__(self, endpoint: str = "https://cn.bing.com/search") -> None:
        self.endpoint = endpoint

    async def search(self, *, query: str, top_k: int, key: Optional[str] = None) -> list:
        url = f"{self.endpoint}?format=rss&{urlencode({'q': query})}"
        resp = await _http_once(None, url, max_bytes=RAW_HTML_MAX)
        if resp.status >= 400:
            raise_code("TLB-805", url=url, http_status=resp.status,
                       advice=f"Bing RSS 搜索返回 HTTP {resp.status}")
        try:
            root = ET.fromstring(resp.raw.decode("utf-8", errors="replace"))
        except ET.ParseError as exc:
            raise_code("TLB-805", url=url, stage="parse",
                       cause=exc, advice="Bing RSS 返回非 XML")
        out: list[dict] = []
        for item in root.findall(".//item"):
            title = " ".join((item.findtext("title") or "").split())
            link = (item.findtext("link") or "").strip()
            snippet = " ".join((item.findtext("description") or "").split())
            if title or link or snippet:
                out.append({"title": title, "url": link, "snippet": snippet})
            if len(out) >= max(1, int(top_k)):
                break
        return out


# ================================================================ web.fetch
async def web_fetch(args: dict, ctx: Any) -> dict:
    """web.fetch(F038):域名 allowlist 自检 → http_get(15s/2MB/≤3 跳)→ 二进制
    拒 → html_to_markdown 正文提取 → redact → ≤max 直返 / >32KB 转 spill 引用。

    三层限制 = allowlist + 正文提取(HTML 原文永不进上下文)+ 截断;抓取内容
    指令视为数据(F024),本函数不执行页面任何指令形态。返回 _FetchOut 字典。
    """
    url = args["url"]
    domain = _domain_allowed(url, ctx)          # 域名归一 + allowlist 自检(第二道)
    if domain is None:                          # 默认空 = 禁一切外发
        raise_code("GRD-401", reason="POL-NET-1", url=url,
                   advice=_POL_NET_ADVICE)
    raw, final_url = await http_get(url, max_bytes=RAW_HTML_MAX, ctx=ctx)
    if _looks_binary(raw):                      # 二进制/非文本拒(TLB-806)
        raise_code("TLB-806", url=url,
                   advice="目标为二进制/非文本内容,换其它手段")
    md = html_to_markdown(raw.decode("utf-8", errors="replace"),
                          base_url=final_url)   # 正文提取;HTML 原文即弃
    md = _redact(ctx, md)                       # 出口脱敏(INV-09)
    cfg_max = _cfg_read(ctx, "loop.content.fetch_max_chars", FETCH_MAX_CHARS)
    try:
        cfg_max = int(cfg_max)
    except (TypeError, ValueError):
        cfg_max = FETCH_MAX_CHARS
    max_chars = args.get("max")
    if max_chars is None:
        max_chars = cfg_max                     # 参数缺省回落配置阈值
    if len(md) <= int(max_chars):               # 小正文直接返回
        return {"content": md, "truncated": False, "final_url": final_url}
    meta = await _spill_put(ctx, md, kind="fetch")   # >阈值转 spill(F039)
    return {"content": str(meta.get("preview", ""))[:500],
            "truncated": True,                  # 上下文只放摘要 + 引用
            "spill_ref": meta, "final_url": final_url}


def _looks_binary(data: bytes) -> bool:
    """二进制探测:NUL 字节样本命中(BINARY_SAMPLE=8192 头样本,与 tool_fs 同口径)。"""
    return b"\x00" in data[:BINARY_SAMPLE]


async def _spill_put(ctx: Any, text: str, kind: str) -> dict:
    """ctx.storage.spill.put 封装(与 tool_fs._spill_put 同款):spill 未接线 →
    PERS-221 结构化上抛(超长输出无法归档,不静默);返回须为 F039 SpillMeta dict。"""
    spill = getattr(getattr(ctx, "storage", None), "spill", None)
    if spill is None:
        raise_code("PERS-221", module="tool_web",
                   hint="storage.spill 未接线(能力未激活),超限内容无法归档")
    try:
        r = spill.put(text, kind=kind)
        if inspect.isawaitable(r):
            r = await r
    except PyHError:
        raise
    except Exception as exc:                    # noqa: BLE001 落盘故障 → PERS-221
        raise_code("PERS-221", module="tool_web", cause=exc,
                   advice="spill 写失败,查 spill 目录权限与磁盘空间")
    if not isinstance(r, dict):
        raise_code("PERS-221", module="tool_web",
                   hint="storage.spill.put 返回非 dict(契约违约)")
    return r


# ------------------------------------------------------------ 受限 HTTP 传输层
@dataclass(frozen=True)
class _RawResponse:
    """单跳原始响应契约(传输 seam 返回型):状态码/头/字节/终址。"""

    status: int
    headers: dict
    raw: bytes
    final_url: str


async def _http_once(ctx: Any, url: str, *, max_bytes: int) -> _RawResponse:
    """单跳 GET(不跟随重定向;传输 seam,偏离 1)——真实路径走 httpx。

    连接 10s/总 15s 超时(httpx.Timeout(15, connect=10),F038);3xx/4xx/5xx 只取
    头不读体(重定向/错误响应无需下载);2xx 限量读流,读满 max_bytes 即 PERS-223
    (超 2MB 拒,防内存/带宽滥用);httpx 超时/网络错误映射 TLB-805 明确原因。
    测试注入面:monkeypatch 本函数即可(mock transport,零 HTTP 外发,T-SEC-07)。
    """
    timeout = httpx.Timeout(HTTP_TIMEOUT, connect=CONNECT_TIMEOUT)
    try:
        async with httpx.AsyncClient(timeout=timeout,
                                     follow_redirects=False) as client:
            async with client.stream("GET", url) as resp:   # 手动控跳
                headers = {str(k).lower(): str(v)
                           for k, v in resp.headers.items()}
                if resp.status_code >= 300:     # 重定向/错误:不读体
                    return _RawResponse(resp.status_code, headers, b"",
                                        str(resp.url))
                raw = b""
                async for chunk in resp.aiter_bytes():      # 限量读流
                    raw += chunk
                    if len(raw) > max_bytes:    # 超原始上限 → 停并拒
                        raise_code("PERS-223", url=url, got=len(raw),
                                   max=max_bytes,
                                   advice="页面超过 2MB 抓取上限,换更小目标")
                return _RawResponse(resp.status_code, headers, raw,
                                    str(resp.url))
    except PyHError:
        raise
    except httpx.TimeoutException:
        raise_code("TLB-805", url=url, stage="timeout",
                   advice="抓取超时(15s)")
    except httpx.HTTPError as e:
        raise_code("TLB-805", url=url, stage="transport",
                   cause=e, advice=f"网络失败:{type(e).__name__}")


def _header(resp: _RawResponse, name: str) -> Optional[str]:
    """大小写不敏感头读取(fake/真实传输头键形态统一收敛)。"""
    target = name.lower()
    for k, v in (resp.headers or {}).items():
        if str(k).lower() == target:
            return str(v)
    return None


async def http_get(url: str, *, max_bytes: int, ctx: Any,
                   redirects: int = REDIRECT_MAX) -> tuple[bytes, str]:
    """受限 HTTP 抓取(F038 传输层,spec 伪码权威 + 偏离 1/2)。

    跟随重定向但逐跳对目标域名重验 allowlist(防白名单域名跳黑名单域名),超
    跳数/域逃逸即拒(GRD-401 POL-NET-1 / TLB-805);4xx/5xx 抛结构化错误带
    http_status;成功返回 (raw_bytes, final_url=重定向终址)。
    """
    if redirects <= 0:                          # 偏离 2:预算耗尽即停(≤3 跳)
        raise_code("TLB-805", url=url, redirects=REDIRECT_MAX,
                   advice=_MAX_REDIRECT_ADVICE)
    if _domain_allowed(url, ctx) is None:       # 每跳重验域(含首跳,第二道闸)
        raise_code("GRD-401", reason="POL-NET-1", url=url,
                   advice="重定向目标域未放行,已停止")
    resp = await _http_once(ctx, url, max_bytes=max_bytes)
    status = resp.status
    if 300 <= status < 400:                     # 重定向:手动跟随(urljoin 补相对)
        loc = _header(resp, "location")
        if not loc:
            raise_code("TLB-805", url=url, http_status=status,
                       advice="重定向无 Location")
        return await http_get(urljoin(url, loc), max_bytes=max_bytes,
                              ctx=ctx, redirects=redirects - 1)
    if status >= 400:                           # 4xx/5xx:明确原因,工具内不自动重试
        raise_code("TLB-805", url=url, http_status=status,
                   advice=f"HTTP {status},可稍后重试或换源")
    return resp.raw, resp.final_url            # 2xx:原文 + 终址


# ------------------------------------------------------------ 正文提取(F038)
class _TextExtractor(html.parser.HTMLParser):
    """手写 HTMLParser 状态机正文提取器(spec 实现要点权威)。

    状态:skip(命中丢弃块后的后代深度计数)/parts(输出流,块界以 "\\n" 标记)/
    links(a 标签栈,出标签拼 [text](url));script/style/noscript/svg/head/
    iframe/nav/footer 标签与 class/id 噪声命中块整体跳过(其后代不产任何输出),
    HTML 标签/脚本永不泄漏进结果。feed 全程容错(标准库 parser 容错模式)。
    """

    def __init__(self, base_url: str = "") -> None:
        super().__init__(convert_charrefs=True)  # &amp; 等实体并入文本
        self.base_url = base_url or ""
        self.parts: list[str] = []              # 输出流(文本 + "\n" 行断标记)
        self.skip: int = 0                      # 丢弃块深度(skip>0 后代全跳)
        self.links: list[dict] = []             # a 标签栈 {"href", "parts"}

    # ---------------------------------------------------------- 工具方法
    def _break(self) -> None:
        """行断标记(连续块界折叠为单个)。"""
        if not self.parts or self.parts[-1] != "\n":
            self.parts.append("\n")

    def _push_text(self, s: str) -> None:
        """文本入流:a 内收进栈顶链接缓冲,否则直出(相邻块空隙去重空格)。"""
        if not s:
            return
        if self.links:
            self.links[-1]["parts"].append(s)
            return
        if (self.parts and self.parts[-1].endswith(" ")
                and s.startswith(" ")):
            s = s.lstrip(" ")                   # 前段尾空格 + 本段首空格去重
        self.parts.append(s)

    # ---------------------------------------------------------- parser 回调
    def handle_starttag(self, tag: str, attrs: list) -> None:
        tag = tag.lower()
        if self.skip:                           # 丢弃块内:每开一标签深度 +1,
            self.skip += 1                      # 直至块闭合(后代整体跳过)
            return
        if tag in _SKIP_TAGS or _class_hit(attrs):
            self.skip = 1                       # 命中丢弃块:跳过其后代
            return
        if tag == "br":                         # 单行断(非块栈成员)
            self._break()
            return
        if tag in _BLOCK_TAGS:
            self._break()                       # 块级开:前导换行
            level = _HEADING_LEVELS.get(tag)
            if level:                           # 标题:写入 "#"*level + " "
                self.parts.append("#" * level + " ")
            return
        if tag == "a":
            href = _attr(attrs, "href")
            self.links.append({"href": href or "", "parts": []})
            return
        if tag == "img":                        # 图片取 alt(spec: 图片取 alt)
            alt = _attr(attrs, "alt")
            if alt:
                self._push_text(str(alt))
            return
        # 其余行内标签(span/b/strong/code…):忽略,文本由 handle_data 流入

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.skip:                           # 丢弃块收口(每个闭合标签 -1)
            self.skip = max(0, self.skip - 1)
            return
        if tag == "a" and self.links:
            info = self.links.pop()
            text = _WS_RE.sub(" ", "".join(info["parts"])).strip()
            href = (info.get("href") or "").strip()
            if text and href:
                self._push_text(f"[{text}]({urljoin(self.base_url, href)})")
            elif text:                          # 无 href 锚:仅保留文本
                self._push_text(text)
            return
        if tag in _BLOCK_TAGS:
            self._break()                       # 块级闭:尾随换行

    def handle_data(self, data: str) -> None:
        if self.skip:
            return                              # 丢弃块内文本不产出
        s = _WS_RE.sub(" ", data)               # 空白折叠(HTML 空白 → 空格)
        if s:
            self._push_text(s)

    # 注释/声明/处理指令一律忽略(handle_comment/decl/pis 默认空实现)


def _attr(attrs: list, name: str) -> Optional[str]:
    """属性大小写不敏感取值(HTMLParser 属性名大小写行为跨版本不一,统一收敛)。"""
    target = name.lower()
    for k, v in attrs or ():
        if k is not None and str(k).lower() == target:
            return v
    return None


def _class_hit(attrs: list) -> bool:
    """class/id 噪声黑名单命中(class/id 正则,spec 实现要点;re.I 偏离 6)。

    命中 class="nav"/"ad-banner"/"footer x" 等即整块跳过(去导航/广告噪声)。
    """
    for attr_name in ("class", "id"):
        v = _attr(attrs, attr_name)
        if v and _NOISE_RE.search(str(v)):
            return True
    return False


def html_to_markdown(html: str, base_url: str) -> str:
    """正文提取转 Markdown(spec 伪码权威;标准库自含,禁第三方解析依赖)。

    流式解析(不建 DOM):去 script/style/noscript/svg/head/nav/footer 与 class/id
    噪声块,块级标签换行、标题加 #、链接转 [text](url)、图片取 alt、空白折叠;
    输出为纯文本 Markdown,HTML 标签/脚本永不泄漏进结果。坏 HTML 容错不中断
    (parser 默认容错,无异常上抛;feed 层兜底记 debug)。输出 ≤ _MAX_MD(2MB)。
    """
    if not isinstance(html, str):
        html = str(html or "")
    parser = _TextExtractor(str(base_url or ""))
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:                    # noqa: BLE001 坏 HTML 不中断
        log.debug("html_to_markdown feed 异常(容错继续): %s", exc)
    text = "".join(parser.parts)
    lines = [ln.strip() for ln in text.splitlines()]   # 逐行清理
    md = "\n".join(ln for ln in lines if ln)           # 去空行
    return md[:_MAX_MD]                                # 防御性上限


# ============================================================ 五要素注册入口
PROVIDERS: dict[str, Any] = {
    "web.search": web_search,
    "web.fetch": web_fetch,
}
"""Provider handle 映射(裸 async handler,executor 关3 契约:handle(args, ctx))。"""


def register(registry: Any) -> list[str]:
    """五要素注册入口(F008/T-05):装配 web.search/web.fetch 两个 ToolDefinition。

    danger 均 none(白名单内只读,SECURITY §4.2),域名约束由 g5(g-net-outbound)
    + 工具内 _domain_allowed 第二道承担,不依赖 danger 审批。偏离 4:ToolDefinition
    无 handler 字段 → register_tool(d, provider=…) 绑定 Provider(tool_fs 同款)。
    """
    defs = [
        ToolDefinition(
            name="web.search", danger="none",
            description="关键词搜索(默认 5 条:标题/URL/摘要),供判断是否值得"
                        "抓取;受会话次数闸限制",
            schema={"type": "object",
                    "properties": {
                        "query": {"type": "string", "minLength": 1,
                                  "maxLength": 500},
                        "top_k": {"type": "integer", "minimum": 1,
                                  "maximum": 10}},
                    "required": ["query"], "additionalProperties": False},
            owner="builtin", timeout_s=30),
        ToolDefinition(
            name="web.fetch", danger="none",
            description="抓取 URL 并提取正文 Markdown(去导航/脚本/广告);"
                        "HTML 不入上下文;需域名已放行",
            schema={"type": "object",
                    "properties": {
                        "url": {"type": "string", "format": "uri",
                                "pattern": "^https?://"},
                        "max": {"type": "integer", "minimum": 1024,
                                "maximum": 1048576}},
                    "required": ["url"], "additionalProperties": False},
            owner="builtin", timeout_s=60),
    ]
    for d in defs:                               # 逐个登记并绑定 Provider
        registry.register_tool(d, provider=PROVIDERS.get(d.name))
    return [d.name for d in defs]                # 注册名清单


__all__ = [
    "web_search", "web_fetch", "BingRssBackend", "DuckDuckGoBackend",
    "_domain_allowed",
    "http_get", "html_to_markdown",
    "_summarize_search", "register", "PROVIDERS",
    "SEARCH_PER_SESSION", "SEARCH_RESULT_CHARS", "FETCH_MAX_CHARS",
    "RAW_HTML_MAX", "HTTP_TIMEOUT", "REDIRECT_MAX",
]
