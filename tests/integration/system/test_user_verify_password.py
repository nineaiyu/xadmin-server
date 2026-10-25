# -*- coding: utf-8 -*-
"""个人口令核验端点（锁屏解锁）集成测试。

端点只做校验：不发凭证、不改状态（与 MFA 二次确认刻意区分），因此用例聚焦
「正确/错误口令」「密文模式拒绝」「失败审计留痕」「登录态要求」四条边界。
"""

import pytest

from audit.services import OperationLog
from common.base.crypto import AESCipherV2
from tests.integration.aes_v2 import encrypt_v2

pytestmark = pytest.mark.django_db

VERIFY_URL = "/api/identity/userinfo/verify-password"
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "Admin@123456"


@pytest.fixture(autouse=True)
def _plaintext_mode(settings):
    """默认按明文模式提交（导入/脚本等非浏览器客户端形态），密文口径单独用例覆盖。"""
    settings.SECURITY_USER_PASSWORD_ENCRYPTED_ENABLED = False


def test_verify_password_accepts_correct_plaintext(auth_client):
    resp = auth_client.post(VERIFY_URL, {"password": ADMIN_PASSWORD}, format="json")
    assert resp.status_code == 200, resp.data
    assert resp.data["code"] == 1000


def test_verify_password_rejects_wrong_and_records_audit(auth_client):
    before = OperationLog.objects.filter(module="User:verify-password").count()
    resp = auth_client.post(VERIFY_URL, {"password": "WrongPass@123"}, format="json")
    assert resp.status_code == 400
    assert resp.data["code"] == 1002
    assert OperationLog.objects.filter(module="User:verify-password").count() == before + 1


def test_verify_password_does_not_touch_confirm_state(auth_client):
    """校验不写二次确认状态：锁屏解锁不应顺带续期敏感操作的免验窗口。"""
    confirm_state_url = "/api/mfa/confirm?confirm_type=password"
    assert auth_client.get(confirm_state_url).data["data"]["confirmed"] is False
    auth_client.post(VERIFY_URL, {"password": ADMIN_PASSWORD}, format="json")
    assert auth_client.get(confirm_state_url).data["data"]["confirmed"] is False


def test_verify_password_encrypted_mode_rejects_bad_ciphertext(auth_client, settings):
    settings.SECURITY_USER_PASSWORD_ENCRYPTED_ENABLED = True
    resp = auth_client.post(VERIFY_URL, {"password": "not-a-cipher-text"}, format="json")
    assert resp.status_code == 400
    assert resp.data["code"] == 1001


def test_verify_password_accepts_v2_ciphertext(auth_client, settings):
    settings.SECURITY_USER_PASSWORD_ENCRYPTED_ENABLED = True
    encrypted = encrypt_v2(ADMIN_USERNAME, ADMIN_PASSWORD)
    resp = auth_client.post(VERIFY_URL, {"password": encrypted}, format="json")
    assert resp.status_code == 200, resp.data
    assert resp.data["code"] == 1000


def test_verify_password_accepts_legacy_v1_ciphertext(auth_client, settings):
    settings.SECURITY_USER_PASSWORD_ENCRYPTED_ENABLED = True
    encrypted = AESCipherV2(ADMIN_USERNAME).encrypt(ADMIN_PASSWORD.encode()).decode()
    resp = auth_client.post(VERIFY_URL, {"password": encrypted}, format="json")
    assert resp.status_code == 200, resp.data
    assert resp.data["code"] == 1000


def test_verify_password_requires_login(api_client):
    resp = api_client.post(VERIFY_URL, {"password": ADMIN_PASSWORD}, format="json")
    assert resp.status_code in (401, 403)
