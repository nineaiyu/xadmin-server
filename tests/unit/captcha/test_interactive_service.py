# -*- coding: utf-8 -*-
"""交互式验证码编排：挑战缓存 / 尝试上限 / ticket 一次性 / 囤票上限。"""

from concurrent.futures import ThreadPoolExecutor

import pytest
from django.core.cache import cache

from captcha import cache as captcha_cache
from captcha import flow, tickets
from captcha.const import CAPTCHA_ACTION_LOGIN, verify_code_send_action
from captcha.services import CaptchaInvalidError, InteractiveCaptcha, build_captcha_meta

_SCENE_ACTION = {
    "login": CAPTCHA_ACTION_LOGIN,
    "reset": verify_code_send_action("reset"),
}


def _answer_for(challenge_id: str):
    stored = captcha_cache.load_challenge(challenge_id)
    assert stored is not None
    return stored["type"], stored["secret"]


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
        sid=challenge.get("_sid", ""),
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
    settings.CAPTCHA_ENABLED_TYPES = ["slide", "rotate", "click"]
    settings.CAPTCHA_IMAGE_FALLBACK_ENABLED = False
    settings.CAPTCHA_TRACK_CHECK_ENABLED = False
    settings.CAPTCHA_MAX_ATTEMPTS = 3
    settings.CAPTCHA_INFLIGHT_TICKET_LIMIT = 3
    return InteractiveCaptcha()


