# -*- coding: utf-8 -*-
"""登录态 token 签发收口（identity.services.token_issue_login）单元测试。

守护 issue_session_token 的五步口径与降级语义：任何新登录路径都必须经本函数
签发，这些断言即收敛后不漂移的依据。
"""

import pytest
from rest_framework.test import APIRequestFactory
from rest_framework_simplejwt.tokens import AccessToken

from audit.models.log import UserLoginLog
from identity.models import UserSession
from identity.services.token_issue_login import issue_session_token

pytestmark = pytest.mark.django_db

_factory = APIRequestFactory()


def _payload(access: str) -> dict:
    return AccessToken(access).payload


class TestIssueSessionToken:
    def test_returns_full_payload_and_registers_session(self, normal_user):
        """返回载荷含 refresh/access/双生存期/改密标记，并登记 UserSession。"""
        request = _factory.post("/api/identity/login/basic")
        result = issue_session_token(request, normal_user, UserLoginLog.LoginTypeChoices.USERNAME)

        assert result["refresh"] and result["access"]
        assert result["access_token_lifetime"] > 0
        assert result["refresh_token_lifetime"] > 0
        assert result["must_change_password"] is False

        # sid claim 指向本次登记的会话
        session = UserSession.objects.get(creator=normal_user, login_type=UserLoginLog.LoginTypeChoices.USERNAME)
        assert _payload(result["access"])["sid"] == str(session.pk)

    def test_touches_last_login(self, normal_user):
        """默认刷新 last_login（账密/验证码/注册/MFA/OAuth 的统一口径）。"""
        request = _factory.post("/api/identity/login/basic")
        assert normal_user.last_login is None

        issue_session_token(request, normal_user, UserLoginLog.LoginTypeChoices.USERNAME)

        normal_user.refresh_from_db()
        assert normal_user.last_login is not None

    def test_skip_last_login_when_disabled(self, normal_user):
        """touch_last_login=False 不刷新（用户模拟：不污染目标账号 last_login）。"""
        request = _factory.post("/api/identity/login/basic")

        issue_session_token(request, normal_user, UserLoginLog.LoginTypeChoices.IMPERSONATE, touch_last_login=False)

        normal_user.refresh_from_db()
        assert normal_user.last_login is None

    def test_extra_claims_written(self, normal_user):
        """extra_claims 写入 token（用户模拟的 impersonator claim）。"""
        request = _factory.post("/api/identity/login/basic")

        result = issue_session_token(
            request,
            normal_user,
            UserLoginLog.LoginTypeChoices.IMPERSONATE,
            extra_claims={"imp": "42"},
            touch_last_login=False,
        )

        assert _payload(result["access"])["imp"] == "42"

    def test_must_change_password_flag(self, normal_user):
        """管理员要求改密时响应带出标记（前端据此引导）。"""
        normal_user.must_change_password = True
        normal_user.save(update_fields=["must_change_password"])
        request = _factory.post("/api/identity/login/basic")

        result = issue_session_token(request, normal_user, UserLoginLog.LoginTypeChoices.USERNAME)

        assert result["must_change_password"] is True

    def test_session_failure_degrades_without_sid(self, normal_user, monkeypatch):
        """会话登记失败仅告警：仍签发无 sid 令牌，登录不阻断。"""

        def boom(*args, **kwargs):
            raise RuntimeError("session down")

        monkeypatch.setattr("identity.services.token_issue_login.register_user_session", boom)
        request = _factory.post("/api/identity/login/basic")

        result = issue_session_token(request, normal_user, UserLoginLog.LoginTypeChoices.USERNAME)

        assert result["refresh"] and result["access"]
        assert "sid" not in _payload(result["access"])
        assert UserSession.objects.filter(creator=normal_user).exists() is False

    def test_claim_bind_failure_degrades_without_sid(self, normal_user, monkeypatch):
        """sid claim 注入失败退回无 sid 令牌（该令牌不参与按会话吊销）。"""

        def boom(*args, **kwargs):
            raise RuntimeError("claim bind failed")

        monkeypatch.setattr("identity.services.token_issue_login.bind_session_claim", boom)
        request = _factory.post("/api/identity/login/basic")

        result = issue_session_token(request, normal_user, UserLoginLog.LoginTypeChoices.USERNAME)

        assert result["refresh"] and result["access"]
        assert "sid" not in _payload(result["access"])
