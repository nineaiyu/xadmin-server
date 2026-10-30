# -*- coding: utf-8 -*-
"""改密后的凭证失效：旧 JWT / refresh / PAT 不得继续使用。"""

import pytest
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIClient, APIRequestFactory
from rest_framework_simplejwt.tokens import RefreshToken

from common.core.auth import CookieJWTAuthentication
from identity.models import UserInfo
from identity.serializers.userinfo import ChangePasswordSerializer
from identity.services.token_issue import issue_access_token

pytestmark = pytest.mark.django_db


@pytest.fixture
def password_user(db):
    return UserInfo.objects.create_user(username="pwd_change_user", password="Test@123456", nickname="改密用户")


def _jwt_client(access_token):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
    return client


def test_change_password_revokes_jwt_refresh_and_pat(password_user, settings):
    settings.SECURITY_USER_PASSWORD_ENCRYPTED_ENABLED = False
    settings.SECURITY_PASSWORD_LEAK_CHECK_ENABLED = False
    access = str(RefreshToken.for_user(password_user).access_token)
    pat, raw_pat = issue_access_token(creator=password_user, name="改密前凭证")

    jwt_client = _jwt_client(access)
    pat_client = APIClient()
    pat_client.credentials(HTTP_AUTHORIZATION=f"Pat {raw_pat}")
    assert jwt_client.get("/api/identity/userinfo").status_code == 200
    assert pat_client.get("/api/identity/userinfo").status_code == 200

    request = APIRequestFactory().post("/api/identity/userinfo")
    request.user = password_user
    serializer = ChangePasswordSerializer(
        password_user,
        data={"old_password": "Test@123456", "sure_password": "New@Password123"},
        context={"request": request},
    )
    assert serializer.is_valid(), serializer.errors
    serializer.save()

    password_user.refresh_from_db()
    assert password_user.check_password("New@Password123")
    assert jwt_client.get("/api/identity/userinfo").status_code == 401
    assert pat_client.get("/api/identity/userinfo").status_code == 401
    pat.refresh_from_db()
    assert pat.is_active is False

    # 直接走认证链复核：用户级失效时间戳已覆盖同秒签发的 access token
    auth_request = APIRequestFactory().get("/api/identity/userinfo", HTTP_AUTHORIZATION=f"Bearer {access}")
    with pytest.raises(AuthenticationFailed):
        CookieJWTAuthentication().authenticate(auth_request)
