r"""tests/unit/test_tool_web.py — Web 工具族 web.* 单测(F037/F038 契约)。

覆盖清单(specs/tool_web.py.md + SECURITY §7.3 + 模块偏离说明):
- web.search(F037):会话次数闸(超闸 GRD-401 reason=search-quota、0=禁用、
  临界不误伤、counters 三形态:dict/bump 对象/未接线回落 0)、SEARCH_API_KEY
  缺失 CRED-701(零后端调用)、search_backend 未接线 CYC-999 fail-closed、
  假 backend 注入(query/top_k/key 透传、top_k 默认 5/非数字回落 5)、结果摘要
  ≤8KB 截断(合计字符预算、装不下整条即停 truncated、字段 200/500/600 单条
  上限、缺失字段空串容错、非 dict 脏条目跳过、total 恒=后端命中数)
- 域名归一闸(_normalize_hostname/_domain_allowed):allowlist 默认空=全禁
  (POL-NET-1,scope 未接线同禁)、精确匹配大小写不敏感、子域不隐含放行、
  allow 条目宽容收 URL 形态、尾点/port/userinfo 剥离、IDNA 归一、非 http(s)
  与坏 URL 拒
- http_get 受限传输层(monkeypatch _http_once 假 transport,零 HTTP 外发):
  重定向跟随 + urljoin 相对 Location、逐跳域重验(逃逸 → GRD-401 POL-NET-1)、
  跳数预算(≤3,超限 TLB-805,恰 3 次传输)、无 Location → TLB-805、
  4xx/5xx → TLB-805(带 http_status)、2xx 返回 (raw, final_url)
- web.fetch(F038):域名闸先于传输(默认空 allowlist 下 _http_once 零调用)、
  HTML→Markdown 正文提取(script/style/noscript/svg/head/iframe/nav/footer
  与 class/id 噪声块剔除、HTML/脚本永不进上下文)、链接转 [text](url)、
  二进制 NUL 探测 → TLB-806、>32KB(默认)转 spill 只回摘要+引用、max 参数
  优先于配置阈值、恰阈值不误转、spill 未接线 PERS-221、put 返回非 dict/
  抛异常 → PERS-221、出口脱敏(直返与 spill 落盘双侧)
- html_to_markdown 纯函数:标题 # 前缀、空白折叠、实体解码、注释忽略、
  坏 HTML 容错、非字符串入参、噪声词边界(整词命中才丢)
- register:两个 ToolDefinition 五要素齐备注册成功、Provider 绑定、
  schema 编译/参数校验(TLB-803:缺必填/多余字段/非法 url/top_k 越界)、
  重名 TLB-801、danger=none/owner=builtin
- 回归锚点:模块编译零 SyntaxWarning(-W error::SyntaxWarning)

已知实现缺陷(不改主体,另见交付摘要):_TextExtractor 对 void 元素
(<img class="ad"> 等无闭合标签)命中噪声块后 skip 计数永不复位,其后整段
正文丢失;本文件不写该场景的绿测(避免固化缺陷行为),留待修复轮。

注:全部测试经 mock/假 transport/假 backend,不触真实网络(T-SEC-07)。
"""
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from pyharness.core import tool_web as web
from pyharness.core.tools_registry import ToolRegistry
from pyharness.errors import PyHError, ToolError

# ===================================================================== 替身
class FakeBackend:
    """SearchBackend 替身:记录调用参数,回放预设 hits(零外发)。"""

    def __init__(self, hits: list | None = None) -> None:
        self.hits = hits if hits is not None else []
        self.calls: list[dict] = []

    async def search(self, *, query: str, top_k: int, key: str) -> list:
        self.calls.append({"query": query, "top_k": top_k, "key": key})
        return self.hits


class BumpCounters:
    """ctx.counters bump 方法形态(偏离 3 兼容形态之一)。"""

    def __init__(self) -> None:
        self.search_calls = 0
        self.bumps: list[tuple] = []

    def bump(self, key: str, delta: int = 1) -> int:
        self.bumps.append((key, delta))
        self.search_calls += delta
        return self.search_calls


class FakeSpill:
    """ctx.storage.spill 替身(异步 put):记录原文/kind,返回 F039 契约 dict。"""

    def __init__(self) -> None:
        self.put_calls: list[tuple[str, str]] = []

    async def put(self, text: str, kind: str) -> dict:
        self.put_calls.append((text, kind))
        return {"spilled": True, "ref": f"spill/{kind}/1",
                "chars": len(text), "lines": text.count("\n") + 1,
                "preview": text[:500]}


class JunkSpill:
    """违约 spill:put 返回非 dict(同步形态)→ PERS-221。"""

    def put(self, text: str, kind: str):  # noqa: ANN201 故意不返回 dict
        return "not-a-dict"


class BoomSpill:
    """故障 spill:put 抛异常 → PERS-221。"""

    def put(self, text: str, kind: str) -> None:
        raise RuntimeError("disk full")


