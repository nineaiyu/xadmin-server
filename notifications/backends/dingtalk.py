#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""钉钉通知渠道：工作通知 asyncsend_v2。

可达性两层：渠道级 = 开关 + 应用三元组齐全（缺 agentId 视为未配置）；
用户级 = 用户有 dingtalk flavor 的 OAuth 绑定（unionId → userid 发送前换算）。
"""

from django.conf import settings

from common.sdk.im import dingtalk as dingtalk_sdk
from common.utils import get_logger

from .im_base import ImBindingBackend

logger = get_logger(__name__)


class DingTalk(ImBindingBackend):
    flavor = "dingtalk"
    is_enable_field_in_settings = "DINGTALK_ENABLED"

    @classmethod
    def get_credentials(cls) -> dict:
        return {
            "app_key": getattr(settings, "DINGTALK_APP_KEY", ""),
            "app_secret": getattr(settings, "DINGTALK_APP_SECRET", ""),
            "agent_id": getattr(settings, "DINGTALK_AGENT_ID", ""),
        }

    @classmethod
    def is_enable(cls):
        if not super().is_enable():
            return False
        credentials = cls.get_credentials()
        return all(credentials.get(key) for key in ("app_key", "app_secret", "agent_id"))

    def send_msg(self, users, message, subject="", **kwargs):
        if not self.is_enable():
            logger.warning("DingTalk notify is not configured, skip dingtalk channel")
            return
        accounts, __, __ = self.get_accounts(users)
        if not accounts:
            return
        client = dingtalk_sdk.DingTalkClient(self.get_credentials())
        userids = []
        for union_id, __user in accounts:
            try:
                userids.append(client.get_userid_by_unionid(union_id))
            except dingtalk_sdk.ImSdkError as exc:
                # 单用户换算失败（不在企业内等）不影响其余收件人
                logger.warning("dingtalk unionid resolve failed: %s", exc)
        content = f"{subject}\n{message}" if subject else message
        return client.send_text(userids, content)


backend = DingTalk
