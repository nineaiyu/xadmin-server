#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""OpenAI 兼容 chat/completions **异步**客户端（httpx.AsyncClient，S7 根治②）。

与同步客户端（``common/sdk/ai/chat.py``）的分工：
- 同步 ``ChatCompletionsClient``：非流式链路（聊天室 / 动作草稿 / NL 查数 / 探测）
  与全部存量调用面，行为零变化；
- 异步 ``AsyncChatCompletionsClient``：**流式链路**专用——SSE 流在 ASGI 事件循环内
  直接 ``await`` 供应商增量，不再经 ``sync_to_async`` 逐帧占用视图线程
  （数百并发流不再受线程池上限约束，线程饥饿根治）。

复用口径：请求体装配与 tool_calls 规范化组合同步客户端实例（同一份实现，两处
行为永不漂移）；报文解析共用 ``parse_chat_message`` / ``raise_if_empty_answer``。
重试语义与同步版一致：max_retries 内对「未建立连接的网络异常」与「5xx/429 响应」
指数退避重试（asyncio.sleep）；流式仅在收到响应头前重试，已产出增量不重试。

注入契约：``http_client`` 与 httpx.AsyncClient 同构——``post(url, ...)`` 返回响应；
``stream("POST", url, ...)`` 返回**异步上下文管理器**（进入后可 ``aiter_lines``）。
测试用同构假客户端注入，无需真实网络。
"""

import asyncio
import json

from common.sdk.ai.chat import (
    AiSdkError,
    ChatCompletionsClient,
    parse_chat_message,
    raise_if_empty_answer,
)
from common.utils import get_logger

logger = get_logger(__name__)

# 重试退避：0.5s 起指数退避，单次上限 2s（与同步版同款）
_RETRY_BASE_DELAY = 0.5
_RETRY_MAX_DELAY = 2.0


def _backoff(attempt: int) -> float:
    return min(_RETRY_BASE_DELAY * (2**attempt), _RETRY_MAX_DELAY)


class AsyncChatCompletionsClient:
    """异步 chat/completions 客户端：chat / chat_tools / chat_stream（async 生成器）。

    凭据与采样参数由调用方注入（档案/Setting 读出的配置 dict），字段语义与同步版
    完全一致；``last_usage`` / ``last_reasoning`` / ``last_tool_calls`` 同名同义。
    """

    def __init__(self, credentials: dict, http_client=None):
        self._builder = ChatCompletionsClient(credentials)
        self.base_url = self._builder.base_url
        self.api_key = self._builder.api_key
        self.model = self._builder.model
        self.timeout = self._builder.timeout
        self.max_retries = self._builder.max_retries
        self.http = http_client
        self.last_usage: dict | None = None
        self.last_reasoning: str | None = None
        self.last_tool_calls: list = []

    # ------------------------------------------------------------------ 基础面

    @property
    def _url(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _require_config(self) -> None:
        if not (self.base_url and self.api_key and self.model):
            raise AiSdkError("AI client is not configured (base_url/api_key/model)")

    def _body(self, messages: list, stream: bool = False, **overrides) -> dict:
        return self._builder._body(messages, stream=stream, **overrides)

    def _normalize_tool_calls(self, raw) -> list:
        return self._builder._normalize_tool_calls(raw)

    async def _send_with_retry(self, send):
        """发送 + 重试：网络异常与 5xx/429 指数退避（``send`` 每次重建请求）。

        ``send`` 是零参异步函数——重试需要重新发起请求（流式响应体未消费即可弃）。
        """
        attempts = self.max_retries + 1
        response = None
        for attempt in range(attempts):
            try:
                response = await send()
            except Exception as exc:
                if attempt + 1 < attempts:
                    logger.warning("ai async chat request failed (attempt %s/%s): %s", attempt + 1, attempts, exc)
                    await asyncio.sleep(_backoff(attempt))
                    continue
                logger.warning("ai async chat request failed: %s", exc)
                raise AiSdkError("Failed to contact the AI provider") from exc
            if (response.status_code >= 500 or response.status_code == 429) and attempt + 1 < attempts:
                logger.warning("ai async chat provider busy (%s), retrying", response.status_code)
                await asyncio.sleep(_backoff(attempt))
                continue
            return response
        return response

    async def _post_once(self, url: str, body: dict):
        """单次非流式 POST（自带客户端生命周期；注入客户端时直接复用）。"""
        if self.http is not None:
            return await self.http.post(
                url, timeout=self.timeout, json=body, headers={"Authorization": f"Bearer {self.api_key}"}
            )
        import httpx

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            return await client.post(url, json=body, headers={"Authorization": f"Bearer {self.api_key}"})

    async def _open_stream(self, url: str, body: dict):
        """打开流式响应：返回 ``(closer, response)``。

        httpx 流式必须「打开者负责关闭」且客户端在响应体消费期间保持存活——
        此处以手动生命周期管理承接：``closer`` 为消费结束后的清理协程函数；
        注入客户端（测试桩）时 closer 为空操作（生命周期归注入方）。
        重试只发生在响应头到达前（响应体未消费即可整体弃置重开）。
        """
        import httpx

        headers = {"Authorization": f"Bearer {self.api_key}"}

        async def _open_with(client):
            cm = client.stream("POST", url, timeout=self.timeout, json=body, headers=headers)
            response = await cm.__aenter__()
            return cm, response

        async def _send():
            if self.http is not None:
                _cm, _response = await _open_with(self.http)
                return _response
            client = httpx.AsyncClient(timeout=self.timeout)
            try:
                cm, response = await _open_with(client)
            except Exception:
                await client.aclose()
                raise
            return _OwnedStream(client, cm, response)

        opened = await self._send_with_retry(_send)
        if self.http is not None:
            return (None, opened)
        return (opened.aclose_all, opened.response)

    # ------------------------------------------------------------------ 非流式

    async def chat(self, messages: list, **overrides) -> str:
        """多轮消息 → 助手回复文本（异步）。错误口径与同步版一致。"""
        self._require_config()
        body = self._body(messages, **overrides)
        response = await self._send_with_retry(lambda: self._post_once(self._url, body))
        try:
            payload = response.json()
        except Exception as exc:
            logger.warning("ai async chat invalid json: %s", exc)
            raise AiSdkError("The AI provider returned an invalid response") from exc
        if not isinstance(payload, dict):
            raise AiSdkError("The AI provider returned an invalid response")
        content, reasoning, usage, _raw_calls = parse_chat_message(payload)
        self.last_reasoning = reasoning
        self.last_usage = usage
        raise_if_empty_answer(content, reasoning, with_tools=False, raw=payload)
        return str(content)

    async def chat_tools(self, messages: list, tools: list, tool_choice: str = "auto", **overrides) -> dict:
        """原生 function calling（异步）。返回 ``{content, tool_calls, usage, reasoning}``。"""
        self._require_config()
        body = self._body(messages, tools=tools, tool_choice=tool_choice, **overrides)
        response = await self._send_with_retry(lambda: self._post_once(self._url, body))
        try:
            payload = response.json()
        except Exception as exc:
            logger.warning("ai async chat tools invalid json: %s", exc)
            raise AiSdkError("The AI provider returned an invalid response") from exc
        if not isinstance(payload, dict):
            raise AiSdkError("The AI provider returned an invalid response")
        content, reasoning, usage, tool_calls_raw = parse_chat_message(payload)
        self.last_reasoning = reasoning
        self.last_usage = usage
        self.last_tool_calls = self._normalize_tool_calls(tool_calls_raw)
        if not content and not self.last_tool_calls:
            raise_if_empty_answer(content, reasoning, with_tools=True, raw=payload)
        return {
            "content": str(content or ""),
            "tool_calls": self.last_tool_calls,
            "usage": self.last_usage,
            "reasoning": self.last_reasoning,
        }

    # ------------------------------------------------------------------ 流式

    async def chat_stream(self, messages: list, **overrides):
        """流式多轮（async 生成器）：产出 ``{"type": "reasoning"|"content", "text": ...}``。

        事件语义与同步版一致（增量在事件循环内 await，不占视图线程）；
        「只有思考、没有回答」不算空回答（由调用方决定展示口径）；已产出增量后
        流中断抛 AiSdkError，由调用方按部分回答处理。
        """
        self._require_config()
        body = self._body(messages, stream=True, **overrides)
        closer, response = await self._open_stream(self._url, body)
        if getattr(response, "status_code", 0) != 200:
            detail = ""
            try:
                detail = response.text[:300]
            except Exception:  # noqa: BLE001 响应体不可读时按无细节处理
                pass
            logger.warning("ai async chat stream rejected: %s", detail)
            raise AiSdkError("The AI provider rejected the streaming request")

        produced = False
        produced_reasoning = False
        try:
            # SSE 规范固定 UTF-8；aiter_lines 逐行产出（与同步版 iter_lines 同口径）
            async for line in response.aiter_lines():
                if not line:
                    continue
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="replace")
                data = line[5:].strip() if line.startswith("data:") else ""
                if not data or data == "[DONE]":
                    if data == "[DONE]":
                        break
                    continue
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                choices = chunk.get("choices") or []
                delta = ((choices[0] or {}).get("delta") or {}) if choices else {}
                reasoning = delta.get("reasoning_content")
                if reasoning:
                    produced_reasoning = True
                    yield {"type": "reasoning", "text": str(reasoning)}
                content = delta.get("content")
                if content:
                    produced = True
                    yield {"type": "content", "text": str(content)}
        except AiSdkError:
            raise
        except Exception as exc:
            logger.warning("ai async chat stream interrupted: %s", exc)
            raise AiSdkError("The AI provider stream was interrupted") from exc
        finally:
            if closer is not None:
                await closer()
        if not produced and not produced_reasoning:
            logger.warning("ai async chat stream empty answer")
            raise AiSdkError("The AI provider returned an empty answer")


class _OwnedStream:
    """自建 httpx 客户端的流式响应持有者：消费结束后关闭响应与客户端。"""

    def __init__(self, client, cm, response):
        self._client = client
        self._cm = cm
        self.response = response

    @property
    def status_code(self) -> int:
        """重试判定需要读响应头状态码（与注入客户端分支的裸 Response 同形）。"""
        return self._response.status_code

    @property
    def response(self):
        return self._response

    @response.setter
    def response(self, value):
        self._response = value

    @property
    def aclose_all(self):
        async def _close():
            try:
                await self._cm.__aexit__(None, None, None)
            finally:
                await self._client.aclose()

        return _close
