# -*- coding: utf-8 -*-
"""identity.utils.auth 单元测试（token 生命周期、验证码校验）。"""

import pytest
from django.conf import settings
from rest_framework.exceptions import APIException

from captcha import tickets
from captcha.const import CAPTCHA_ACTION_LOGIN
from identity.utils.auth import check_captcha, get_token_lifetime

pytestmark = pytest.mark.django_db


class TestGetTokenLifetime:
    def test_returns_seconds(self):
        result = get_token_lifetime(None)
        assert result["access_token_lifetime"] == int(settings.SIMPLE_JWT.get("ACCESS_TOKEN_LIFETIME").total_seconds())
        assert result["refresh_token_lifetime"] == int(
            settings.SIMPLE_JWT.get("REFRESH_TOKEN_LIFETIME").total_seconds()
        )


class TestCheckCaptcha:
    def test_skipped_when_not_required(self):
        assert check_captcha(need=False, captcha_ticket="") is True

    def test_raises_when_required_but_missing(self):
        with pytest.raises(APIException):
            check_captcha(need=True, captcha_ticket="")

    def test_valid_ticket_passes(self):
        ticket = tickets.issue_ticket(scene="login", action=CAPTCHA_ACTION_LOGIN, ttl=60)
        assert check_captcha(need=True, captcha_ticket=ticket, action=CAPTCHA_ACTION_LOGIN) is True

    def test_wrong_ticket_raises(self):
        with pytest.raises(APIException):
            check_captcha(need=True, captcha_ticket="invalid", action=CAPTCHA_ACTION_LOGIN)
