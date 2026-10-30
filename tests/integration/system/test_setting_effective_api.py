# -*- coding: utf-8 -*-
"""设置 tab GET/PUT 响应携带「实际生效状态」诊断的接口层守护。

诊断经 BaseSettingViewSet.get_readonly_values 钩子注入(SettingSaveContractMixin
序列化输出挂载),不声明为 serializer 字段——列元数据不得出现 effective,
否则会在设置表单渲染成一个 JSON 输入框。
"""

import pytest

pytestmark = pytest.mark.django_db

REGISTER_URL = "/api/settings/register/auth"
REGISTER_COLUMNS_URL = "/api/settings/register/auth/search-columns"


class TestRegisterAuthEffective:
    def test_retrieve_carries_effective(self, auth_client, settings):
        """注册 tab GET 响应带 effective;默认(通道全关)注册未生效且给出原因。"""
        settings.SECURITY_REGISTER_ACCESS_ENABLED = True
        resp = auth_client.get(REGISTER_URL)
        assert resp.status_code == 200, resp.data
        data = resp.data["data"]
        assert data["SECURITY_REGISTER_ACCESS_ENABLED"] is True
        (item,) = data["effective"]["items"]
        assert item["title"]
        assert item["enabled"] is False
        assert item["reasons"]

    def test_update_response_carries_effective(self, auth_client, settings):
        """PUT 保存后响应同样携带诊断(横幅随保存即时刷新,无需重进页面)。"""
        settings.SECURITY_REGISTER_ACCESS_ENABLED = True
        settings.SECURITY_REGISTER_BY_EMAIL_ENABLED = True
        settings.EMAIL_ENABLED = True
        resp = auth_client.put(REGISTER_URL, {"SECURITY_REGISTER_ACCESS_ENABLED": True}, format="json")
        assert resp.status_code == 200, resp.data
        (item,) = resp.data["data"]["effective"]["items"]
        assert item["enabled"] is True

    def test_columns_metadata_excludes_effective(self, auth_client):
        """effective 不进入列元数据(否则设置表单会渲染出一个 JSON 输入框)。"""
        resp = auth_client.get(REGISTER_COLUMNS_URL)
        assert resp.status_code == 200, resp.data
        columns = resp.data["data"]
        assert isinstance(columns, list) and columns, "列元数据不能为空"
        keys = {column.get("key") for column in columns}
        assert "effective" not in keys
