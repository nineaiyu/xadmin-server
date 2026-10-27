# -*- coding: utf-8 -*-
"""交互式验证码编排：挑战缓存 / 尝试上限 / ticket 一次性 / 囤票上限。"""

from concurrent.futures import ThreadPoolExecutor

import pytest
from django.core.cache import cache

from captcha import cache as captcha_cache
from captcha import tickets
from captcha.const import CAPTCHA_ACTION_LOGIN, verify_code_send_action
from captcha.services import (
    CaptchaExhaustedError,
    CaptchaInvalidError,
    InteractiveCaptcha,
    build_captcha_meta,
    inspect_challenge,
)

_SCENE_ACTION = {
    "login": CAPTCHA_ACTION_LOGIN,
    "reset": verify_code_send_action("reset"),
}


def _answer_for(challenge_id: str):
    data = inspect_challenge(challenge_id)
    assert data is not None
    return data["type"], data["answer"]


def _solve(service: InteractiveCaptcha, challenge: dict, scene: str = "login", ip: str = "10.0.0.8"):
    _, answer = _answer_for(challenge["challenge_id"])
    return service.verify(
        challenge_id=challenge["challenge_id"],
        captcha_type=challenge["type"],
        scene=scene,
        answer=answer,
        track=None,
        ip=ip,
        action=_SCENE_ACTION[scene],
    )


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
def interactive(settings):
    settings.CAPTCHA_MODE = "interactive"
    settings.CAPTCHA_INTERACTIVE_TYPES = ["slide", "rotate", "click"]
    settings.CAPTCHA_TRACK_CHECK_ENABLED = False
    settings.CAPTCHA_MAX_ATTEMPTS = 3
    settings.CAPTCHA_INFLIGHT_TICKET_LIMIT = 3
    return InteractiveCaptcha()


class TestChallenge:
    def test_image_mode_rejects(self, settings):
        settings.CAPTCHA_MODE = "image"
        with pytest.raises(CaptchaInvalidError):
            InteractiveCaptcha().challenge(scene="login")

    def test_global_switch_off_disables_everything(self, settings, interactive):
        settings.CAPTCHA_ENABLED = False
        with pytest.raises(CaptchaInvalidError):
            interactive.challenge(scene="login")
        assert build_captcha_meta("login") == {
            "mode": "interactive",
            "types": [],
            "image_fallback": False,
            "captcha_flow": "always",
        }

    def test_unknown_scene_rejects(self, interactive):
        with pytest.raises(CaptchaInvalidError):
            interactive.challenge(scene="unknown")

    def test_disabled_scene_rejects(self, settings, interactive):
        settings.SECURITY_LOGIN_CAPTCHA_ENABLED = False
        with pytest.raises(CaptchaInvalidError):
            interactive.challenge(scene="login")

    def test_disabled_type_rejects(self, settings, interactive):
        settings.CAPTCHA_INTERACTIVE_TYPES = ["rotate"]
        with pytest.raises(CaptchaInvalidError):
            interactive.challenge(captcha_type="slide", scene="login")

    def test_meta_only_exposes_types_in_interactive_mode(self, settings):
        settings.CAPTCHA_MODE = "image"
        assert build_captcha_meta("login") == {
            "mode": "image",
            "types": [],
            "image_fallback": False,
            "captcha_flow": "always",
        }
        settings.CAPTCHA_MODE = "interactive"
        assert build_captcha_meta("login")["types"] == ["slide", "rotate", "click"]
        assert build_captcha_meta("login")["image_fallback"] is False
        settings.CAPTCHA_IMAGE_FALLBACK_ENABLED = True
        assert build_captcha_meta("login")["image_fallback"] is True
        settings.SECURITY_LOGIN_CAPTCHA_ENABLED = False
        assert build_captcha_meta("login")["types"] == []
        assert build_captcha_meta("login")["image_fallback"] is False


