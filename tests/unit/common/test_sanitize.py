#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""富文本净化白名单与 XSS 样本回归。"""

import pytest

from common.utils.sanitize import sanitize_rich_text


@pytest.mark.parametrize(
    "raw,expected",
    [
        # 正常排版内容原样保留
        ('<p style="text-align: center;"><b>hi</b></p>', '<p style="text-align: center;"><b>hi</b></p>'),
        ('<a href="https://example.com" title="t">x</a>', '<a href="https://example.com" title="t">x</a>'),
        ('<img src="/media/a.png" alt="a">', '<img src="/media/a.png" alt="a">'),
    ],
)
def test_safe_html_preserved(raw, expected):
    assert sanitize_rich_text(raw) == expected


@pytest.mark.parametrize(
    "raw,forbidden",
    [
        ('<p onclick="steal()">x</p>', "onclick"),
        ("<script>alert(1)</script>hello", "<script"),
        ("<img src=x onerror=alert(1)>", "onerror"),
        ('<iframe src="https://evil.com"></iframe>', "<iframe"),
        ('<a href="javascript:alert(1)">x</a>', "javascript:"),
        ("<svg onload=alert(1)>x</svg>", "<svg"),
    ],
)
def test_xss_payloads_stripped(raw, forbidden):
    cleaned = sanitize_rich_text(raw)
    assert forbidden not in cleaned.lower()


def test_style_attr_css_sanitized():
    # 允许的属性保留，危险 CSS（url/expression）被剥离
    cleaned = sanitize_rich_text('<p style="color:red;background:url(evil)">x</p>')
    assert "color" in cleaned
    assert "url(" not in cleaned


def test_empty_and_non_string_passthrough():
    assert sanitize_rich_text("") == ""
    assert sanitize_rich_text(None) is None
    assert sanitize_rich_text(123) == 123


def test_notice_serializer_message_sanitized():
    """公告/站内信写路径（NoticeMessageSerializer）入库前净化。"""
    from notifications.serializers.message import NoticeMessageSerializer

    serializer = NoticeMessageSerializer()
    cleaned = serializer.validate_message('<p onclick="x()">a<script>b</script></p>')
    # bleach strip 语义：剥离危险标签与属性，保留其中的纯文本
    assert "onclick" not in cleaned
    assert "<script" not in cleaned
    assert "a" in cleaned and "b" in cleaned