class MaskRedact:
    """ctx.redact 替身:密钥打码(验证出口脱敏被调用)。"""

    def __call__(self, text: str) -> str:
        return text.replace("sk-SECRET9", "sk-***")


def _search_ctx(*, counters=None, key="SEARCH-KEY-1", backend=None,
                cfg=None) -> SimpleNamespace:
    """web.search 的 ctx 替身(cfg 为扁平点分 dict 形态或 None=默认)。"""
    return SimpleNamespace(
        counters={} if counters is None else counters,
        cfg=cfg,
        get_secret=(lambda name: key),
        search_backend=backend if backend is not None else FakeBackend())


def _fetch_ctx(*, domains=(), spill=None, redact=None) -> SimpleNamespace:
    """web.fetch 的 ctx 替身:scope.policy.allowed_domains + 注入面。"""
    return SimpleNamespace(
        scope=SimpleNamespace(policy=SimpleNamespace(
            allowed_domains=list(domains))),
        cfg=None,
        storage=SimpleNamespace(spill=spill) if spill is not None else None,
        redact=redact)


def _raw(status: int, raw: bytes = b"", *, location: str | None = None,
         final_url: str | None = None) -> web._RawResponse:
    """传输 seam 假响应构造(headers 只放 Location;final_url 缺省=请求 URL)。"""
    headers = {"location": location} if location else {}
    return web._RawResponse(status, headers, raw, final_url or "")


# ================================================================= web.search
async def test_search_happy_path_calls_backend_and_summarizes():
    backend = FakeBackend(hits=[{"title": "t1", "url": "https://h/1",
                                 "snippet": "s1"},
                                {"title": "t2", "url": "https://h/2"}])
    ctx = _search_ctx(counters={}, backend=backend)
    out = await web.web_search({"query": "pytest", "top_k": 2}, ctx)
    assert out == {"results": [{"title": "t1", "url": "https://h/1",
                                "snippet": "s1"},
                               {"title": "t2", "url": "https://h/2",
                                "snippet": ""}],
                   "truncated": False, "total": 2}
    assert backend.calls == [{"query": "pytest", "top_k": 2,
                              "key": "SEARCH-KEY-1"}]   # 凭据透传后端
    assert ctx.counters["search_calls"] == 1             # 会话计数 +1


async def test_search_top_k_defaults_to_5():
    backend = FakeBackend()
    ctx = _search_ctx(counters={}, backend=backend)
    await web.web_search({"query": "x"}, ctx)
    assert backend.calls[0]["top_k"] == 5


async def test_search_top_k_non_integer_falls_back_to_5():
    """直调路径兜底:非数字 top_k 回落默认 5(契约层之外)。"""
    backend = FakeBackend()
    ctx = _search_ctx(counters={}, backend=backend)
    await web.web_search({"query": "x", "top_k": "abc"}, ctx)
    assert backend.calls[0]["top_k"] == 5


async def test_search_quota_exceeded_grd401_zero_backend():
    """超闸(used=21 > limit=20):GRD-401 search-quota,零后端外发。"""
    backend = FakeBackend()
    ctx = _search_ctx(counters={"search_calls": 20}, backend=backend,
                      cfg={"security.network.web_search_per_session": 20})
    with pytest.raises(PyHError) as ei:
        await web.web_search({"query": "x"}, ctx)
    assert ei.value.code == "GRD-401"
    assert ei.value.ctx["reason"] == "search-quota"
    assert ei.value.ctx["used"] == 21 and ei.value.ctx["limit"] == 20
    assert "上限" in ei.value.ctx["advice"]
    assert backend.calls == []                        # 先计数后判断,零外发
    assert ctx.counters["search_calls"] == 21         # 拒绝也留计数痕


async def test_search_quota_limit_zero_disables():
    """limit=0 = 禁用(0=禁用语义):首次调用即拒,零外发。"""
    backend = FakeBackend()
    ctx = _search_ctx(counters={}, backend=backend,
                      cfg={"security.network.web_search_per_session": 0})
    with pytest.raises(PyHError) as ei:
        await web.web_search({"query": "x"}, ctx)
    assert ei.value.code == "GRD-401"
    assert backend.calls == []


async def test_search_quota_allows_up_to_limit():
    """恰在限内(used=2 <= limit=2)放行——临界不误伤。"""
    backend = FakeBackend(hits=[{"title": "t", "url": "u"}])
    ctx = _search_ctx(counters={"search_calls": 1}, backend=backend,
                      cfg={"security.network.web_search_per_session": 2})
    out = await web.web_search({"query": "x"}, ctx)
    assert out["total"] == 1 and len(backend.calls) == 1


