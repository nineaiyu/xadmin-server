# -*- coding: utf-8 -*-
"""已授权应用：个人视角 / 管理员审计 / 撤销边界（不误伤 client-credentials 凭证）。"""

import pytest

from identity.models import ApiApplication, OAuthRefreshToken, PersonalAccessToken

pytestmark = pytest.mark.django_db

MY_BASE = "/api/identity/my-authorized-applications"
ADMIN_BASE = "/api/identity/authorized-applications"


def make_application(creator, client_id="app_test"):
    return ApiApplication.objects.create(
        name="Test App",
        client_id=client_id,
        client_secret_hash="hash-" + client_id,
        client_secret_prefix=client_id[:8],
        creator=creator,
    )


def make_refresh(application, user, token_hash="refresh-hash"):
    return OAuthRefreshToken.objects.create(
        token_hash=token_hash,
        application=application,
        user=user,
        scopes=["GET ^/api/identity/user/?$"],
    )


def make_oauth_access(application, user, token_hash="access-hash"):
    return PersonalAccessToken.objects.create(
        name=f"oauth:{application.client_id}",
        token_hash=token_hash,
        token_prefix=token_hash[:8],
        api_application=application,
        creator=user,
        scopes=["GET ^/api/identity/user/?$"],
    )


class TestMyAuthorizations:
    def test_anonymous_rejected(self, api_client):
        assert api_client.get(MY_BASE).status_code == 401

    def test_lists_only_my_authorizations(self, api_client, superuser, normal_user):
        application = make_application(superuser)
        make_refresh(application, normal_user)
        make_oauth_access(application, normal_user)
        api_client.force_authenticate(user=normal_user)
        body = api_client.get(MY_BASE).json()
        assert body["data"]["total"] == 1
        row = body["data"]["results"][0]
        assert row["application"]["client_id"] == "app_test"
        assert row["user_count"] == 1
        assert row["refresh_token_count"] == 1
        assert row["access_token_count"] == 1

    def test_revoke_only_my_tokens(self, api_client, superuser, normal_user, role):
        from identity.models import UserInfo

        other = UserInfo.objects.create_user(username="other", password="Test@123456")
        other.roles.add(role)
        application = make_application(superuser)
        mine_refresh = make_refresh(application, normal_user, token_hash="mine-refresh")
        mine_access = make_oauth_access(application, normal_user, token_hash="mine-access")
        other_refresh = make_refresh(application, other, token_hash="other-refresh")
        app_token = PersonalAccessToken.objects.create(
            name=f"app:{application.client_id}",
            token_hash="app-token",
            token_prefix="app",
            api_application=application,
            creator=superuser,
        )
        api_client.force_authenticate(user=normal_user)
        response = api_client.post(f"{MY_BASE}/{application.pk}/revoke")
        assert response.json()["code"] == 1000, response.data
        mine_refresh.refresh_from_db()
        mine_access.refresh_from_db()
        other_refresh.refresh_from_db()
        app_token.refresh_from_db()
        assert mine_refresh.is_revoked is True
        assert mine_access.is_active is False
        assert other_refresh.is_revoked is False
        assert app_token.is_active is True


class TestAdminAuthorizations:
    def test_normal_user_rejected(self, api_client, normal_user):
        api_client.force_authenticate(user=normal_user)
        assert api_client.get(ADMIN_BASE).status_code == 403

    def test_admin_lists_all_and_revokes(self, auth_client, superuser, normal_user):
        application = make_application(superuser)
        refresh = make_refresh(application, normal_user)
        access = make_oauth_access(application, normal_user)
        body = auth_client.get(ADMIN_BASE).json()
        assert body["data"]["total"] == 1
        assert body["data"]["results"][0]["user_count"] == 1
        response = auth_client.post(f"{ADMIN_BASE}/{application.pk}/revoke")
        assert response.json()["code"] == 1000, response.data
        refresh.refresh_from_db()
        access.refresh_from_db()
        assert refresh.is_revoked is True
        assert access.is_active is False
