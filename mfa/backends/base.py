#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : base
import abc

from django.utils.translation import gettext_lazy as _

from mfa.const import ConfirmType


class BaseMFA(abc.ABC):
    """MFA 验证后端抽象（策略模式）

    新增验证方式三步走：
    1. 继承本类，实现 check_code（挑战型再实现 send_challenge）；
    2. 在 mfa/backends/__init__.py 的 MFA_BACKEND_CLASSES 中注册；
    3. 将 name 加入 SECURITY_MFA_CONFIRM_BACKENDS 配置（或后台设置页勾选）。
    之后即可被敏感操作二次验证与登录 MFA 自动识别，无需改动框架代码。
    """

    name = ''
    display_name = ''
    placeholder = ''
    # True: 服务端先下发验证码（短信/邮件），前端需要先调 send-code
    challenge_required = False
    # 该方式验证通过后可满足的确认级别
    confirm_level = ConfirmType.MFA

    def __init__(self, user, request=None):
        self.user = user
        self.request = request

    @classmethod
    def global_enabled(cls) -> bool:
        """全局配置是否启用该方式（管理员可配置）"""
        return True

    def is_active(self) -> bool:
        """当前用户是否具备使用该方式的条件（如手机号/邮箱/已绑定 OTP）"""
        return True

    def send_challenge(self) -> tuple:
        """下发挑战验证码，返回 (是否成功, 失败原因)"""
        return False, _('This method does not support sending verification codes')

    @abc.abstractmethod
    def check_code(self, code) -> tuple:
        """校验验证码，返回 (是否通过, 失败原因)"""
