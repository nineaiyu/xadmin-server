# -*- coding: utf-8 -*-
"""结构化签名票据：签发 / 验签 / 一次性 / (scene, action) 绑定 / 轮换 / verify 加签。"""

import json
import time

import pytest
from django.core.cache import cache

from captcha import tickets
from captcha.const import (
    CAPTCHA_RISK_MODE_ENFORCE,
    CAPTCHA_RISK_MODE_OFF,
    current_risk_mode,
    verify_code_send_action,
)


def _purge_keys():
    try:
        cache.delete_pattern("captcha_*")
    except AttributeError:
        pass


@pytest.fixture(autouse=True)
def _clean_cache():
    _purge_keys()
    yield
    _purge_keys()


def _craft_ticket(payload: dict, key_id: str, secret: str) -> str:
    """手工构造签名票据（绕过 issue_ticket，用于过期 / 旧密钥等边界）。"""
    body = tickets._b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    return f"{body}.{tickets._sign(secret, body)}"


class TestIssueAndParse:
    def test_issue_returns_two_segment_signed_ticket(self):
        ticket = tickets.issue_ticket("login", ttl=60)
        body, _, signature = ticket.partition(".")
        payload = json.loads(tickets._b64url_decode(body))
        assert payload["v"] == tickets.TICKET_VERSION
        assert payload["scene"] == "login"
        assert payload["exp"] > payload["iat"]
        assert len(payload["n"]) == 32
        # 票据对客户端可解码：风险分不进票据（只留服务端日志），避免评分表变探针
        assert "risk" not in payload
        secret = tickets.ticket_keys()[payload["kid"]]
        assert tickets._sign(secret, body) == signature

    def test_unconfigured_secret_derives_from_secret_key(self, settings):
        settings.CAPTCHA_TICKET_SECRET = ""
        assert "derived" in tickets.ticket_keys()

    def test_configured_secret_wins(self, settings):
        settings.CAPTCHA_TICKET_SECRET = "k-s1"
        settings.CAPTCHA_TICKET_SECRET_KEY_ID = "k1"
        assert tickets.ticket_keys() == {"k1": "k-s1"}

    def test_parse_rejects_garbage(self):
        assert tickets.parse_ticket("") is None
        assert tickets.parse_ticket("not-a-ticket") is None
        assert tickets.parse_ticket("a.b.c") is None
        assert tickets.parse_ticket("####.ff") is None

    def test_parse_rejects_tampered_payload(self):
        ticket = tickets.issue_ticket("login", ttl=60)
        body, _, signature = ticket.partition(".")
        payload = json.loads(tickets._b64url_decode(body))
        payload["scene"] = "register"
        forged = f"{tickets._b64url_encode(json.dumps(payload).encode())}.{signature}"
        assert tickets.parse_ticket(forged) is None

    def test_parse_rejects_unknown_version_or_kid(self, settings):
        settings.CAPTCHA_TICKET_SECRET = "k-s1"
        settings.CAPTCHA_TICKET_SECRET_KEY_ID = "k1"
        secret = tickets.ticket_keys()["k1"]
        base = {"v": 1, "kid": "k1", "n": "x" * 32, "exp": int(time.time()) + 60, "scene": "login"}
        assert tickets.parse_ticket(_craft_ticket({**base, "v": 2}, "k1", secret)) is None
        assert tickets.parse_ticket(_craft_ticket({**base, "kid": "k9"}, "k9", secret)) is None

    def test_ticket_ttl_for_modes(self, settings):
        settings.CAPTCHA_TICKET_TTL = 120
        settings.CAPTCHA_TICKET_TTL_ALLOW = 60
        settings.CAPTCHA_TICKET_TTL_LIGHT = 90
        settings.CAPTCHA_TICKET_TTL_HARD = 90
        assert tickets.ticket_ttl_for("challenge") == 120
        assert tickets.ticket_ttl_for("allow") == 60
        assert tickets.ticket_ttl_for("light") == 90
        assert tickets.ticket_ttl_for("hard") == 90


