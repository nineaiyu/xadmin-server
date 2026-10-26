# -*- coding: utf-8 -*-
"""交互式验证码端到端语义：出题 → 校验 → ticket → 登录消费。

图片验证码链路由 ``test_captcha_login.py`` 守护；本文件只覆盖交互式分支，
以及「默认配置不挂载 inspect 路由」的生产安全边界。
"""

import pytest
from django.core.cache import cache
from django.test import override_settings
from django.urls import Resolver404, resolve

from captcha.models import CaptchaStore
from captcha.services import CaptchaAuth, inspect_challenge

pytestmark = pytest.mark.django_db

CHALLENGE_URL = "/api/identity/auth/captcha/challenge"
VERIFY_URL = "/api/identity/auth/captcha/verify"
LOGIN_URL = "/api/identity/login/basic"


@pytest.fixture(autouse=True)
def _clean_captcha_cache():
    try:
        cache.delete_pattern("captcha_*")
    except AttributeError:
        pass
    yield


@pytest.fixture
def interactive_login(settings):
    settings.CAPTCHA_MODE = "interactive"
    settings.CAPTCHA_INTERACTIVE_TYPES = ["slide", "rotate", "click"]
    settings.CAPTCHA_TRACK_CHECK_ENABLED = False
    settings.SECURITY_LOGIN_CAPTCHA_ENABLED = True
    settings.SECURITY_LOGIN_ENCRYPTED_ENABLED = False
    settings.SECURITY_LOGIN_TEMP_TOKEN_ENABLED = False


def _challenge(api_client, captcha_type="slide", scene="login"):
    resp = api_client.post(CHALLENGE_URL, {"type": captcha_type, "scene": scene}, format="json")
    assert resp.status_code == 200, resp.data
    assert resp.data["code"] == 1000
    return resp.data["data"]


def _image_captcha():
    data = CaptchaAuth().generate()
    return data["captcha_key"], CaptchaStore.objects.get(hashkey=data["captcha_key"]).response


def _answer(challenge_id):
    data = inspect_challenge(challenge_id)
    assert data is not None
    return data["answer"]


def _verify(api_client, challenge, answer=None):
    return api_client.post(
        VERIFY_URL,
        {
            "challenge_id": challenge["challenge_id"],
            "type": challenge["type"],
            "scene": "login",
            "answer": _answer(challenge["challenge_id"]) if answer is None else answer,
        },
        format="json",
    )


class TestCapabilities:
    def test_capabilities_expose_types_in_interactive_mode(self, api_client, interactive_login):
        resp = api_client.get("/api/identity/auth/capabilities")
        basic = resp.data["data"]["basic"]
        assert basic["captcha"] is True
        assert basic["captcha_mode"] == "interactive"
        assert basic["captcha_types"] == ["slide", "rotate", "click"]

    def test_capabilities_empty_in_image_mode(self, api_client):
        resp = api_client.get("/api/identity/auth/capabilities")
        basic = resp.data["data"]["basic"]
        assert basic["captcha_mode"] == "image"
        assert basic["captcha_types"] == []

    def test_global_switch_off_hides_captcha_everywhere(self, api_client, settings):
        settings.CAPTCHA_ENABLED = False
        settings.CAPTCHA_MODE = "interactive"
        settings.CAPTCHA_INTERACTIVE_TYPES = ["slide", "rotate", "click"]
        settings.SECURITY_LOGIN_CAPTCHA_ENABLED = True
        basic = api_client.get("/api/identity/auth/capabilities").data["data"]["basic"]
        assert basic["captcha"] is False
        assert basic["captcha_types"] == []
        assert basic["captcha_image_fallback"] is False

    def test_verify_code_config_respects_global_switch(self, api_client, settings):
        settings.CAPTCHA_ENABLED = False
        settings.SECURITY_LOGIN_CAPTCHA_ENABLED = True
        data = api_client.get("/api/identity/auth/verify?category=login").data["data"]
        assert data["captcha"] is False

    def test_capabilities_types_follow_login_scene_switch(self, api_client, settings):
        settings.CAPTCHA_MODE = "interactive"
        settings.CAPTCHA_INTERACTIVE_TYPES = ["slide", "rotate", "click"]
        settings.CAPTCHA_IMAGE_FALLBACK_ENABLED = True
        settings.SECURITY_LOGIN_CAPTCHA_ENABLED = False
        basic = api_client.get("/api/identity/auth/capabilities").data["data"]["basic"]
        assert basic["captcha_types"] == []
        assert basic["captcha_image_fallback"] is False

    def test_verify_code_config_exposes_types(self, api_client, interactive_login):
        resp = api_client.get("/api/identity/auth/verify?category=login")
        assert resp.data["data"]["captcha_types"] == ["slide", "rotate", "click"], resp.data
        assert resp.data["data"]["captcha_mode"] == "interactive"


