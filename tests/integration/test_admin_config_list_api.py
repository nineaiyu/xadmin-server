# -*- coding: utf-8 -*-
"""管理端配置列表接口：cache_value 整页批量预取语义。

列表页每行展示「生效值」（cache_value）。实现从逐行 get_value 改为整页
批量读取（缓存 get_many / DB key__in 各至多一次），本文件守护改后的逐行
值与逐行单读完全一致：命中行值、未激活行回退系统生效值、缺席返回 {}。
"""

import pytest

from common.core.auth.credentials import MASK, decrypt_setting_value, encrypt_setting_value
from common.core.config import SysConfig, UserConfig
from system.models import SystemConfig, UserPersonalConfig

pytestmark = pytest.mark.django_db

SYSTEM_URL = "/api/system/config/system"
USER_URL = "/api/system/config/user"

KEY = "ADMIN_LIST_PROBE"


@pytest.fixture
def admin_client(api_client, superuser):
    api_client.force_authenticate(user=superuser)
    return api_client


def _list_results(client, url):
    resp = client.get(url)
    assert resp.status_code == 200 and resp.data["code"] == 1000
    return resp.data["data"]["results"]


class TestSystemConfigListCacheValue:
    def test_cache_value_matches_single_read(self, admin_client):
        SystemConfig.objects.create(key=KEY, value={"n": 1}, inherit=True, is_active=True)
        SystemConfig.objects.create(key=f"{KEY}_OFF", value=2, inherit=True, is_active=False)

        rows = {row["key"]: row["cache_value"] for row in _list_results(admin_client, SYSTEM_URL)}

        assert rows[KEY] == SysConfig.get_value(KEY) == {"n": 1}
        # 未激活行不参与生效值：展示为 {}（与逐行读取同语义）
        assert rows[f"{KEY}_OFF"] == SysConfig.get_value(f"{KEY}_OFF") == {}


class TestUserConfigListCacheValue:
    def test_personal_inherit_and_inactive_rows_match_single_reads(self, admin_client, superuser, normal_user):
        SystemConfig.objects.create(key=KEY, value=7, inherit=True, is_active=True)
        UserConfig(normal_user).set_value(KEY, 100, is_active=True, access=True)
        # 未激活个人行：等同无个人行，回退系统生效值（ORM 直建触发信号清缓存）
        UserPersonalConfig.objects.create(owner=superuser, key=KEY, value=999, is_active=False)

        rows = _list_results(admin_client, f"{USER_URL}?username={normal_user.username}") + _list_results(
            admin_client, f"{USER_URL}?username={superuser.username}"
        )
        by_owner = {(row["owner"]["pk"], row["key"]): row["cache_value"] for row in rows}

        assert by_owner[(normal_user.pk, KEY)] == UserConfig(normal_user).get_value(KEY) == 100
        assert by_owner[(superuser.pk, KEY)] == UserConfig(superuser).get_value(KEY) == 7


class TestSensitiveCacheValueMasking:
    """敏感配置键的生效值不外发明文（P1：cache_value 曾回显解密后的凭据）。"""

    def test_whole_value_secret_masked(self, admin_client):
        SystemConfig.objects.create(
            key="SCIM_TOKEN",
            value=encrypt_setting_value("SCIM_TOKEN", "scim-plain-token"),
            inherit=True,
            is_active=True,
        )

        rows = {row["key"]: row["cache_value"] for row in _list_results(admin_client, SYSTEM_URL)}

        assert rows["SCIM_TOKEN"] == MASK
        # 掩码只作用于出口：服务端运行期仍能读到真实生效值
        assert SysConfig.get_value("SCIM_TOKEN") == "scim-plain-token"

    def test_field_level_secret_masked_only(self, admin_client):
        providers = [
            {"key": "feishu", "name": "飞书", "client_id": "cli_public", "client_secret": "sec-private"},
        ]
        SystemConfig.objects.create(
            key="OAUTH_PROVIDERS",
            value=encrypt_setting_value("OAUTH_PROVIDERS", providers),
            inherit=True,
            is_active=True,
        )

        rows = {row["key"]: row["cache_value"] for row in _list_results(admin_client, SYSTEM_URL)}

        masked = rows["OAUTH_PROVIDERS"][0]
        assert masked["client_secret"] == MASK
        # 非敏感字段照常展示（页面仍要看到 provider 名称 / client_id）
        assert masked["client_id"] == "cli_public"
        assert masked["name"] == "飞书"
        assert SysConfig.get_value("OAUTH_PROVIDERS")[0]["client_secret"] == "sec-private"

    def test_non_sensitive_value_unchanged(self, admin_client):
        SystemConfig.objects.create(key=KEY, value={"n": 1}, inherit=True, is_active=True)

        rows = {row["key"]: row["cache_value"] for row in _list_results(admin_client, SYSTEM_URL)}

        assert rows[KEY] == {"n": 1}

    def test_user_personal_override_masked(self, admin_client, normal_user):
        """个人配置页同源：敏感键的个人覆盖值同样掩码（P1-027）。"""
        UserPersonalConfig.objects.create(
            owner=normal_user,
            key="SCIM_TOKEN",
            value=encrypt_setting_value("SCIM_TOKEN", "personal-token"),
            is_active=True,
        )

        rows = _list_results(admin_client, f"{USER_URL}?username={normal_user.username}")
        row = next(item for item in rows if item["key"] == "SCIM_TOKEN")

        assert row["cache_value"] == MASK
        assert UserConfig(normal_user).get_value("SCIM_TOKEN") == "personal-token"


