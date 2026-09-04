# -*- coding: utf-8 -*-
"""captcha 视图集成测试（图片 / 刷新 / 过期处理）。"""
import pytest

from captcha.models import CaptchaStore
from captcha.utils import CaptchaAuth

pytestmark = pytest.mark.django_db

CAPTCHA_IMAGE_URL = "/api/system/captcha/image/{key}/"


def _make_store():
    result = CaptchaAuth().generate()
    return CaptchaStore.objects.get(hashkey=result["captcha_key"]), result["captcha_key"]


class TestCaptchaImage:
    def test_valid_key_returns_png(self, api_client):
        _, key = _make_store()
        resp = api_client.get(CAPTCHA_IMAGE_URL.format(key=key))
        assert resp.status_code == 200
        assert resp["Content-Type"] == "image/png"
        assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"

    def test_missing_key_returns_410(self, api_client):
        resp = api_client.get(CAPTCHA_IMAGE_URL.format(key="abcdefghijklmn"))
        assert resp.status_code == 410

    def test_2x_disabled_returns_404(self, api_client):
        store, key = _make_store()
        resp = api_client.get(f"{CAPTCHA_IMAGE_URL.format(key=key)}@2/")
        assert resp.status_code == 404


class TestCaptchaRefresh:
    def test_xhr_returns_new_key(self, api_client):
        resp = api_client.get(
            "/api/system/captcha/refresh/", HTTP_X_REQUESTED_WITH="XMLHttpRequest"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["key"]
        assert data["image_url"].endswith(f"/{data['key']}/")
        assert CaptchaStore.objects.filter(hashkey=data["key"]).exists()

    def test_non_xhr_returns_404(self, api_client):
        resp = api_client.get("/api/system/captcha/refresh/")
        assert resp.status_code == 404


class TestCaptchaAudio:
    def test_no_flite_returns_404(self, api_client):
        _, key = _make_store()
        resp = api_client.get(f"/api/system/captcha/audio/{key}.wav")
        assert resp.status_code == 404