async def test_search_counters_bump_object_form():
    """counters 以带 bump 方法的对象挂载(装配形态二)同样生效。"""
    backend = FakeBackend(hits=[{"title": "t", "url": "u"}])
    counters = BumpCounters()
    ctx = _search_ctx(counters=counters, backend=backend)
    await web.web_search({"query": "x"}, ctx)
    assert counters.search_calls == 1
    assert counters.bumps == [("search_calls", 1)]


async def test_search_counters_unwired_falls_back_open():
    """counters 未接线 → 回落 0 读数(偏离 3):不崩溃、放行、无计数对象。"""
    backend = FakeBackend(hits=[{"title": "t", "url": "u"}])
    ctx = SimpleNamespace(  # 刻意不挂 counters 属性
        cfg=None, get_secret=(lambda name: "K"),
        search_backend=backend)
    out = await web.web_search({"query": "x"}, ctx)
    assert out["total"] == 1


async def test_search_missing_key_cred701():
    """SEARCH_API_KEY 缺失(F016)→ CRED-701,零后端调用;计数仍先发生(spec 序)。"""
    backend = FakeBackend()
    counters = {"search_calls": 0}
    ctx = _search_ctx(counters=counters, backend=backend, key=None)
    with pytest.raises(PyHError) as ei:
        await web.web_search({"query": "x"}, ctx)
    assert ei.value.code == "CRED-701"
    assert "SEARCH_API_KEY" in ei.value.ctx["hint"]
    assert backend.calls == []
    assert counters["search_calls"] == 1


async def test_search_backend_unwired_cyc999():
    """search_backend 未接线 = 装配 bug → CYC-999 fail-closed(绝不回落默认端点)。"""
    ctx = SimpleNamespace(counters={}, cfg=None,
                          get_secret=(lambda name: "K"))
    with pytest.raises(PyHError) as ei:
        await web.web_search({"query": "x"}, ctx)
    assert ei.value.code == "CYC-999"
    assert ei.value.ctx["module"] == "tool_web"


async def test_search_results_capped_at_8kb_budget():
    """合计字符预算 8000:超预算截断留痕;total 恒=后端命中总数。"""
    hits = [{"title": f"T{i}", "url": f"https://h/{i}",
             "snippet": "x" * 3000} for i in range(20)]
    backend = FakeBackend(hits=hits)
    ctx = _search_ctx(counters={}, backend=backend)
    out = await web.web_search({"query": "q"}, ctx)
    assert out["truncated"] is True
    assert len(out["results"]) < 20                 # 预算内装不下 20 条
    assert out["total"] == 20                       # 截断不吞命中总数
    used = sum(len(v) for r in out["results"] for v in r.values())
    assert used <= 8000                             # 返回合计不超预算
    assert all(len(r["title"]) <= 200 and len(r["url"]) <= 500
               and len(r["snippet"]) <= 600 for r in out["results"])


async def test_search_result_budget_configurable():
    """loop.content.search_result_chars 可配(512):单条装不下 → 空结果+留痕。"""
    backend = FakeBackend(hits=[{"title": "t" * 200, "url": "u" * 500}])
    ctx = _search_ctx(counters={}, backend=backend,
                      cfg={"loop.content.search_result_chars": 512})
    out = await web.web_search({"query": "q"}, ctx)
    assert out["results"] == [] and out["truncated"] is True
    assert out["total"] == 1


# ======================================================== _summarize_search
def test_summarize_missing_fields_blank():
    """hits 字段缺失按空串容错(偏离 5);total=len(hits)。"""
    out = web._summarize_search(
        [{"title": "only-title"}, {"url": "only-url", "snippet": "s"}],
        max_chars=8000)
    assert out["results"][0] == {"title": "only-title", "url": "",
                                 "snippet": ""}
    assert out["results"][1] == {"title": "", "url": "only-url",
                                 "snippet": "s"}
    assert out["total"] == 2 and out["truncated"] is False


def test_summarize_field_caps_200_500_600():
    """单条字段各自截断(title 200 / url 500 / snippet 600)。"""
    hit = {"title": "t" * 250, "url": "u" * 600, "snippet": "s" * 700}
    out = web._summarize_search([hit], max_chars=8000)
    item = out["results"][0]
    assert len(item["title"]) == 200
    assert len(item["url"]) == 500
    assert len(item["snippet"]) == 600


def test_summarize_junk_entries_skipped_but_counted():
    """非 dict 脏条目跳过不入结果,total 仍按后端命中数计。"""
    hits = ["junk", None, 3, {"title": "real", "url": "u"}]
    out = web._summarize_search(hits, max_chars=8000)
    assert [r["title"] for r in out["results"]] == ["real"]
    assert out["total"] == 4


def test_summarize_empty_hits():
    assert web._summarize_search([], max_chars=8000) == \
        {"results": [], "truncated": False, "total": 0}
    assert web._summarize_search(None, max_chars=8000)["total"] == 0


