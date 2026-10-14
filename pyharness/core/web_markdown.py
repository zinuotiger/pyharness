"""Web markdown: complete migrated implementation."""
from __future__ import annotations
from pyharness.core.web_policy import log, _MAX_MD
import html.parser
import re
from typing import Optional
from urllib.parse import urljoin


_SKIP_TAGS: frozenset = frozenset({
    "script", "style", "noscript", "svg", "head", "iframe", "nav", "footer",
})

_BLOCK_TAGS: frozenset = frozenset({
    "p", "div", "li", "tr", "pre", "h1", "h2", "h3", "h4", "h5", "h6",
})

_HEADING_LEVELS: dict[str, int] = {f"h{i}": i for i in range(1, 7)}

_NOISE_RE: re.Pattern = re.compile(
    r"(^|[\s_-])(nav|menu|ad|ads?|footer|sidebar|banner)([\s_-]|$)", re.I)

_WS_RE: re.Pattern = re.compile(r"\s+")

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

# Preserve historical import and pickle identities through the compatibility facade.
_TextExtractor.__module__ = 'pyharness.core.tool_web'
