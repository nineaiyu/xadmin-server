#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""音频验证码的 Range 响应（替代停维的 django-ranged-response）。

仅实现单段 ``bytes=`` 语义：音频播放器（含 iOS Safari）按需分段拉取，
返回 206 + Content-Range；非法区间按 RFC 返回 416。响应关闭后清理临时 wav。
"""

import os
from typing import Any

from django.http import FileResponse, HttpResponse


def _cleanup(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _parse_range(raw: str, size: int) -> tuple[int, int] | None:
    if not raw.startswith("bytes=") or "," in raw:
        return None
    value = raw[len("bytes=") :].strip()
    start_text, _, end_text = value.partition("-")
    try:
        if start_text == "":
            length = int(end_text)
            if length <= 0:
                return None
            start = max(size - length, 0)
            return start, size - 1
        start = int(start_text)
        end = int(end_text) if end_text else size - 1
    except ValueError:
        return None
    if start < 0 or start >= size or end < start:
        return None
    return start, min(end, size - 1)


def audio_file_response(request: Any, path: str, filename: str) -> HttpResponse:
    size = os.path.getsize(path)
    raw_range = str(request.headers.get("Range") or "")
    if not raw_range:
        response: HttpResponse = FileResponse(open(path, "rb"), content_type="audio/wav")
        response["Accept-Ranges"] = "bytes"
        response._resource_closers.append(lambda: _cleanup(path))
    else:
        parsed = _parse_range(raw_range, size)
        if parsed is None:
            response = HttpResponse(status=416)
            response["Content-Range"] = f"bytes */{size}"
            _cleanup(path)
        else:
            start, end = parsed
            with open(path, "rb") as handle:
                handle.seek(start)
                data = handle.read(end - start + 1)
            response = HttpResponse(data, status=206, content_type="audio/wav")
            response["Content-Range"] = f"bytes {start}-{end}/{size}"
            response["Accept-Ranges"] = "bytes"
            _cleanup(path)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
