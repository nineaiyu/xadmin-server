#!/usr/bin/env python
# -*- coding:utf-8 -*-
from django.apps import AppConfig


class IntegrationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "integrations"

    def ready(self):
        # 验证码短信发送实现注入框架层：common 不直接依赖本域（外部服务接入域），
        # 未注册时发送任务 fail-fast，不静默丢码。
        from common.utils.verify_code import register_verify_code_sender
        from integrations.sdk.sms.endpoint import send_verify_code

        register_verify_code_sender(send_verify_code)
