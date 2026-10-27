# -*- coding: utf-8 -*-
"""风险三档端到端：off/shadow 不改变行为，enforce 分级放行与拒绝同形。"""

import hashlib
import time
import uuid

import pytest

from captcha import tickets
from captcha.backends.pow import leading_zero_bits
from captcha.models import CaptchaLog
from captcha.services import inspect_challenge

pytestmark = pytest.mark.django_db

CHALLENGE_URL = "/api/identity/auth/captcha/challenge"
VERIFY_URL = "/api/identity/auth/captcha/verify"
ACTION = "identity.login.basic"
ENV = {
    "v": 1,
    "timezone": "Asia/Shanghai",
    "language": "zh-CN",
    "screen": {"width": 1920, "height": 1080, "dpr": 1},
}


@pytest.fixture
def interactive(settings):
    settings.CAPTCHA_MODE = "interactive"
    settings.CAPTCHA_INTERACTIVE_TYPES = ["slide", "rotate", "click", "wordorder"]
    settings.CAPTCHA_TRACK_CHECK_ENABLED = False
    settings.SECURITY_LOGIN_CAPTCHA_ENABLED = True
    settings.SECURITY_LOGIN_ENCRYPTED_ENABLED = False
    settings.SECURITY_LOGIN_TEMP_TOKEN_ENABLED = False
    settings.CAPTCHA_RISK_TRUST_PRIVATE_NETWORKS = False
    settings.CAPTCHA_TYPE_WEIGHTS = {"slide": 30, "rotate": 30, "click": 30, "wordorder": 10, "pow": 0}
    settings.CAPTCHA_POW_BITS_LIGHT = 6
    settings.CAPTCHA_POW_BITS_HARD = 6


def _challenge(api_client, env=None, **extra):
    payload = {"scene": "login", "action": ACTION, "env": ENV if env is None else env, **extra}
    return api_client.post(CHALLENGE_URL, payload, format="json")


def _sign(ck, challenge_id, captcha_type, answer, gen_time):
    return tickets.compute_verify_sign(ck, challenge_id, captcha_type, "login", tickets.answer_digest(answer), gen_time)


def _verify(api_client, challenge, answer, env=None, extra=None):
    data = challenge
    gen_time = int(time.time())
    body = {
        "challenge_id": data["challenge_id"],
        "type": data["type"],
        "scene": "login",
        "answer": answer,
        "action": ACTION,
        "env": ENV if env is None else env,
        "gen_time": gen_time,
        "nonce": uuid.uuid4().hex,
    }
    if data.get("ck"):
        body["sign"] = _sign(data["ck"], data["challenge_id"], data["type"], answer, gen_time)
    if extra:
        body.update(extra)
    return api_client.post(VERIFY_URL, body, format="json")


def _solve_pow(secret):
    nonce = 0
    while leading_zero_bits(hashlib.sha256(f"{secret['challenge']}{nonce}".encode()).hexdigest()) < secret["bits"]:
        nonce += 1
    return {"nonce": str(nonce)}


class TestOffParity:
    def test_off_response_has_no_risk_fields(self, api_client, interactive, settings):
        settings.CAPTCHA_RISK_MODE = "off"
        resp = _challenge(api_client)
        assert resp.status_code == 200, resp.data
        data = resp.data["data"]
        for key in ("decision", "difficulty", "ck", "ticket"):
            assert key not in data
        assert {"challenge_id", "type", "background", "ttl"} <= set(data)


class TestShadow:
    def test_shadow_keeps_response_and_logs_would_decision(self, api_client, interactive, settings):
        settings.CAPTCHA_RISK_MODE = "shadow"
        resp = _challenge(api_client, env={**ENV, "webdriver": True})
        assert resp.status_code == 200, resp.data
        data = resp.data["data"]
        assert data["background"]
        assert "decision" not in data
        assert "difficulty" not in data
        row = CaptchaLog.objects.filter(phase="challenge").order_by("-ts").first()
        assert row is not None
        assert row.mode == "shadow"
        assert row.would_decision in ("light", "challenge", "hard", "deny")
        assert row.risk_score is not None


