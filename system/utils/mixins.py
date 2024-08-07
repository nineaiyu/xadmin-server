#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : mixins
# author : ly_13
# date : 8/2/2024

from django.core.cache import cache

from common.utils import get_logger
from common.utils.token import random_string

logger = get_logger(__file__)

class ResetPasswordMixin(object):
    CACHE_KEY_USER_RESET_PASSWORD_PREFIX = "_KEY_USER_RESET_PASSWORD_{}"
    email = ""
    mobile = ""
    id = None

    def generate_reset_token(self, timeout=3600):
        token = random_string(50)
        key = self.CACHE_KEY_USER_RESET_PASSWORD_PREFIX.format(token)
        cache.set(key, {"id": self.id, "email": self.email, "mobile": self.mobile}, timeout)
        return token

    @classmethod
    def validate_reset_password_token(cls, token):
        if not token:
            return None
        key = cls.CACHE_KEY_USER_RESET_PASSWORD_PREFIX.format(token)
        value = cache.get(key)
        if not value:
            return None
        try:
            user_id = value.get("id", "")
            email = value.get("email", "")
            mobile = value.get("mobile", "")
            user = cls.objects.get(id=user_id, email=email, mobile=mobile)
            return user
        except (AttributeError, cls.DoesNotExist) as e:
            logger.error(e, exc_info=True)
            return None

    @classmethod
    def expired_reset_password_token(cls, token):
        key = cls.CACHE_KEY_USER_RESET_PASSWORD_PREFIX.format(token)
        cache.delete(key)
