# -*- coding: utf-8 -*-
"""票据强绑定：sid + fp + audience 必须在签发与消费两端一致。"""

import pytest

from captcha import cache as captcha_cache
from captcha.const import CAPTCHA_ACTION_LOGIN
from captcha.services import CaptchaInvalidError, InteractiveCaptcha

pytestmark = pytest.mark.django_db


@pytest.fixture
def strict(settings):
    settings.CAPTCHA_STRICT_BINDING = True
    settings.CAPTCHA_VERIFY_SIGN_REQUIRED = False
    settings.CAPTCHA_TRACK_CHECK_ENABLED = False
    settings.CAPTCHA_IMAGE_FALLBACK_ENABLED = False
    settings.CAPTCHA_ENABLED_TYPES = ["slide"]
    settings.CAPTCHA_TICKET_AUDIENCE = "deploy-a"


def _issue(service: InteractiveCaptcha, env_fp: str = "fph") -> tuple[str, str]:
    challenge = service.challenge(
        captcha_type="slide",
        scene="login",
        action=CAPTCHA_ACTION_LOGIN,
        env={"fp": env_fp},
    )
    stored = captcha_cache.load_challenge(challenge["challenge_id"])
    assert stored is not None
    result = service.verify(
        challenge_id=challenge["challenge_id"],
        captcha_type=challenge["type"],
        scene="login",
        answer=stored["secret"],
        action=CAPTCHA_ACTION_LOGIN,
        sid=challenge["_sid"],
    )
    assert result["passed"] is True
    return result["ticket"], challenge["_sid"]


class TestStrictBinding:
    def test_matching_sid_and_fp_passes(self, strict):
        service = InteractiveCaptcha()
        ticket, sid = _issue(service)
        assert service.consume_ticket(ticket, "login", action=CAPTCHA_ACTION_LOGIN, fp="fph", sid=sid) is True

    def test_missing_or_wrong_sid_rejected(self, strict):
        service = InteractiveCaptcha()
        ticket, sid = _issue(service)
        assert service.consume_ticket(ticket, "login", action=CAPTCHA_ACTION_LOGIN, fp="fph") is False
        ticket2, _ = _issue(service)
        assert service.consume_ticket(ticket2, "login", action=CAPTCHA_ACTION_LOGIN, fp="fph", sid="wrong") is False

    def test_missing_or_wrong_fp_rejected(self, strict):
        service = InteractiveCaptcha()
        ticket, sid = _issue(service)
        assert service.consume_ticket(ticket, "login", action=CAPTCHA_ACTION_LOGIN, sid=sid) is False
        ticket2, sid2 = _issue(service)
        assert service.consume_ticket(ticket2, "login", action=CAPTCHA_ACTION_LOGIN, fp="other", sid=sid2) is False

    def test_audience_mismatch_rejected(self, strict, settings):
        service = InteractiveCaptcha()
        ticket, sid = _issue(service)
        settings.CAPTCHA_TICKET_AUDIENCE = "deploy-b"
        assert service.consume_ticket(ticket, "login", action=CAPTCHA_ACTION_LOGIN, fp="fph", sid=sid) is False

    def test_action_binding_off_keeps_strict_sid_fp(self, strict, settings):
        settings.CAPTCHA_ACTION_BIND = False
        service = InteractiveCaptcha()
        ticket, sid = _issue(service)
        assert service.consume_ticket(ticket, "login", fp="fph", sid=sid) is True
        missing_fp, sid2 = _issue(service)
        assert service.consume_ticket(missing_fp, "login", sid=sid2) is False
        missing_sid, sid3 = _issue(service)
        assert service.consume_ticket(missing_sid, "login", fp="fph") is False
        assert service.consume_ticket(missing_sid, "login", fp="fph", sid=sid3) is False  # 已失败消费，票据作废

    def test_strict_challenge_requires_sid(self, strict):
        service = InteractiveCaptcha()
        challenge = service.challenge(
            captcha_type="slide",
            scene="login",
            action=CAPTCHA_ACTION_LOGIN,
            env={"fp": "fph"},
        )
        stored = captcha_cache.load_challenge(challenge["challenge_id"])
        with pytest.raises(CaptchaInvalidError):
            service.verify(
                challenge_id=challenge["challenge_id"],
                captcha_type=challenge["type"],
                scene="login",
                answer=stored["secret"],
                action=CAPTCHA_ACTION_LOGIN,
            )