def test_summarize_exact_fit_no_false_truncated():
    """恰用尽预算不误标 truncated;下一条装不下才截断。"""
    out = web._summarize_search(
        [{"title": "t", "url": "u", "snippet": ""},
         {"title": "t", "url": "u", "snippet": ""}], max_chars=4)
    assert len(out["results"]) == 2 and out["truncated"] is False
    out2 = web._summarize_search(
        [{"title": "t", "url": "u", "snippet": ""}], max_chars=0)
    assert out2["results"] == [] and out2["truncated"] is True


# ========================================================= 域名归一闸(F038)
def test_domain_allowed_empty_default_all_denied():
    """allowlist 默认空 = 禁一切外发;scope 未接线同样全禁(fail-closed)。"""
    ctx = _fetch_ctx(domains=[])
    assert web._domain_allowed("https://example.com/a", ctx) is None
    assert web._domain_allowed("https://evil.example/b", ctx) is None
    assert web._domain_allowed("https://example.com/a",
                               SimpleNamespace()) is None   # 无 scope


def test_domain_allowed_exact_match_case_insensitive():
    """精确匹配(归一后),大小写不敏感;子域/IP 不隐含放行。"""
    ctx = _fetch_ctx(domains=["example.com"])
    assert web._domain_allowed("https://example.com/a", ctx) == "example.com"
    assert web._domain_allowed("HTTPS://EXAMPLE.COM/b", ctx) == "example.com"
    assert web._domain_allowed("https://api.example.com/c", ctx) is None
    assert web._domain_allowed("http://127.0.0.1/x", ctx) is None
    assert web._domain_allowed("http://example.com.evil.com/y", ctx) is None


def test_domain_allowed_allow_entries_url_form_and_idna():
    """allow 条目宽容收 URL 形态(归一后比对);IDNA 域可精确命中。"""
    ctx = _fetch_ctx(domains=["https://sub.example.org/",
                              "xn--bcher-kva.example"])
    assert web._domain_allowed("http://sub.example.org/x", ctx) == \
        "sub.example.org"
    assert web._domain_allowed("https://bücher.example/y", ctx) == \
        "xn--bcher-kva.example"
    assert web._domain_allowed("https://plain.example.org/z", ctx) is None


def test_domain_allowed_bare_policy_shape():
    """ctx.scope 直挂裸 ScopePolicy(无 policy 包装)两形态同收。"""
    ctx = SimpleNamespace(scope=SimpleNamespace(allowed_domains=["e.com"]))
    assert web._domain_allowed("https://e.com/x", ctx) == "e.com"


def test_domain_allowed_invalid_url_denied():
    """非 http(s)/坏 URL → None(绝不误放)。"""
    ctx = _fetch_ctx(domains=["example.com"])
    for bad in ("ftp://example.com/x", "file:///etc/passwd",
                "http://[bad", "", "javascript:alert(1)"):
        assert web._domain_allowed(bad, ctx) is None


@pytest.mark.parametrize("raw,expect", [
    ("", None),
    (None, None),
    ("http://[bad", None),
    ("ftp://example.com/x", None),
    ("example.com", "example.com"),
    ("HTTPS://Example.COM.:8080/x?y=1", "example.com"),
    ("http://user:pass@example.com:8080/x", "example.com"),
    ("https://bücher.example/x", "xn--bcher-kva.example"),
])
def test_normalize_hostname_forms(raw, expect):
    """归一:小写/去尾点/去 userinfo/port/路径/IDNA;非 http(s) 拒。"""
    assert web._normalize_hostname(raw) == expect


# ====================================================== http_get(假 transport)
async def test_http_get_follows_redirect_reevaluating_domain(
        monkeypatch):
    """跟随重定向:相对 Location 经 urljoin 补全;返回 (raw, final_url)。"""
    seen: list[str] = []

    async def fake_once(ctx, url, *, max_bytes):
        seen.append(url)
        if url.endswith("/start"):
            return _raw(302, location="/final")
        return _raw(200, b"<p>ok</p>", final_url="https://a.example/final")

    monkeypatch.setattr(web, "_http_once", fake_once)
    ctx = _fetch_ctx(domains=["a.example"])
    raw, final = await web.http_get("https://a.example/start",
                                    max_bytes=1024, ctx=ctx)
    assert raw == b"<p>ok</p>"
    assert final == "https://a.example/final"
    assert seen == ["https://a.example/start", "https://a.example/final"]


async def test_http_get_redirect_escape_blocked_grd401(monkeypatch):
    """重定向目标域逃逸 allowlist → GRD-401 POL-NET-1,第二跳零传输。"""
    seen: list[str] = []

    async def fake_once(ctx, url, *, max_bytes):
        seen.append(url)
        return _raw(302, location="https://evil.example/steal")

    monkeypatch.setattr(web, "_http_once", fake_once)
    ctx = _fetch_ctx(domains=["a.example"])
    with pytest.raises(PyHError) as ei:
        await web.http_get("https://a.example/start", max_bytes=1024, ctx=ctx)
    assert ei.value.code == "GRD-401"
    assert ei.value.ctx["reason"] == "POL-NET-1"
    assert "未放行" in ei.value.ctx["advice"]
    assert seen == ["https://a.example/start"]     # 逃逸跳转未被发出


