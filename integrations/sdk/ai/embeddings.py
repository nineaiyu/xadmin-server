#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""OpenAI 兼容 embeddings 客户端（批量文本 → 向量；凭据由调用方注入）。

与 chat 客户端同源口径：``POST {base_url}/embeddings``，请求体 ``{"model", "input": [...]}``；
响应 ``{"data": [{"index", "embedding"}], "usage"}`` 按 ``index`` 重排后返回，保证与入参
顺序一致（供应商允许乱序返回）；条数/结构异常一律按协议错误抛出，不返回残缺结果。
未建立连接的网络异常与 5xx/429 按指数退避重试（``max_retries > 0`` 时），4xx 配置类
错误不重试；失败抛 ``AiSdkError``（与 chat 共用异常类型，调用方统一走可读转换）。
"""

import time

from common.utils import get_logger
from integrations.sdk.ai.chat import AiSdkError

logger = get_logger(__name__)

# 重试退避：0.5s 起指数退避，单次上限 2s（与 chat 客户端同口径）
_RETRY_BASE_DELAY = 0.5
_RETRY_MAX_DELAY = 2.0
#: 单次请求文本条数上限（调用方分批；超出视为调用错误，避免供应商侧超限截断）
MAX_BATCH_SIZE = 256


class EmbeddingClient:
    """批量文本向量化客户端（OpenAI 兼容 /embeddings）。

    与 ``ChatCompletionsClient`` 相同的凭据形态（base_url / api_key / model / timeout /
    max_retries），可与 chat 档案并存复用同一 base_url（同一网关同时提供两类端点）。
    """

    def __init__(self, credentials: dict, http_client=None):
        self.base_url = str(credentials.get("base_url") or "").rstrip("/")
        self.api_key = str(credentials.get("api_key") or "")
        self.model = str(credentials.get("model") or "")
        self.timeout = int(credentials.get("timeout") or 60)
        self.max_retries = max(0, int(credentials.get("max_retries") or 0))
        self.http = http_client
        # 最近一次成功的 token 用量（供应商 payload.usage 原样，缺省 None）：供记账观测
        self.last_usage: dict | None = None

    def _client(self):
        if self.http is None:
            import requests

            self.http = requests
        return self.http

    def _post(self, body: dict):
        """POST + 重试：网络异常与 5xx/429 指数退避；4xx 不重试。"""
        http = self._client()
        attempts = self.max_retries + 1
        response = None
        for attempt in range(attempts):
            try:
                response = http.post(
                    f"{self.base_url}/embeddings",
                    json=body,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=self.timeout,
                )
            except Exception as exc:
                if attempt + 1 < attempts:
                    logger.warning("ai embedding request failed (attempt %s/%s): %s", attempt + 1, attempts, exc)
                    time.sleep(min(_RETRY_BASE_DELAY * (2**attempt), _RETRY_MAX_DELAY))
                    continue
                logger.warning("ai embedding request failed: %s", exc)
                raise AiSdkError("Failed to contact the AI provider") from exc
            if (response.status_code >= 500 or response.status_code == 429) and attempt + 1 < attempts:
                logger.warning("ai embedding provider busy (%s), retrying", response.status_code)
                time.sleep(min(_RETRY_BASE_DELAY * (2**attempt), _RETRY_MAX_DELAY))
                continue
            return response
        return response

    def embed(self, texts: list) -> list:
        """批量向量化：返回与入参等长、顺序一致的向量列表（元素为 float 列表）。"""
        if not (self.base_url and self.api_key and self.model):
            raise AiSdkError("AI embedding client is not configured (base_url/api_key/model)")
        items = [str(text or "") for text in texts]
        if not items:
            return []
        if len(items) > MAX_BATCH_SIZE:
            raise AiSdkError("AI embedding batch is too large")
        response = self._post({"model": self.model, "input": items})
        if response.status_code != 200:
            logger.warning("ai embedding rejected: %s", response.text[:300])
            raise AiSdkError("The AI provider rejected the embedding request")
        try:
            payload = response.json()
        except Exception as exc:
            logger.warning("ai embedding invalid json: %s", exc)
            raise AiSdkError("The AI provider returned an invalid response") from exc
        if not isinstance(payload, dict):
            raise AiSdkError("The AI provider returned an invalid response")
        data = payload.get("data")
        if not isinstance(data, list) or len(data) != len(items):
            logger.warning("ai embedding count mismatch: expected %s", len(items))
            raise AiSdkError("The AI provider returned an unexpected embedding count")
        usage = payload.get("usage")
        self.last_usage = usage if isinstance(usage, dict) else None
        vectors: list[list[float]] = [[] for _ in items]
        for position, item in enumerate(data):
            if not isinstance(item, dict):
                raise AiSdkError("The AI provider returned an invalid response")
            raw = item.get("embedding")
            if not isinstance(raw, list) or not raw:
                raise AiSdkError("The AI provider returned an invalid response")
            try:
                vector = [float(value) for value in raw]
            except (TypeError, ValueError) as exc:
                raise AiSdkError("The AI provider returned an invalid response") from exc
            index = item.get("index")
            slot = index if isinstance(index, int) and 0 <= index < len(items) else position
            vectors[slot] = vector
        if any(not vector for vector in vectors):
            raise AiSdkError("The AI provider returned an invalid response")
        return vectors
