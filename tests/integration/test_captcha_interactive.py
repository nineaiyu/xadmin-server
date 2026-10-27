# -*- coding: utf-8 -*-
"""统一人机验证端到端语义：出题 → 校验 → ticket → 登录消费。

覆盖 slide 常规题、image 回退题（含 PoW）、一次性票据、统一失败、动作绑定、
旧图片码路由/字段已删除。
"""

import hashlib

import pytest
from django.core.cache import cache
from django.urls import Resolver404, resolve

from captcha import cache as captcha_cache
from captcha.backends.pow import leading_zero_bits

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
    try:
        cache.delete_pattern("captcha_*")
    except AttributeError:
        pass


@pytest.fixture
def unified_login(settings):
    settings.CAPTCHA_ENABLED = True
    settings.CAPTCHA_ENABLED_TYPES = ["slide", "rotate", "click"]
    settings.CAPTCHA_IMAGE_FALLBACK_ENABLED = False
    settings.CAPTCHA_POW_FOR_IMAGE_FALLBACK = True
    settings.CAPTCHA_TRACK_CHECK_ENABLED = False
    settings.SECURITY_LOGIN_CAPTCHA_ENABLED = True
    settings.SECURITY_LOGIN_ENCRYPTED_ENABLED = False
    settings.SECURITY_LOGIN_TEMP_TOKEN_ENABLED = False


def _challenge(api_client, captcha_type="slide", scene="login"):
    resp = api_client.post(CHALLENGE_URL, {"type": captcha_type, "scene": scene}, format="json")
    assert resp.status_code == 200, resp.data
    assert resp.data["code"] == 1000
    data = resp.data["data"]
    assert "decision" not in data and "difficulty" not in data
    return data


def _stored(challenge_id):
    stored = captcha_cache.load_challenge(challenge_id)
    assert stored is not None
    return stored


def _answer(challenge_id):
    return _stored(challenge_id)["secret"]


def _solve_pow(pow_payload):
    challenge = str(pow_payload["challenge"])
    bits = int(pow_payload["difficulty_bits"])
    nonce = 0
    while leading_zero_bits(hashlib.sha256(f"{challenge}{nonce}".encode()).hexdigest()) < bits:
        nonce += 1
    return {"nonce": str(nonce)}


def _verify(api_client, challenge, answer=None, action="identity.login.basic", scene="login"):
    body = {
        "challenge_id": challenge["challenge_id"],
        "type": challenge["type"],
        "scene": scene,
        "answer": _answer(challenge["challenge_id"]) if answer is None else answer,
    }
    if action is not None:
        body["action"] = action
    return api_client.post(VERIFY_URL, body, format="json")


class TestCapabilities:
    def test_capabilities_expose_types_without_mode(self, api_client, unified_login):
        basic = api_client.get("/api/identity/auth/capabilities").data["data"]["basic"]
        assert basic["captcha"] is True
        assert basic["captcha_types"] == ["slide", "rotate", "click"]
        assert "captcha_mode" not in basic

    def test_image_fallback_appears_in_types_when_enabled(self, api_client, unified_login, settings):
        settings.CAPTCHA_IMAGE_FALLBACK_ENABLED = True
        basic = api_client.get("/api/identity/auth/capabilities").data["data"]["basic"]
        assert "image" in basic["captcha_types"]
        assert basic["captcha_image_fallback"] is True

    def test_global_switch_off_hides_captcha_everywhere(self, api_client, unified_login, settings):
        settings.CAPTCHA_ENABLED = False
        basic = api_client.get("/api/identity/auth/capabilities").data["data"]["basic"]
        assert basic["captcha"] is False
        assert basic["captcha_types"] == []
        assert basic["captcha_image_fallback"] is False

    def test_verify_code_config_respects_global_switch(self, api_client, settings):
        settings.CAPTCHA_ENABLED = False
        settings.SECURITY_LOGIN_CAPTCHA_ENABLED = True
        data = api_client.get("/api/identity/auth/verify?category=login").data["data"]
        assert data["captcha"] is False
        assert "captcha_mode" not in data