class TestChallenge:
    def test_global_switch_off_disables_everything(self, settings, interactive):
        settings.CAPTCHA_ENABLED = False
        with pytest.raises(CaptchaInvalidError):
            interactive.challenge(scene="login")
        assert build_captcha_meta("login") == {
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
        settings.CAPTCHA_ENABLED_TYPES = ["rotate"]
        with pytest.raises(CaptchaInvalidError):
            interactive.challenge(captcha_type="slide", scene="login")

    def test_meta_exposes_types_and_image_fallback(self, settings):
        # 显式固定基线，避免并行/顺序导致的全局 settings 残留
        settings.CAPTCHA_ENABLED = True
        settings.CAPTCHA_ENABLED_TYPES = ["slide", "rotate", "click"]
        settings.CAPTCHA_IMAGE_FALLBACK_ENABLED = False
        settings.SECURITY_LOGIN_CAPTCHA_ENABLED = True
        assert build_captcha_meta("login")["types"] == ["slide", "rotate", "click"]
        assert build_captcha_meta("login")["image_fallback"] is False
        settings.CAPTCHA_IMAGE_FALLBACK_ENABLED = True
        assert build_captcha_meta("login")["image_fallback"] is True
        assert "image" in build_captcha_meta("login")["types"]
        settings.SECURITY_LOGIN_CAPTCHA_ENABLED = False
        assert build_captcha_meta("login")["types"] == []
        assert build_captcha_meta("login")["image_fallback"] is False


class TestVerifyAndTicket:
    def test_challenge_exposes_server_time(self, interactive):
        challenge = interactive.challenge(captcha_type="slide", scene="login")
        assert isinstance(challenge["ts"], int) and challenge["ts"] > 0

    def test_session_sid_is_reused_across_challenges(self, interactive):
        sid = "s" * 32
        first = interactive.challenge(captcha_type="slide", scene="login", sid=sid)
        second = interactive.challenge(captcha_type="slide", scene="login", sid=sid)
        assert first["_sid"] == sid
        assert second["_sid"] == sid
        # 第二个挑战不会顶掉第一个的会话绑定
        assert _solve(interactive, first)["passed"] is True

    def test_unusable_sid_is_replaced(self, interactive):
        challenge = interactive.challenge(captcha_type="slide", scene="login", sid="short")
        assert challenge["_sid"] != "short"
        assert len(challenge["_sid"]) >= 16

    def test_click_track_min_points_follow_target_count(self, settings, interactive, monkeypatch):
        settings.CAPTCHA_TRACK_CHECK_ENABLED = True
        settings.CAPTCHA_CLICK_COUNT_EASY = 2
        settings.CAPTCHA_ENABLED_TYPES = ["click"]
        monkeypatch.setattr(flow, "pick_type", lambda *_args, **_kwargs: ("click", "easy"))
        challenge = interactive.challenge(captcha_type="click", scene="login", action=CAPTCHA_ACTION_LOGIN)
        stored = captcha_cache.load_challenge(challenge["challenge_id"])
        assert stored is not None
        assert len(stored["secret"]["points"]) == 2
        track = [{"x": 0, "y": 0, "t": 0}, {"x": 1, "y": 1, "t": 200}]
        result = interactive.verify(
            challenge_id=challenge["challenge_id"],
            captcha_type="click",
            scene="login",
            answer=stored["secret"],
            track=track,
            action=CAPTCHA_ACTION_LOGIN,
            sid=challenge["_sid"],
        )
        assert result["passed"] is True

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

    def test_concurrent_verify_issues_one_ticket(self, interactive):
        challenge = interactive.challenge(captcha_type="slide", scene="login")

        def attempt(_index):
            try:
                return _solve(interactive, challenge, ip="10.0.0.11")
            except CaptchaInvalidError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, range(2)))
        assert sum(1 for result in results if result and result.get("passed")) == 1

    def test_pow_budget_falls_back_to_enabled_non_pow_type(self, monkeypatch, settings, interactive):
        settings.CAPTCHA_ENABLED_TYPES = ["pow", "slide"]
        monkeypatch.setattr(flow, "pick_type", lambda *_args, **_kwargs: ("pow", "easy"))
        monkeypatch.setattr(flow, "pow_budget_ok", lambda _ip: False)
        challenge = interactive.challenge(scene="login")
        assert challenge["type"] == "slide"

    def test_ticket_scene_mismatch_consumes_and_fails(self, interactive):
        challenge = interactive.challenge(captcha_type="rotate", scene="reset")
        ticket = _solve(interactive, challenge, scene="reset")["ticket"]
        assert interactive.consume_ticket(ticket, "login", action=CAPTCHA_ACTION_LOGIN) is False
        # 跨场景消费已原子删除，不能再次使用
        assert interactive.consume_ticket(ticket, "reset", action=verify_code_send_action("reset")) is False

    def test_attempts_limit(self, interactive):
        challenge = interactive.challenge(captcha_type="slide", scene="login")
        for _ in range(2):
            result = interactive.verify(
                challenge_id=challenge["challenge_id"],
                captcha_type=challenge["type"],
                scene="login",
                answer={"x": -10000},
                track=None,
                action=CAPTCHA_ACTION_LOGIN,
            )
            assert result == {"passed": False}
        with pytest.raises(CaptchaInvalidError):
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
        assert result == {"passed": False}

    def test_inflight_ticket_limit(self, interactive):
        for _ in range(3):
            challenge = interactive.challenge(captcha_type="slide", scene="login")
            assert _solve(interactive, challenge, ip="10.0.0.9")["passed"] is True
        fourth = interactive.challenge(captcha_type="slide", scene="login")
        with pytest.raises(CaptchaInvalidError):
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

    def test_verify_action_must_match_challenge_action(self, interactive):
        challenge = interactive.challenge(captcha_type="slide", scene="login", action=CAPTCHA_ACTION_LOGIN)
        _, answer = _answer_for(challenge["challenge_id"])
        base = dict(
            challenge_id=challenge["challenge_id"],
            captcha_type=challenge["type"],
            scene="login",
            answer=answer,
            track=None,
        )
        # 同 scene 的另一个 action 不得套用本题（出题动作必须贯穿到校验）
        with pytest.raises(CaptchaInvalidError):
            interactive.verify(**base, action=verify_code_send_action("login"))
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
