# -*- coding: utf-8 -*-
"""OTP 密钥落库加密：读写透明、存量明文兼容。"""

import pytest

from identity.models import UserInfo

pytestmark = pytest.mark.django_db


def test_otp_secret_roundtrip_through_encrypted_field():
    user = UserInfo.objects.create_user(username="otp-encrypted", password="Test@123456")
    user.otp_secret_key = "JBSWY3DPEHPK3PXP"
    user.save(update_fields=["otp_secret_key"])

    user.refresh_from_db()
    assert user.otp_secret_key == "JBSWY3DPEHPK3PXP"

    # 直接读列值应为 v3 密文（不是明文）
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("SELECT otp_secret_key FROM identity_userinfo WHERE id = %s", [user.pk])
        raw = cursor.fetchone()[0]
    assert raw.startswith("v3:")
    assert "JBSWY3DPEHPK3PXP" not in raw


def test_legacy_plaintext_otp_still_readable():
    from django.db import connection

    user = UserInfo.objects.create_user(username="otp-legacy", password="Test@123456")
    # 模拟升级前存量明文（QuerySet.update 会走字段加密，必须用原始 SQL）
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE identity_userinfo SET otp_secret_key = %s WHERE id = %s",
            ["LEGACYPLAINTEXT", user.pk],
        )
    user.refresh_from_db()
    assert user.otp_secret_key == "LEGACYPLAINTEXT"
