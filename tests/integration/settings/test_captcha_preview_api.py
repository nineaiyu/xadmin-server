# -*- coding: utf-8 -*-
"""验证码设置预览端点：只读生成、不落库、不依赖当前启用模式。"""

import pytest

from captcha.models import CaptchaStore

pytestmark = pytest.mark.django_db

IMAGE_PREVIEW_URL = "/api/settings/captcha/preview"
INTERACTIVE_PREVIEW_URL = "/api/settings/captcha/interactive/preview"


class TestImagePreview:
    def test_returns_png_without_creating_captcha_store(self, auth_client):
        before = CaptchaStore.objects.count()
        resp = auth_client.post(IMAGE_PREVIEW_URL, {}, format="json")
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["image"].startswith("data:image/png;base64,")
        assert resp.data["data"]["length"] > 0
        assert CaptchaStore.objects.count() == before

    def test_anonymous_denied(self, api_client):
        resp = api_client.post(IMAGE_PREVIEW_URL, {}, format="json")
        assert resp.status_code in (401, 403)


class TestInteractivePreview:
    def test_works_even_when_mode_is_image_and_disabled(self, auth_client, settings):
        settings.CAPTCHA_MODE = "image"
        settings.CAPTCHA_ENABLED = False
        resp = auth_client.post(INTERACTIVE_PREVIEW_URL, {"type": "rotate"}, format="json")
        assert resp.data["code"] == 1000, resp.data
        data = resp.data["data"]
        assert data["type"] == "rotate"
        assert data["background"].startswith("data:image/png;base64,")
        assert "challenge_id" not in data

    def test_unknown_type_rejected(self, auth_client):
        resp = auth_client.post(INTERACTIVE_PREVIEW_URL, {"type": "unknown"}, format="json")
        assert resp.status_code == 400

    def test_anonymous_denied(self, api_client):
        resp = api_client.post(INTERACTIVE_PREVIEW_URL, {"type": "slide"}, format="json")
        assert resp.status_code in (401, 403)
