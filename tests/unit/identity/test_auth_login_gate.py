# -*- coding: utf-8 -*-
"""登录唯一收口：全局登录开关对 complete_login 的所有调用方生效。"""

import pytest
from django.conf import settings
from django.test import RequestFactory

from common.base.crypto import AESCipherV2
from identity.models import UserInfo
from identity.services.auth_login import complete_login
from identity.utils.auth import ValidateError, decrypt_submitted

pytestmark = pytest.mark.django_db


def test_complete_login_rejects_when_login_disabled(monkeypatch):
    user = UserInfo.objects.create_user(username="gate-user", password="Test@123456")
    request = RequestFactory().post("/api/identity/login")
    monkeypatch.setattr(settings, "SECURITY_LOGIN_ACCESS_ENABLED", False)

    response = complete_login(request, user)

    assert response is not None
    assert response.data["code"] == 1001


@pytest.mark.parametrize("bad", ["x", "", "not-base64!!!"])
def test_decrypt_submitted_rejects_bad_ciphertext(bad):
    with pytest.raises(ValidateError):
        decrypt_submitted(bad, "token")


def test_decrypt_submitted_accepts_valid_ciphertext():
    ciphertext = AESCipherV2("token").encrypt(b"plain-value")
    assert decrypt_submitted(ciphertext, "token") == "plain-value"
