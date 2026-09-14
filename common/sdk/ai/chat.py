#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""OpenAI 兼容 chat/completions 客户端。"""

import json

from common.utils import get_logger

logger = get_logger(__name__)


class AiSdkError(Exception):
    """LLM 调用失败（网络/协议/供应商拒绝）。message 面向日志与可读转换。"""


class ChatCompletionsClient:
    """凭据由调用方注入（settings 读出的配置 dict）。"""

    def __init__(self, credentials: dict, http_client=None):
        self.base_url = str(credentials.get("base_url") or "").rstrip("/")
        self.api_key = str(credentials.get("api_key") or "")
        self.model = str(credentials.get("model") or "")
        self.timeout = int(credentials.get("timeout") or 60)
        self.http = http_client

    def _client(self):
        if self.http is None:
            import requests

            self.http = requests
        return self.http

    def chat(self, messages: list, temperature: float = 0.2) -> str:
        """多轮消息 → 助手回复文本。失败抛 AiSdkError（可读、不含原始报文）。"""
        if not (self.base_url and self.api_key and self.model):
            raise AiSdkError("AI client is not configured (base_url/api_key/model)")
        url = f"{self.base_url}/chat/completions"
        try:
            response = self._client().post(
                url,
                json={"model": self.model, "messages": messages, "temperature": temperature},
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout,
            )
            payload = response.json()
        except Exception as exc:
            logger.warning("ai chat request failed: %s", exc)
            raise AiSdkError("Failed to contact the AI provider") from exc
        if not isinstance(payload, dict):
            raise AiSdkError("The AI provider returned an invalid response")
        choices = payload.get("choices") or []
        content = ((choices[0] or {}).get("message") or {}).get("content") if choices else None
        if not content:
            logger.warning("ai chat rejected: %s", str(payload)[:300])
            raise AiSdkError("The AI provider returned an empty answer")
        return str(content)

    def chat_stream(self, messages: list, temperature: float = 0.2):
        """流式多轮：逐段产出文本增量（OpenAI `stream=true` SSE 兼容，二期）。

        与 ``chat()`` 同源凭据与错误口径：连接/协议/空回答失败抛 AiSdkError；
        已经产出过增量后再失败（流中断）同样抛错，由调用方决定保留部分回答还是降级。
        """
        if not (self.base_url and self.api_key and self.model):
            raise AiSdkError("AI client is not configured (base_url/api_key/model)")
        url = f"{self.base_url}/chat/completions"
        try:
            response = self._client().post(
                url,
                json={"model": self.model, "messages": messages, "temperature": temperature, "stream": True},
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout,
                stream=True,
            )
        except Exception as exc:
            logger.warning("ai chat stream request failed: %s", exc)
            raise AiSdkError("Failed to contact the AI provider") from exc
        if response.status_code != 200:
            detail = response.text[:300]
            logger.warning("ai chat stream rejected: %s", detail)
            raise AiSdkError("The AI provider rejected the streaming request")

        produced = False
        try:
            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    continue
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
                delta = ((choices[0] or {}).get("delta") or {}).get("content") if choices else None
                if delta:
                    produced = True
                    yield str(delta)
        except AiSdkError:
            raise
        except Exception as exc:
            logger.warning("ai chat stream interrupted: %s", exc)
            raise AiSdkError("The AI provider stream was interrupted") from exc
        if not produced:
            logger.warning("ai chat stream empty answer")
            raise AiSdkError("The AI provider returned an empty answer")
