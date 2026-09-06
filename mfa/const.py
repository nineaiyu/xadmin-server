#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : const
from django.db.models import TextChoices
from django.utils.translation import gettext_lazy as _


class ConfirmType(TextChoices):
    """敏感操作二次验证类型

    级别递增：PASSWORD < MFA，高级别方式确认通过后，可同时满足低级别要求。
    """
    PASSWORD = 'password', _('Password')
    MFA = 'mfa', _('MFA')


# 各验证类型对应的确认级别
CONFIRM_TYPE_LEVEL = {
    ConfirmType.PASSWORD: 1,
    ConfirmType.MFA: 2,
}

# 各验证类型确认有效期（秒）对应的 Django settings 配置项
CONFIRM_TYPE_TTL_SETTING = {
    ConfirmType.PASSWORD: 'SECURITY_MFA_PASSWORD_CONFIRM_TTL',
    ConfirmType.MFA: 'SECURITY_MFA_VERIFY_TTL',
}
