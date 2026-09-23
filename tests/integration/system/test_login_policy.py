# -*- coding: utf-8 -*-
"""登录访问策略集成测试。

口径钉死：
- 按 priority 升序首个命中生效；accept 可豁免后续 reject（白名单窗口）；
- 时段（星期 + 起止，支持跨天）/ 网段（CIDR 与区间）匹配；
- reject 命中 → 登录被拒 + 登录日志记 policy_result；
- require_mfa 命中且用户有可用方式 → 返回 mfa_required；无可用方式降级放行（防自锁）；
- 并发会话限制：超限踢最久未活跃会话（会话级令牌失效）。
"""

from datetime import time

import pytest
from django.utils import timezone

from system.models import LoginAccessPolicy, UserLoginLog, UserSession
from system.utils.login_policy import evaluate_login_policy, match_ip, match_time, preview_login_policy

pytestmark = pytest.mark.django_db

BASIC_LOGIN_URL = "/api/system/login/basic"
POLICY_URL = "/api/system/login-policies"
PREVIEW_URL = "/api/system/login-policies/preview"


@pytest.fixture
def login_free(settings):
    settings.SECURITY_LOGIN_CAPTCHA_ENABLED = False
    settings.SECURITY_LOGIN_ENCRYPTED_ENABLED = False
    settings.SECURITY_LOGIN_TEMP_TOKEN_ENABLED = False


def _login(api_client):
    return api_client.post(BASIC_LOGIN_URL, {"username": "zhangsan", "password": "Test@123456"}, format="json")


class TestMatchHelpers:
    def test_match_time_weekday_and_window(self):
        policy = LoginAccessPolicy(name="work", weekdays=[1, 2, 3, 4, 5], start_time=time(9, 0), end_time=time(18, 0))
        # 2026-09-22 是周二
        assert match_time(policy, timezone.make_aware(timezone.datetime(2026, 9, 22, 10, 0))) is True
        assert match_time(policy, timezone.make_aware(timezone.datetime(2026, 9, 22, 20, 0))) is False
        # 周日
        assert match_time(policy, timezone.make_aware(timezone.datetime(2026, 9, 27, 10, 0))) is False

    def test_match_time_cross_day(self):
        policy = LoginAccessPolicy(name="night", start_time=time(22, 0), end_time=time(6, 0))
        assert match_time(policy, timezone.make_aware(timezone.datetime(2026, 9, 22, 23, 30))) is True
        assert match_time(policy, timezone.make_aware(timezone.datetime(2026, 9, 22, 5, 0))) is True
        assert match_time(policy, timezone.make_aware(timezone.datetime(2026, 9, 22, 12, 0))) is False

    def test_match_ip(self):
        policy = LoginAccessPolicy(name="intranet", ip_ranges="192.168.0.0/24\n10.1.1.1-10.1.1.20")
        assert match_ip(policy, "192.168.0.10") is True
        assert match_ip(policy, "10.1.1.5") is True
        assert match_ip(policy, "8.8.8.8") is False
        # 有网段限制但拿不到 IP → 不匹配（防绕过）
        assert match_ip(policy, "") is False


class TestEvaluatePolicy:
    def test_first_match_wins_and_accept_exempts_reject(self, normal_user):
        LoginAccessPolicy.objects.create(
            name="allow-all",
            priority=10,
            target_type=LoginAccessPolicy.TargetType.ALL,
            action=LoginAccessPolicy.Action.ACCEPT,
        )
        LoginAccessPolicy.objects.create(
            name="reject-all",
            priority=100,
            target_type=LoginAccessPolicy.TargetType.ALL,
            action=LoginAccessPolicy.Action.REJECT,
        )
        result = evaluate_login_policy(normal_user, "127.0.0.1")
        assert result["action"] == "accept"
        assert result["policy"] == "allow-all"

    def test_role_target_match(self, normal_user, role):
        policy = LoginAccessPolicy.objects.create(
            name="role-reject",
            priority=10,
            target_type=LoginAccessPolicy.TargetType.ROLE,
            target_value=role.code,
            action=LoginAccessPolicy.Action.REJECT,
        )
        assert policy.pk
        result = evaluate_login_policy(normal_user, "127.0.0.1")
        assert result["action"] == "reject"

    def test_no_policy_returns_none(self, normal_user):
        result = evaluate_login_policy(normal_user, "127.0.0.1")
        assert result["action"] is None
        assert result["result"] == ""

    def test_preview_reports_each_policy(self, normal_user):
        LoginAccessPolicy.objects.create(
            name="p1", priority=10, target_type=LoginAccessPolicy.TargetType.ALL, action=LoginAccessPolicy.Action.RECORD
        )
        LoginAccessPolicy.objects.create(
            name="p2",
            priority=20,
            target_type=LoginAccessPolicy.TargetType.USER,
            target_value="other",
            action=LoginAccessPolicy.Action.REJECT,
        )
        preview = preview_login_policy(normal_user, "127.0.0.1")
        assert preview["action"] == "record"
        assert preview["policy"] == "p1"
        assert [item["matched"] for item in preview["items"]] == [True, False]


