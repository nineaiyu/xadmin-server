#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""飞书 IM 消息客户端：im/v1/messages 文本消息。

凭据：appId / appSecret。OAuth 绑定的 subject 即 union_id，
以 receive_id_type=union_id 直发（逐条发送，单条失败不影响其余）。
"""

import json

from common.utils import get_logger

from .base import BaseImClient, ImSdkError

logger = get_logger(__name__)

API_BASE = "https://open.feishu.cn/open-apis"


class FeishuClient(BaseImClient):
    token_url = f"{API_BASE}/authen/v1/tenant_access_token/internal"
    token_ttl = 7200
    token_cache_prefix = "im_feishu_token_"

    def __init__(self, credentials: dict, http_client=None):
        super().__init__(credentials=credentials, http_client=http_client)
        self.app_id = credentials.get("app_id") or ""
        self.app_secret = credentials.get("app_secret") or ""

    def _check(self, payload, url):
        code = payload.get("code")
        if code not in (0, None):
            action = url.rsplit("/", 1)[-1].split("?")[0]
            raise ImSdkError(f"feishu rejected {action}: code={code} msg={payload.get('msg')}", code=code)
        return payload

    def _fetch_token(self, credentials: dict) -> str:
        # tenant_access_token/internal 不走 code 包裹，直接返回 {"tenant_access_token": ...}
        payload = self._post_json(self.token_url, {"app_id": self.app_id, "app_secret": self.app_secret})
        token = str(payload.get("tenant_access_token") or "")
        if not token:
            raise ImSdkError("feishu token response missing tenant_access_token")
        return token

    def send_text(self, accounts, content) -> None:
        """accounts 为 union_id 列表；逐条发送（im 批量接口有 200 上限且错误粒度粗）。"""
        receive_ids = [str(a) for a in accounts if a]
        if not receive_ids:
            return
        token = self._cached_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}
        for receive_id in receive_ids:
            try:
                self._post_json(
                    f"{API_BASE}/im/v1/messages",
                    params={"receive_id_type": "union_id"},
                    headers=headers,
                    body={"receive_id": receive_id, "msg_type": "text", "content": json.dumps({"text": content})},
                )
            except ImSdkError as exc:
                # 单用户失败（不在应用可见范围等）不影响其余收件人
                logger.warning("feishu send to %s*** failed: %s", receive_id[:8], exc)
