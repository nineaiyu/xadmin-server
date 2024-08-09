#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : endpoint
# author : ly_13
# date : 8/6/2024
import importlib
from collections import OrderedDict

from django.conf import settings
from django.db.models import TextChoices
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import APIException

from common.utils import get_logger
from .base import BaseSMSClient

logger = get_logger(__name__)


class BACKENDS(TextChoices):
    ALIBABA = 'alibaba', _('Alibaba cloud')


class SMS:
    client: BaseSMSClient

    def __init__(self, backend=None):
        backend = backend or settings.SMS_BACKEND
        if backend not in BACKENDS:
            raise APIException(
                code='sms_provider_not_support',
                detail=_('SMS provider not support: {}').format(backend)
            )
        m = importlib.import_module(f'.{backend or settings.SMS_BACKEND}', __package__)
        self.client = m.client.new_from_settings()

    def send_sms(self, phone_numbers: list, sign_name: str, template_code: str, template_param: dict, **kwargs):
        return self.client.send_sms(
            phone_numbers=phone_numbers,
            sign_name=sign_name,
            template_code=template_code,
            template_param=template_param,
            **kwargs
        )

    def send_verify_code(self, phone_number, code):
        prefix = getattr(self.client, 'SIGN_AND_TMPL_SETTING_FIELD_PREFIX', '')
        sign_name = getattr(settings, f'{prefix}_VERIFY_SIGN_NAME', None)
        template_code = getattr(settings, f'{prefix}_VERIFY_TEMPLATE_CODE', None)

        if self.client.need_pre_check() and not (sign_name and template_code):
            raise APIException(
                code='verify_code_sign_tmpl_invalid',
                detail=_('SMS verification code signature or template invalid')
            )
        return self.send_sms([phone_number], sign_name, template_code, OrderedDict(code=code))
