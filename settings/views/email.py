#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : email
# author : ly_13
# date : 7/31/2024
from smtplib import SMTPSenderRefused
from typing import Any

from django.core.mail import get_connection, send_mail
from django.utils.translation import gettext_lazy as _
from rest_framework.request import Request

from common.core.response import ApiResponse
from common.utils import get_logger
from settings.serializers.email import EmailSettingSerializer
from settings.utils.test_connection import build_test_values
from settings.views.settings import BaseSettingViewSet

logger = get_logger(__name__)


class EmailServerSettingViewSet(BaseSettingViewSet):
    """邮件服务"""

    serializer_class = EmailSettingSerializer
    category = "email"

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Any:
        """测试{cls}"""
        serializer = self.get_serializer_class()(data=request.data)
        serializer.is_valid(raise_exception=True)

        # 测试连接统一口径：按表单值传参构造连接（未提交键回退已存
        # 配置、密码留空沿用已存值），不改进程全局 settings——并发期间真实
        # 请求不可能读到测试值
        values = build_test_values(
            serializer.validated_data,
            request.data,
            keys=[
                "EMAIL_HOST",
                "EMAIL_PORT",
                "EMAIL_HOST_USER",
                "EMAIL_HOST_PASSWORD",
                "EMAIL_SUBJECT_PREFIX",
                "EMAIL_USE_SSL",
                "EMAIL_USE_TLS",
            ],
            secret_keys={"EMAIL_HOST_PASSWORD"},
        )
        email_recipient = serializer.validated_data.get("EMAIL_RECIPIENT")

        try:
            # 括号必须：`or` 优先级低于 `+`，裸写 `prefix or "" + "Test"` 在已设前缀时
            # subject 只剩前缀、丢失 "Test"
            subject = (values["EMAIL_SUBJECT_PREFIX"] or "") + "Test"
            message = _("Test smtp setting")
            email_recipient = email_recipient or values["EMAIL_HOST_USER"]
            connection = get_connection(
                host=values["EMAIL_HOST"],
                port=int(values["EMAIL_PORT"]),
                username=values["EMAIL_HOST_USER"],
                password=values["EMAIL_HOST_PASSWORD"],
                use_tls=bool(values["EMAIL_USE_TLS"]),
                use_ssl=bool(values["EMAIL_USE_SSL"]),
            )
            send_mail(subject, message, values["EMAIL_HOST_USER"], [email_recipient], connection=connection)
        except SMTPSenderRefused as e:
            error = e.smtp_error
            if isinstance(error, bytes):
                decoded = ""
                for coding in ("gbk", "utf8"):
                    try:
                        decoded = error.decode(coding)
                    except UnicodeDecodeError:
                        continue
                    else:
                        break
                # 两种编码都解不出时退回 bytes 原文（str(bytes) 至少可读形态不丢信息）
                return ApiResponse(code=1001, detail=decoded or str(error))
            return ApiResponse(code=1001, detail=str(error))
        except Exception:
            # 测试入口兜底：网络/协议等原始异常细节只留服务端日志，对外统一文案避免泄露内部信息
            logger.warning("SMTP test send unexpected error", exc_info=True)
            return ApiResponse(code=1002, detail=_("Email test failed, please check the SMTP configuration"))
        return ApiResponse(detail=_("Test mail sent to {}, please check").format(email_recipient))
