#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""飞书通知渠道：im/v1/messages 文本消息。

可达性两层：渠道级 = 开关 + 应用二元组齐全；用户级 = 用户有 feishu flavor
的 OAuth 绑定（subject 即 union_id，receive_id_type=union_id 直发）。
"""

from django.conf import settings

from common.sdk.im import feishu as feishu_sdk
from common.utils import get_logger

from .im_base import ImBindingBackend

logger = get_logger(__name__)


class FeiShu(ImBindingBackend):
    flavor = "feishu"
    is_enable_field_in_settings = "FEISHU_ENABLED"

    @classmethod
    def get_credentials(cls) -> dict:
        return {
            "app_id": getattr(settings, "FEISHU_APP_ID", ""),
            "app_secret": getattr(settings, "FEISHU_APP_SECRET", ""),
        }

    @classmethod
    def is_enable(cls):
        if not super().is_enable():
            return False
        credentials = cls.get_credentials()
        return all(credentials.get(key) for key in ("app_id", "app_secret"))

    def send_msg(self, users, message, subject="", **kwargs):
        if not self.is_enable():
            logger.warning("FeiShu notify is not configured, skip feishu channel")
            return
        accounts, __, __ = self.get_accounts(users)
        if not accounts:
            return
        client = feishu_sdk.FeishuClient(self.get_credentials())
        content = f"{subject}\n{message}" if subject else message
        return client.send_text([account for account, __user in accounts], content)


backend = FeiShu
