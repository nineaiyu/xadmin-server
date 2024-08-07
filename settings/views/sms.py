#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : sms
# author : ly_13
# date : 8/6/2024
import importlib

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.mixins import ListModelMixin
from rest_framework.viewsets import GenericViewSet

from common.base.utils import get_choices_dict
from common.core.response import ApiResponse
from common.sdk.sms.endpoint import BACKENDS
from common.utils import get_logger
from settings.models import Setting
from settings.serializers.sms import AlibabaSMSSettingSerializer, SMSSettingSerializer, SMSBackendSerializer
from settings.views.settings import BaseSettingView

logger = get_logger(__file__)


class SmsSettingView(BaseSettingView):
    serializer_class = SMSSettingSerializer
    category = "sms"


class SMSBackendView(ListModelMixin, GenericViewSet):
    serializer_class = SMSBackendSerializer

    def list(self, request, *args, **kwargs):
        return ApiResponse(data=get_choices_dict(BACKENDS.choices))


class SmsConfigView(BaseSettingView):
    serializer_class_mapper = {
        'alibaba': AlibabaSMSSettingSerializer,
    }

    @property
    def test_code(self):
        return '6' * settings.VERIFY_CODE_LENGTH

    @staticmethod
    def get_or_from_setting(key, value=''):
        if not value:
            secret = Setting.objects.filter(name=key).first()
            if secret:
                value = secret.cleaned_value

        return value or ''

    def get_alibaba_params(self, data):
        init_params = {
            'access_key_id': settings.ALIBABA_ACCESS_KEY_ID,
            'access_key_secret': self.get_or_from_setting(
                'ALIBABA_ACCESS_KEY_SECRET', data.get('ALIBABA_ACCESS_KEY_SECRET')
            )
        }
        send_sms_params = {
            'sign_name': data['ALIBABA_VERIFY_SIGN_NAME'],
            'template_code': data['ALIBABA_VERIFY_TEMPLATE_CODE'],
            'template_param': {'code': self.test_code}
        }
        return init_params, send_sms_params


    def get_params_by_backend(self, backend, data):
        """
        返回两部分参数
            1、实例化参数
            2、发送测试短信参数
        """
        get_params_func = getattr(self, 'get_%s_params' % backend)
        return get_params_func(data)


    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer_class()(data=request.data)
        serializer.is_valid(raise_exception=True)

        test_phone = serializer.validated_data.get('SMS_TEST_PHONE')
        if not test_phone:
            return ApiResponse(code=1001, detail=_('test_phone is required'))

        init_params, send_sms_params = self.get_params_by_backend(self.category, serializer.validated_data)
        m = importlib.import_module(f'common.sdk.sms.{self.category}', __package__)
        try:
            client = m.client(**init_params)
            client.send_sms(
                phone_numbers=[test_phone],
                **send_sms_params
            )
            status_code = status.HTTP_200_OK
            detail = _('Test success')
        except APIException as e:
            try:
                error = e.detail['errmsg']
            except:
                error = e.detail
            status_code = status.HTTP_400_BAD_REQUEST
            detail =  error
        except Exception as e:
            status_code = status.HTTP_400_BAD_REQUEST
            detail = str(e)
        return ApiResponse(code=status_code, detail=detail)