class TestChallengeVerifyLogin:
    def test_full_flow_issues_ticket_and_login_succeeds(self, api_client, normal_user, unified_login):
        challenge = _challenge(api_client)
        verify = _verify(api_client, challenge)
        assert verify.status_code == 200
        assert verify.data["data"]["passed"] is True
        assert verify.data["data"]["expires_in"] > 0
        ticket = verify.data["data"]["ticket"]
        login = api_client.post(
            LOGIN_URL,
            {"username": "zhangsan", "password": "Test@123456", "captcha_ticket": ticket},
            format="json",
        )
        assert login.status_code == 200
        assert login.data["code"] == 1000, login.data

    def test_ticket_is_one_time(self, api_client, normal_user, unified_login):
        challenge = _challenge(api_client, captcha_type="rotate")
        ticket = _verify(api_client, challenge).data["data"]["ticket"]
        payload = {"username": "zhangsan", "password": "Test@123456", "captcha_ticket": ticket}
        assert api_client.post(LOGIN_URL, payload, format="json").data["code"] == 1000
        second = api_client.post(LOGIN_URL, payload, format="json")
        assert second.status_code == 400

    def test_wrong_answer_has_uniform_shape(self, api_client, unified_login):
        challenge = _challenge(api_client)
        first = _verify(api_client, challenge, answer={"x": -99999})
        assert first.status_code == 200
        assert first.data["data"] == {"passed": False}
        second = _verify(api_client, challenge)
        assert second.data["data"]["passed"] is True

    def test_attempts_exhausted_is_uniform_400(self, api_client, unified_login):
        challenge = _challenge(api_client)
        for _ in range(2):
            resp = _verify(api_client, challenge, answer={"x": -99999})
            assert resp.data["data"] == {"passed": False}
        exhausted = _verify(api_client, challenge, answer={"x": -99999})
        assert exhausted.status_code == 400
        assert exhausted.data["code"] == 400
        assert exhausted.data["error"] == "captcha_invalid"
        assert "attempts_left" not in exhausted.data.get("data", {})

    def test_global_switch_off_skips_login_captcha(self, api_client, normal_user, unified_login, settings):
        settings.CAPTCHA_ENABLED = False
        login = api_client.post(
            LOGIN_URL,
            {"username": "zhangsan", "password": "Test@123456"},
            format="json",
        )
        assert login.data["code"] == 1000, login.data

    def test_global_switch_off_rejects_challenge(self, api_client, unified_login, settings):
        settings.CAPTCHA_ENABLED = False
        resp = api_client.post(CHALLENGE_URL, {"type": "slide", "scene": "login"}, format="json")
        assert resp.status_code == 400
        assert resp.data["error"] == "captcha_invalid"

    def test_unknown_challenge_returns_400(self, api_client, unified_login):
        resp = api_client.post(
            VERIFY_URL,
            {
                "challenge_id": "does-not-exist",
                "type": "slide",
                "scene": "login",
                "answer": {"x": 1},
                "action": "identity.login.basic",
            },
            format="json",
        )
        assert resp.status_code == 400
        assert resp.data["code"] == 400
        assert resp.data["error"] == "captcha_invalid"

    def test_image_backend_requires_pow_and_issues_ticket(self, api_client, normal_user, unified_login, settings):
        settings.CAPTCHA_IMAGE_FALLBACK_ENABLED = True
        challenge = _challenge(api_client, captcha_type="image")
        assert challenge["type"] == "image"
        assert challenge["background"].startswith("data:image/png;base64,")
        assert challenge["pow"]["difficulty_bits"] > 0
        secret = _answer(challenge["challenge_id"])
        answer = {"code": secret["answer"], "pow_answer": _solve_pow(challenge["pow"])}
        verify = _verify(api_client, challenge, answer=answer)
        assert verify.data["data"]["passed"] is True
        login = api_client.post(
            LOGIN_URL,
            {"username": "zhangsan", "password": "Test@123456", "captcha_ticket": verify.data["data"]["ticket"]},
            format="json",
        )
        assert login.data["code"] == 1000, login.data

    def test_image_fallback_rejected_when_disabled(self, api_client, unified_login):
        resp = api_client.post(CHALLENGE_URL, {"type": "image", "scene": "login"}, format="json")
        assert resp.status_code == 400
        assert resp.data["error"] == "captcha_invalid"

    def test_cross_origin_challenge_rejected(self, api_client, unified_login):
        resp = api_client.post(
            CHALLENGE_URL,
            {"type": "slide", "scene": "login", "action": "identity.login.basic"},
            format="json",
            HTTP_ORIGIN="http://evil.example",
        )
        assert resp.status_code == 400
        assert resp.data["error"] == "captcha_invalid"

    def test_same_origin_challenge_allowed(self, api_client, unified_login):
        resp = api_client.post(
            CHALLENGE_URL,
            {"type": "slide", "scene": "login", "action": "identity.login.basic"},
            format="json",
            HTTP_ORIGIN="http://testserver",
        )
        assert resp.status_code == 200, resp.data


class TestActionBindingAPI:
    def test_verify_without_action_rejected(self, api_client, unified_login):
        challenge = _challenge(api_client)
        resp = _verify(api_client, challenge, action=None)
        assert resp.status_code == 400
        assert resp.data["error"] == "captcha_invalid"

    def test_verify_with_foreign_action_rejected(self, api_client, unified_login):
        challenge = _challenge(api_client)
        resp = _verify(api_client, challenge, action="invite.accept")
        assert resp.status_code == 400
        assert resp.data["error"] == "captcha_invalid"

    def test_send_code_ticket_rejected_for_login(self, api_client, normal_user, unified_login):
        challenge = _challenge(api_client)
        verify = _verify(api_client, challenge, action="auth.verify_code.send:login")
        assert verify.data["data"]["passed"] is True
        login = api_client.post(
            LOGIN_URL,
            {
                "username": "zhangsan",
                "password": "Test@123456",
                "captcha_ticket": verify.data["data"]["ticket"],
            },
            format="json",
        )
        assert login.status_code == 400


class TestLegacyRemoved:
    def test_old_image_route_absent(self, api_client):
        assert api_client.get("/api/identity/auth/captcha").status_code == 404
        assert api_client.get("/api/system/captcha/refresh/").status_code == 404

    def test_inspect_route_absent(self):
        with pytest.raises(Resolver404):
            resolve("/api/identity/auth/captcha/inspect")

    def test_old_captcha_fields_rejected(self, api_client, normal_user, unified_login):
        login = api_client.post(
            LOGIN_URL,
            {
                "username": "zhangsan",
                "password": "Test@123456",
                "captcha_key": "legacy",
                "captcha_code": "1234",
            },
            format="json",
        )
        assert login.status_code == 400
