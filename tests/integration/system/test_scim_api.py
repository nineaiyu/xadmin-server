# -*- coding: utf-8 -*-
"""S1 SCIM 2.0 用户目录同步：鉴权 / 能力声明 / Users / Groups / 审计 / 限流。

覆盖 RFC 7644 子集的核心契约：独立 Bearer Token（与 JWT/PAT 分离）、
filter 子集、PATCH 停用即踢会话、Group ↔ UserRole 成员同步、写操作审计。
"""

import pytest

from common.cache.storage import UserTokenRevokedCache
from common.core.config import SysConfig
from system.models import OperationLog, UserInfo, UserRole

pytestmark = pytest.mark.django_db

SCIM_BASE = "/api/scim/v2"
TOKEN = "scim-secret-token"
AUTH = {"HTTP_AUTHORIZATION": f"Bearer {TOKEN}"}
USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"


@pytest.fixture
def scim_enabled():
    SysConfig.set_value("SCIM_ENABLED", True)
    SysConfig.set_value("SCIM_TOKEN", TOKEN)
    return TOKEN


class TestScimAuthAndConfig:
    def test_disabled_by_default(self, api_client):
        """默认休眠：即使带正确格式的令牌也 403（避免未配置环境暴露目录同步面）。"""
        SysConfig.set_value("SCIM_TOKEN", TOKEN)
        response = api_client.get(f"{SCIM_BASE}/Users", **AUTH)
        assert response.status_code == 403
        assert response.json()["schemas"][-1].endswith("Error")

    def test_missing_and_invalid_token(self, api_client, scim_enabled):
        assert api_client.get(f"{SCIM_BASE}/Users").status_code == 401
        bad = {"HTTP_AUTHORIZATION": "Bearer wrong-token"}
        assert api_client.get(f"{SCIM_BASE}/Users", **bad).status_code == 401

    def test_business_user_token_denied(self, api_client, scim_enabled, superuser):
        """JWT 登录态不能访问 SCIM（凭证体系分离）。

        force_authenticate 会跳过认证器，此时由权限类拒绝（403）；真实 JWT 请求
        走认证器无 Bearer 头 → 401。两种拒绝都是"业务身份不得访问 SCIM"。
        """
        api_client.force_authenticate(user=superuser)
        assert api_client.get(f"{SCIM_BASE}/Users").status_code in (401, 403)

    def test_service_provider_and_schemas(self, api_client, scim_enabled):
        config = api_client.get(f"{SCIM_BASE}/ServiceProviderConfig", **AUTH)
        assert config.status_code == 200
        assert config.json()["patch"]["supported"] is True
        assert config.json()["bulk"]["supported"] is False

        schemas = api_client.get(f"{SCIM_BASE}/Schemas", **AUTH).json()
        assert schemas["schemas"] == [LIST_SCHEMA]
        assert {item["id"] for item in schemas["Resources"]} == {
            USER_SCHEMA,
            "urn:ietf:params:scim:schemas:core:2.0:Group",
        }

        types = api_client.get(f"{SCIM_BASE}/ResourceTypes", **AUTH).json()
        assert {item["id"] for item in types["Resources"]} == {"User", "Group"}

    def test_rate_limit(self, api_client, scim_enabled):
        """凭证级限流：1/min 下第二次请求 429。"""
        SysConfig.set_value("SCIM_RATE_LIMIT", "1/min")
        assert api_client.get(f"{SCIM_BASE}/Users", **AUTH).status_code == 200
        assert api_client.get(f"{SCIM_BASE}/Users", **AUTH).status_code == 429


