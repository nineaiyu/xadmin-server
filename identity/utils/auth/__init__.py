#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""identity 登录工具集（按职责拆分的再导出面）。

原单文件 ``identity/utils/auth.py`` 混有四类职责（前置校验 / 锁定检查 /
登录日志与验证码核验 / token 生存期），拆为 :mod:`~identity.utils.auth.captcha`、
:mod:`~identity.utils.auth.block`、:mod:`~identity.utils.auth.login_log`、
:mod:`~identity.utils.auth.lifetime` 四个模块。本文件只做符号再导出，
``from identity.utils.auth import X`` 的既有导入面保持不变——新增符号请在此登记。
"""

from identity.utils.auth.block import check_is_block
from identity.utils.auth.captcha import (
    DECRYPT_FAILED_MESSAGE,
    ValidateError,
    check_captcha,
    check_tmp_token,
    check_token_and_captcha,
    decrypt_submitted,
    get_username_password,
)
from identity.utils.auth.lifetime import get_token_lifetime
from identity.utils.auth.login_log import (
    check_different_city_login_if_need,
    save_login_log,
    verify_sms_email_code,
)

__all__ = [
    "DECRYPT_FAILED_MESSAGE",
    "ValidateError",
    "decrypt_submitted",
    "check_captcha",
    "check_token_and_captcha",
    "check_tmp_token",
    "get_username_password",
    "check_is_block",
    "save_login_log",
    "verify_sms_email_code",
    "check_different_city_login_if_need",
    "get_token_lifetime",
]