class TestConsume:
    def test_consume_once(self):
        ticket = tickets.issue_ticket("login", action="identity.login.basic", ttl=60)
        assert tickets.consume_ticket(ticket, "login", action="identity.login.basic") is True
        assert tickets.consume_ticket(ticket, "login", action="identity.login.basic") is False

    def test_expired_ticket_rejected_without_consuming_nonce(self):
        payload = {
            "v": 1,
            "kid": next(iter(tickets.ticket_keys())),
            "n": "expirednonce" + "0" * 19,
            "exp": int(time.time()) - 1,
            "scene": "login",
        }
        ticket = _craft_ticket(payload, payload["kid"], tickets.ticket_keys()[payload["kid"]])
        assert tickets.consume_ticket(ticket, "login") is False
        # 过期判定在 nonce 消费之前：nonce 仍未被消费（登记键不存在）
        assert cache.get(f"{tickets.NONCE_KEY_PREFIX}_{payload['n']}") is None

    def test_scene_mismatch_consumes_and_fails(self):
        ticket = tickets.issue_ticket("login", action="identity.login.basic", ttl=60)
        assert tickets.consume_ticket(ticket, "register", action="identity.register") is False
        # 跨场景消费已原子删除 nonce，不能换回正确场景再用
        assert tickets.consume_ticket(ticket, "login", action="identity.login.basic") is False

    def test_action_binding(self):
        ticket = tickets.issue_ticket("login", action="identity.login.basic", ttl=60)
        assert tickets.consume_ticket(ticket, "login", action="identity.login.basic") is True
        # 票据一次性：第二次必失败
        assert tickets.consume_ticket(ticket, "login", action="identity.login.basic") is False

    def test_action_mismatch_fails(self):
        ticket = tickets.issue_ticket("login", action="identity.login.basic", ttl=60)
        assert tickets.consume_ticket(ticket, "login", action="invite.accept") is False

    def test_consume_without_declared_action_fails(self):
        # 消费端必须声明动作：不声明即失败（与签发端「必填」对称）
        ticket = tickets.issue_ticket("login", action="identity.login.basic", ttl=60)
        assert tickets.consume_ticket(ticket, "login") is False

    def test_unbound_ticket_rejected_under_binding(self):
        # 未绑 action 的票据（绑定关闭窗口签发 / 旧格式遗留）在绑定开启时一律拒绝
        ticket = tickets.issue_ticket("login", ttl=60)
        assert tickets.consume_ticket(ticket, "login", action="identity.login.basic") is False

    def test_fingerprint_weak_binding(self):
        ticket = tickets.issue_ticket("login", action="identity.login.basic", fp="fph", ttl=60)
        assert tickets.consume_ticket(ticket, "login", action="identity.login.basic", fp_hash="other") is False
        ticket2 = tickets.issue_ticket("login", action="identity.login.basic", fp="fph", ttl=60)
        assert tickets.consume_ticket(ticket2, "login", action="identity.login.basic", fp_hash="fph") is True

    def test_unbound_ticket_legacy_semantics_when_binding_off(self, settings):
        settings.CAPTCHA_ACTION_BIND = False
        ticket = tickets.issue_ticket("login", ttl=60)
        assert tickets.consume_ticket(ticket, "login", action="identity.login.basic") is True

    def test_action_bind_switch_off_disables_binding(self, settings):
        settings.CAPTCHA_ACTION_BIND = False
        ticket = tickets.issue_ticket("login", action="identity.login.basic", ttl=60)
        assert tickets.consume_ticket(ticket, "login", action="invite.accept") is True


class TestKeyRotation:
    def test_previous_key_verifies_during_rotation(self, settings):
        settings.CAPTCHA_TICKET_SECRET = "old-secret"
        settings.CAPTCHA_TICKET_SECRET_KEY_ID = "k1"
        ticket = tickets.issue_ticket("login", action="identity.login.basic", ttl=60)
        settings.CAPTCHA_TICKET_SECRET = "new-secret"
        settings.CAPTCHA_TICKET_SECRET_KEY_ID = "k2"
        # 未配置 PREVIOUS：旧票据验签失败
        assert tickets.consume_ticket(ticket, "login", action="identity.login.basic") is False
        settings.CAPTCHA_TICKET_SECRET_PREVIOUS = "k1:old-secret"
        assert tickets.consume_ticket(ticket, "login", action="identity.login.basic") is True

    def test_malformed_previous_ignored(self, settings):
        settings.CAPTCHA_TICKET_SECRET = "new-secret"
        settings.CAPTCHA_TICKET_SECRET_KEY_ID = "k2"
        settings.CAPTCHA_TICKET_SECRET_PREVIOUS = "no-colon"
        assert tickets.ticket_keys() == {"k2": "new-secret"}


