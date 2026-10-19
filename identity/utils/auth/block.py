#!/usr/bin/env python
# -*- coding: utf-8 -*-
# project : xadmin-server
# filename : auth_block
"""登录锁定检查：IP 维度与账号维度的封禁判定（登录前置，先于任何业务校验）。"""

from typing import Any

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from identity.utils.auth.captcha import ValidateError
from settings.services import LoginBlockUtil, LoginIpBlockUtil


def check_is_block(
    username: Any, ipaddr: Any, ip_block: Any = LoginIpBlockUtil, login_block: Any = LoginBlockUtil
) -> None:
    if ip_block and ip_block(ipaddr).is_block():
        ip_block(ipaddr).set_block_if_need()
        raise ValidateError(
            _("The address has been locked (please contact admin to unlock it or try again after {} minutes)").format(
                settings.SECURITY_LOGIN_IP_LIMIT_TIME
            )
        )

    if login_block and login_block(username, ipaddr).is_block():
        raise ValidateError(
            _("The account has been locked (please contact admin to unlock it or try again after {} minutes)").format(
                settings.SECURITY_LOGIN_LIMIT_TIME
            )
        )
