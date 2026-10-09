#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : recovery
"""恢复码验证后端（OTP 因子的自救通道）。

与 OTP / 短信 / 邮件后端的差异：凭据是绑定时一次性展示的 10 个静态恢复码
（哈希落库），无需服务端下发挑战，``challenge_required = False``。

同一条 ``check_code`` 链路同时服务登录 MFA 与敏感操作二次确认（412）——
TOTP 设备全丢的用户凭恢复码即可完成登录与解绑重绑，不再只能管理员 reset。
"""

from typing import Any

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from mfa import recovery
from mfa.backends.base import BaseMFA
from mfa.const import ConfirmType


class RecoveryCodeBackend(BaseMFA):
    name = "recovery"
    display_name = _("Recovery code")
    placeholder = _("Please enter a recovery code")
    challenge_required = False
    confirm_level = ConfirmType.MFA

    @classmethod
    def global_enabled(cls) -> bool:
        """恢复码随 OTP 因子启停：仅 OTP 绑定路径产生恢复码。

        不进 SECURITY_MFA_CONFIRM_BACKENDS 独立配置——它不是常规登录方式，
        是 otp 的配套自救凭据；策略层收窄（SECURITY_MFA_METHODS ∩ 角色 ∩ 用户）
        与其他后端同口径。
        """
        return "otp" in settings.SECURITY_MFA_CONFIRM_BACKENDS

    def is_active(self) -> bool:
        """当前用户仍有未使用的恢复码才可用。"""
        if not getattr(self.user, "pk", None):
            return False
        try:
            return recovery.remaining_count(self.user) > 0
        except Exception:  # noqa: BLE001 用户未落库等场景视为不可用
            return False

    def check_code(self, code: str) -> tuple[bool, Any]:
        return recovery.verify_and_consume(self.user, code)
