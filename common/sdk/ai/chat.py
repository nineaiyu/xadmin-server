#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""OpenAI 兼容 chat/completions 客户端（ADR-023）。"""

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
