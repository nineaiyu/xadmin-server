# -*- coding: utf-8 -*-
"""强制下线（在线用户 / 登录日志）的超管保护。

非超管持有强制下线权限时也不能踢超级管理员会话；批量入口逐用户过滤并在
failures 里回传被拒目标，不能因一个超管目标让整批不可用或静默跳过。
"""

import pytest
from django.core.cache import cache as django_cache

from audit.models import UserLoginLog
from identity.models import UserInfo, UserSession
from system.models import DataPermission

pytestmark = pytest.mark.django_db

ONLINE_DETAIL_PATH = r"api/identity/online/(?P<pk>[^/.]+)$"
ONLINE_BATCH_DESTROY_PATH = "api/identity/online/batch-destroy$"
ONLINE_BATCH_FORCE_PATH = "api/identity/online/batch-force-logout$"
LOGIN_LOG_LOGOUT_PATH = r"api/audit/logs/login/(?P<pk>[^/.]+)/logout$"


@pytest.fixture
def admin_target(db):
    return UserInfo.objects.create_superuser(
        username="protect_admin", email="protect_admin@example.com", password="Test@123456"
    )


@pytest.fixture
def normal_target(db):
    return UserInfo.objects.create_user(username="protect_user", password="Test@123456")


def grant(role, menu_factory, name, path, method):
    role.menu.add(menu_factory(name, path=path, method=method))
    # 权限解析缓存 24h：授权变更后清空
    django_cache.clear()


def make_session(user):
    return UserSession.objects.create(creator=user, channel_name="")


def grant_all_data(user, table):
    """数据权限 fail-closed：非超管需显式授予全部数据才能看到会话 / 登录日志行。"""
    user.rules.add(
        DataPermission.objects.create(
            name=f"protect-{table}",
            rules=[{"table": table, "field": "id", "type": "value.all", "match": "all", "value": "", "exclude": False}],
        )
    )
    django_cache.clear()


class TestOnlineForceLogoutProtection:
    def test_non_superuser_cannot_kick_superuser_session(
        self, api_client, normal_user, role, menu_factory, admin_target
    ):
        grant(role, menu_factory, "protect-online-delete", ONLINE_DETAIL_PATH, "DELETE")
        grant_all_data(normal_user, "identity.usersession")
        session = make_session(admin_target)

        api_client.force_authenticate(user=normal_user)
        resp = api_client.delete(f"/api/identity/online/{session.pk}")
        assert resp.status_code == 403
        session.refresh_from_db()
        assert session.status == UserSession.Status.ONLINE

    def test_superuser_can_kick_superuser_session(self, auth_client, admin_target):
        session = make_session(admin_target)
        resp = auth_client.delete(f"/api/identity/online/{session.pk}")
        assert resp.data["code"] == 1000
        session.refresh_from_db()
        assert session.status == UserSession.Status.OFFLINE

    def test_batch_force_logout_filters_superuser_targets(
        self, api_client, normal_user, role, menu_factory, admin_target, normal_target
    ):
        grant(role, menu_factory, "protect-online-batch-force", ONLINE_BATCH_FORCE_PATH, "POST")
        grant_all_data(normal_user, "identity.usersession")
        admin_session = make_session(admin_target)
        normal_session = make_session(normal_target)

        api_client.force_authenticate(user=normal_user)
        resp = api_client.post(
            "/api/identity/online/batch-force-logout",
            [str(admin_session.pk), str(normal_session.pk)],
            format="json",
        )
        assert resp.data["code"] == 1000
        assert resp.data["data"]["users"] == 1
        assert [item["pk"] for item in resp.data["data"]["failures"]] == [str(admin_target.pk)]
        admin_session.refresh_from_db()
        normal_session.refresh_from_db()
        assert admin_session.status == UserSession.Status.ONLINE
        assert normal_session.status == UserSession.Status.OFFLINE

    def test_batch_destroy_reports_superuser_failure(
        self, api_client, normal_user, role, menu_factory, admin_target, normal_target
    ):
        grant(role, menu_factory, "protect-online-batch-destroy", ONLINE_BATCH_DESTROY_PATH, "POST")
        grant_all_data(normal_user, "identity.usersession")
        admin_session = make_session(admin_target)
        normal_session = make_session(normal_target)

        api_client.force_authenticate(user=normal_user)
        resp = api_client.post(
            "/api/identity/online/batch-destroy",
            [str(admin_session.pk), str(normal_session.pk)],
            format="json",
        )
        assert resp.data["code"] == 1000
        failures = resp.data["data"]["failures"]
        assert str(admin_session.pk) in [item["pk"] for item in failures]
        admin_session.refresh_from_db()
        normal_session.refresh_from_db()
        assert admin_session.status == UserSession.Status.ONLINE
        assert normal_session.status == UserSession.Status.OFFLINE


class TestLoginLogForceLogoutProtection:
    def test_non_superuser_cannot_kick_superuser(self, api_client, normal_user, role, menu_factory, admin_target):
        grant(role, menu_factory, "protect-login-logout", LOGIN_LOG_LOGOUT_PATH, "POST")
        grant_all_data(normal_user, "audit.userloginlog")
        log = UserLoginLog.objects.create(creator=admin_target, channel_name="ch-protect")

        api_client.force_authenticate(user=normal_user)
        resp = api_client.post(f"/api/audit/logs/login/{log.pk}/logout")
        assert resp.status_code == 403

    def test_superuser_can_kick_superuser(self, auth_client, admin_target):
        log = UserLoginLog.objects.create(creator=admin_target, channel_name="ch-protect")
        resp = auth_client.post(f"/api/audit/logs/login/{log.pk}/logout")
        assert resp.data["code"] == 1000
