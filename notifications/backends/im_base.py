#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""IM 通知渠道公共基类：从 OAuth 绑定派生收件账号。

`account_field` 语义是「User 上的接收账号字段」，而 IM 渠道的接收账号
（unionId/userid）来自用户经对应 flavor provider 登录留下的 `UserOAuthBinding`：
- provider key 由管理员自由命名，归属判定按 provider 配置的 **flavor** 归集；
- 未绑定用户沿用 BackendBase 的 debug 日志口径静默跳过；
- `BACKEND.get_account` 无外部调用方，覆写 `get_accounts`/`get_account` 不影响
  订阅页等其他链路。
"""

from common.utils import get_logger

from .base import BackendBase

logger = get_logger(__name__)


class ImBindingBackend(BackendBase):
    """binding 派生账号的 IM 渠道基类。"""

    # 对应的 provider flavor（dingtalk / wecom / feishu），子类覆盖
    flavor = ""

    @classmethod
    def provider_keys_for_flavor(cls) -> list:
        """flavor 匹配的所有已配置 provider key（含管理员自定义命名）。"""
        from system.utils.oauth import get_providers

        return [
            item["key"] for item in get_providers(enabled_only=False) if (item.get("flavor") or "oauth2") == cls.flavor
        ]

    def get_accounts(self, users):
        """按绑定派生收件账号，返回 (accounts, unbound_users, subject_user_mapper)。

        与 BackendBase 的差异：accounts 元素为 ``(账号, user)`` 元组——钉钉需要
        逐用户携带 unionId 换 userid，子类按需消费。
        """
        from system.models import UserOAuthBinding

        provider_keys = self.provider_keys_for_flavor()
        users = list(users)
        if not provider_keys or not users:
            return [], users, {}

        bindings = (
            UserOAuthBinding.objects.filter(user__in=users, provider__in=provider_keys)
            .order_by("-created_time")
            .values_list("user_id", "subject")
        )
        # 同一用户同 provider 受唯一约束；跨 provider key 重名时取最新绑定
        user_subject = {}
        for user_id, subject in bindings:
            user_subject.setdefault(user_id, str(subject))

        accounts, unbound_users = [], []
        for user in users:
            subject = user_subject.get(user.pk)
            if subject:
                accounts.append((subject, user))
            else:
                unbound_users.append(user)
        if unbound_users:
            logger.debug(
                "Notification backend %s skip %s user(s) without %s binding",
                type(self).__name__,
                len(unbound_users),
                self.flavor,
            )
        return accounts, unbound_users, {subject: user for subject, user in accounts}

    @classmethod
    def get_account(cls, user):
        from system.models import UserOAuthBinding

        binding = UserOAuthBinding.objects.filter(user=user, provider__in=cls.provider_keys_for_flavor()).first()
        return binding.subject if binding else None
