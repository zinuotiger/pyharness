"""Web domains: complete migrated implementation."""
from __future__ import annotations
from pyharness.core.web_domains_config import _allowed_domains
from typing import Any, Optional
from urllib.parse import urlsplit


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
