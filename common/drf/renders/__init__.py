import json

from rest_framework import renderers

from .csv import *
from .excel import *

#: 线程池推进同步生成器的结束哨兵（StopIteration 不能穿越 await 边界，PEP 479）
_STREAM_END = object()


def sse_frame(event) -> str:
    """单帧序列化（`event:` + `data:` + 空行）。

    JSON 序列化对 UUID/datetime 宽松处理（default=str），与 message_payload 的
    JSON 广播口径一致；每个事件独立成帧，客户端按空行切分。
    """
    return f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False, default=str)}\n\n"


def sse_frames(events):
    """事件字典序列 → 同步帧生成器（同步消费场景/测试；HTTP 流式勿直接使用，见下）。"""
    for event in events:
        yield sse_frame(event)


def _next_or_end(iterator):
    """在线程池内推进一步同步生成器：结束返回哨兵（线程内捕获 StopIteration）。"""
    try:
        return next(iterator)
    except StopIteration:
        return _STREAM_END


async def async_sse_frames(events):
    """SSE 帧异步迭代器（ASGI 实时逐帧 flush 的唯一正确形态）。

    **"一次性输出"根因**：StreamingHttpResponse 的 streaming_content 若为
    **同步生成器**，Django 在 ASGI 下（django/http/response.py 的 ``__aiter__``
    兜底分支）会退化为 ``sync_to_async(list)(self.streaming_content)``——先把
    整个生成器（含全部 LLM 阻塞调用）跑完转成 list，再一次性 yield 全部字节，
    SSE 完全失去实时性（实测 body 在模型完成后一次性到达，逐帧时间戳相同）。

    这里用 ``sync_to_async``（**thread_sensitive 默认 True**）逐帧推进同步生成器：
    - 每帧独立 yield，Daphne 收到即 flush（客户端逐帧可见）；
    - 推进发生在「视图执行所在的同一线程」（asgiref 的 thread-sensitive 执行器），
      保持 Django 线程本地数据库连接与请求事务语义（换 asyncio.to_thread 会在
      新线程拿新连接——测试库表锁、生产事务隔离丢失）；
    - 阻塞粒度与既有实现一致（修复前 sync_to_async(list) 同样占用该线程整段跑完），
      流内 LLM 阻塞调用不改变现状。
    """
    from asgiref.sync import sync_to_async

    iterator = iter(events)
    while True:
        event = await sync_to_async(_next_or_end)(iterator)
        if event is _STREAM_END:
            return
        yield sse_frame(event)


def sse_response(events):
    """SSE 响应装配（HTTP 流式的唯一入口）：异步帧迭代器逐帧 flush + 关闭代理缓冲。

    事件源为同步生成器（``event`` 字典序列，见 ``async_sse_frames`` 的「一次性输出」
    根因说明）；``Cache-Control`` / ``X-Accel-Buffering`` 两个响应头在所有流式端点
    保持一致（缺任一项时 nginx 会缓冲/缓存 SSE，表现为前端整段一次性出现）。
    """
    from django.http import StreamingHttpResponse

    response = StreamingHttpResponse(async_sse_frames(events), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


class SseRendererMixin:
    """SSE 端点的渲染器协商（ViewSet 专用：``@renderer_classes`` 只对函数视图生效）。

    用法：ViewSet 声明 ``sse_actions``（需 SSE 协商的 action 名），命中时在渲染器列表
    追加 ``EventStreamRenderer``，使浏览器 ``Accept: text/event-stream`` 能协商成功
    （否则一律 406；APIClient 默认 ``Accept: */*`` 会命中 JSONRenderer，单测发现不了）。
    """

    #: 需要 SSE 协商的 action 名（``url_path`` 的驼峰形式）
    sse_actions: tuple = ()

    def get_renderers(self):
        if getattr(self, "action", None) in self.sse_actions:
            return [renderers.JSONRenderer(), EventStreamRenderer()]
        return super().get_renderers()


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