class TestChallengeVerifyLogin:
    def test_full_flow_issues_ticket_and_login_succeeds(self, api_client, normal_user, interactive_login):
        challenge = _challenge(api_client)
        verify = _verify(api_client, challenge)
        assert verify.status_code == 200
        assert verify.data["data"]["passed"] is True
        ticket = verify.data["data"]["ticket"]

        login = api_client.post(
            LOGIN_URL,
            {"username": "zhangsan", "password": "Test@123456", "captcha_ticket": ticket},
            format="json",
        )
        assert login.status_code == 200
        assert login.data["code"] == 1000, login.data

    def test_ticket_is_one_time(self, api_client, normal_user, interactive_login):
        challenge = _challenge(api_client, captcha_type="rotate")
        ticket = _verify(api_client, challenge).data["data"]["ticket"]
        payload = {"username": "zhangsan", "password": "Test@123456", "captcha_ticket": ticket}
        assert api_client.post(LOGIN_URL, payload, format="json").data["code"] == 1000
        second = api_client.post(LOGIN_URL, payload, format="json")
        assert second.status_code == 400

    def test_wrong_answer_returns_attempts_left(self, api_client, interactive_login):
        challenge = _challenge(api_client)
        first = _verify(api_client, challenge, answer={"x": -99999})
        assert first.status_code == 200
        assert first.data["data"] == {"passed": False, "attempts_left": 2}
        second = _verify(api_client, challenge)
        assert second.data["data"]["passed"] is True

    def test_attempts_exhausted_returns_400(self, api_client, interactive_login):
        challenge = _challenge(api_client)
        for expected in (2, 1):
            resp = _verify(api_client, challenge, answer={"x": -99999})
            assert resp.data["data"]["attempts_left"] == expected
        exhausted = _verify(api_client, challenge, answer={"x": -99999})
        assert exhausted.status_code == 400
        assert exhausted.data["code"] == 400
        assert exhausted.data["error"] == "captcha_exhausted"

    def test_global_switch_off_skips_login_captcha(self, api_client, normal_user, interactive_login, settings):
        settings.CAPTCHA_ENABLED = False
        login = api_client.post(
            LOGIN_URL,
            {"username": "zhangsan", "password": "Test@123456"},
            format="json",
        )
        assert login.data["code"] == 1000, login.data

    def test_global_switch_off_rejects_challenge(self, api_client, interactive_login, settings):
        settings.CAPTCHA_ENABLED = False
        resp = api_client.post(CHALLENGE_URL, {"type": "slide", "scene": "login"}, format="json")
        assert resp.status_code == 400
        assert resp.data["error"] == "captcha_invalid"

    def test_unknown_challenge_returns_400(self, api_client, interactive_login):
        resp = api_client.post(
            VERIFY_URL,
            {"challenge_id": "does-not-exist", "type": "slide", "scene": "login", "answer": {"x": 1}},
            format="json",
        )
        assert resp.status_code == 400
        assert resp.data["code"] == 400
        assert resp.data["error"] == "captcha_invalid"

    def test_challenge_rejected_in_image_mode(self, api_client):
        resp = api_client.post(CHALLENGE_URL, {"type": "slide", "scene": "login"}, format="json")
        assert resp.status_code == 400
        assert resp.data["code"] == 400
        assert resp.data["error"] == "captcha_invalid"

    def test_image_fallback_login_when_enabled(self, api_client, normal_user, interactive_login, settings):
        settings.CAPTCHA_IMAGE_FALLBACK_ENABLED = True
        key, answer = _image_captcha()
        login = api_client.post(
            LOGIN_URL,
            {"username": "zhangsan", "password": "Test@123456", "captcha_key": key, "captcha_code": answer},
            format="json",
        )
        assert login.data["code"] == 1000, login.data

    def test_image_fallback_rejected_when_disabled(self, api_client, normal_user, interactive_login, settings):
        settings.CAPTCHA_IMAGE_FALLBACK_ENABLED = False
        key, answer = _image_captcha()
        login = api_client.post(
            LOGIN_URL,
            {"username": "zhangsan", "password": "Test@123456", "captcha_key": key, "captcha_code": answer},
            format="json",
        )
        assert login.status_code == 400


class TestInspectRouteGuard:
    def test_route_absent_under_default_settings(self):
        assert not hasattr(__import__("django.conf", fromlist=["settings"]).settings, "CAPTCHA_INSPECT_ENABLED")
        with pytest.raises(Resolver404):
            resolve("/api/identity/auth/captcha/inspect")

    @override_settings(CAPTCHA_INSPECT_ENABLED=False)
    def test_view_rejects_when_flag_missing(self, api_client):
        # 直接命中视图（路由未挂载时的兜底防御）：视图内部同样 fail-closed
        from identity.views.auth.captcha_challenge import CaptchaInspectAPIView

        view = CaptchaInspectAPIView.as_view()
        from django.test import RequestFactory

        response = view(RequestFactory().get("/api/identity/auth/captcha/inspect?challenge_id=x"))
        assert response.status_code == 400
