#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""企业 IM 通知渠道配置序列化器。

字段名即配置名（BaseSettingViewSet 约定）；三个 `*_SECRET` write_only ⇒
Setting.encrypted=True 值级加密落库，retrieve 回显时自动剔除。

按渠道拆分为三个 serializer：视图按 `?channel=` 收敛字段集合（与 SMS 的
serializer_class_mapper 同思路），各页签只读写/校验自己的字段，因此非密文字段
可 `required=True`——前端据此渲染必填标记，并在保存/测试时拦截空值。
`*_SECRET` 回显为空，沿用邮件密码口径保持 `required=False`（否则「不回显」与
「必填」互相死锁）；凭据是否齐全由测试入口给出可读提示。
`ImNotifySettingSerializer` 是缺省（不带 channel）的兼容入口，字段一律非必填。
"""

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers


class DingTalkSettingSerializer(serializers.Serializer):
    """钉钉工作通知：应用三元组（AppKey / AppSecret / AgentId）"""

    DINGTALK_ENABLED = serializers.BooleanField(
        default=False, label=_("DingTalk notification"), help_text=_("Send notifications via DingTalk work message")
    )
    DINGTALK_APP_KEY = serializers.CharField(max_length=128, required=True, label=_("DingTalk AppKey"))
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
        required=True,
        label=_("DingTalk AgentId"),
        help_text=_("AgentId of the corp app used to send work messages"),
    )


class WeComSettingSerializer(serializers.Serializer):
    """企业微信应用消息：企业三元组（CorpId / CorpSecret / AgentId）"""

    WECOM_ENABLED = serializers.BooleanField(
        default=False, label=_("WeCom notification"), help_text=_("Send notifications via WeCom app message")
    )
    WECOM_CORP_ID = serializers.CharField(max_length=128, required=True, label=_("WeCom CorpId"))
    WECOM_CORP_SECRET = serializers.CharField(
        max_length=256,
        required=False,
        allow_blank=True,
        write_only=True,
        label=_("WeCom CorpSecret"),
        help_text=_("Encrypted at rest; never returned by the API"),
    )
    WECOM_AGENT_ID = serializers.CharField(max_length=64, required=True, label=_("WeCom AgentId"))


class FeiShuSettingSerializer(serializers.Serializer):
    """飞书 IM 消息：应用二元组（App ID / App Secret）"""

    FEISHU_ENABLED = serializers.BooleanField(
        default=False, label=_("FeiShu notification"), help_text=_("Send notifications via FeiShu IM message")
    )
    FEISHU_APP_ID = serializers.CharField(max_length=128, required=True, label=_("FeiShu App ID"))
    FEISHU_APP_SECRET = serializers.CharField(
        max_length=256,
        required=False,
        allow_blank=True,
        write_only=True,
        label=_("FeiShu App Secret"),
        help_text=_("Encrypted at rest; never returned by the API"),
    )


class ImNotifySettingSerializer(serializers.Serializer):
    """全量合并入口（不带 ?channel=）：字段非必填，保持旧接口形态不变"""

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