async def test_http_get_redirect_budget_exhausted_tlb805(monkeypatch):
    """跳数预算 ≤3(偏离 2):恰 3 次传输后仍 302 → TLB-805,零第 4 跳。"""
    seen: list[str] = []

    async def loop_once(ctx, url, *, max_bytes):
        seen.append(url)
        return _raw(302, location="/again")

    monkeypatch.setattr(web, "_http_once", loop_once)
    ctx = _fetch_ctx(domains=["a.example"])
    with pytest.raises(PyHError) as ei:
        await web.http_get("https://a.example/hop", max_bytes=1024, ctx=ctx)
    assert ei.value.code == "TLB-805"
    assert "超过 3 跳" in ei.value.ctx["advice"]
    assert len(seen) == 3


async def test_http_get_redirect_without_location_tlb805(monkeypatch):
    async def no_loc_once(ctx, url, *, max_bytes):
        return _raw(302)                          # 无 Location 头

    monkeypatch.setattr(web, "_http_once", no_loc_once)
    ctx = _fetch_ctx(domains=["a.example"])
    with pytest.raises(PyHError) as ei:
        await web.http_get("https://a.example/x", max_bytes=1024, ctx=ctx)
    assert ei.value.code == "TLB-805"
    assert ei.value.ctx["http_status"] == 302
    assert "无 Location" in ei.value.ctx["advice"]


@pytest.mark.parametrize("status", [404, 500, 503])
async def test_http_get_4xx_5xx_tlb805_with_status(monkeypatch, status):
    async def err_once(ctx, url, *, max_bytes):
        return _raw(status)

    monkeypatch.setattr(web, "_http_once", err_once)
    ctx = _fetch_ctx(domains=["a.example"])
    with pytest.raises(PyHError) as ei:
        await web.http_get("https://a.example/x", max_bytes=1024, ctx=ctx)
    assert ei.value.code == "TLB-805"
    assert ei.value.ctx["http_status"] == status
    assert f"HTTP {status}" in ei.value.ctx["advice"]


async def test_http_get_2xx_returns_raw_and_final_url(monkeypatch):
    async def ok_once(ctx, url, *, max_bytes):
        return _raw(200, b"body-bytes",
                    final_url="https://cdn.example/redirected")

    monkeypatch.setattr(web, "_http_once", ok_once)
    ctx = _fetch_ctx(domains=["a.example"])
    raw, final = await web.http_get("https://a.example/x",
                                    max_bytes=1024, ctx=ctx)
    assert raw == b"body-bytes"
    assert final == "https://cdn.example/redirected"


# ================================================================= web.fetch
async def test_fetch_denied_when_no_allowlist_zero_http(monkeypatch):
    """默认空 allowlist → POL-NET-1,传输 seam 零调用(零外发可证)。"""
    def boom(ctx, url, *, max_bytes):
        raise AssertionError("allowlist 未命中不应触网")

    monkeypatch.setattr(web, "_http_once", boom)
    ctx = _fetch_ctx(domains=[])
    with pytest.raises(PyHError) as ei:
        await web.web_fetch({"url": "https://evil.example/x"}, ctx)
    assert ei.value.code == "GRD-401"
    assert ei.value.ctx["reason"] == "POL-NET-1"


async def test_fetch_extracts_markdown_no_html_leak(monkeypatch):
    """正文提取:HTML/脚本/样式不入上下文,链接转 Markdown,块界换行。"""
    html = ("<html><head><title>t</title><style>.hide{}</style></head>"
            "<body><h1>标题</h1>"
            "<script>alert(1)</script>"
            "<p>a &amp; b   c</p>"
            '<a href="/rel">链接</a>'
            '<img alt="logo">'
            "<br>下<br>一"
            '<div class="NAV">导航噪声</div>'
            "<p>尾</p></body></html>")

    async def ok_once(ctx, url, *, max_bytes):
        return _raw(200, html.encode("utf-8"),
                    final_url="https://example.com/page")

    monkeypatch.setattr(web, "_http_once", ok_once)
    ctx = _fetch_ctx(domains=["example.com"])
    out = await web.web_fetch({"url": "https://example.com/page"}, ctx)
    assert out["truncated"] is False
    assert out["final_url"] == "https://example.com/page"
    # 确定性正文:标题 # 前缀、实体解码、空白折叠、链接 join 到 final_url
    assert out["content"] == \
        "# 标题\na & b c\n[链接](https://example.com/rel)logo\n下\n一\n尾"
    for frag in ("<", "script", "alert", "style", "nav"):
        assert frag not in out["content"]          # 无任何 HTML 残留