class TestLoginBlockedByPolicy:
    def test_reject_policy_blocks_login(self, api_client, normal_user, login_free):
        LoginAccessPolicy.objects.create(
            name="仅工作时间可登录",
            priority=100,
            target_type=LoginAccessPolicy.TargetType.ALL,
            action=LoginAccessPolicy.Action.REJECT,
        )
        resp = _login(api_client)
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1001
        assert "仅工作时间可登录" in str(resp.data["detail"])
        log = UserLoginLog.objects.filter(creator=normal_user).first()
        assert log is not None and log.policy_result.startswith("reject:")

    def test_record_policy_allows_login(self, api_client, normal_user, login_free):
        LoginAccessPolicy.objects.create(
            name="record-only",
            priority=100,
            target_type=LoginAccessPolicy.TargetType.ALL,
            action=LoginAccessPolicy.Action.RECORD,
        )
        resp = _login(api_client)
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["access"]
        log = UserLoginLog.objects.filter(creator=normal_user).first()
        assert log.policy_result == "record:record-only"

    def test_require_mfa_without_method_degrade(self, api_client, normal_user, login_free):
        """策略要求 MFA 但用户无可用方式 → 降级放行（避免策略把自己锁在门外）"""
        LoginAccessPolicy.objects.create(
            name="must-mfa",
            priority=100,
            target_type=LoginAccessPolicy.TargetType.ALL,
            action=LoginAccessPolicy.Action.REQUIRE_MFA,
        )
        resp = _login(api_client)
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["access"]

    def test_require_mfa_with_otp_returns_mfa_required(self, api_client, normal_user, login_free):
        normal_user.otp_secret_key = "JBSWY3DPEHPK3PXP"
        normal_user.mfa_level = 1
        normal_user.save(update_fields=["otp_secret_key", "mfa_level"])
        LoginAccessPolicy.objects.create(
            name="must-mfa",
            priority=100,
            target_type=LoginAccessPolicy.TargetType.ALL,
            action=LoginAccessPolicy.Action.REQUIRE_MFA,
        )
        resp = _login(api_client)
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["mfa_required"] is True
        assert resp.data["data"]["mfa_token"]


class TestPolicyApi:
    def test_crud_and_preview(self, auth_client, normal_user):
        resp = auth_client.post(
            POLICY_URL,
            {
                "name": "办公网段",
                "priority": 10,
                "target_type": "all",
                "action": "reject",
                "ip_ranges": "192.168.0.0/24",
            },
            format="json",
        )
        assert resp.data["code"] == 1000, resp.data
        pk = resp.data["data"]["pk"]
        resp = auth_client.post(PREVIEW_URL, {"username": normal_user.username, "ip": "192.168.0.8"}, format="json")
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["action"] == "reject"
        assert resp.data["data"]["items"][0]["effective"] is True
        # 时段只给一半 → 校验失败
        resp = auth_client.patch(f"{POLICY_URL}/{pk}", {"start_time": "09:00:00"}, format="json")
        assert resp.data["code"] != 1000


class TestSessionLimit:
    def test_limit_kicks_oldest_session(self, normal_user, settings):
        from system.utils.session import register_user_session

        settings.SECURITY_LOGIN_MAX_SESSIONS = 2
        sessions = [register_user_session(None, normal_user, UserLoginLog.LoginTypeChoices.USERNAME) for _ in range(3)]
        online = UserSession.objects.filter(creator=normal_user, status=UserSession.Status.ONLINE)
        assert online.count() == 2
        assert not online.filter(pk=sessions[0].pk).exists()
        # 被踢会话写会话级失效标记（token 立即失效）
        from common.cache.storage import SessionTokenRevokedCache

        assert SessionTokenRevokedCache(sessions[0].pk).get_storage_cache()

    def test_limit_zero_means_unlimited(self, normal_user, settings):
        from system.utils.session import register_user_session

        settings.SECURITY_LOGIN_MAX_SESSIONS = 0
        for _ in range(3):
            register_user_session(None, normal_user, UserLoginLog.LoginTypeChoices.USERNAME)
        assert UserSession.objects.filter(creator=normal_user, status=UserSession.Status.ONLINE).count() == 3
