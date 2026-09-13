# -*- coding: utf-8 -*-
"""角色页表单元数据契约测试（with_meta=1 内联 vs 独立接口）。

回归守护：角色新增/编辑弹层的表单列由 search-columns 元数据驱动，menu 列
（授权树）必须在列内且可写。若内联载荷误用 ListRoleSerializer（menu/field 为
只读 MethodField），前端 RePlusPage 会丢掉 menu 列并在弹层报
"Cannot set properties of undefined (setting 'fieldProps')"。
"""

import json

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.utils import encoders

from system.views.admin.role import RoleViewSet

pytestmark = pytest.mark.django_db


def _fetch(action_map, path, superuser):
    request = APIRequestFactory().get(path)
    force_authenticate(request, user=superuser)
    response = RoleViewSet.as_view(action_map)(request)
    assert response.status_code == 200, response.data
    return json.loads(json.dumps(response.data["data"], cls=encoders.JSONEncoder))


class TestRoleInlineMetadata:
    def test_inline_columns_include_menu_and_match_standalone(self, superuser):
        standalone = _fetch({"get": "search_columns"}, "/api/system/role/search-columns", superuser)
        inline = _fetch({"get": "list"}, "/api/system/role?with_meta=1", superuser)["search_columns"]

        assert inline == standalone
        read_only = {item["key"]: item.get("read_only", False) for item in inline}
        # 表单列口径：menu（授权树）/ fields（字段权限）必须可写
        assert read_only["menu"] is False
        assert read_only["fields"] is False