async def test_fetch_strips_noise_and_executable_blocks(monkeypatch):
    """script/style/noscript/svg/iframe/nav/footer/噪声 class 整块剔除。"""
    html = ("<nav>导航块</nav><footer>页脚块</footer>"
            '<div class="menu">菜单块</div><div class="ad">广告块</div>'
            '<div class="ads">多广告块</div><div id="sidebar">侧栏块</div>'
            '<div class="banner">横幅块</div>'
            '<div class="Ad-Banner">大小写横幅</div>'
            '<script>function steal(){fetch("https://evil.example")}</script>'
            "<svg><text>svg块</text></svg><noscript>noscript块</noscript>"
            '<iframe src="https://evil">iframe块</iframe>'
            "<style>.c{color:red}</style>"
            "<p>正文保留 keep-me</p>")

    async def ok_once(ctx, url, *, max_bytes):
        return _raw(200, html.encode("utf-8"), final_url="https://e.com/p")

    monkeypatch.setattr(web, "_http_once", ok_once)
    ctx = _fetch_ctx(domains=["e.com"])
    out = await web.web_fetch({"url": "https://e.com/p"}, ctx)
    assert "keep-me" in out["content"]             # 正文保留
    for noise in ("导航块", "页脚块", "菜单块", "广告块", "多广告块",
                  "侧栏块", "横幅块", "大小写横幅", "steal", "svg块",
                  "noscript块", "iframe块", "<"):
        assert noise not in out["content"]         # 噪声与可执行块零泄漏


async def test_fetch_binary_nul_tlb806(monkeypatch):
    async def bin_once(ctx, url, *, max_bytes):
        return _raw(200, b"\x89PNG\r\n\x1a\n\x00\x01\x02")

    monkeypatch.setattr(web, "_http_once", bin_once)
    ctx = _fetch_ctx(domains=["e.com"])
    with pytest.raises(PyHError) as ei:
        await web.web_fetch({"url": "https://e.com/img"}, ctx)
    assert ei.value.code == "TLB-806"


async def test_fetch_over_32kb_spills_summary_only(monkeypatch):
    """>32KB(默认):上下文只放 500 字摘要+spill_ref,全文进 spill 不进结果。"""
    html = "<p>" + "x" * 40_000 + "</p>"

    async def big_once(ctx, url, *, max_bytes):
        return _raw(200, html.encode("utf-8"), final_url="https://e.com/big")

    monkeypatch.setattr(web, "_http_once", big_once)
    spill = FakeSpill()
    ctx = _fetch_ctx(domains=["e.com"], spill=spill)
    out = await web.web_fetch({"url": "https://e.com/big"}, ctx)
    assert out["truncated"] is True
    assert out["content"] == "x" * 500             # 摘要(preview[:500])
    assert out["spill_ref"]["ref"].startswith("spill/fetch/")
    assert out["final_url"] == "https://e.com/big"
    assert len(spill.put_calls) == 1
    assert spill.put_calls[0][1] == "fetch"        # kind 正确
    assert len(spill.put_calls[0][0]) == 40_000    # 全文落 spill
    assert "x" * 40_000 not in out["content"]      # 全文绝不上上下文


async def test_fetch_max_arg_overrides_cfg_threshold(monkeypatch):
    """args.max 优先于配置阈值:40000 ≤ max=40000 → 直返不转 spill。"""
    html = "<p>" + "y" * 40_000 + "</p>"

    async def ok_once(ctx, url, *, max_bytes):
        return _raw(200, html.encode("utf-8"), final_url="https://e.com/b")

    monkeypatch.setattr(web, "_http_once", ok_once)
    spill = FakeSpill()
    ctx = _fetch_ctx(domains=["e.com"], spill=spill)
    out = await web.web_fetch({"url": "https://e.com/b", "max": 40_000}, ctx)
    assert out["truncated"] is False
    assert len(out["content"]) == 40_000
    assert spill.put_calls == []


async def test_fetch_exactly_at_threshold_no_false_spill(monkeypatch):
    """len(md) 恰等于阈值 32768 → 直返(> 才转 spill)。"""
    html = "<p>" + "z" * 32_768 + "</p>"

    async def ok_once(ctx, url, *, max_bytes):
        return _raw(200, html.encode("utf-8"), final_url="https://e.com/e")

    monkeypatch.setattr(web, "_http_once", ok_once)
    spill = FakeSpill()
    ctx = _fetch_ctx(domains=["e.com"], spill=spill)
    out = await web.web_fetch({"url": "https://e.com/e"}, ctx)
    assert out["truncated"] is False
    assert len(out["content"]) == 32_768
    assert spill.put_calls == []


