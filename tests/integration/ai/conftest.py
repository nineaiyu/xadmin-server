# -*- coding: utf-8 -*-
"""AI 集成测试公共桩：异步流式链路复用同步客户端的请求桩。

流式视图已切到 ``AsyncChatCompletionsClient``（事件循环内 await）；存量用例
仍通过替换同步 ``ChatCompletionsClient._request`` / ``chat_stream`` 注入假响应。
这里把异步 ``chat_stream`` 桥接到同一实例的同步 builder，测试桩无需逐处改造。
用例显式替换异步 ``chat_stream`` 时，后设置的 monkeypatch 会覆盖本桥接。
"""

import pytest


@pytest.fixture(autouse=True)
def _bridge_async_chat_stream(monkeypatch):
    from integrations.sdk.ai.async_chat import AsyncChatCompletionsClient

    async def bridge(client, messages, **kwargs):
        for item in client._builder.chat_stream(messages, **kwargs):
            yield item

    monkeypatch.setattr(AsyncChatCompletionsClient, "chat_stream", bridge)
