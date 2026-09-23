# -*- coding: utf-8 -*-
"""表头排序元数据守护测试。

search-columns 的 ``sortable`` 必须与 ViewSet 的 ``ordering_fields`` 声明面同源：

- 声明字段（含 ``-`` 默认降序前缀）→ 下发 ``sortable=true``；
- 未声明字段 → 不下发（前端表头保持不可排序）；
- 未声明 ``ordering_fields`` 的视图集 → 全部不下发（页面零变化）；
- ``__all__`` → 全部字段可排序。
"""

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from system.views.admin.passkey import PasskeyViewSet
from system.views.admin.user import UserViewSet

pytestmark = pytest.mark.django_db


def _fetch(viewset_cls, url, superuser):
    request = APIRequestFactory().get(url)
    force_authenticate(request, user=superuser)
    response = viewset_cls.as_view({"get": "search_columns"})(request)
    assert response.status_code == 200, response.data
    return {item["key"]: item for item in response.data["data"]}


class TestSearchColumnsSortable:
    def test_declared_ordering_fields_are_sortable(self, superuser):
        """声明 ordering_fields 且落在序列化器字段面的列下发 sortable"""
        columns = _fetch(UserViewSet, "/api/system/user/search-columns", superuser)
        declared = [key for key in UserViewSet.ordering_fields if key in columns]
        assert declared, f"ordering_fields 与列无交集：{sorted(columns)}"
        for key in declared:
            assert columns[key].get("sortable") is True, key

    def test_undeclared_field_is_not_sortable(self, superuser):
        """未声明的展示列不带 sortable（username 在列表面板中但不在 ordering_fields）"""
        columns = _fetch(UserViewSet, "/api/system/user/search-columns", superuser)
        assert "username" in columns
        assert "sortable" not in columns["username"]

    def test_viewset_without_ordering_fields_has_no_sortable(self, superuser):
        """未声明 ordering_fields 的视图集整体不下发（前端零变化）"""
        columns = _fetch(PasskeyViewSet, "/api/passkeys/search-columns", superuser)
        assert columns
        assert all("sortable" not in item for item in columns.values())


class _PrefixedOrderingViewSet(UserViewSet):
    """`-` 前缀仅表默认方向，不影响该字段可排序"""

    ordering_fields = ["-date_joined", "username"]


class _AllOrderingViewSet(UserViewSet):
    ordering_fields = "__all__"


class TestSearchColumnsSortableDeclarations:
    def test_prefixed_ordering_field_is_sortable(self, superuser):
        columns = _fetch(_PrefixedOrderingViewSet, "/api/system/user/search-columns", superuser)
        assert columns["date_joined"].get("sortable") is True
        assert columns["username"].get("sortable") is True
        assert "sortable" not in columns["nickname"]
        assert "sortable" not in columns["last_login"]

    def test_all_ordering_fields_are_sortable(self, superuser):
        columns = _fetch(_AllOrderingViewSet, "/api/system/user/search-columns", superuser)
        readable = [item for item in columns.values() if not item.get("write_only")]
        assert readable
        assert all(item.get("sortable") is True for item in readable)
