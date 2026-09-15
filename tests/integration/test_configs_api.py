# -*- coding: utf-8 -*-
"""个人配置接口集成测试：partial_update 的自服务语义。

接口约定（见 ConfigsViewSet.partial_update）：
1. 只允许写自服务白名单键（SELF_WRITABLE_CONFIG_KEYS，个人偏好类），
   配额/限流等键由管理员在「用户配置」管理页调整，防止用户自提配额；
2. dict 键只允许更新「既有键」，防止客户端把任意未知配置写进库；
   新增配置字段必须先在 loadjson/systemconfig.json 的 WEB_SITE_CONFIG
   默认值里登记；
3. 未知键（无个人行且无可继承系统行）显式返回 1001，不再静默成功。
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
        "WEB_SITE_CONFIG",
        {"Grey": False, "Weak": False, "HideTabs": False},
        is_active=True,
        access=True,
    )
    return "WEB_SITE_CONFIG"


class TestPartialUpdateMerge:
    def test_existing_key_is_updated(self, authed_client, normal_user, seeded_platform_config):
        resp = authed_client.patch(f"{CONFIG_BASE_URL}/{seeded_platform_config}", data={"Grey": True}, format="json")
        assert resp.status_code == 200
        config = UserConfig(normal_user).get_value(seeded_platform_config)
        assert config["Grey"] is True

    def test_existing_keys_not_in_request_are_preserved(self, authed_client, normal_user, seeded_platform_config):
        resp = authed_client.patch(f"{CONFIG_BASE_URL}/{seeded_platform_config}", data={"Grey": True}, format="json")
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


class TestSelfServiceGuard:
    def test_unknown_key_rejected_explicitly(self, authed_client, normal_user):
        """未知键（无个人行且无可继承系统行）返回 1001，不再静默 200。"""
        UserConfig(normal_user).get_value("WEB_SITE_CONFIG")  # 确认无个人行
        resp = authed_client.patch(f"{CONFIG_BASE_URL}/not_a_config_key", data={"x": 1}, format="json")
        assert resp.data["code"] == 1001

    def test_non_whitelisted_key_rejected(self, authed_client, normal_user):
        """配额类键即使存在个人行也不允许自服务改写（防自提配额）。"""
        UserConfig(normal_user).set_value("FILE_STORAGE_QUOTA_MB", 5, is_active=True, access=True)
        resp = authed_client.patch(f"{CONFIG_BASE_URL}/FILE_STORAGE_QUOTA_MB", data=999, format="json")
        assert resp.data["code"] == 1001
        assert UserConfig(normal_user).get_value("FILE_STORAGE_QUOTA_MB") == 5

    def test_whitelisted_scalar_key_writable(self, authed_client, normal_user):
        """白名单内的标量偏好键（布尔 False 是合法值）可整包写入。"""
        UserConfig(normal_user).set_value("PUSH_MESSAGE_NOTICE", True, is_active=True, access=True)
        resp = authed_client.patch(f"{CONFIG_BASE_URL}/PUSH_MESSAGE_NOTICE", data=False, format="json")
        assert resp.status_code == 200
        assert UserConfig(normal_user).get_value("PUSH_MESSAGE_NOTICE") is False


class TestSplitPanesSync:
    """分栏宽度持久化（SplitPanes 子键：{页面标识: 左栏百分比}，前端整包读写）。"""

    @pytest.fixture
    def seeded_site_config(self):
        """测试库不跑 load_init_json，种一条与种子一致的 WEB_SITE_CONFIG 系统行。"""
        from common.core.config import SysConfig

        SysConfig.set_value("WEB_SITE_CONFIG", {"SplitPanes": {}}, is_active=True, access=True)

    def test_split_panes_dict_update_roundtrip(self, authed_client, normal_user, seeded_site_config):
        # 个人行不存在：继承系统默认（SplitPanes={} 空占位），PATCH 单键整包替换
        resp = authed_client.patch(
            f"{CONFIG_BASE_URL}/WEB_SITE_CONFIG",
            data={"SplitPanes": {"system/user": 24.5}},
            format="json",
        )
        assert resp.status_code == 200
        assert resp.data["config"]["SplitPanes"] == {"system/user": 24.5}
        # 再次更新另一页面：既有页面比例保留（整包读改写后回传）
        resp = authed_client.patch(
            f"{CONFIG_BASE_URL}/WEB_SITE_CONFIG",
            data={"SplitPanes": {"system/user": 24.5, "system/menu": 54}},
            format="json",
        )
        assert resp.status_code == 200
        assert resp.data["config"]["SplitPanes"] == {"system/user": 24.5, "system/menu": 54}
        # 落库为个人行，可重复读取
        assert UserConfig(normal_user).get_value("WEB_SITE_CONFIG")["SplitPanes"]["system/menu"] == 54
