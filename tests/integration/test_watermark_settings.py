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
    "FRONT_END_WEB_WATERMARK_FONT_SIZE",
    "FRONT_END_WEB_WATERMARK_OPACITY",
    "FRONT_END_WEB_WATERMARK_ROTATE",
    "FRONT_END_WEB_WATERMARK_COLOR",
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

    def test_paths_normalizes_chinese_comma_and_blank(self, auth_client):
        """中文逗号/空白分隔的路径在保存期归一化为英文逗号列表。"""
        resp = auth_client.patch(BASIC_URL, {"FRONT_END_WEB_WATERMARK_PATHS": "/a/b ， /c/d，"})
        assert resp.status_code == 200
        assert resp.data["code"] == 1000
        setting = Setting.objects.filter(name="FRONT_END_WEB_WATERMARK_PATHS", category="basic").first()
        assert setting.cleaned_value == "/a/b,/c/d"

    def test_paths_rejects_entries_without_leading_slash(self, auth_client):
        """无效路径（非路由前缀）保存期直接拒绝：避免水印因范围匹配不上而静默失效。"""
        resp = auth_client.patch(BASIC_URL, {"FRONT_END_WEB_WATERMARK_PATHS": "是大丰收的"})
        assert resp.status_code == 400

    def test_style_fields_persist(self, auth_client):
        payload = {
            "FRONT_END_WEB_WATERMARK_FONT_SIZE": 24,
            "FRONT_END_WEB_WATERMARK_OPACITY": 0.2,
            "FRONT_END_WEB_WATERMARK_ROTATE": -30,
        }
        resp = auth_client.patch(BASIC_URL, payload)
        assert resp.status_code == 200
        assert resp.data["code"] == 1000
        for key, value in payload.items():
            setting = Setting.objects.filter(name=key, category="basic").first()
            assert setting is not None, f"未持久化 {key}"
            assert setting.cleaned_value == value

    def test_color_accepts_hex_rgba_and_name(self, auth_client):
        for color in ("#909399", "rgba(0, 0, 0, 0.3)", "red"):
            resp = auth_client.patch(BASIC_URL, {"FRONT_END_WEB_WATERMARK_COLOR": color})
            assert resp.status_code == 200, f"颜色 {color} 应合法"
            assert resp.data["code"] == 1000

    def test_color_rejects_non_color_text(self, auth_client):
        resp = auth_client.patch(BASIC_URL, {"FRONT_END_WEB_WATERMARK_COLOR": "深灰色"})
        assert resp.status_code == 400

    def test_text_template_with_placeholders_persists(self, auth_client):
        """文案模板含占位符原样持久化（占位符由前端按当前用户解析）。"""
        template = "{username}-{phone}-{time}"
        resp = auth_client.patch(BASIC_URL, {"FRONT_END_WEB_WATERMARK_TEXT": template})
        assert resp.status_code == 200
        setting = Setting.objects.filter(name="FRONT_END_WEB_WATERMARK_TEXT", category="basic").first()
        assert setting.cleaned_value == template

    def test_columns_metadata_drives_form_controls(self, auth_client):
        """元数据驱动表单控件：颜色走 color-picker（color 渲染器），数值带边界与步进。"""
        resp = auth_client.get(f"{BASIC_URL}/search-columns")
        assert resp.status_code == 200
        columns = {item["key"]: item for item in resp.data["data"]}

        assert columns["FRONT_END_WEB_WATERMARK_COLOR"]["input_type"] == "color"

        opacity = columns["FRONT_END_WEB_WATERMARK_OPACITY"]
        assert opacity["input_type"] == "float"
        assert opacity["step"] == 0.1
        assert opacity["min_value"] == 0.01
        assert opacity["max_value"] == 1

        font_size = columns["FRONT_END_WEB_WATERMARK_FONT_SIZE"]
        assert font_size["min_value"] == 8
        assert font_size["max_value"] == 72


class TestUserInfoWatermarkConfig:
    def test_userinfo_returns_watermark_config(self, auth_client):
        resp = auth_client.get(USERINFO_URL)
        assert resp.status_code == 200
        config = resp.data.get("config") or {}
        for key in WATERMARK_KEYS:
            assert key in config, f"用户信息接口未下发 {key}"
            assert config[key] == getattr(dj_settings, key)
