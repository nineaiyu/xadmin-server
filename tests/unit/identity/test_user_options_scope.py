# -*- coding: utf-8 -*-
"""用户候选数据源双口径守护：组织管理套数据权限 / 跨域功能选人显式豁免。"""

import pytest

from identity.models import UserInfo
from identity.utils.user_options import search_user_options

pytestmark = pytest.mark.django_db


class TestSearchUserOptions:
    def test_no_keyword_no_pks_returns_empty(self):
        """无关键字且无合法主键不枚举（通讯录全量枚举面收敛）。"""
        assert search_user_options() == []

    def test_functional_picker_without_user_searches_all(self):
        """跨域功能选人口径（不传 user）：按关键字可搜到全公司用户。"""
        UserInfo.objects.create_user(username="picker_target_a", password="Test@123456")
        UserInfo.objects.create_user(username="picker_target_b", password="Test@123456")
        results = search_user_options(keyword="picker_target")
        assert {item["username"] for item in results} == {"picker_target_a", "picker_target_b"}

    def test_org_manage_with_user_applies_data_scope(self, settings):
        """组织管理口径（传 user）：无数据权限授权的非超管 fail-closed 为空候选。"""
        from common.core.filter import get_filter_queryset

        settings.PERMISSION_DATA_ENABLED = True
        operator = UserInfo.objects.create_user(username="org_operator", password="Test@123456")
        UserInfo.objects.create_user(username="org_target", password="Test@123456")
        # 无 DataPermission 授权：get_filter_queryset 返回 none()（fail-closed），
        # 传 user 的组织管理口径候选同样为空——这是与 dept/post 页面一致的行为
        assert not list(get_filter_queryset(UserInfo.objects.filter(is_active=True), operator))
        assert search_user_options(keyword="org_target", user=operator) == []
        # 超管不受数据权限约束
        admin = UserInfo.objects.create_superuser(username="org_admin", email="a@example.com", password="Test@123456")
        assert len(search_user_options(keyword="org_target", user=admin)) == 1

    def test_result_fields_and_limit_capped(self):
        """字段收敛（pk/username/nickname）与 20 条上限。"""
        for i in range(25):
            UserInfo.objects.create_user(username=f"cap_user_{i:02d}", password="Test@123456")
        results = search_user_options(keyword="cap_user")
        assert len(results) == 20
        assert set(results[0].keys()) == {"pk", "username", "nickname"}
