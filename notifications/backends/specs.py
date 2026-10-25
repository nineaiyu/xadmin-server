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
    # 站点级转发渠道：无逐用户绑定（消息与目标用户整体出站），account_field 留空
    ChannelSpec(
        code="webhook",
        module="webhook",
        renderer="get_text_msg",
        enable_setting="WEBHOOK_ENABLED",
    ),
)

#: 运行期登记的扩展渠道 spec（register_channel_spec 写入；reset_channel_specs 清空）
_EXTRA_CHANNEL_SPECS: list[ChannelSpec] = []


def all_channel_specs() -> tuple["ChannelSpec", ...]:
    """内置 + 运行期登记的全部渠道 spec（消费侧统一入口，勿直接遍历 CHANNEL_SPECS）。"""
    return (*CHANNEL_SPECS, *_EXTRA_CHANNEL_SPECS)


def register_channel_spec(spec: "ChannelSpec") -> "ChannelSpec":
    """登记渠道 spec（二开渠道扩展点）：与内置/既有扩展 code 冲突 fail-fast。

    约束：``code`` 必须已存在于 :class:`BACKEND` 枚举（渠道枚举是序列化契约，
    运行期不扩枚举）；渲染方法接线由 ``notifications.registry.register_channel``
    一并完成，本函数只管 spec 清单（订阅页渠道列表 / 净化与 HTML 名单等消费面）。
    """
    from notifications.backends import BACKEND  # 延迟导入：枚举定义在包 __init__（避免循环）

    if spec.code not in BACKEND.values:
        raise ValueError(f"channel spec code {spec.code!r} not in BACKEND enum")
    if spec.code in {item.code for item in all_channel_specs()}:
        raise ValueError(f"channel spec code {spec.code!r} already declared")
    _EXTRA_CHANNEL_SPECS.append(spec)
    return spec


def reset_channel_specs() -> None:
    """清空运行期登记的渠道 spec（测试隔离用；内置清单不受影响）。"""
    _EXTRA_CHANNEL_SPECS.clear()


SPEC_BY_CODE = {spec.code: spec for spec in CHANNEL_SPECS}

__all__ = [
    "CHANNEL_SPECS",
    "SPEC_BY_CODE",
    "ChannelSpec",
    "all_channel_specs",
    "register_channel_spec",
    "reset_channel_specs",
]