class TestVerifyAndTicket:
    def test_pass_issues_one_time_ticket_bound_to_scene(self, interactive):
        challenge = interactive.challenge(captcha_type="slide", scene="login")
        result = _solve(interactive, challenge)
        assert result["passed"] is True
        assert result["ticket"]
        assert interactive.consume_ticket(result["ticket"], "login", ip="10.0.0.8", action=CAPTCHA_ACTION_LOGIN) is True
        assert (
            interactive.consume_ticket(result["ticket"], "login", ip="10.0.0.8", action=CAPTCHA_ACTION_LOGIN) is False
        )
        assert interactive.consume_ticket(result["ticket"], "register", action="identity.register") is False

    def test_ticket_consumed_once_under_concurrency(self, interactive):
        challenge = interactive.challenge(captcha_type="slide", scene="login")
        ticket = _solve(interactive, challenge, ip="10.0.0.10")["ticket"]
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(
                pool.map(
                    lambda _index: interactive.consume_ticket(
                        ticket, "login", ip="10.0.0.10", action=CAPTCHA_ACTION_LOGIN
                    ),
                    range(8),
                )
            )
        assert results.count(True) == 1

    def test_ticket_scene_mismatch_consumes_and_fails(self, interactive):
        challenge = interactive.challenge(captcha_type="rotate", scene="reset")
        ticket = _solve(interactive, challenge, scene="reset")["ticket"]
        assert interactive.consume_ticket(ticket, "login", action=CAPTCHA_ACTION_LOGIN) is False
        # 跨场景消费已原子删除，不能再次使用
        assert interactive.consume_ticket(ticket, "reset", action=verify_code_send_action("reset")) is False

    def test_attempts_limit(self, interactive):
        challenge = interactive.challenge(captcha_type="slide", scene="login")
        for expected_left in (2, 1):
            result = interactive.verify(
                challenge_id=challenge["challenge_id"],
                captcha_type=challenge["type"],
                scene="login",
                answer={"x": -10000},
                track=None,
                action=CAPTCHA_ACTION_LOGIN,
            )
            assert result == {"passed": False, "attempts_left": expected_left}
        with pytest.raises(CaptchaExhaustedError):
            interactive.verify(
                challenge_id=challenge["challenge_id"],
                captcha_type=challenge["type"],
                scene="login",
                answer={"x": -10000},
                track=None,
                action=CAPTCHA_ACTION_LOGIN,
            )
        with pytest.raises(CaptchaInvalidError):
            interactive.verify(
                challenge_id=challenge["challenge_id"],
                captcha_type=challenge["type"],
                scene="login",
                answer={"x": -10000},
                track=None,
                action=CAPTCHA_ACTION_LOGIN,
            )

    def test_unknown_challenge_and_wrong_type(self, interactive):
        with pytest.raises(CaptchaInvalidError):
            interactive.verify("missing", "slide", "login", {"x": 1}, action=CAPTCHA_ACTION_LOGIN)
        challenge = interactive.challenge(captcha_type="slide", scene="login")
        with pytest.raises(CaptchaInvalidError):
            interactive.verify(challenge["challenge_id"], "rotate", "login", {"x": 1}, action=CAPTCHA_ACTION_LOGIN)

    def test_track_required_when_enabled(self, settings, interactive):
        settings.CAPTCHA_TRACK_CHECK_ENABLED = True
        challenge = interactive.challenge(captcha_type="slide", scene="login")
        _, answer = _answer_for(challenge["challenge_id"])
        result = interactive.verify(
            challenge_id=challenge["challenge_id"],
            captcha_type=challenge["type"],
            scene="login",
            answer=answer,
            track=None,
            action=CAPTCHA_ACTION_LOGIN,
        )
        assert result == {"passed": False, "attempts_left": 2}

    def test_inflight_ticket_limit(self, interactive):
        for _ in range(3):
            challenge = interactive.challenge(captcha_type="slide", scene="login")
            assert _solve(interactive, challenge, ip="10.0.0.9")["passed"] is True
        fourth = interactive.challenge(captcha_type="slide", scene="login")
        with pytest.raises(CaptchaExhaustedError):
            _solve(interactive, fourth, ip="10.0.0.9")

    def test_cache_failure_is_fail_closed(self, monkeypatch, interactive):
        challenge = interactive.challenge(captcha_type="slide", scene="login")
        monkeypatch.setattr(
            captcha_cache, "load_challenge", lambda _cid: (_ for _ in ()).throw(ConnectionError("down"))
        )
        with pytest.raises(CaptchaInvalidError):
            interactive.verify(challenge["challenge_id"], "slide", "login", {"x": 0}, action=CAPTCHA_ACTION_LOGIN)
        monkeypatch.undo()
        assert interactive.consume_ticket("any-ticket", "login") is False


class TestActionBinding:
    def test_verify_requires_registered_action(self, interactive):
        challenge = interactive.challenge(captcha_type="slide", scene="login")
        _, answer = _answer_for(challenge["challenge_id"])
        base = dict(
            challenge_id=challenge["challenge_id"],
            captcha_type=challenge["type"],
            scene="login",
            answer=answer,
            track=None,
        )
        # 缺失 / 越权 action：与答案错误同形失败，不消耗挑战尝试次数
        with pytest.raises(CaptchaInvalidError):
            interactive.verify(**base)
        with pytest.raises(CaptchaInvalidError):
            interactive.verify(**base, action="invite.accept")
        assert interactive.verify(**base, action=CAPTCHA_ACTION_LOGIN)["passed"] is True

    def test_login_ticket_cannot_be_used_to_send_verify_code(self, interactive):
        # 本方案要消灭的核心挪用：登录票拿去发短信验证码必须失败
        challenge = interactive.challenge(captcha_type="slide", scene="login")
        ticket = _solve(interactive, challenge)["ticket"]
        assert interactive.consume_ticket(ticket, "login", action=verify_code_send_action("login")) is False

    def test_binding_off_restores_legacy_semantics(self, settings, interactive):
        settings.CAPTCHA_ACTION_BIND = False
        challenge = interactive.challenge(captcha_type="slide", scene="login")
        assert _solve(interactive, challenge)["passed"] is True
        # 关闭窗口签发的未绑票据按旧语义（只绑 scene）消费
        ticket = tickets.issue_ticket("login", ttl=60)
        assert interactive.consume_ticket(ticket, "login") is True
