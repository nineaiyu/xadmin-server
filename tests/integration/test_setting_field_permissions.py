# -*- coding: utf-8 -*-
"""基本/水印设置共用 PATCH 端点时的字段级权限隔离。

同一 ``path+method`` 登记了 ``partialUpdate:SettingBasic`` 与
``partialUpdate:SettingWatermark`` 两个权限码：运行时鉴权是 OR 语义（任一码即可
调用接口），因此字段面必须在保存前按提交字段所属权限码分别校验——否则只有水印
权限的角色可以改站点级开关，只有基本权限的角色可以改水印配置。
"""

import pytest
from django.core.cache import cache as django_cache

from settings.models import Setting

pytestmark = pytest.mark.django_db

BASIC_URL = "/api/settings/basic"


def _grant(role, menu_factory, *codes):
    for code in codes:
        menu = menu_factory(code, path="api/settings/basic$", method="PATCH")
        role.menu.add(menu)
    # 权限解析缓存 24h：授权变更后清空再取
    django_cache.clear()


def _auth(api_client, user):
    api_client.force_authenticate(user=user)
    return api_client


class TestSettingFieldPermissionIsolation:
    def test_watermark_only_cannot_update_site_url(self, api_client, normal_user, role, menu_factory):
        _grant(role, menu_factory, "partialUpdate:SettingWatermark")
        resp = _auth(api_client, normal_user).patch(BASIC_URL, {"SITE_URL": "https://evil.example.com"})
        assert resp.status_code == 400
        assert "SITE_URL" in str(resp.data)
        assert not Setting.objects.filter(name="SITE_URL").exists()

    def test_watermark_only_cannot_update_permission_switches(self, api_client, normal_user, role, menu_factory):
        _grant(role, menu_factory, "partialUpdate:SettingWatermark")
        resp = _auth(api_client, normal_user).patch(BASIC_URL, {"PERMISSION_FIELD_ENABLED": False})
        assert resp.status_code == 400
        assert "PERMISSION_FIELD_ENABLED" in str(resp.data)
        assert not Setting.objects.filter(name="PERMISSION_FIELD_ENABLED").exists()

    def test_watermark_only_can_update_watermark_fields(self, api_client, normal_user, role, menu_factory):
        _grant(role, menu_factory, "partialUpdate:SettingWatermark")
        resp = _auth(api_client, normal_user).patch(BASIC_URL, {"FRONT_END_WEB_WATERMARK_TEXT": "内部资料"})
        assert resp.status_code == 200, resp.data
        setting = Setting.objects.filter(name="FRONT_END_WEB_WATERMARK_TEXT", category="basic").first()
        assert setting is not None
        assert setting.cleaned_value == "内部资料"

    def test_basic_only_cannot_update_watermark_fields(self, api_client, normal_user, role, menu_factory):
        _grant(role, menu_factory, "partialUpdate:SettingBasic")
        resp = _auth(api_client, normal_user).patch(BASIC_URL, {"FRONT_END_WEB_WATERMARK_TEXT": "越权水印"})
        assert resp.status_code == 400
        assert "FRONT_END_WEB_WATERMARK_TEXT" in str(resp.data)
        assert not Setting.objects.filter(name="FRONT_END_WEB_WATERMARK_TEXT").exists()

    def test_basic_only_can_update_basic_fields(self, api_client, normal_user, role, menu_factory):
        _grant(role, menu_factory, "partialUpdate:SettingBasic")
        resp = _auth(api_client, normal_user).patch(BASIC_URL, {"SITE_URL": "https://xadmin.example.com/"})
        assert resp.status_code == 200, resp.data
        setting = Setting.objects.filter(name="SITE_URL").first()
        assert setting is not None
        assert setting.cleaned_value == "https://xadmin.example.com"

    def test_both_codes_allow_both_groups(self, api_client, normal_user, role, menu_factory):
        _grant(role, menu_factory, "partialUpdate:SettingBasic", "partialUpdate:SettingWatermark")
        resp = _auth(api_client, normal_user).patch(
            BASIC_URL,
            {"SITE_URL": "https://xadmin.example.com", "FRONT_END_WEB_WATERMARK_TEXT": "内部资料"},
        )
        assert resp.status_code == 200, resp.data
        assert Setting.objects.filter(name="SITE_URL").exists()
        assert Setting.objects.filter(name="FRONT_END_WEB_WATERMARK_TEXT").exists()

    def test_superuser_is_exempt(self, auth_client):
        resp = auth_client.patch(BASIC_URL, {"FRONT_END_WEB_WATERMARK_TEXT": "超管配置"})
        assert resp.status_code == 200, resp.data
