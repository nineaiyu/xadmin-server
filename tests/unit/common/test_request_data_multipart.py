# -*- coding: utf-8 -*-
"""multipart 请求的审计字段（get_request_data）：字段名清单 + 不破坏 DRF 解析链。

背景：旧实现对 multipart 一律返回哨兵字符串 "multipart/form-data"，操作日志里
看不到「提交了哪些字段」。改为记录字段名清单，但有两条硬约束必须守住：

1. 不能触发 ``request.POST``（会耗尽请求流，DRF 只能退回 Django 的 POST/FILES，
   绕过本仓 AxiosMultiPartParser 的点号键展开）；
2. 大文件上传不能整包进内存（超过阈值只留标记、不读正文）。
"""

import pytest
from django.test import RequestFactory

from common.drf.parsers.axios_form_data import AxiosMultiPartParser
from common.utils.request import MULTIPART_FIELD_PARSE_LIMIT, get_request_data

BOUNDARY = "XADMINBOUNDARY"


def _multipart_body(fields: list) -> bytes:
    """构造 multipart 正文：fields = [(name, filename|None, value)]。"""
    chunks = []
    for name, filename, value in fields:
        disposition = f'Content-Disposition: form-data; name="{name}"'
        if filename:
            disposition += f'; filename="{filename}"'
        chunk = f"--{BOUNDARY}\r\n{disposition}\r\n"
        if filename:
            chunk += "Content-Type: application/octet-stream\r\n"
        chunk += f"\r\n{value}\r\n"
        chunks.append(chunk)
    chunks.append(f"--{BOUNDARY}--\r\n")
    return "".join(chunks).encode("utf-8")


def _request(body: bytes, path="/api/demo/book/1/upload"):
    return RequestFactory().post(path, data=body, content_type=f"multipart/form-data; boundary={BOUNDARY}")


class TestMultipartFieldNames:
    def test_small_multipart_records_field_names_only(self):
        """只记字段名（含点号键原文），不落任何字段值。"""
        body = _multipart_body([("name", None, "书"), ("cover.file", "a.png", "PNG")])
        request = _request(body)
        data = get_request_data(request)
        assert data == {"_multipart_fields": ["cover.file", "name"]}
        assert "书" not in str(data)

    def test_oversized_multipart_skips_body(self):
        """超过阈值的请求不读正文（大文件上传不得整包进内存），只留标记。"""
        body = _multipart_body([("file", "big.bin", "x" * (MULTIPART_FIELD_PARSE_LIMIT + 1024))])
        request = _request(body)
        data = get_request_data(request)
        assert data == {"_multipart_fields": [], "_multipart_body_skipped": True}
        assert request._read_started is False, "超限请求不应触发正文读取"


class TestDrfParsingStillWorks:
    """读正文之后的 DRF 解析链：点号键展开仍在（回归护栏）。"""

    def _drf_request(self, request):
        from rest_framework.request import Request

        return Request(request, parsers=[AxiosMultiPartParser()])

    def test_dotted_keys_expanded_after_middleware_read(self):
        body = _multipart_body([("name", None, "书"), ("detail.is_active", None, "true"), ("file", "a.png", "PNG")])
        request = _request(body)
        get_request_data(request)  # 中间件先读（等价 ApiLoggingMiddleware.__handle_request）

        drf_request = self._drf_request(request)
        # 点号键必须被 AxiosMultiPartParser 展开（未被 Django POST/FILES 旁路）
        assert drf_request.data["name"] == "书"
        assert drf_request.data["detail"] == {"is_active": "true"}
        assert drf_request.FILES["file"].read() == b"PNG"

    def test_dotted_keys_expanded_when_body_not_read(self):
        """未读正文（超限分支）同样正常解析。"""
        body = _multipart_body([("detail.is_active", None, "true")])
        request = _request(body)
        get_request_data(request)

        assert self._drf_request(request).data["detail"] == {"is_active": "true"}


def test_plain_request_path_unchanged():
    """非 multipart 路径行为不变（JSON 正文解析）。"""
    request = RequestFactory().post("/api/x", data={"a": 1}, content_type="application/json")
    assert get_request_data(request) == {"a": 1}


@pytest.mark.parametrize("content_length", ["", "abc"])
def test_malformed_content_length_does_not_crash(content_length):
    request = _request(_multipart_body([("name", None, "x")]))
    request.META["CONTENT_LENGTH"] = content_length
    assert get_request_data(request) == {"_multipart_fields": [], "_multipart_body_skipped": True}
