# -*- coding: utf-8 -*-
"""测试侧响应读取助手。

导出接口自 PERF-EXP-01 起返回 ``FileResponse``（流式落盘 + 分块下发），
它没有 ``.content``——直接用旧写法会抛 ``AttributeError``。统一经本模块读取，
既兼容二进制附件，也兼容普通 JSON/DRF 响应。
"""

from typing import Any


def read_body(response: Any) -> bytes:
    """取出响应正文：流式响应把分块拼起来，普通响应直接读 ``content``。"""
    streaming = getattr(response, "streaming_content", None)
    if streaming is not None:
        return b"".join(streaming)
    return response.content


def read_text(response: Any, encoding: str = "utf-8-sig") -> str:
    """按文本读取（导出 CSV 默认带 BOM，故缺省 utf-8-sig）。"""
    return read_body(response).decode(encoding)