class TestSensitiveConfigEdit:
    """敏感键编辑值掩码：密文不外发；回传 MASK 表示「不修改」。"""

    def test_whole_value_masked_and_mask_keeps_existing(self, admin_client):
        row = SystemConfig.objects.create(
            key="SCIM_TOKEN",
            value=encrypt_setting_value("SCIM_TOKEN", "scim-token"),
            inherit=True,
            is_active=True,
        )

        detail = admin_client.get(f"{SYSTEM_URL}/{row.pk}")
        assert detail.status_code == 200
        assert detail.data["data"]["value"] == MASK

        resp = admin_client.patch(f"{SYSTEM_URL}/{row.pk}", {"value": MASK}, format="json")
        assert resp.status_code == 200 and resp.data["code"] == 1000
        row.refresh_from_db()
        assert decrypt_setting_value("SCIM_TOKEN", row.value) == "scim-token"

    def test_field_level_secret_masked_and_restored(self, admin_client):
        providers = [
            {
                "key": "feishu",
                "name": "飞书",
                "client_id": "cli_public",
                "client_secret": "sec-private",
                "authorize_url": "https://example.com/authorize",
                "token_url": "https://example.com/token",
                "userinfo_url": "https://example.com/userinfo",
            },
        ]
        row = SystemConfig.objects.create(
            key="OAUTH_PROVIDERS",
            value=encrypt_setting_value("OAUTH_PROVIDERS", providers),
            inherit=True,
            is_active=True,
        )

        detail = admin_client.get(f"{SYSTEM_URL}/{row.pk}")
        masked = detail.data["data"]["value"][0]
        assert masked["client_secret"] == MASK
        assert masked["client_id"] == "cli_public"

        # 回传掩码字段：用库内原值回填，改 client_id 不会把掩码加密落库
        resp = admin_client.patch(
            f"{SYSTEM_URL}/{row.pk}",
            {"value": [{**providers[0], "client_id": "cli_new", "client_secret": MASK}]},
            format="json",
        )
        assert resp.status_code == 200 and resp.data["code"] == 1000
        row.refresh_from_db()
        stored = decrypt_setting_value("OAUTH_PROVIDERS", row.value)[0]
        assert stored["client_id"] == "cli_new"
        assert stored["client_secret"] == "sec-private"


class TestUserConfigEndpointScope:
    def test_user_config_does_not_inherit_registered_keys(self, admin_client):
        resp = admin_client.get(f"{USER_URL}/registered-keys")
        assert resp.status_code == 200
        assert resp.data["code"] == 1001

    def test_batch_create_returns_first_created_row(self, admin_client, superuser, normal_user):
        resp = admin_client.post(
            USER_URL,
            {"key": "BATCH_PROBE", "value": 1, "config_user": [superuser.pk, normal_user.pk]},
            format="json",
        )
        assert resp.status_code == 200 and resp.data["code"] == 1000
        assert resp.data["data"]["owner"]["pk"] == superuser.pk
