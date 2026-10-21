#!/usr/bin/env python
# -*- coding: utf-8 -*-
# project : xadmin-server
# filename : auth_lifetime
"""登录响应附带的 token 生存期（秒）：供登录 / 刷新等响应组装使用。"""

from typing import Any

from django.conf import settings


def get_token_lifetime(user_obj: Any) -> Any:
    access_token_lifetime = settings.SIMPLE_JWT.get("ACCESS_TOKEN_LIFETIME")
    refresh_token_lifetime = settings.SIMPLE_JWT.get("REFRESH_TOKEN_LIFETIME")
    return {
        "access_token_lifetime": int(access_token_lifetime.total_seconds()),
        "refresh_token_lifetime": int(refresh_token_lifetime.total_seconds()),
        # 'username': user_obj.username
    }
