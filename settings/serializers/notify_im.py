#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""企业 IM 通知渠道配置序列化器（ADR-019）。

字段名即配置名（BaseSettingViewSet 约定）；三个 `*_SECRET` write_only ⇒
Setting.encrypted=True 值级加密落库，retrieve 回显时自动剔除。
渠道可达性 = 开关 AND 凭据齐全（SMS 同款降级语义）。
"""

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers


class ImNotifySettingSerializer(serializers.Serializer):
    # 钉钉（工作通知）
    DINGTALK_ENABLED = serializers.BooleanField(
        default=False, label=_("DingTalk notification"), help_text=_("Send notifications via DingTalk work message")
    )
    DINGTALK_APP_KEY = serializers.CharField(
        max_length=128, required=False, allow_blank=True, label=_("DingTalk AppKey")
    )
    DINGTALK_APP_SECRET = serializers.CharField(
        max_length=256,
        required=False,
        allow_blank=True,
        write_only=True,
        label=_("DingTalk AppSecret"),
        help_text=_("Encrypted at rest; never returned by the API"),
    )
    DINGTALK_AGENT_ID = serializers.CharField(
        max_length=64,
        required=False,
        allow_blank=True,
        label=_("DingTalk AgentId"),
        help_text=_("AgentId of the corp app used to send work messages"),
    )

    # 企业微信（应用消息）
    WECOM_ENABLED = serializers.BooleanField(
        default=False, label=_("WeCom notification"), help_text=_("Send notifications via WeCom app message")
    )
    WECOM_CORP_ID = serializers.CharField(max_length=128, required=False, allow_blank=True, label=_("WeCom CorpId"))
    WECOM_CORP_SECRET = serializers.CharField(
        max_length=256,
        required=False,
        allow_blank=True,
        write_only=True,
        label=_("WeCom CorpSecret"),
        help_text=_("Encrypted at rest; never returned by the API"),
    )
    WECOM_AGENT_ID = serializers.CharField(max_length=64, required=False, allow_blank=True, label=_("WeCom AgentId"))

    # 飞书（IM 消息）
    FEISHU_ENABLED = serializers.BooleanField(
        default=False, label=_("FeiShu notification"), help_text=_("Send notifications via FeiShu IM message")
    )
    FEISHU_APP_ID = serializers.CharField(max_length=128, required=False, allow_blank=True, label=_("FeiShu App ID"))
    FEISHU_APP_SECRET = serializers.CharField(
        max_length=256,
        required=False,
        allow_blank=True,
        write_only=True,
        label=_("FeiShu App Secret"),
        help_text=_("Encrypted at rest; never returned by the API"),
    )