class TestVerifySign:
    def _nonce(self) -> str:
        return "n" * 24

    def test_sign_off_mode_not_enforced(self, settings):
        settings.CAPTCHA_VERIFY_SIGN_REQUIRED = True
        settings.CAPTCHA_RISK_MODE = CAPTCHA_RISK_MODE_OFF
        assert tickets.verify_sign_enforced() is False
        settings.CAPTCHA_RISK_MODE = CAPTCHA_RISK_MODE_ENFORCE
        assert tickets.verify_sign_enforced() is True

    def test_valid_signature_passes(self):
        ck = "ck-session-key"
        gen_time = int(time.time())
        answer = {"x": 123}
        digest = tickets.answer_digest(answer)
        sign = tickets.compute_verify_sign(ck, "cid", "slide", "login", digest, gen_time)
        assert (
            tickets.verify_request_signature(ck, "cid", "slide", "login", answer, gen_time, self._nonce(), sign) is True
        )

    def test_replayed_nonce_rejected(self):
        ck = "ck-session-key"
        gen_time = int(time.time())
        answer = {"x": 123}
        sign = tickets.compute_verify_sign(ck, "cid", "slide", "login", tickets.answer_digest(answer), gen_time)
        nonce = self._nonce()
        assert tickets.verify_request_signature(ck, "cid", "slide", "login", answer, gen_time, nonce, sign) is True
        assert tickets.verify_request_signature(ck, "cid", "slide", "login", answer, gen_time, nonce, sign) is False

    def test_wrong_signature_or_stale_time_rejected(self):
        ck = "ck-session-key"
        answer = {"x": 123}
        gen_time = int(time.time())
        digest = tickets.answer_digest(answer)
        bad = "f" * 64
        assert (
            tickets.verify_request_signature(ck, "cid", "slide", "login", answer, gen_time, self._nonce(), bad) is False
        )
        stale = gen_time - 3600
        stale_sign = tickets.compute_verify_sign(ck, "cid", "slide", "login", digest, stale)
        assert (
            tickets.verify_request_signature(ck, "cid", "slide", "login", answer, stale, self._nonce(), stale_sign)
            is False
        )

    def test_short_nonce_and_missing_sign_rejected(self):
        gen_time = int(time.time())
        answer = {"x": 1}
        digest = tickets.answer_digest(answer)
        sign = tickets.compute_verify_sign("ck", "cid", "slide", "login", digest, gen_time)
        assert tickets.verify_request_signature("ck", "cid", "slide", "login", answer, gen_time, "short", sign) is False
        assert (
            tickets.verify_request_signature("ck", "cid", "slide", "login", answer, gen_time, self._nonce(), "")
            is False
        )
        assert (
            tickets.verify_request_signature("ck", "cid", "slide", "login", answer, "bad", self._nonce(), sign) is False
        )


class TestConstHelpers:
    def test_risk_mode_fallback(self, settings):
        settings.CAPTCHA_RISK_MODE = "bogus"
        assert current_risk_mode() == CAPTCHA_RISK_MODE_OFF
        settings.CAPTCHA_RISK_MODE = ""
        assert current_risk_mode() == CAPTCHA_RISK_MODE_OFF

    def test_verify_code_send_action_derivation(self):
        assert verify_code_send_action("login") == "auth.verify_code.send:login"

    def test_scene_actions_cover_all_scenes(self):
        from captcha.const import CAPTCHA_SCENE_ACTIONS, CAPTCHA_SCENES

        for scene in CAPTCHA_SCENES:
            assert scene in CAPTCHA_SCENE_ACTIONS
            assert CAPTCHA_SCENE_ACTIONS[scene]
