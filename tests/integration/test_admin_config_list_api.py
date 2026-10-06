# -*- coding: utf-8 -*-
"""管理端配置列表接口：cache_value 整页批量预取语义。

列表页每行展示「生效值」（cache_value）。实现从逐行 get_value 改为整页
批量读取（缓存 get_many / DB key__in 各至多一次），本文件守护改后的逐行
值与逐行单读完全一致：命中行值、未激活行回退系统生效值、缺席返回 {}。
"""

import pytest

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
