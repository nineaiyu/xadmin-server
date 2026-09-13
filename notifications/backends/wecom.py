#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""企业微信通知渠道（ADR-019）：应用消息 message/send。

可达性两层：渠道级 = 开关 + 应用三元组齐全；用户级 = 用户有 wecom flavor
的 OAuth 绑定（subject 即 userid，直发）。
"""

from django.conf import settings

from common.sdk.im import wecom as wecom_sdk
from common.utils import get_logger
from .im_base import ImBindingBackend

logger = get_logger(__name__)


class WeCom(ImBindingBackend):
    flavor = "wecom"
    is_enable_field_in_settings = "WECOM_ENABLED"

    @classmethod
    def get_credentials(cls) -> dict:
        return {
            "corp_id": getattr(settings, "WECOM_CORP_ID", ""),
            "corp_secret": getattr(settings, "WECOM_CORP_SECRET", ""),
            "agent_id": getattr(settings, "WECOM_AGENT_ID", ""),
        }

    @classmethod
    def is_enable(cls):
        if not super().is_enable():
            return False
        credentials = cls.get_credentials()
        return all(credentials.get(key) for key in ("corp_id", "corp_secret", "agent_id"))

    def send_msg(self, users, message, subject="", **kwargs):
        if not self.is_enable():
            logger.warning("WeCom notify is not configured, skip wecom channel")
            return
        accounts, __, __ = self.get_accounts(users)
        if not accounts:
            return
        client = wecom_sdk.WeComClient(self.get_credentials())
        content = f"{subject}\n{message}" if subject else message
        return client.send_text([account for account, __user in accounts], content)


backend = WeCom
