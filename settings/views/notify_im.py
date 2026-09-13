#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""企业 IM 通知渠道设置视图（ADR-019）：retrieve 回显 / partialUpdate 保存 / create 渠道测试。

``?channel=dingtalk|wecom|feishu`` 是「渠道作用域」参数：retrieve / search-columns /
partialUpdate / create 全链路按它收敛字段集合（设置页三个页签各自只读写自己的字段，
非密文字段此时可必填）；缺省（不带 channel）保持旧的全量接口形态。

``POST`` 即「测试」：对目标**已启用且凭据齐全**的渠道实际获取一次 token（按表单当前
值，未带字段回退已存配置；token 缓存按凭据摘要隔离，改密不会沿用旧 token）。单渠道
模式只测该渠道；缺省模式逐个测试全部渠道，分渠道结果互不影响。未启用渠道标记
Disabled、不计入失败，但**没有任何渠道真正测通**时按失败反馈——避免"什么都没配也
提示测试完成"。detail 始终可直接展示：首个失败原因 / 渠道未启用 / 测试完成。
"""

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import ValidationError

from common.core.response import ApiResponse
from common.sdk.im.base import ImSdkError
from common.sdk.im.dingtalk import DingTalkClient
from common.sdk.im.feishu import FeishuClient
from common.sdk.im.wecom import WeComClient
from common.utils import get_logger
from settings.serializers.notify_im import (
    DingTalkSettingSerializer,
    FeiShuSettingSerializer,
    ImNotifySettingSerializer,
    WeComSettingSerializer,
)
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

# 渠道作用域（?channel=）→ 结果名 / 客户端 / 序列化器 / 可达性字段 / 客户端凭据映射
_CHANNELS = {
    "dingtalk": {
        "name": "DingTalk",
        "client": DingTalkClient,
        "serializer": DingTalkSettingSerializer,
        "required": ("DINGTALK_ENABLED", "DINGTALK_APP_KEY", "DINGTALK_APP_SECRET", "DINGTALK_AGENT_ID"),
        "credentials": {
            "app_key": "DINGTALK_APP_KEY",
            "app_secret": "DINGTALK_APP_SECRET",
            "agent_id": "DINGTALK_AGENT_ID",
        },
    },
    "wecom": {
        "name": "WeCom",
        "client": WeComClient,
        "serializer": WeComSettingSerializer,
        "required": ("WECOM_ENABLED", "WECOM_CORP_ID", "WECOM_CORP_SECRET", "WECOM_AGENT_ID"),
        "credentials": {
            "corp_id": "WECOM_CORP_ID",
            "corp_secret": "WECOM_CORP_SECRET",
            "agent_id": "WECOM_AGENT_ID",
        },
    },
    "feishu": {
        "name": "FeiShu",
        "client": FeishuClient,
        "serializer": FeiShuSettingSerializer,
        "required": ("FEISHU_ENABLED", "FEISHU_APP_ID", "FEISHU_APP_SECRET"),
        "credentials": {"app_id": "FEISHU_APP_ID", "app_secret": "FEISHU_APP_SECRET"},
    },
}


def _test_channel(channel: dict) -> str:
    """单渠道连通性：返回可直接展示的结果文本（Disabled / 缺配置 / 渠道错误 / OK）。"""
    if not getattr(settings, channel["required"][0]):
        return str(_("Disabled"))
    missing = [key for key in channel["required"][1:] if not getattr(settings, key)]
    if missing:
        return str(_("Missing configuration: {}").format(", ".join(missing)))
    credentials = {alias: getattr(settings, key) for alias, key in channel["credentials"].items()}
    try:
        channel["client"](credentials)._cached_token()
    except ImSdkError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001 测试入口兜底，不回显堆栈
        logger.warning("IM notify test unexpected error for %s", channel["name"], exc_info=True)
        return str(exc)
    return str(_("OK"))


class ImNotifySettingViewSet(BaseSettingViewSet):
    """企业 IM 通知渠道设置与连通性测试"""

    serializer_class = ImNotifySettingSerializer
    category = "notify_im"

    def get_serializer_class(self):
        """按 `?channel=` 收敛到渠道序列化器（各页签只读写/校验自己的字段）。"""
        channel = self.request.query_params.get("channel")
        if channel in _CHANNELS:
            return _CHANNELS[channel]["serializer"]
        return self.serializer_class

    def create(self, request, *args, **kwargs):
        """测试{cls}"""
        serializer = self.get_serializer_class()(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        scope = request.query_params.get("channel")
        if scope and scope not in _CHANNELS:
            raise ValidationError({"channel": _("Unknown channel: {}").format(scope)})
        channels = {scope: _CHANNELS[scope]} if scope else _CHANNELS

        saved = {key: getattr(settings, key) for key in _SETTINGS_KEYS}
        try:
            for key in _SETTINGS_KEYS:
                if key in request.data:
                    setattr(settings, key, data.get(key))
            for key in _SECRET_KEYS:
                # write_only secret：表单未重新输入时回退已存配置
                if data.get(key):
                    setattr(settings, key, data.get(key))

            results = {channel["name"]: _test_channel(channel) for channel in channels.values()}
        finally:
            for key, value in saved.items():
                setattr(settings, key, value)

        ok, disabled = str(_("OK")), str(_("Disabled"))
        failed = [name for name, result in results.items() if result not in (ok, disabled)]
        if failed:
            # 首个失败原因作为 detail：失败渠道可读、可展示（不再笼统报"测试完成"）
            return ApiResponse(code=1002, detail=results[failed[0]], data=results)
        if ok not in results.values():
            # 全部渠道未启用 = 没有任何渠道被真正测试：同样按失败反馈
            return ApiResponse(code=1002, detail=_("Channel not enabled"), data=results)
        return ApiResponse(code=1000, detail=_("Test completed"), data=results)
