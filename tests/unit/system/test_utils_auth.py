# -*- coding: utf-8 -*-
"""system.utils.auth 单元测试（token 生命周期、验证码校验）。"""
import pytest
from django.conf import settings
from rest_framework.exceptions import APIException

from captcha.models import CaptchaStore
from captcha.utils import CaptchaAuth
from system.utils.auth import check_captcha, get_token_lifetime

pytestmark = pytest.mark.django_db


class TestGetTokenLifetime:
    def test_returns_seconds(self):
        result = get_token_lifetime(None)
        assert result["access_token_lifetime"] == int(
            settings.SIMPLE_JWT.get("ACCESS_TOKEN_LIFETIME").total_seconds()
        )
        assert result["refresh_token_lifetime"] == int(
            settings.SIMPLE_JWT.get("REFRESH_TOKEN_LIFETIME").total_seconds()
        )


class TestCheckCaptcha:
    def test_skipped_when_not_required(self):
        assert check_captcha(need=False, captcha_key="", captcha_code="") is True

    def test_raises_when_required_but_missing(self):
        with pytest.raises(APIException):
            check_captcha(need=True, captcha_key="", captcha_code="")

    def test_valid_captcha_passes(self):
        result = CaptchaAuth().generate()
        captcha = CaptchaStore.objects.get(hashkey=result["captcha_key"])
        assert (
            check_captcha(
                need=True,
                captcha_key=result["captcha_key"],
                captcha_code=captcha.response,
            )
            is True
        )

    def test_wrong_captcha_raises(self):
        result = CaptchaAuth().generate()
        with pytest.raises(APIException):
            check_captcha(
                need=True, captcha_key=result["captcha_key"], captcha_code="wrong"
            )