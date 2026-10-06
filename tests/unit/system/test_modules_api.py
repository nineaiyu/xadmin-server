# -*- coding: utf-8 -*-
"""功能模块清单接口（/api/system/modules）单元测试 + 种子权限点守护。"""

import contextlib
import json
import os
import threading

import pytest
from django.conf import settings as dj_settings
from django.utils.translation import gettext as _
from django_redis import get_redis_connection

from common.cache.lock import LOCK_KEY_PREFIX, ReentrantLock
from common.core.modules import CORE, all_module_specs, override_active
from system.models import ModuleOverride
from system.views.platform.modules import OVERRIDE_WRITE_LOCK_NAME

pytestmark = pytest.mark.django_db

MODULE_API_PATH = "api/system/modules$"
MODULE_APPLY_PATH = "api/system/modules/apply$"
MODULE_RESET_PATH = "api/system/modules/reset$"
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


class TestSystemModuleWriteApi:
    """后台覆盖写入：校验同启动口径、保存不热更新、可恢复部署配置。"""

    @staticmethod
    def _apply(client, **payload):
        return client.post("/api/system/modules/apply", payload, format="json")

    def test_apply_persists_without_hot_reload(self, auth_client, module_config):
        module_config(preset="full")

        resp = self._apply(auth_client, preset="standard", enable=[], disable=[])
        assert resp.status_code == 200
        data = resp.json()["data"]

        assert data["override_active"] is True
        assert data["desired"]["preset"] == "standard"
        # 生效态不变（装配期语义，重启才生效）
        assert data["preset"] == "full"
        assert data["pending"] is True
        assert "chat" in data["diff"]["disable"]
        assert data["diff"]["preset_changed"] is True
        assert data["restart_command"]
        assert ModuleOverride.objects.filter(key="module").exists()

    def test_apply_rejects_unknown_module(self, auth_client):
        resp = self._apply(auth_client, preset="full", disable=["not-exist"])
        assert resp.status_code == 400
        assert "未知模块" in resp.json()["detail"]

    def test_apply_rejects_core_disable(self, auth_client):
        resp = self._apply(auth_client, preset="full", disable=["core_rbac"])
        assert resp.status_code == 400
        assert "内核模块不可关闭" in resp.json()["detail"]

    def test_apply_rejects_invalid_preset(self, auth_client):
        assert self._apply(auth_client, preset="huge").status_code == 400

    def test_reset_clears_override(self, auth_client, module_override):
        module_override(preset="standard")
        assert override_active() is True

        resp = auth_client.post("/api/system/modules/reset", {}, format="json")
        assert resp.status_code == 200
        assert resp.json()["data"]["override_active"] is False
        assert override_active() is False

    def test_normal_user_cannot_apply(self, api_client, normal_user):
        api_client.force_authenticate(user=normal_user)
        assert self._apply(api_client, preset="full").status_code == 403

    def test_normal_user_cannot_reset(self, api_client, normal_user):
        api_client.force_authenticate(user=normal_user)
        assert api_client.post("/api/system/modules/reset", {}, format="json").status_code == 403


