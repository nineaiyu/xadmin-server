#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""富文本 HTML 白名单净化（服务端为主，前端 DOMPurify 兜底）。

用于公告/站内信等 wangEditor 产出的 HTML 内容：入库前剥离 script/iframe、
事件属性（on*）与 javascript: 等危险协议，保留正常排版所需的标签与属性。
"""

from typing import Any

import bleach
from bleach.css_sanitizer import CSSSanitizer

# wangEditor 常用排版标签白名单
ALLOWED_TAGS = [
    "a",
    "abbr",
    "b",
    "blockquote",
    "br",
    "code",
    "col",
    "colgroup",
    "del",
    "div",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "img",
    "ins",
    "li",
    "mark",
    "ol",
    "p",
    "pre",
    "q",
    "s",
    "small",
    "span",
    "strong",
    "sub",
    "sup",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "u",
    "ul",
    "video",
    "source",
]
ALLOWED_ATTRS = {
    "*": ["class", "style"],
    "a": ["href", "title", "target", "rel"],
    "img": ["src", "alt", "title", "width", "height"],
    "td": ["colspan", "rowspan"],
    "th": ["colspan", "rowspan"],
    "ol": ["start"],
    "video": ["src", "controls", "width", "height", "poster"],
    "source": ["src", "type"],
}
# 允许的 URL 协议；相对路径（图片/附件）不受协议校验影响
ALLOWED_PROTOCOLS = ["http", "https", "mailto"]
# style 属性内允许的 CSS 属性（防 CSS 注入夹带 url()/expression 等）
ALLOWED_CSS_PROPERTIES = [
    "background-color",
    "border",
    "border-collapse",
    "border-color",
    "border-radius",
    "border-style",
    "border-width",
    "bottom",
    "clear",
    "color",
    "display",
    "float",
    "font-family",
    "font-size",
    "font-style",
    "font-weight",
    "height",
    "line-height",
    "margin",
    "margin-bottom",
    "margin-left",
    "margin-right",
    "margin-top",
    "max-height",
    "max-width",
    "min-height",
    "min-width",
    "overflow",
    "padding",
    "padding-bottom",
    "padding-left",
    "padding-right",
    "padding-top",
    "table-layout",
    "text-align",
    "text-decoration",
    "text-indent",
    "vertical-align",
    "white-space",
    "width",
]

_css_sanitizer = CSSSanitizer(allowed_css_properties=ALLOWED_CSS_PROPERTIES)


def sanitize_rich_text(value: Any) -> Any:
    """净化富文本 HTML；非字符串或空值原样返回。"""
    if not isinstance(value, str) or not value:
        return value
    return bleach.clean(
        value,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        protocols=ALLOWED_PROTOCOLS,
        css_sanitizer=_css_sanitizer,
        strip=True,
    )


def sanitize_markdown(value: Any) -> Any:
    """中和 Markdown 源码中的内联/块级 HTML，保留 Markdown 语法字符。

    Markdown 正文的渲染安全不能只依赖某一个客户端：移动端、第三方 API 消费方
    或未来渲染器若开启 HTML 透传，作者注入的标签会被执行。存储侧统一剥离标签
    （围栏代码块里的 ``<`` 会转义成实体，渲染后仍显示为原文），
    因此任何渲染器都拿不到可执行 HTML。
    """
    if not isinstance(value, str) or not value:
        return value
    # strip=False：标签转义为文本而不是连同内容删除——围栏代码块里的 HTML
    # 仍能作为示例展示，同时任何渲染器都不会把它当标签执行。
    return bleach.clean(value, tags=[], attributes={}, protocols=[], strip=False)
