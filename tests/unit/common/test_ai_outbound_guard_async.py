# -*- coding: utf-8 -*-
"""AsyncChatCompletionsClient 出站守卫单测：生产路径发送前严格校验、语义错误不重试。"""

import pytest

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


from integrations.sdk.ai.async_chat import AsyncChatCompletionsClient
from integrations.sdk.ai.chat import AiSdkError


def _credentials(base_url="http://10.9.8.7:11434/v1"):
    return {
        "base_url": base_url,
        "api_key": "sk-test",
        "model": "test-model",
        "timeout": 5,
        "max_retries": 0,
        "allowed_hosts": (),
    }


async def test_private_target_blocked_before_send():
    client = AsyncChatCompletionsClient(_credentials())
    with pytest.raises(AiSdkError) as exc:
        await client.chat([{"role": "user", "content": "hi"}])
    assert "outbound policy" in str(exc.value)


async def test_private_target_blocked_for_stream():
    client = AsyncChatCompletionsClient(_credentials())
    with pytest.raises(AiSdkError) as exc:
        async for _event in client.chat_stream([{"role": "user", "content": "hi"}]):
            pass
    assert "outbound policy" in str(exc.value)