class TestSystemModuleWriteConcurrency:
    """覆盖写入互斥：「校验 → 落库 → 回显」持锁串行，竞争方快速失败。

    持锁方放在独立线程：ReentrantLock 的可重入计数挂在 thread-local 上，
    测试线程直接持锁会让同线程内的视图请求重入成功，验证不了互斥。
    """

    LOCK_KEY = LOCK_KEY_PREFIX + OVERRIDE_WRITE_LOCK_NAME
    CONFLICT_MSGID = "Another module configuration save is in progress, please try again later"

    @pytest.fixture(autouse=True)
    def _clean_override_lock(self):
        """裸 redis 锁键不在 conftest _clean_cache 范围内（只清带前缀的 django cache），前后兜底清除。"""
        conn = get_redis_connection("default")
        conn.delete(self.LOCK_KEY)
        yield
        conn.delete(self.LOCK_KEY)

    @contextlib.contextmanager
    def _hold_write_lock_in_other_thread(self):
        """独立线程持有写入锁（模拟另一 worker 正在保存），退出时释放。"""
        acquired = threading.Event()
        release = threading.Event()

        def _hold():
            holder = ReentrantLock(OVERRIDE_WRITE_LOCK_NAME, timeout=30)
            assert holder.acquire(blocking=False)
            acquired.set()
            release.wait(timeout=10)
            holder.release()

        thread = threading.Thread(target=_hold)
        thread.start()
        assert acquired.wait(timeout=10)
        try:
            yield
        finally:
            release.set()
            thread.join(timeout=10)

    def _apply(self, client, **payload):
        return client.post("/api/system/modules/apply", payload, format="json")

    def test_apply_conflicts_while_another_save_in_progress(self, auth_client):
        with self._hold_write_lock_in_other_thread():
            resp = self._apply(auth_client, preset="standard")
        assert resp.status_code == 400
        # 文案断言与产文同源取 gettext（本机装 .mo 为中文、CI 无 .mo 为英文源串）
        assert resp.json()["detail"] == _(self.CONFLICT_MSGID)
        # 快速失败而非交错落库：冲突请求不产生覆盖行
        assert not ModuleOverride.objects.filter(key="module").exists()
        # 持锁方释放后重试成功：锁不粘滞
        resp = self._apply(auth_client, preset="standard")
        assert resp.status_code == 200
        assert resp.json()["data"]["override_active"] is True

    def test_reset_conflicts_while_another_save_in_progress(self, auth_client, module_override):
        module_override(preset="standard")
        with self._hold_write_lock_in_other_thread():
            resp = auth_client.post("/api/system/modules/reset", {}, format="json")
        assert resp.status_code == 400
        assert resp.json()["detail"] == _(self.CONFLICT_MSGID)
        # 与 apply 互斥：持锁窗口内覆盖行未被清除
        assert ModuleOverride.objects.filter(key="module").exists()

    def test_validation_failure_keeps_lock_free(self, auth_client):
        resp = self._apply(auth_client, preset="full", disable=["not-exist"])
        assert resp.status_code == 400
        # 校验失败不遗留锁占用：redis 锁键已释放，后续保存可立即进行
        assert not get_redis_connection("default").exists(self.LOCK_KEY)
        resp = self._apply(auth_client, preset="standard")
        assert resp.status_code == 200

    def test_apply_success_response_structure_unchanged(self, auth_client, module_config):
        module_config(preset="full")
        list_data = auth_client.get("/api/system/modules").json()["data"]
        apply_data = self._apply(auth_client, preset="standard").json()["data"]
        # 成功路径回显由同一 build_payload 产出：键集合与 list 完全一致，语义字段不变
        assert set(apply_data.keys()) == set(list_data.keys())
        assert apply_data["override_active"] is True
        assert apply_data["desired"]["preset"] == "standard"
        assert apply_data["preset"] == "full"
        assert apply_data["pending"] is True


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

    @pytest.mark.parametrize(
        "path,name",
        [(MODULE_APPLY_PATH, "apply:SystemModule"), (MODULE_RESET_PATH, "reset:SystemModule")],
    )
    def test_write_permissions_registered(self, path, name):
        rows = self._seed("menu.json")
        perm = next((row for row in rows if row["fields"].get("path") == path), None)
        assert perm is not None, f"{name} 未在 menu.json 登记权限点"
        assert perm["fields"]["name"] == name
        assert perm["fields"]["method"] == "POST"
        assert perm["fields"]["menu_type"] == 2
        assert perm["fields"]["parent"] == "3e48aac3-b1b8-4f5d-85b6-7ceac64ce2f0"

        metas = {row["pk"]: row["fields"] for row in self._seed("menumeta.json")}
        assert metas[perm["fields"]["meta"]]["title"]

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
