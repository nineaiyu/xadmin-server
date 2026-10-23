#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""通知渠道注册表：渠道元数据的唯一声明处。

新增渠道 = 在 ``CHANNEL_SPECS`` 加一条 + 在 ``backends/<module>.py`` 暴露模块级
``backend``；客户端加载、渲染方法注册、HTML 净化名单均由此派生，不再分别维护。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ChannelSpec:
    """单个通知渠道的静态元数据。

    ``code`` 对应 ``BACKEND`` 枚举值；``module`` 为 ``notifications/backends/`` 下的
    模块名；``renderer`` 是 ``Message`` 上的正文渲染方法名；``html`` 标记正文按
    HTML 处理（渲染收口做白名单净化）；``account_field`` / ``enable_setting`` 用于
    与渠道客户端类声明做一致性校验。
    """

    code: str
    module: str
    renderer: str
    html: bool = False
    account_field: str = ""
    enable_setting: str = ""


CHANNEL_SPECS = (
    ChannelSpec(
        code="email",
        module="email",
        renderer="get_email_msg",
        html=True,
        account_field="email",
        enable_setting="EMAIL_ENABLED",
    ),
    ChannelSpec(
        code="site_msg",
        module="site_msg",
        renderer="get_site_msg_msg",
        html=True,
        account_field="id",
    ),
    ChannelSpec(
        code="sms",
        module="sms",
        renderer="get_sms_msg",
        account_field="phone",
        enable_setting="SMS_ENABLED",
    ),
    # IM 渠道收件账号来自 UserOAuthBinding，不在 User 表上，account_field 留空
    ChannelSpec(
        code="dingtalk",
        module="dingtalk",
        renderer="get_text_msg",
        enable_setting="DINGTALK_ENABLED",
    ),
    ChannelSpec(
        code="wecom",
        module="wecom",
        renderer="get_text_msg",
        enable_setting="WECOM_ENABLED",
    ),
    ChannelSpec(
        code="feishu",
        module="feishu",
        renderer="get_text_msg",
        enable_setting="FEISHU_ENABLED",
    ),
)

SPEC_BY_CODE = {spec.code: spec for spec in CHANNEL_SPECS}

__all__ = ["CHANNEL_SPECS", "SPEC_BY_CODE", "ChannelSpec"]
