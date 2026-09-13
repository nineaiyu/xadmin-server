#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""企业 IM 通知渠道设置视图（ADR-019）：retrieve 回显 / partialUpdate 保存 / create 渠道测试。

``POST`` 即「测试」：对每个**已启用且凭据齐全**的渠道实际获取一次 token
（按表单当前值，未带字段回退已存配置），返回分渠道结果；失败渠道给出可读
错误且不影响其他渠道判定。未启用的渠道标记 Disabled，不计入失败。
"""

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from common.core.response import ApiResponse
from common.sdk.im.base import ImSdkError
from common.sdk.im.dingtalk import DingTalkClient
from common.sdk.im.feishu import FeishuClient
from common.sdk.im.wecom import WeComClient
from common.utils import get_logger
from settings.serializers.notify_im import ImNotifySettingSerializer
from settings.views.settings import BaseSettingViewSet

logger = get_logger(__name__)

_SETTINGS_KEYS = [
    "DINGTALK_ENABLED",
    "DINGTALK_APP_KEY",
    "DINGTALK_APP_SECRET",
    "DINGTALK_AGENT_ID",
    "WECOM_ENABLED",
    "WECOM_CORP_ID",
    "WECOM_CORP_SECRET",
    "WECOM_AGENT_ID",
    "FEISHU_ENABLED",
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
]

_SECRET_KEYS = {"DINGTALK_APP_SECRET", "WECOM_CORP_SECRET", "FEISHU_APP_SECRET"}

# 渠道名 → (客户端类, [开关, 凭据...])
_CHANNEL_MATRIX = [
    ("DingTalk", DingTalkClient, ("DINGTALK_ENABLED", "DINGTALK_APP_KEY", "DINGTALK_APP_SECRET", "DINGTALK_AGENT_ID")),
    ("WeCom", WeComClient, ("WECOM_ENABLED", "WECOM_CORP_ID", "WECOM_CORP_SECRET", "WECOM_AGENT_ID")),
    ("FeiShu", FeishuClient, ("FEISHU_ENABLED", "FEISHU_APP_ID", "FEISHU_APP_SECRET")),
]


def _channel_credentials() -> dict:
    """三家凭据合并表（客户端按需取键）。"""
    return {
        "app_key": settings.DINGTALK_APP_KEY,
        "app_secret": settings.DINGTALK_APP_SECRET,
        "agent_id": settings.DINGTALK_AGENT_ID,
        "corp_id": settings.WECOM_CORP_ID,
        "corp_secret": settings.WECOM_CORP_SECRET,
        "app_id": settings.FEISHU_APP_ID,
    }


class ImNotifySettingViewSet(BaseSettingViewSet):
    """企业 IM 通知渠道设置与连通性测试"""

    serializer_class = ImNotifySettingSerializer
    category = "notify_im"

    def create(self, request, *args, **kwargs):
        """测试{cls}"""
        serializer = self.get_serializer_class()(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        saved = {key: getattr(settings, key) for key in _SETTINGS_KEYS}
        try:
            for key in _SETTINGS_KEYS:
                if key in request.data:
                    setattr(settings, key, data.get(key))
            for key in _SECRET_KEYS:
                # write_only secret：表单未重新输入时回退已存配置
                if data.get(key):
                    setattr(settings, key, data.get(key))

            results = {}
            for name, client_cls, required_keys in _CHANNEL_MATRIX:
                if not getattr(settings, required_keys[0]):
                    results[name] = str(_("Disabled"))
                    continue
                missing = [key for key in required_keys[1:] if not getattr(settings, key)]
                if missing:
                    results[name] = str(_("Missing configuration: {}").format(", ".join(missing)))
                    continue
                try:
                    client_cls(_channel_credentials())._cached_token({})
                except ImSdkError as exc:
                    results[name] = str(exc)
                except Exception as exc:  # noqa: BLE001 测试入口兜底，不回显堆栈
                    logger.warning("IM notify test unexpected error for %s", name, exc_info=True)
                    results[name] = str(exc)
                else:
                    results[name] = str(_("OK"))
        finally:
            for key, value in saved.items():
                setattr(settings, key, value)

        disabled = str(_("Disabled"))
        ok = str(_("OK"))
        failed = [name for name, result in results.items() if result not in (ok, disabled)]
        return ApiResponse(code=1000 if not failed else 1002, detail=_("Test completed"), data=results)
