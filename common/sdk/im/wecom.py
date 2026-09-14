#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""企业微信应用消息客户端：message/send 文本消息。

凭据：corpId / corpSecret / agentId。OAuth 绑定的 subject 即 userid，直发。
"""

from common.utils import get_logger

from .base import BaseImClient, ImSdkError

logger = get_logger(__name__)

API_BASE = "https://qyapi.weixin.qq.com/cgi-bin"
# 单次 message/send 的 touser 上限（官方 1000，取保守值分批）
SEND_BATCH_SIZE = 900


class WeComClient(BaseImClient):
    token_url = f"{API_BASE}/gettoken"
    token_ttl = 7200
    token_cache_prefix = "im_wecom_token_"

    def __init__(self, credentials: dict, http_client=None):
        super().__init__(credentials=credentials, http_client=http_client)
        self.corp_id = credentials.get("corp_id") or ""
        self.corp_secret = credentials.get("corp_secret") or ""
        self.agent_id = credentials.get("agent_id") or ""

    def _check(self, payload, url):
        errcode = payload.get("errcode")
        if errcode not in (0, None):
            action = url.rsplit("/", 1)[-1].split("?")[0]
            raise ImSdkError(f"wecom rejected {action}: errcode={errcode} errmsg={payload.get('errmsg')}", code=errcode)
        return payload

    def _fetch_token(self, credentials: dict) -> str:
        payload = self._get_json(self.token_url, params={"corpid": self.corp_id, "corpsecret": self.corp_secret})
        token = str(payload.get("access_token") or "")
        if not token:
            raise ImSdkError("wecom token response missing access_token")
        return token

    def _send_batch(self, token: str, batch: list, content: str) -> dict:
        try:
            response = self._client().post(
                f"{API_BASE}/message/send",
                params={"access_token": token},
                json={
                    "touser": "|".join(batch),
                    "msgtype": "text",
                    "agentid": self.agent_id,
                    "text": {"content": content},
                },
                timeout=10,
            )
            payload = response.json()
        except ImSdkError:
            raise
        except Exception as exc:
            raise ImSdkError(f"request failed: {exc}") from exc
        return self._check(payload if isinstance(payload, dict) else {}, "message/send")

    def send_text(self, accounts, content) -> None:
        """accounts 为 userid 列表；touser 以 | 连接，分批发送。"""
        userids = [str(a) for a in accounts if a]
        if not userids:
            return
        token = self._cached_token()
        for start in range(0, len(userids), SEND_BATCH_SIZE):
            batch = userids[start : start + SEND_BATCH_SIZE]
            payload = self._send_batch(token, batch, content)
            logger.debug("wecom send batch=%s resp=%s", len(batch), payload)
