# -*- coding: utf-8 -*-
"""WS 自信息载荷：字段权限不得裁剪本人身份。

聊天室气泡左右对齐依赖 USERINFO 帧里的 pk；WS 认证请求是裸 ASGIRequest，
没有 HTTP 权限中间件挂载的 ``request.fields``，字段权限 fail-closed 曾把
非超管的载荷裁成空对象。
"""

from types import SimpleNamespace

import pytest
from asgiref.sync import async_to_sync
from django.test import override_settings

from common.local import set_current_request
from message.base import get_userinfo

pytestmark = pytest.mark.django_db


@override_settings(PERMISSION_FIELD_ENABLED=True)
def test_userinfo_payload_not_stripped_by_field_permission():
    from identity.models import UserInfo

    user = UserInfo.objects.create_user(username="ws-self-info", password="Test@123456", nickname="自信息")
    # 复刻 WS 认证后的请求上下文：只有 user，没有 request.fields
    set_current_request(SimpleNamespace(user=user))
    try:
        payload = async_to_sync(get_userinfo)(user)
    finally:
        set_current_request(None)

    assert payload["pk"] == user.pk
    assert payload["username"] == "ws-self-info"
    assert payload["nickname"] == "自信息"
