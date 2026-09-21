#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""OpenAI 兼容 chat/completions 客户端（凭据与采样参数由调用方注入）。"""

import json
import time

from common.utils import get_logger

logger = get_logger(__name__)

# SDK 兜底采样温度（credentials 未提供 temperature 时生效）
DEFAULT_TEMPERATURE = 0.2
# 重试退避：0.5s 起指数退避，单次上限 2s
_RETRY_BASE_DELAY = 0.5
_RETRY_MAX_DELAY = 2.0


class AiSdkError(Exception):
    """LLM 调用失败（网络/协议/供应商拒绝）。message 面向日志与可读转换。"""


class ChatCompletionsClient:
    """凭据与采样参数由调用方注入（档案/Setting 读出的配置 dict）。

    请求体参数全集：model / messages / temperature / max_tokens / top_p /
    frequency_penalty / presence_penalty / stop / seed（流式再 + stream）。
    None 或缺省的参数不下发（走供应商默认；temperature 例外，兜底 DEFAULT_TEMPERATURE）。
    max_retries > 0 时对「未建立连接的网络异常」与「5xx/429 响应」重试（指数退避）；
    流式仅在收到响应前重试——已产出增量不重试，由调用方按部分回答处理。
    """

    def __init__(self, credentials: dict, http_client=None):
        self.base_url = str(credentials.get("base_url") or "").rstrip("/")
        self.api_key = str(credentials.get("api_key") or "")
        self.model = str(credentials.get("model") or "")
        self.timeout = int(credentials.get("timeout") or 60)
        self.max_retries = max(0, int(credentials.get("max_retries") or 0))
        self.temperature = credentials.get("temperature")
        self.max_tokens = credentials.get("max_tokens")
        self.top_p = credentials.get("top_p")
        self.frequency_penalty = credentials.get("frequency_penalty")
        self.presence_penalty = credentials.get("presence_penalty")
        self.seed = credentials.get("seed")
        stop = credentials.get("stop")
        if isinstance(stop, str):
            stop = stop.split(",")
        self.stop = [str(item).strip() for item in (stop or []) if str(item).strip()] if stop else []
        self.http = http_client
        # 最近一次成功 chat() 的 token 用量（供应商 payload.usage 原样，缺省 None）：
        # 供调用方写审计（成本维度观测），不改变 chat() 的返回契约
        self.last_usage = None
        # 最近一次 chat() 的思考内容（reasoning_content，缺省 None）：用于「只有思考
        # 没有回答」的错误区分（见 chat() 的空回答判定）；流式场景由 chat_stream 逐段产出
        self.last_reasoning = None

    def _client(self):
        if self.http is None:
            import requests

            self.http = requests
        return self.http

    def _body(self, messages: list, stream: bool = False, **overrides) -> dict:
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": DEFAULT_TEMPERATURE if self.temperature is None else self.temperature,
        }
        if self.max_tokens:
            body["max_tokens"] = int(self.max_tokens)
        if self.top_p is not None:
            body["top_p"] = self.top_p
        if self.frequency_penalty is not None:
            body["frequency_penalty"] = self.frequency_penalty
        if self.presence_penalty is not None:
            body["presence_penalty"] = self.presence_penalty
        if self.seed is not None:
            body["seed"] = int(self.seed)
        if self.stop:
            body["stop"] = self.stop
        if stream:
            body["stream"] = True
        for key, value in overrides.items():
            if value is not None:
                body[key] = value
        return body

    def _post(self, url: str, body: dict, stream: bool = False):
        """POST + 重试：网络异常与 5xx/429 指数退避重试；4xx 配置类错误不重试。"""
        http = self._client()
        attempts = self.max_retries + 1
        response = None
        for attempt in range(attempts):
            kwargs = {
                "json": body,
                "headers": {"Authorization": f"Bearer {self.api_key}"},
                "timeout": self.timeout,
            }
            if stream:
                kwargs["stream"] = True
            try:
                response = http.post(url, **kwargs)
            except Exception as exc:
                if attempt + 1 < attempts:
                    logger.warning("ai chat request failed (attempt %s/%s): %s", attempt + 1, attempts, exc)
                    time.sleep(min(_RETRY_BASE_DELAY * (2**attempt), _RETRY_MAX_DELAY))
                    continue
                logger.warning("ai chat request failed: %s", exc)
                raise AiSdkError("Failed to contact the AI provider") from exc
            if (response.status_code >= 500 or response.status_code == 429) and attempt + 1 < attempts:
                logger.warning("ai chat provider busy (%s), retrying", response.status_code)
                time.sleep(min(_RETRY_BASE_DELAY * (2**attempt), _RETRY_MAX_DELAY))
                continue
            return response
        return response

    def chat(self, messages: list, **overrides) -> str:
        """多轮消息 → 助手回复文本。失败抛 AiSdkError（可读、不含原始报文）。

        overrides：显式覆盖请求体参数（如 temperature=0.7），None 值忽略。
        思考型模型（reasoning_content）的思考内容采集到 ``last_reasoning``（供展示）；
        只有思考没有最终回答时报错文案会区分说明（便于前端给出可操作提示）。
        """
        if not (self.base_url and self.api_key and self.model):
            raise AiSdkError("AI client is not configured (base_url/api_key/model)")
        url = f"{self.base_url}/chat/completions"
        response = self._post(url, self._body(messages, **overrides))
        try:
            payload = response.json()
        except Exception as exc:
            logger.warning("ai chat invalid json: %s", exc)
            raise AiSdkError("The AI provider returned an invalid response") from exc
        if not isinstance(payload, dict):
            raise AiSdkError("The AI provider returned an invalid response")
        choices = payload.get("choices") or []
        message = ((choices[0] or {}).get("message") or {}) if choices else {}
        content = message.get("content")
        reasoning = message.get("reasoning_content")
        self.last_reasoning = str(reasoning) if reasoning else None
        usage = payload.get("usage")
        self.last_usage = usage if isinstance(usage, dict) else None
        if not content:
            logger.warning("ai chat rejected: %s", str(payload)[:300])
            if self.last_reasoning:
                raise AiSdkError("The AI provider returned only reasoning content without a final answer")
            raise AiSdkError("The AI provider returned an empty answer")
        return str(content)

    def chat_stream(self, messages: list, **overrides):
        """流式多轮：产出结构化增量事件（OpenAI `stream=true` SSE 兼容）。

        产出 ``{"type": "reasoning"|"content", "text": <增量>}``：
        - ``reasoning``：思考型模型的思考过程增量（`delta.reasoning_content`），
          供前端「思考过程」面板实时展示；
        - ``content``：正式回答增量（`delta.content`）。

        与 ``chat()`` 同源凭据与错误口径：连接/协议/空回答失败抛 AiSdkError；
        已经产出过增量后再失败（流中断）同样抛错，由调用方按部分回答处理；
        「只有思考、没有回答」不算空回答（已有 reasoning 增量），由调用方决定展示口径。
        """
        if not (self.base_url and self.api_key and self.model):
            raise AiSdkError("AI client is not configured (base_url/api_key/model)")
        url = f"{self.base_url}/chat/completions"
        response = self._post(url, self._body(messages, stream=True, **overrides), stream=True)
        if response.status_code != 200:
            detail = response.text[:300]
            logger.warning("ai chat stream rejected: %s", detail)
            raise AiSdkError("The AI provider rejected the streaming request")

        produced = False
        produced_reasoning = False
        try:
            # 不依赖 requests 的 decode_unicode：SSE 响应头常见 `text/event-stream`
            # 不带 charset，requests 会按 text/* 把编码推断成 ISO-8859-1，导致中文
            # 增量全部 mojibake（数据库 → æ°æ®åº）；SSE 规范固定 UTF-8，这里自行解码。
            # 兼容测试桩可能返回 str 行（预解码），bytes 才需要 decode。
            for line in response.iter_lines():
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
            logger.warning("ai chat stream interrupted: %s", exc)
            raise AiSdkError("The AI provider stream was interrupted") from exc
        if not produced and not produced_reasoning:
            logger.warning("ai chat stream empty answer")
            raise AiSdkError("The AI provider returned an empty answer")
