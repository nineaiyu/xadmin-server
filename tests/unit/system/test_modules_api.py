# -*- coding: utf-8 -*-
"""功能模块清单接口（/api/system/modules）单元测试 + 种子权限点守护。"""

import json
import os

import pytest
from django.conf import settings as dj_settings

from common.core.modules import CORE, all_module_specs

pytestmark = pytest.mark.django_db

MODULE_API_PATH = "api/system/modules$"
MODULE_MENU_PATH = "/system/module/index"


class TestSystemModuleApi:
    def test_list_returns_report(self, auth_client):
        payload = auth_client.get("/api/system/modules").json()
        assert payload["code"] == 1000
        data = payload["data"]
        assert data["preset"] == "full"
        assert data["total"] == len(all_module_specs())
        assert data["enabled_count"] == len(all_module_specs())
        assert data["disabled"] == []
        assert "MODULE_PRESET: full" in data["config_snippet"]
        assert data["docs"]

        by_id = {item["id"]: item for item in data["modules"]}
        assert by_id["core_rbac"]["level"] == CORE
        assert by_id["chat"]["enabled"] is True
        assert by_id["chat"]["label"]

    def test_preset_choices_carry_module_counts(self, auth_client):
        data = auth_client.get("/api/system/modules").json()["data"]
        counts = {item["value"]: item["enabled_count"] for item in data["presets"]}
        assert counts["full"] == len(all_module_specs())
        assert counts["core"] == sum(1 for spec in all_module_specs() if spec.level == CORE)
        assert counts["core"] < counts["standard"] < counts["full"]

    def test_list_reflects_disabled_modules(self, auth_client, module_config):
        module_config(disable=["chat"])
        data = auth_client.get("/api/system/modules").json()["data"]
        by_id = {item["id"]: item for item in data["modules"]}
        assert by_id["chat"]["enabled"] is False
        assert data["disabled"] == ["chat"]
        assert data["enabled_count"] == len(all_module_specs()) - 1
        assert "MODULE_DISABLE:\n  - chat" in data["config_snippet"]

    def test_requires_authentication(self, api_client):
        assert api_client.get("/api/system/modules").status_code in (401, 403)

    def test_unauthorized_user_denied(self, api_client, normal_user):
        """无该权限点的普通用户不可见模块清单（菜单权限链 fail-closed）。"""
        api_client.force_authenticate(user=normal_user)
        assert api_client.get("/api/system/modules").status_code == 403


class TestModuleSeedRegistration:
    """新页面/接口必须在种子里登记（否则新装库无菜单、无权限点可授）。"""

    @staticmethod
    def _seed(name):
        path = os.path.join(dj_settings.PROJECT_DIR, "loadjson", name)
        with open(path, encoding="utf-8") as fp:
            return json.load(fp)

    def test_menu_and_permission_registered(self):
        rows = self._seed("menu.json")
        perm = next((row for row in rows if row["fields"].get("path") == MODULE_API_PATH), None)
        assert perm is not None, "模块清单接口未在 menu.json 登记权限点"
        assert perm["fields"]["name"] == "list:SystemModule"
        assert perm["fields"]["method"] == "GET"
        assert perm["fields"]["menu_type"] == 2

        menu = next((row for row in rows if row["pk"] == perm["fields"]["parent"]), None)
        assert menu is not None, "权限点的父菜单缺失"
        assert menu["fields"]["name"] == "SystemModule"
        assert menu["fields"]["path"] == MODULE_MENU_PATH
        assert menu["fields"]["component"] == "system/module/index"

    def test_menu_meta_registered(self):
        menus = {row["pk"]: row["fields"] for row in self._seed("menu.json") if row["fields"]["name"] == "SystemModule"}
        assert menus, "菜单未登记"
        menu = next(iter(menus.values()))
        metas = {row["pk"]: row["fields"] for row in self._seed("menumeta.json")}
        assert metas[menu["meta"]]["title"] == "menus.moduleManager"
        assert metas[menu["meta"]]["icon"]
        # 权限点也要有自己的 meta（前端「权限预览」按 meta 展示）
        perm = next(row["fields"] for row in self._seed("menu.json") if row["fields"].get("path") == MODULE_API_PATH)
        assert metas[perm["meta"]]["title"]
