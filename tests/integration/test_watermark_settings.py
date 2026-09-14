# -*- coding: utf-8 -*-
"""站点水印配置与下发。

- 基本设置接口（/api/settings/basic）读写三项水印配置；
- 用户信息接口把三项配置随 `config` 下发给前端（App.vue 按「生效页面」范围挂载）。

断言口径：读值取 `django.conf.settings` 同源值（BaseSettingViewSet.get_object 从
django settings 读取，值回写在生产上由 pubsub 订阅者执行，测试内手动 refresh_setting）。
"""

import pytest
from django.conf import settings as dj_settings

from settings.models import Setting

pytestmark = pytest.mark.django_db

BASIC_URL = "/api/settings/basic"
USERINFO_URL = "/api/system/userinfo"

WATERMARK_KEYS = (
    "FRONT_END_WEB_WATERMARK_ENABLED",
    "FRONT_END_WEB_WATERMARK_TEXT",
    "FRONT_END_WEB_WATERMARK_PATHS",
)


class TestBasicWatermarkSettings:
    def test_retrieve_returns_watermark_fields(self, auth_client):
        resp = auth_client.get(BASIC_URL)
        assert resp.status_code == 200
        assert resp.data["code"] == 1000
        data = resp.data["data"]
        for key in WATERMARK_KEYS:
            assert key in data, f"基本设置未暴露 {key}"
            assert data[key] == getattr(dj_settings, key)

    def test_partial_update_persists_and_applies(self, auth_client):
        payload = {
            "FRONT_END_WEB_WATERMARK_ENABLED": True,
            "FRONT_END_WEB_WATERMARK_TEXT": "内部资料",
            "FRONT_END_WEB_WATERMARK_PATHS": "/system/user/index,/system/role/index",
        }
        resp = auth_client.patch(BASIC_URL, payload)
        assert resp.status_code == 200
        assert resp.data["code"] == 1000

        for key, value in payload.items():
            setting = Setting.objects.filter(name=key, category="basic").first()
            assert setting is not None, f"未持久化 {key}"
            assert setting.cleaned_value == value

        # 生产上由 pubsub 订阅者执行；测试内直接模拟 worker 侧回写
        Setting.objects.get(name="FRONT_END_WEB_WATERMARK_PATHS", category="basic").refresh_setting()
        assert dj_settings.FRONT_END_WEB_WATERMARK_PATHS == payload["FRONT_END_WEB_WATERMARK_PATHS"]

    def test_paths_accept_blank(self, auth_client):
        resp = auth_client.patch(BASIC_URL, {"FRONT_END_WEB_WATERMARK_PATHS": ""})
        assert resp.status_code == 200
        assert resp.data["code"] == 1000
        setting = Setting.objects.filter(name="FRONT_END_WEB_WATERMARK_PATHS", category="basic").first()
        assert setting.cleaned_value == ""


class TestUserInfoWatermarkConfig:
    def test_userinfo_returns_watermark_config(self, auth_client):
        resp = auth_client.get(USERINFO_URL)
        assert resp.status_code == 200
        config = resp.data.get("config") or {}
        for key in WATERMARK_KEYS:
            assert key in config, f"用户信息接口未下发 {key}"
            assert config[key] == getattr(dj_settings, key)
