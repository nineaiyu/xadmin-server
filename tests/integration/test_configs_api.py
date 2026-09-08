# -*- coding: utf-8 -*-
"""个人配置接口集成测试：partial_update 的合并语义。

接口约定（见 ConfigsViewSet.partial_update 注释）：只允许更新「既有键」，
防止客户端把任意未知配置写进库；新增配置字段必须先在
loadjson/systemconfig.json 的 WEB_SITE_CONFIG 默认值里登记。
"""

import pytest

from common.core.config import UserConfig

pytestmark = pytest.mark.django_db

CONFIG_BASE_URL = "/api/system/configs"


@pytest.fixture
def authed_client(api_client, normal_user):
    api_client.force_authenticate(user=normal_user)
    return api_client


@pytest.fixture
def seeded_platform_config(normal_user):
    # 与界面保存链路同参（is_active/access），否则 update_or_create 的 lookup
    # 含 access 维度会查不到既有行而触发 UNIQUE(owner, key)
    UserConfig(normal_user).set_value(
        "platform",
        {"Grey": False, "Weak": False, "HideTabs": False},
        is_active=True,
        access=True,
    )
    return "platform"


class TestPartialUpdateMerge:
    def test_existing_key_is_updated(self, authed_client, normal_user, seeded_platform_config):
        resp = authed_client.patch(
            f"{CONFIG_BASE_URL}/{seeded_platform_config}", data={"Grey": True}, format="json"
        )
        assert resp.status_code == 200
        config = UserConfig(normal_user).get_value(seeded_platform_config)
        assert config["Grey"] is True

    def test_existing_keys_not_in_request_are_preserved(
        self, authed_client, normal_user, seeded_platform_config
    ):
        resp = authed_client.patch(
            f"{CONFIG_BASE_URL}/{seeded_platform_config}", data={"Grey": True}, format="json"
        )
        assert resp.status_code == 200
        config = UserConfig(normal_user).get_value(seeded_platform_config)
        assert config["Weak"] is False
        assert config["HideTabs"] is False

    def test_unknown_keys_are_ignored(self, authed_client, normal_user, seeded_platform_config):
        # 防止存储未知配置：未在 WEB_SITE_CONFIG 默认值里登记的字段一律不落库
        resp = authed_client.patch(
            f"{CONFIG_BASE_URL}/{seeded_platform_config}",
            data={"UnknownField": "anything"},
            format="json",
        )
        assert resp.status_code == 200
        config = UserConfig(normal_user).get_value(seeded_platform_config)
        assert "UnknownField" not in config