class TestEnforce:
    def test_trusted_network_gets_ticket_without_puzzle(self, api_client, interactive, settings):
        settings.CAPTCHA_RISK_MODE = "enforce"
        # 回环地址在默认信任网段内：enforce 也必须无题直过
        settings.CAPTCHA_RISK_TRUST_PRIVATE_NETWORKS = True
        resp = _challenge(api_client)
        assert resp.status_code == 200, resp.data
        data = resp.data["data"]
        assert data["decision"] == "allow"
        assert data["ticket"]
        from captcha.services import InteractiveCaptcha

        assert InteractiveCaptcha().consume_ticket(data["ticket"], "login", action=ACTION) is True

    def test_allow_path_server_latency_p50_under_300ms(self, api_client, interactive, settings):
        import statistics
        import time

        settings.CAPTCHA_RISK_MODE = "enforce"
        settings.CAPTCHA_RISK_TRUST_PRIVATE_NETWORKS = True
        samples = []
        for _ in range(10):
            started = time.perf_counter()
            resp = _challenge(api_client)
            assert resp.status_code == 200, (len(samples), resp.data)
            data = resp.data["data"]
            assert data["decision"] == "allow"
            from captcha.services import InteractiveCaptcha

            assert InteractiveCaptcha().consume_ticket(data["ticket"], "login", ip="127.0.0.1", action=ACTION) is True
            samples.append((time.perf_counter() - started) * 1000)
        assert statistics.median(samples) < 300, samples

    def test_light_issues_pow_then_ticket(self, api_client, interactive, settings):
        settings.CAPTCHA_RISK_MODE = "enforce"
        # 非法时区 → env_contradiction 25 分 → light
        resp = _challenge(api_client, env={**ENV, "timezone": "Not/AZone"})
        assert resp.status_code == 200, resp.data
        data = resp.data["data"]
        assert data["decision"] == "light"
        assert data["type"] == "pow"
        assert data["pow"]["difficulty_bits"] > 0
        secret = inspect_challenge(data["challenge_id"])["answer"]
        answer = _solve_pow(secret)
        verify = _verify(api_client, data, answer, env={**ENV, "timezone": "Not/AZone"})
        assert verify.status_code == 200, verify.data
        assert verify.data["data"]["passed"] is True
        payload = tickets.parse_ticket(verify.data["data"]["ticket"])
        assert payload["mode"] == "light"

    def test_hard_selects_wordorder(self, api_client, interactive, settings):
        settings.CAPTCHA_RISK_MODE = "enforce"
        env = {**ENV, "webdriver": True, "timezone": "Not/AZone"}
        # webdriver 60 + env_contradiction 25 = 85 → hard
        resp = _challenge(api_client, env=env)
        assert resp.status_code == 200, resp.data
        data = resp.data["data"]
        assert data["decision"] == "hard"
        assert data["difficulty"] == "hard"
        assert data["type"] == "wordorder"
        secret = inspect_challenge(data["challenge_id"])["answer"]
        verify = _verify(api_client, data, {"points": secret["points"]}, env=env)
        assert verify.status_code == 200, verify.data
        payload = tickets.parse_ticket(verify.data["data"]["ticket"])
        assert payload["mode"] == "hard"

    def test_honeypot_denied_same_shape(self, api_client, interactive, settings):
        settings.CAPTCHA_RISK_MODE = "enforce"
        resp = _challenge(api_client, hp="i-am-a-bot")
        assert resp.status_code == 400
        assert resp.data["error"] == "captcha_invalid"
        row = CaptchaLog.objects.filter(phase="decision").order_by("-ts").first()
        assert row is not None
        assert row.decision == "deny"
        assert "honeypot" in row.risk_reasons

    def test_allow_path_respects_inflight_ticket_limit(self, api_client, interactive, settings, monkeypatch):
        from django.core.cache import cache

        from captcha import cache as captcha_cache

        settings.CAPTCHA_RISK_MODE = "enforce"
        settings.CAPTCHA_RISK_TRUST_PRIVATE_NETWORKS = True
        monkeypatch.setattr(captcha_cache, "TICKET_INFLIGHT_LIMIT", 1)
        cache.delete_pattern("captcha_ticket_inflight_*")
        try:
            first = _challenge(api_client)
            assert first.status_code == 200, first.data
            assert first.data["data"]["decision"] == "allow"
            assert first.data["data"]["ticket"]
            second = _challenge(api_client)
            assert second.status_code == 400
            assert second.data["error"] == "captcha_exhausted"
        finally:
            cache.delete_pattern("captcha_ticket_inflight_*")

    def test_enforce_rejects_unsigned_verify(self, api_client, interactive, settings):
        settings.CAPTCHA_RISK_MODE = "enforce"
        # 非法时区 25 分 → light（PoW），challenge 下发 ck
        data = _challenge(api_client, env={**ENV, "timezone": "Not/AZone"}).data["data"]
        assert data["type"] == "pow"
        assert data["ck"]
        secret = inspect_challenge(data["challenge_id"])["answer"]
        body = {
            "challenge_id": data["challenge_id"],
            "type": data["type"],
            "scene": "login",
            "answer": _solve_pow(secret),
            "action": ACTION,
            "gen_time": int(time.time()),
            "nonce": uuid.uuid4().hex,
        }
        resp = api_client.post(VERIFY_URL, body, format="json")
        assert resp.status_code == 400
        assert resp.data["error"] == "captcha_invalid"

    def test_enforce_rejects_replayed_verify_nonce(self, api_client, interactive, settings):
        settings.CAPTCHA_RISK_MODE = "enforce"
        data = _challenge(api_client, env={**ENV, "timezone": "Not/AZone"}).data["data"]
        gen_time = int(time.time())
        # 非数字 nonce 必然校验失败，避免 1/2^bits 概率命中正确答案
        answer = {"nonce": "not-a-number"}
        body = {
            "challenge_id": data["challenge_id"],
            "type": data["type"],
            "scene": "login",
            "answer": answer,
            "action": ACTION,
            "env": {**ENV, "timezone": "Not/AZone"},
            "gen_time": gen_time,
            "nonce": uuid.uuid4().hex,
            "sign": _sign(data["ck"], data["challenge_id"], data["type"], answer, gen_time),
        }
        first = api_client.post(VERIFY_URL, body, format="json")
        assert first.status_code == 200, first.data
        assert first.data["data"]["passed"] is False
        replay = api_client.post(VERIFY_URL, body, format="json")
        assert replay.status_code == 400
        assert replay.data["error"] == "captcha_invalid"

    def test_failure_ladder_forces_hard_next_challenge(self, api_client, interactive, settings):
        settings.CAPTCHA_RISK_MODE = "enforce"
        settings.CAPTCHA_FAIL_STEP = "1,8,20"
        settings.CAPTCHA_MAX_ATTEMPTS = 5
        risky_env = {**ENV, "webdriver": True, "timezone": "Not/AZone"}
        challenge = _challenge(api_client, env=risky_env).data["data"]
        verify = _verify(api_client, challenge, {"points": []}, env=risky_env)
        assert verify.status_code == 200, verify.data
        assert verify.data["data"]["passed"] is False

        # 干净环境分数为 0（本会 allow），阶梯第一档必须把它抬成 hard
        clean_env = {**ENV, "timezone": "Asia/Shanghai"}
        next_challenge = _challenge(api_client, env=clean_env)
        assert next_challenge.status_code == 200, next_challenge.data
        data = next_challenge.data["data"]
        assert data["decision"] == "hard"
        assert data["difficulty"] == "hard"
        assert data["type"] == "wordorder"
        row = CaptchaLog.objects.filter(phase="challenge").order_by("-ts").first()
        assert row is not None
        assert "fail_ladder" in row.risk_reasons

    def test_failure_ladder_never_downgrades_deny(self, api_client, interactive, settings):
        settings.CAPTCHA_RISK_MODE = "enforce"
        settings.CAPTCHA_FAIL_STEP = "1,8,20"
        settings.CAPTCHA_MAX_ATTEMPTS = 5
        risky_env = {**ENV, "webdriver": True, "timezone": "Not/AZone"}
        challenge = _challenge(api_client, env=risky_env).data["data"]
        _verify(api_client, challenge, {"points": []}, env=risky_env)
        # 阶梯只升不降：蜜罐 deny 优先于「强制难题」
        denied = _challenge(api_client, hp="i-am-a-bot")
        assert denied.status_code == 400
        assert denied.data["error"] == "captcha_invalid"

    def test_deny_failure_has_same_shape_as_wrong_answer(self, api_client, interactive, settings):
        settings.CAPTCHA_RISK_MODE = "enforce"
        settings.CAPTCHA_RISK_T_ALLOW = -1
        settings.CAPTCHA_RISK_T_LIGHT = 0
        challenge = _challenge(api_client).data["data"]
        wrong = _verify(api_client, challenge, {"x": -99999})
        challenge2 = _challenge(api_client).data["data"]
        denied = _verify(api_client, challenge2, {"x": -99999}, extra={"hp": "bot"})
        assert wrong.status_code == denied.status_code == 200
        assert wrong.data["data"] == denied.data["data"]
        assert set(wrong.data) == set(denied.data)

    def test_ladder_cooldown_blocks_new_challenge(self, api_client, interactive, settings):
        settings.CAPTCHA_RISK_MODE = "enforce"
        settings.CAPTCHA_FAIL_STEP = "1,2,3"
        settings.CAPTCHA_MAX_ATTEMPTS = 5
        env = {**ENV, "webdriver": True, "timezone": "Not/AZone"}  # hard → wordorder
        for _ in range(2):
            challenge = _challenge(api_client, env=env).data["data"]
            verify = _verify(api_client, challenge, {"points": []}, env=env)
            assert verify.status_code == 200, (verify.status_code, verify.data)
            assert verify.status_code == 200, (verify.status_code, verify.data)
            assert verify.data["data"]["passed"] is False
        blocked = _challenge(api_client)
        assert blocked.status_code == 400
        assert blocked.data["error"] == "captcha_invalid"
