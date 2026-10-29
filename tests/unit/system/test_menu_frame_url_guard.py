# -*- coding: utf-8 -*-
"""菜单内嵌页地址守卫（同源相对路径）。

内嵌页（``meta.frame_url``）会被渲染进站内 Iframe：绝对地址与 ``javascript:`` /
``data:`` 会把外部页面塞进可信外壳（站内「正常页面」里骗凭据、借壳触发站内操作）。
写入侧只放行同源相对路径；API 侧另由 ``X-Frame-Options: DENY`` 兜住反向嵌帧。
"""

import pytest
from django.utils import translation
from rest_framework import serializers

from system.serializers.menu import MenuMetaSerializer


@pytest.fixture(autouse=True)
def _english_messages():
    with translation.override("en"):
        yield


def _validate(url):
    return MenuMetaSerializer().validate_frame_url(url)


class TestFrameUrlGuard:
    @pytest.mark.parametrize("url", ["", "   ", None, "/api-docs/swagger/", "/api/flower/"])
    def test_same_origin_paths_allowed(self, url):
        assert _validate(url) == (url or "").strip()

    @pytest.mark.parametrize(
        "url",
        [
            "https://evil.example.com/phish",
            "http://127.0.0.1:8896/api/identity/user/",
            "//evil.example.com/x",  # 协议相对：仍是外站
            "javascript:alert(document.cookie)",
            "data:text/html,<script>alert(1)</script>",
            "api/relative-without-slash",
            "/\\evil.example.com/x",  # 反斜杠会被浏览器归一为路径分隔符
            "/\t/evil.example.com/x",  # 控制字符
            "/%5cevil.example.com/x",  # 百分号编码的反斜杠
            "/%2f%2fevil.example.com/x",  # 编码后的协议相对地址
            "/%255cevil.example.com/x",  # 双重编码
        ],
    )
    def test_external_or_script_urls_rejected(self, url):
        with pytest.raises(serializers.ValidationError):
            _validate(url)