async def test_fetch_spill_unwired_pers221(monkeypatch):
    """storage.spill 未接线:超长内容无法归档 → PERS-221(不静默)。"""
    html = "<p>" + "w" * 40_000 + "</p>"

    async def big_once(ctx, url, *, max_bytes):
        return _raw(200, html.encode("utf-8"), final_url="https://e.com/b")

    monkeypatch.setattr(web, "_http_once", big_once)
    ctx = _fetch_ctx(domains=["e.com"])           # storage=None
    with pytest.raises(PyHError) as ei:
        await web.web_fetch({"url": "https://e.com/b"}, ctx)
    assert ei.value.code == "PERS-221"
    assert ei.value.ctx["module"] == "tool_web"


async def test_fetch_spill_put_non_dict_pers221(monkeypatch):
    html = "<p>" + "w" * 40_000 + "</p>"

    async def big_once(ctx, url, *, max_bytes):
        return _raw(200, html.encode("utf-8"), final_url="https://e.com/b")

    monkeypatch.setattr(web, "_http_once", big_once)
    ctx = _fetch_ctx(domains=["e.com"], spill=JunkSpill())
    with pytest.raises(PyHError) as ei:
        await web.web_fetch({"url": "https://e.com/b"}, ctx)
    assert ei.value.code == "PERS-221"


async def test_fetch_spill_put_raises_pers221(monkeypatch):
    html = "<p>" + "w" * 40_000 + "</p>"

    async def big_once(ctx, url, *, max_bytes):
        return _raw(200, html.encode("utf-8"), final_url="https://e.com/b")

    monkeypatch.setattr(web, "_http_once", big_once)
    ctx = _fetch_ctx(domains=["e.com"], spill=BoomSpill())
    with pytest.raises(PyHError) as ei:
        await web.web_fetch({"url": "https://e.com/b"}, ctx)
    assert ei.value.code == "PERS-221"


async def test_fetch_redact_applied_direct_and_spill(monkeypatch):
    """出口脱敏:直返内容与 spill 落盘文本双侧都只见掩码(INV-09)。"""
    small = "<p>token sk-SECRET9 end</p>"

    async def ok_once(ctx, url, *, max_bytes):
        if url.endswith("/small"):
            return _raw(200, small.encode("utf-8"), final_url=url)
        big = "<p>sk-SECRET9" + "v" * 40_000 + "</p>"
        return _raw(200, big.encode("utf-8"), final_url=url)

    monkeypatch.setattr(web, "_http_once", ok_once)
    spill = FakeSpill()
    ctx = _fetch_ctx(domains=["e.com"], spill=spill, redact=MaskRedact())
    out = await web.web_fetch({"url": "https://e.com/small"}, ctx)
    assert "sk-***" in out["content"]
    assert "sk-SECRET9" not in out["content"]     # 直返已脱敏
    await web.web_fetch({"url": "https://e.com/big"}, ctx)
    assert "sk-SECRET9" not in spill.put_calls[0][0]
    assert spill.put_calls[0][0].startswith("sk-***")   # 落盘同样脱敏


# ===================================================== html_to_markdown 纯函数
def test_md_noise_class_whole_word_only():
    """class/id 噪声黑名单整词命中才丢:navbar/myfooter 前缀后缀不误伤。"""
    html = ("<div class='nav'>D1</div><div class='nav-item'>D2</div>"
            "<div class='menu'>D3</div><div class='ad'>D4</div>"
            "<div class='ads'>D5</div><div class='footer x'>D6</div>"
            "<div class='sidebar'>D7</div><div id='ad-banner'>D8</div>"
            "<div class='navbar-fixed'>K1</div>"
            "<div class='myfooter'>K2</div>"
            "<div class='notnav'>K3</div><div class='rad'>K4</div>"
            "<p>kept</p>")
    out = web.html_to_markdown(html, "")
    for gone in ("D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8"):
        assert gone not in out
    for kept in ("K1", "K2", "K3", "K4", "kept"):
        assert kept in out
    assert "<" not in out


def test_md_heading_levels():
    html = "".join(f"<h{i}>标题{i}</h{i}>" for i in range(1, 7))
    lines = web.html_to_markdown(html, "").splitlines()
    assert lines == ["#" * i + " 标题" + str(i) for i in range(1, 7)]


def test_md_links_resolution_and_edge_cases():
    """绝对/相对链接、无 href 锚保留文本、空文本锚丢弃、a 内 img alt 收为链接文本。"""
    html = ('<a href="https://abs.example/x">绝对</a>'
            '<a href="../up">相对</a><a>裸文</a><a href=""></a>'
            '<a href="/pic"><img alt="图"></a><p>end</p>')
    out = web.html_to_markdown(html, "https://e.com/dir/page.html")
    assert out == ("[绝对](https://abs.example/x)"
                   "[相对](https://e.com/up)裸文"
                   "[图](https://e.com/pic)\nend")


def test_md_whitespace_collapse_entities_comments():
    html = "<!-- 隐藏注释 --><p>a   b</p><p>c &lt; d &amp; e</p>"
    assert web.html_to_markdown(html, "") == "a b\nc < d & e"


