#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""富文本 HTML 白名单净化（服务端为主，前端 DOMPurify 兜底）。

用于公告/站内信等 wangEditor 产出的 HTML 内容：入库前剥离 script/iframe、
事件属性（on*）与 javascript: 等危险协议，保留正常排版所需的标签与属性。
"""

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


def sanitize_rich_text(value):
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