class TestScimUsers:
    def test_create_and_list_and_filter(self, api_client, scim_enabled):
        response = api_client.post(
            f"{SCIM_BASE}/Users",
            {
                "schemas": [USER_SCHEMA],
                "userName": "scim_alice",
                "displayName": "Alice",
                "emails": [{"value": "alice@example.com", "primary": True}],
                "phoneNumbers": [{"value": "13800000000"}],
            },
            format="json",
            **AUTH,
        )
        assert response.status_code == 201
        payload = response.json()
        assert payload["userName"] == "scim_alice"
        assert payload["active"] is True
        assert payload["emails"][0]["value"] == "alice@example.com"
        user = UserInfo.objects.get(username="scim_alice")
        # 未下发密码 → 不可用密码（只能经身份联邦登录）
        assert not user.has_usable_password()

        # 重复 userName → 409 uniqueness
        duplicate = api_client.post(f"{SCIM_BASE}/Users", {"userName": "scim_alice"}, format="json", **AUTH)
        assert duplicate.status_code == 409
        assert duplicate.json()["scimType"] == "uniqueness"

        # filter=userName eq "..." 命中；不支持的 filter 表达式 → invalidFilter
        listed = api_client.get(f'{SCIM_BASE}/Users?filter=userName eq "scim_alice"', **AUTH).json()
        assert listed["totalResults"] == 1
        assert listed["Resources"][0]["userName"] == "scim_alice"
        bad_filter = api_client.get(f'{SCIM_BASE}/Users?filter=userName co "alice"', **AUTH)
        assert bad_filter.status_code == 400
        assert bad_filter.json()["scimType"] == "invalidFilter"

    def test_get_put_and_patch(self, api_client, scim_enabled):
        api_client.post(f"{SCIM_BASE}/Users", {"userName": "scim_bob", "displayName": "Bob"}, format="json", **AUTH)
        user = UserInfo.objects.get(username="scim_bob")

        detail = api_client.get(f"{SCIM_BASE}/Users/{user.pk}", **AUTH)
        assert detail.status_code == 200
        assert detail.json()["id"] == str(user.pk)

        replaced = api_client.put(
            f"{SCIM_BASE}/Users/{user.pk}",
            {
                "userName": "scim_bob2",
                "displayName": "Bob Two",
                "emails": [{"value": "bob@example.com"}],
                "active": True,
            },
            format="json",
            **AUTH,
        )
        assert replaced.status_code == 200
        user.refresh_from_db()
        assert user.username == "scim_bob2" and user.email == "bob@example.com"

        patched = api_client.patch(
            f"{SCIM_BASE}/Users/{user.pk}",
            {
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                "Operations": [
                    {"op": "replace", "path": "displayName", "value": "Bob Three"},
                    {"op": "replace", "path": "phoneNumbers", "value": [{"value": "13900000000"}]},
                ],
            },
            format="json",
            **AUTH,
        )
        assert patched.status_code == 200
        user.refresh_from_db()
        assert user.nickname == "Bob Three" and user.phone == "13900000000"

    def test_patch_deactivate_revokes_sessions(self, api_client, scim_enabled):
        """停用即失效：active=false 触发用户级令牌失效时间戳（与强制下线同链路）。"""
        api_client.post(f"{SCIM_BASE}/Users", {"userName": "scim_carol"}, format="json", **AUTH)
        user = UserInfo.objects.get(username="scim_carol")

        response = api_client.patch(
            f"{SCIM_BASE}/Users/{user.pk}",
            {"Operations": [{"op": "replace", "path": "active", "value": False}]},
            format="json",
            **AUTH,
        )
        assert response.status_code == 200
        user.refresh_from_db()
        assert user.is_active is False
        assert UserTokenRevokedCache(user.pk).get_storage_cache() is not None

    def test_delete_deactivates(self, api_client, scim_enabled):
        """DELETE 按 RFC 7644 §3.6 语义为停用（保留审计与历史数据）。"""
        api_client.post(f"{SCIM_BASE}/Users", {"userName": "scim_dave"}, format="json", **AUTH)
        user = UserInfo.objects.get(username="scim_dave")
        assert api_client.delete(f"{SCIM_BASE}/Users/{user.pk}", **AUTH).status_code == 204
        user.refresh_from_db()
        assert user.is_active is False

    def test_not_found_and_write_audit(self, api_client, scim_enabled):
        assert api_client.get(f"{SCIM_BASE}/Users/999999", **AUTH).status_code == 404
        api_client.post(f"{SCIM_BASE}/Users", {"userName": "scim_erin"}, format="json", **AUTH)
        log = OperationLog.objects.filter(auth_type=OperationLog.AuthType.SCIM).order_by("-created_time").first()
        assert log is not None
        assert log.module == "SCIM:create"
        assert log.path.endswith("/Users")
        # 审计绝不落请求体（可能含密码）
        assert not log.body


class TestScimGroups:
    def test_group_lifecycle(self, api_client, scim_enabled):
        api_client.post(f"{SCIM_BASE}/Users", {"userName": "scim_member"}, format="json", **AUTH)
        member = UserInfo.objects.get(username="scim_member")

        created = api_client.post(
            f"{SCIM_BASE}/Groups",
            {"displayName": "SCIM Team", "externalId": "scim_team", "members": [{"value": str(member.pk)}]},
            format="json",
            **AUTH,
        )
        assert created.status_code == 201
        payload = created.json()
        assert payload["displayName"] == "SCIM Team" and payload["externalId"] == "scim_team"
        assert payload["members"][0]["value"] == str(member.pk)
        assert UserRole.objects.filter(code="scim_team").exists()
        assert member.roles.filter(code="scim_team").exists()

        # filter=displayName eq / externalId eq
        listed = api_client.get(f'{SCIM_BASE}/Groups?filter=displayName eq "SCIM Team"', **AUTH).json()
        assert listed["totalResults"] == 1

        # PATCH：增成员（追加语义）
        other = UserInfo.objects.create_user(username="scim_member2", password="Test@123456")
        patched = api_client.patch(
            f"{SCIM_BASE}/Groups/{payload['id']}",
            {"Operations": [{"op": "add", "path": "members", "value": [{"value": str(other.pk)}]}]},
            format="json",
            **AUTH,
        )
        assert patched.status_code == 200
        assert len(patched.json()["members"]) == 2

        # PATCH：remove 成员
        removed = api_client.patch(
            f"{SCIM_BASE}/Groups/{payload['id']}",
            {"Operations": [{"op": "remove", "path": f'members[value eq "{other.pk}"]'}]},
            format="json",
            **AUTH,
        )
        assert removed.status_code == 200
        assert len(removed.json()["members"]) == 1

        # 组删除
        assert api_client.delete(f"{SCIM_BASE}/Groups/{payload['id']}", **AUTH).status_code == 204
        assert not UserRole.objects.filter(code="scim_team").exists()
