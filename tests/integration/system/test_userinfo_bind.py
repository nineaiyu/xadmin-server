# -*- coding: utf-8 -*-
"""个人绑定邮箱/手机：唯一性冲突必须拒绝，禁止清除其他账号的绑定。"""

from unittest.mock import patch

import pytest

from identity.models import UserInfo
from mfa.cache import UserConfirmStateCache
from mfa.const import ConfirmType

pytestmark = pytest.mark.django_db

BIND_URL = "/api/identity/userinfo/bind"


def _confirm(user) -> None:
    # 绑定是敏感操作：直接写入密码确认状态，聚焦绑定语义本身
    UserConfirmStateCache(user).set(ConfirmType.PASSWORD, "password")


@pytest.fixture
def authed_client(api_client, superuser):
    api_client.force_authenticate(user=superuser)
    _confirm(superuser)
    return api_client


class TestBindConflict:
    def test_target_bound_to_another_account_is_rejected(self, authed_client, superuser):
        other = UserInfo.objects.create_user(username="other-bind", email="shared@example.com", password="Test@123456")
        with patch(
            "identity.views.user.userinfo.verify_sms_email_code",
            return_value=("email", "shared@example.com", "verify-token"),
        ):
            resp = authed_client.post(
                BIND_URL,
                {"verify_token": "verify-token", "verify_code": "123456"},
                format="json",
            )
        assert resp.status_code == 400, resp.data
        superuser.refresh_from_db()
        other.refresh_from_db()
        assert superuser.email == "admin@example.com"
        assert other.email == "shared@example.com"

    def test_bind_own_email_still_succeeds(self, authed_client, superuser):
        superuser.email = ""
        superuser.save(update_fields=["email"])
        with patch(
            "identity.views.user.userinfo.verify_sms_email_code",
            return_value=("email", "own@example.com", "verify-token"),
        ):
            resp = authed_client.post(
                BIND_URL,
                {"verify_token": "verify-token", "verify_code": "123456"},
                format="json",
            )
        assert resp.status_code == 200, resp.data
        superuser.refresh_from_db()
        assert superuser.email == "own@example.com"


class TestBindRequiresReauth:
    def test_bind_requires_password_confirmation(self, api_client, superuser):
        """换绑找回渠道必须先过原密码确认（会话劫持不能直接改绑）。"""
        api_client.force_authenticate(user=superuser)
        resp = api_client.post(
            BIND_URL,
            {"verify_token": "verify-token", "verify_code": "123456"},
            format="json",
        )
        assert resp.status_code == 412, resp.data
        assert resp.data["type"] == "user_confirm_required"
        assert resp.data["confirm_type"] == "password"
