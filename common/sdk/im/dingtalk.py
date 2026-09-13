#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""钉钉工作通知客户端（ADR-019）：asyncsend_v2 文本消息。

凭据：appKey / appSecret / agentId。身份链路：OAuth 绑定存的是 unionId，
发送前经 `topapi/user/getbyunionid` 换 userid（缓存，避免逐条换算的频控压力）。
"""

from urllib.parse import urlencode

from common.utils import get_logger
from .base import BaseImClient, ImSdkError

logger = get_logger(__name__)

API_BASE = "https://oapi.dingtalk.com"


class DingTalkClient(BaseImClient):
    token_url = f"{API_BASE}/gettoken"
    token_ttl = 7200
    token_cache_prefix = "im_dingtalk_token_"
    userid_cache_prefix = "im_dingtalk_userid_"

    def __init__(self, credentials: dict, http_client=None):
        super().__init__(http_client=http_client)
        self.app_key = credentials.get("app_key") or ""
        self.app_secret = credentials.get("app_secret") or ""
        self.agent_id = credentials.get("agent_id") or ""

    def _check(self, payload, url):
        errcode = payload.get("errcode")
        if errcode not in (0, None):
            action = url.rsplit("/", 1)[-1].split("?")[0]
            raise ImSdkError(
                f"dingtalk rejected {action}: errcode={errcode} errmsg={payload.get('errmsg')}", code=errcode
            )
        return payload

    def _fetch_token(self, credentials: dict) -> str:
        payload = self._get_json(self.token_url, params={"appkey": self.app_key, "appsecret": self.app_secret})
        token = str(payload.get("access_token") or "")
        if not token:
            raise ImSdkError("dingtalk token response missing access_token")
        return token

    def _topapi_post(self, path: str, access_token: str, body: dict) -> dict:
        """钉钉 oapi 惯例：access_token 走 query，业务参数走 JSON 体。"""
        url = f"{API_BASE}{path}?{urlencode({'access_token': access_token})}"
        try:
            response = self._client().post(url, json=body, timeout=10)
            payload = response.json()
        except ImSdkError:
            raise
        except Exception as exc:
            raise ImSdkError(f"request failed: {exc}") from exc
        return self._check(payload if isinstance(payload, dict) else {}, path)

    def get_userid_by_unionid(self, union_id: str) -> str:
        """unionId → userid（缓存）：钉钉工作通知只认 userid。"""
        from django.core.cache import cache

        cache_key = f"{self.userid_cache_prefix}{union_id}"
        userid = cache.get(cache_key)
        if userid:
            return str(userid)
        payload = self._topapi_post("/topapi/user/getbyunionid", self._cached_token({}), {"unionid": union_id})
        userid = str((payload.get("result") or {}).get("userid") or "")
        if not userid:
            raise ImSdkError(f"dingtalk userid not found for unionid {union_id[:8]}***")
        cache.set(cache_key, userid, 7200 - 120)
        return userid

    def send_text(self, accounts, content) -> None:
        """accounts 为 userid 列表（后端已完成 unionId 换算）。"""
        if not accounts:
            return
        body = {
            "agent_id": self.agent_id,
            "userid_list": "|".join(str(a) for a in accounts),
            "msg": {"msgtype": "text", "text": {"content": content}},
        }
        payload = self._topapi_post("/topapi/message/corpconversation/asyncsend_v2", self._cached_token({}), body)
        logger.debug("dingtalk asyncsend task_id=%s", payload.get("task_id"))