def test_md_img_alt_outside_link():
    html = '<p>图:</p><img alt="示例图" src="x.png"><p>后</p>'
    out = web.html_to_markdown(html, "")
    assert "示例图" in out and "<img" not in out


def test_md_malformed_html_tolerant():
    """坏 HTML 容错不中断(parser 默认容错):文本仍尽量保留。"""
    html = "<div><p>ok<div>deep<b>bold"        # 全不闭合
    out = web.html_to_markdown(html, "")
    assert "ok" in out and "deep" in out and "bold" in out


def test_md_non_string_inputs():
    assert web.html_to_markdown("", "") == ""
    assert web.html_to_markdown(None, "") == ""
    assert web.html_to_markdown(0, "") == ""    # falsy → 空


def test_md_html_never_leaks_tags():
    """任何标签形态(含大小写混合/属性)不进输出——纯文本契约。"""
    html = ('<P CLASS="x">t</P><SpAn>s</SpAn><BR><CODE>c</CODE>'
            "<input type='text' value='v'>tail")
    out = web.html_to_markdown(html, "")
    assert out == "t\ns\nc\ntail" or "tail" in out
    assert "<" not in out and ">" not in out


@pytest.mark.parametrize("data,expect", [
    (b"plain\x00bytes", True),
    (b"no-nul-here", False),
    (b"a" * 9000 + b"\x00", False),             # NUL 超出 8192 样本窗口
])
def test_looks_binary_nul_sample(data, expect):
    assert web._looks_binary(data) is expect


# ============================================================== register 五要素
def test_register_two_definitions_with_provider_binding():
    reg = ToolRegistry()
    names = web.register(reg)
    assert names == list(web.PROVIDERS) == ["web.search", "web.fetch"]
    for n in names:
        d = reg.lookup(n)
        assert d.name and d.description and d.schema and d.danger and d.owner
        assert d.danger == "none" and d.owner == "builtin"
        reg.get_model(n)                        # schema 编译通过(F026)
    # Provider 绑定(偏离 4:executor 关3 按名取实现)
    assert reg.lookup_provider("web.search") is web.web_search
    assert reg.lookup_provider("web.fetch") is web.web_fetch
    # 契约要点抽查
    ws = reg.lookup("web.search").schema
    assert ws["required"] == ["query"]
    assert ws["properties"]["query"]["maxLength"] == 500
    assert ws["properties"]["top_k"]["maximum"] == 10
    assert reg.lookup("web.fetch").schema["properties"]["url"]["pattern"] == \
        "^https?://"


def test_register_duplicate_tlb801():
    reg = ToolRegistry()
    web.register(reg)
    with pytest.raises(ToolError) as ei:
        web.register(reg)
    assert ei.value.code == "TLB-801"


def test_validate_args_search_contract():
    """schema 契约:缺必填/多余字段/越界 → TLB-803 零执行(INV-06)。"""
    reg = ToolRegistry()
    web.register(reg)
    for bad in ({"top_k": 1},                        # 缺 query
                {"query": "x", "evil": 1},           # additionalProperties=False
                {"query": ""},                       # minLength=1
                {"query": "x", "top_k": 11}):        # maximum=10
        with pytest.raises(ToolError) as ei:
            reg.validate_args("web.search", bad)
        assert ei.value.code == "TLB-803"
    ok = reg.validate_args("web.search", {"query": "q", "top_k": 3})
    assert ok["query"] == "q" and ok["top_k"] == 3


def test_validate_args_fetch_contract():
    reg = ToolRegistry()
    web.register(reg)
    for bad in ({},                                     # 缺 url
                {"url": "ftp://example.com/x"},         # pattern ^https?://
                {"url": "https://e.com/", "max": 100}): # minimum=1024
        with pytest.raises(ToolError) as ei:
            reg.validate_args("web.fetch", bad)
        assert ei.value.code == "TLB-803"
    ok = reg.validate_args("web.fetch", {"url": "https://e.com/a"})
    assert ok["url"] == "https://e.com/a"


# ============================================== SyntaxWarning 回归锚点
def test_module_compiles_without_syntax_warning():
    r"""回归:docstring 的 \s 转义曾触发 SyntaxWarning(改 r-string 修复)。

    以 -W error 级别编译整个模块,任何 invalid escape 都会让本测试失败。
    """
    root = Path(web.__file__).resolve().parents[2]     # 仓库根(含 pyharness/)
    script = ("import warnings\n"
              "warnings.simplefilter('error', SyntaxWarning)\n"
              f"compile(open({str(Path(web.__file__).resolve())!r}, "
              "encoding='utf-8').read(), 'tool_web.py', 'exec')\n"
              "print('OK')")
    r = subprocess.run([sys.executable, "-c", script], capture_output=True,
                       text=True, cwd=str(root))
    assert r.returncode == 0, r.stderr
    assert "OK" in r.stdout
