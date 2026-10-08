#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : sms
# author : ly_13
# date : 8/6/2024
import importlib

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import APIException

from common.base.utils import get_choices_dict
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger
from integrations.sdk.sms.endpoint import BACKENDS
from settings.models import Setting
from settings.serializers.sms import AlibabaSMSSettingSerializer, BaseSMSSettingSerializer, SMSSettingSerializer
from settings.views.settings import BaseSettingViewSet

logger = get_logger(__name__)

#: 测试短信验证码的固定数字（长度仍取 VERIFY_CODE_LENGTH，保持与真实校验码同格式）
TEST_CODE_DIGIT = "6"


class SmsSettingViewSet(BaseSettingViewSet):
    """短信配置"""

    serializer_class = SMSSettingSerializer
    category = "sms"

    @extend_schema(
        parameters=None,
        responses=get_default_response_schema(
            {
                "data": build_array_type(
                    build_object_type(
                        properties={
                            "value": build_basic_type(OpenApiTypes.STR),
                            "label": build_basic_type(OpenApiTypes.STR),
                        }
                    )
                )
            }
        ),
    )
    @action(methods=["get"], detail=False)
    def backends(self, request, *args, **kwargs):
        """获取可配置短信后端"""
        return ApiResponse(data=get_choices_dict(BACKENDS.choices))


class SmsConfigViewSet(BaseSettingViewSet):
    """短信服务"""

    # 字段面按供应商显式收敛：有专属配置项的供应商在 mapper 里给专属序列化器，
    # 未收录的已注册供应商回落短信公共字段面（仅测试手机号），不再错配
    # 基础设置序列化器导致取字段面/校验对不上
    serializer_class = BaseSMSSettingSerializer
    serializer_class_mapper = {
        "alibaba": AlibabaSMSSettingSerializer,
    }

    @property
    def test_code(self):
        return TEST_CODE_DIGIT * settings.VERIFY_CODE_LENGTH

    @staticmethod
    def get_or_from_setting(key, value=""):
        if not value:
            secret = Setting.objects.filter(name=key).first()
            if secret:
                value = secret.cleaned_value

        return value or ""

    def get_alibaba_params(self, data):
        init_params = {
            "access_key_id": data["ALIBABA_ACCESS_KEY_ID"],
            "access_key_secret": self.get_or_from_setting(
                "ALIBABA_ACCESS_KEY_SECRET", data.get("ALIBABA_ACCESS_KEY_SECRET")
            ),
        }
        send_sms_params = {
            "sign_name": data["ALIBABA_VERIFY_SIGN_NAME"],
            "template_code": data["ALIBABA_VERIFY_TEMPLATE_CODE"],
            "template_param": {"code": self.test_code},
        }
        return init_params, send_sms_params

    def get_params_by_backend(self, backend, data):
        """
        返回两部分参数
            1、实例化参数
            2、发送测试短信参数
        """
        get_params_func = getattr(self, f"get_{backend}_params", None)
        if get_params_func is None:
            # 已注册但尚未接入测试发送的供应商：给出受控报错，不让 AttributeError 变成 500
            raise APIException(
                code="sms_provider_not_support", detail=_("SMS provider not support: {}").format(backend)
            )
        return get_params_func(data)

    def create(self, request, *args, **kwargs):
        """测试{cls}"""
        serializer = self.get_serializer_class()(data=request.data)
        serializer.is_valid(raise_exception=True)

        test_phone = serializer.validated_data.get("SMS_TEST_PHONE")
        if not test_phone:
            return ApiResponse(code=1001, detail=_("test_phone is required"))

        try:
            init_params, send_sms_params = self.get_params_by_backend(self.category, serializer.validated_data)
            m = importlib.import_module(f"integrations.sdk.sms.{self.category}", __package__)
            client = m.client(**init_params)
            client.send_sms(phone_numbers=[test_phone], **send_sms_params)
            status_code = status.HTTP_200_OK
            detail = _("Test success")
        except APIException as e:
            try:
                error = e.detail["errmsg"]
            except Exception:
                error = e.detail
            status_code = status.HTTP_400_BAD_REQUEST
            detail = error
        except Exception:
            # 测试入口兜底：SDK/网络等原始异常细节只留服务端日志，对外统一文案避免泄露内部信息
            logger.warning("SMS test send unexpected error", exc_info=True)
            status_code = status.HTTP_400_BAD_REQUEST
            detail = _("SMS test failed, please check the SMS configuration")
        return ApiResponse(code=status_code, detail=detail)
