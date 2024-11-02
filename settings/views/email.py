#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : email
# author : ly_13
# date : 7/31/2024
from smtplib import SMTPSenderRefused

from django.conf import settings
from django.core.mail import get_connection, send_mail
from django.utils.translation import gettext_lazy as _

from common.core.response import ApiResponse
from common.utils import get_logger
from settings.serializers.email import EmailSettingSerializer
from settings.views.settings import BaseSettingViewSet

logger = get_logger(__name__)


class EmailServerSettingViewSet(BaseSettingViewSet):
    """邮件服务"""
    serializer_class = EmailSettingSerializer
    category = "email"

    def create(self, request, *args, **kwargs):
        """测试{cls}"""
        serializer = self.get_serializer_class()(data=request.data)
        serializer.is_valid(raise_exception=True)

        # 测试邮件时，邮件服务器信息从配置中获取
        email_host = settings.EMAIL_HOST
        email_port = settings.EMAIL_PORT
        email_host_user = settings.EMAIL_HOST_USER
        email_host_password = settings.EMAIL_HOST_PASSWORD
        email_use_ssl = settings.EMAIL_USE_SSL
        email_use_tls = settings.EMAIL_USE_TLS
        email_recipient = serializer.validated_data.get('EMAIL_RECIPIENT')

        try:
            subject = settings.EMAIL_SUBJECT_PREFIX or '' + "Test"
            message = _("Test smtp setting")
            email_recipient = email_recipient or email_host_user
            connection = get_connection(
                host=email_host, port=email_port,
                username=email_host_user, password=email_host_password,
                use_tls=email_use_tls, use_ssl=email_use_ssl,
            )
            send_mail(
                subject, message, email_host_user, [email_recipient],
                connection=connection
            )
        except SMTPSenderRefused as e:
            error = e.smtp_error
            if isinstance(error, bytes):
                for coding in ('gbk', 'utf8'):
                    try:
                        error = error.decode(coding)
                    except UnicodeDecodeError:
                        continue
                    else:
                        break
            return ApiResponse(code=1001, detail=str(error))
        except Exception as e:
            logger.error(e)
            return ApiResponse(code=1002, detail=str(e))
        return ApiResponse(detail=_("Test mail sent to {}, please check").format(email_recipient))
