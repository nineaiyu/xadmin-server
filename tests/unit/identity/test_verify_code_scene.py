# -*- coding: utf-8 -*-
"""验证码令牌场景绑定：login / reset / bind 的码不能跨场景复用。"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from identity.utils.auth.captcha import ValidateError
from identity.utils.auth.login_log import verify_sms_email_code
from settings.utils.security import ResetBlockUtil

pytestmark = pytest.mark.django_db


def _request():
    return SimpleNamespace(
        data={"verify_token": "verify-token", "verify_code": "123456"},
        META={"REMOTE_ADDR": "10.0.0.1"},
    )


def test_scene_mismatch_rejected():
    data = {"target": "a@example.com", "query_key": "email", "scene": "login"}
    with patch("identity.utils.auth.login_log.TokenTempCache.validate_cache_token", return_value=data):
        with pytest.raises(ValidateError):
            verify_sms_email_code(_request(), ResetBlockUtil, scenes=("reset",))


def test_matching_scene_passes():
    data = {"target": "a@example.com", "query_key": "email", "scene": "reset"}
    with (
        patch("identity.utils.auth.login_log.TokenTempCache.validate_cache_token", return_value=data),
        patch("identity.utils.auth.login_log.SendAndVerifyCodeUtil") as util,
    ):
        assert verify_sms_email_code(_request(), ResetBlockUtil, scenes=("reset",)) == (
            "email",
            "a@example.com",
            "verify-token",
        )
        util.return_value.verify.assert_called_once_with("123456")


def test_legacy_token_without_scene_still_accepted():
    """升级前签发的令牌没有 scene 字段：按 TTL 自然过期，不因缺失字段误伤。"""
    data = {"target": "a@example.com", "query_key": "email"}
    with (
        patch("identity.utils.auth.login_log.TokenTempCache.validate_cache_token", return_value=data),
        patch("identity.utils.auth.login_log.SendAndVerifyCodeUtil"),
    ):
        assert verify_sms_email_code(_request(), ResetBlockUtil, scenes=("reset",)) == (
            "email",
            "a@example.com",
            "verify-token",
        )
