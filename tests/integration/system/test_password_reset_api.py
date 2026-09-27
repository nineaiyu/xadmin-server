# -*- coding: utf-8 -*-
"""免登密码重置接口（system/views/auth/reset.py）的分支覆盖。

该视图是匿名可达的安全链路（permission_classes=[]），post() 内的每个拒绝
分支都是防线：缺密码 / LDAP 绑定用户 / 弱口令 / 泄露口令 / 历史口令复用，
以及成功路径的令牌消费与哈希台账。

传输协议：SECURITY_RESET_PASSWORD_ENCRYPTED_ENABLED 默认开启，客户端提交
的是以 verify_token 为密钥的 AES v2（WebCrypto PBKDF2+GCM）密文——复用
test_aes_cipher_v2 的 _encrypt_v2 构造与前端同源的密文；另覆盖开关关闭时
的明文分支。验证码校验（verify_sms_email_code）在 auth 工具层有独立覆盖，
此处打桩聚焦视图分支本身。
"""

from types import SimpleNamespace

import pytest

from system.models.ldap import LdapUserBinding
from system.views.auth import reset as reset_view
from tests.unit.common.test_aes_cipher_v2 import _encrypt_v2

RESET_URL = "/api/system/auth/reset"
VERIFY_TOKEN = "vt-test-token"
VERIFY_CODE = "123456"


@pytest.fixture
def email_user(normal_user):
    """verify_sms_email_code 返回 (query_key, target, verify_token)，以 email 定位用户。"""
    normal_user.email = "zhangsan@example.com"
    normal_user.save()
    return normal_user


@pytest.fixture
def verified(monkeypatch, email_user):
    """打桩验证码校验：视为已完成「邮箱验证码已下发且校验通过」。"""
    monkeypatch.setattr(
        reset_view,
        "verify_sms_email_code",
        lambda request, block_utils: ("email", email_user.email, VERIFY_TOKEN),
    )
    expired: list[str] = []
    monkeypatch.setattr(
        reset_view,
        "TokenTempCache",
        SimpleNamespace(expired_cache_token=lambda token: expired.append(token)),
    )
    return expired


def post_reset(api_client, password, *, encrypt=True):
    payload = {"verify_token": VERIFY_TOKEN, "verify_code": VERIFY_CODE}
    if password is not None:
        payload["password"] = _encrypt_v2(VERIFY_TOKEN, password) if encrypt else password
    return api_client.post(RESET_URL, payload, format="json")


class TestResetPasswordApi:
    def test_missing_password_rejected(self, api_client, verified):
        resp = post_reset(api_client, password=None)
        assert resp.data["code"] == 1004

    def test_ldap_bound_user_cannot_reset_locally(self, api_client, verified, email_user):
        LdapUserBinding.objects.create(user=email_user, dn="uid=zhangsan,dc=example,dc=com")
        resp = post_reset(api_client, "NewStrong@2026")
        assert resp.data["code"] == 1002
        assert "LDAP" in str(resp.data["detail"])

    def test_weak_password_rejected_by_rules(self, api_client, verified):
        # 默认最短 10 位（SECURITY_PASSWORD_MIN_LENGTH=10）
        resp = post_reset(api_client, "12345678")
        assert resp.data["code"] == 1002
        # 激活语言为 zh，断言用中英双关键词防词条调整
        assert any(k in str(resp.data["detail"]) for k in ("rules", "安全规则"))

    def test_leaked_password_rejected(self, api_client, verified, monkeypatch):
        monkeypatch.setattr(reset_view, "check_leak_password", lambda pwd: True)
        resp = post_reset(api_client, "NewStrong@2026")
        assert resp.data["code"] == 1002
        assert any(k in str(resp.data["detail"]) for k in ("leaked", "泄露"))

    def test_history_password_rejected(self, api_client, verified, monkeypatch):
        monkeypatch.setattr(reset_view, "check_history_password", lambda user, pwd: True)
        resp = post_reset(api_client, "NewStrong@2026")
        assert resp.data["code"] == 1002
        assert any(k in str(resp.data["detail"]) for k in ("recent", "最近"))

    def test_success_resets_password_and_expires_token(self, api_client, verified, email_user, monkeypatch):
        recorded: list[str] = []
        monkeypatch.setattr(reset_view, "record_password_hash", lambda user, pwd_hash: recorded.append(pwd_hash))
        resp = post_reset(api_client, "NewStrong@2026")
        assert resp.data["code"] == 1000, resp.data
        email_user.refresh_from_db()
        assert email_user.check_password("NewStrong@2026")
        # 哈希入台账（历史口令复用校验的数据源）+ 一次性令牌立即作废
        assert recorded
        assert verified == [VERIFY_TOKEN]

    def test_encryption_disabled_uses_raw_password(self, api_client, verified, email_user, settings):
        settings.SECURITY_RESET_PASSWORD_ENCRYPTED_ENABLED = False
        resp = post_reset(api_client, "NewStrong@2026", encrypt=False)
        assert resp.data["code"] == 1000, resp.data
        email_user.refresh_from_db()
        assert email_user.check_password("NewStrong@2026")
