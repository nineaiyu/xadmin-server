import json

from rest_framework import renderers

from .csv import *
from .excel import *


class PassthroughRenderer(renderers.BaseRenderer):
    """
    Return data as-is. View should supply a Response.
    """

    media_type = "application/octet-stream"
    format = ""

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data


class EventStreamRenderer(renderers.BaseRenderer):
    """SSE 内容协商渲染器（text/event-stream）。

    浏览器 fetch 以 ``Accept: text/event-stream`` 请求流式端点时，DRF 必须能
    协商到该 media type 的渲染器，否则一律 406（APIClient 默认 Accept: */*
    会命中 JSONRenderer，单测发现不了这个问题——AI 流式链路曾在浏览器侧整体不可用）。

    流式端点实际返回 StreamingHttpResponse（DRF 不做二次渲染），本渲染器只在
    「协商兜底 + 流式端点的门禁类 JSON 错误响应」时被用到：非流式数据按 JSON
    序列化，保证「门禁错误头前返回 JSON 1001」的既有契约不变。
    """

    media_type = "text/event-stream"
    format = "event-stream"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        if data is None:
            return b""
        if isinstance(data, (bytes, bytearray)):
            return bytes(data)
        if isinstance(data, str):
            return data
        return json.dumps(data, ensure_ascii=False).encode("utf-8")
