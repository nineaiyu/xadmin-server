# -*- coding: utf-8 -*-
"""邀请开户与账号有效期测试。

口径钉死：

- 邀请发送 = 待激活（``invite_status=pending``）+ 密码不可用（登录被拒）+ 邮件含一次性链接；
  未配置邮件渠道（非 locmem/console 且 EMAIL_HOST 为空）时 fail-closed 返回可读错误；
- 激活 = 令牌一次性（激活即失效、复用返回「已激活」）、无效令牌不泄露原因、
  密码强度 / 泄露库 / 历史校验与注册 / 忘记密码同口径；
- 账号有效期 = 到期登录拦截（``login_success`` 与密码过期同一拦截面）、
  到期前提醒（同日去重）、到期自动停用（is_active=False + 通知）。
"""

import datetime
from types import SimpleNamespace

import pytest
from django.conf import settings as dj_settings
from django.core import mail
from django.utils import timezone

from system.models import UserInfo
from system.utils import account_expiry, user_invite
from system.utils.account_expiry import disable_expired_accounts, is_account_expired, notify_expiring_accounts
from system.utils.auth import ValidateError
from system.views.auth.login import login_success

pytestmark = pytest.mark.django_db

USER_URL = "/api/system/user"
ACCEPT_URL = "/api/system/auth/invite/accept"
VALIDATE_URL = "/api/system/auth/invite/validate"
NEW_PASSWORD = "Invite@2026Abc"
WEAK_PASSWORD = "123"


@pytest.fixture
def invited_user(db):
    return UserInfo.objects.create_user(username="invite_target", password="Old@123456", email="invite@example.com")


class TestSendInvite:
    def test_invite_marks_pending_and_sends_mail(self, auth_client, invited_user):
        resp = auth_client.post(f"{USER_URL}/{invited_user.pk}/invite", {}, format="json")
        assert resp.status_code == 200, resp.content
        assert resp.json()["code"] == 1000

        invited_user.refresh_from_db()
        assert invited_user.invite_status == UserInfo.InviteStatusChoices.PENDING
        assert invited_user.invited_time is not None
        assert invited_user.has_usable_password() is False  # 邀请态不可用密码 → 登录被拒

        assert len(mail.outbox) == 1
        assert "invite_target" in mail.outbox[0].body
        assert "/#/invite/accept?token=" in mail.outbox[0].body

    def test_invite_fail_closed_without_mail_channel(self, auth_client, invited_user, monkeypatch):
        monkeypatch.setattr(dj_settings, "EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
        monkeypatch.setattr(dj_settings, "EMAIL_HOST", "")
        resp = auth_client.post(f"{USER_URL}/{invited_user.pk}/invite", {}, format="json")
        assert resp.status_code == 200
        assert resp.json()["code"] == 1001
        invited_user.refresh_from_db()
        assert invited_user.invite_status == ""  # 未产生半邀请状态

    def test_invited_account_cannot_login_with_old_password(self, auth_client, invited_user):
        from django.contrib.auth import authenticate

        auth_client.post(f"{USER_URL}/{invited_user.pk}/invite", {}, format="json")
        assert authenticate(username="invite_target", password="Old@123456") is None


class TestCreateWithInvite:
    """创建即邀请（遗留收口）：``POST /user`` 带 ``invite=true`` 一步完成邀请开户。"""

    def test_create_with_invite_marks_pending_and_sends_mail(self, auth_client):
        payload = {
            "username": "created_invite",
            "nickname": "一步邀请",
            "email": "created_invite@example.com",
            "invite": True,
        }
        resp = auth_client.post(USER_URL, payload, format="json")
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000, resp.data

        user = UserInfo.objects.get(username="created_invite")
        assert user.invite_status == UserInfo.InviteStatusChoices.PENDING
        assert user.invited_time is not None
        assert user.has_usable_password() is False  # 邀请态不可用密码 → 登录被拒
        assert resp.data["data"]["invite_status"]["value"] == "pending"
        assert len(mail.outbox) == 1
        assert "/#/invite/accept?token=" in mail.outbox[0].body

    def test_create_with_invite_ignores_submitted_password(self, auth_client):
        """邀请模式下提交的密码一律忽略（最终密码只能由被邀请人自行设置）。"""
        payload = {
            "username": "created_invite2",
            "password": "Whatever@123456",
            "email": "created_invite2@example.com",
            "invite": True,
        }
        resp = auth_client.post(USER_URL, payload, format="json")
        assert resp.data["code"] == 1000, resp.data
        user = UserInfo.objects.get(username="created_invite2")
        assert user.has_usable_password() is False
        assert user.check_password("Whatever@123456") is False

    def test_create_without_invite_still_requires_password(self, auth_client):
        resp = auth_client.post(USER_URL, {"username": "plain_user"}, format="json")
        assert resp.data["code"] != 1000, resp.data
        assert not UserInfo.objects.filter(username="plain_user").exists()

    def test_create_with_invite_fail_closed_without_mail_channel(self, auth_client, monkeypatch):
        """邮件渠道不可用时不创建（避免「收不到邀请又无法登录」的死号）。"""
        monkeypatch.setattr(dj_settings, "EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
        monkeypatch.setattr(dj_settings, "EMAIL_HOST", "")
        resp = auth_client.post(USER_URL, {"username": "no_channel", "invite": True}, format="json")
        assert resp.data["code"] == 1001
        assert not UserInfo.objects.filter(username="no_channel").exists()

    def test_create_with_invite_requires_invite_permission(self, auth_client, monkeypatch):
        """创建即邀请需同时具备邀请权限点（invite:SystemUser），否则 403 且不创建。"""
        monkeypatch.setattr("system.views.admin.user.user_has_permission", lambda *args, **kwargs: False)
        resp = auth_client.post(
            USER_URL, {"username": "no_perm", "email": "no_perm@example.com", "invite": True}, format="json"
        )
        assert resp.status_code == 403
        assert not UserInfo.objects.filter(username="no_perm").exists()


class TestAcceptInvite:
    def test_accept_flow_and_token_single_use(self, api_client, auth_client, invited_user):
        token = user_invite.send_invite(invited_user)

        resp = api_client.get(VALIDATE_URL, {"token": token})
        assert resp.json()["data"]["state"] == "pending"
        assert resp.json()["data"]["username"] == "invite_target"

        resp = api_client.post(ACCEPT_URL, {"token": token, "password": NEW_PASSWORD}, format="json")
        assert resp.status_code == 200, resp.content
        assert resp.json()["code"] == 1000
        invited_user.refresh_from_db()
        assert invited_user.invite_status == UserInfo.InviteStatusChoices.ACCEPTED
        assert invited_user.check_password(NEW_PASSWORD)

        # 令牌一次性：激活后复用返回「已激活」，且不再改写密码
        resp = api_client.post(ACCEPT_URL, {"token": token, "password": "Another@2026Xyz"}, format="json")
        assert resp.json()["code"] == 1002
        invited_user.refresh_from_db()
        assert invited_user.check_password(NEW_PASSWORD)

    def test_invalid_token_rejected(self, api_client):
        resp = api_client.post(ACCEPT_URL, {"token": "not-a-token", "password": NEW_PASSWORD}, format="json")
        assert resp.json()["code"] == 1001

    def test_weak_password_rejected(self, api_client, invited_user):
        token = user_invite.send_invite(invited_user)
        resp = api_client.post(ACCEPT_URL, {"token": token, "password": WEAK_PASSWORD}, format="json")
        assert resp.json()["code"] == 1003
        invited_user.refresh_from_db()
        assert invited_user.invite_status == UserInfo.InviteStatusChoices.PENDING

    def test_leaked_password_rejected(self, api_client, invited_user, monkeypatch):
        monkeypatch.setattr("settings.utils.password.check_leak_password", lambda password: True)
        token = user_invite.send_invite(invited_user)
        resp = api_client.post(ACCEPT_URL, {"token": token, "password": NEW_PASSWORD}, format="json")
        assert resp.json()["code"] == 1003


class TestAccountExpiry:
    def test_login_blocked_when_expired(self, invited_user):
        invited_user.date_expired = timezone.now() - datetime.timedelta(days=1)
        invited_user.save(update_fields=["date_expired"])
        assert is_account_expired(invited_user) is True
        with pytest.raises(ValidateError):
            login_success(SimpleNamespace(), invited_user)

    def test_not_expired_without_or_future_date(self, invited_user):
        assert is_account_expired(invited_user) is False  # 空 = 永不过期
        invited_user.date_expired = timezone.now() + datetime.timedelta(days=30)
        invited_user.save(update_fields=["date_expired"])
        assert is_account_expired(invited_user) is False

    def test_notify_expiring_accounts_daily_once(self, invited_user):
        invited_user.date_expired = timezone.now() + datetime.timedelta(days=3)
        invited_user.save(update_fields=["date_expired"])

        assert notify_expiring_accounts() == 1
        assert len(mail.outbox) == 1
        # 同一天重复执行不重复打扰（cache.add 去重）
        assert notify_expiring_accounts() == 0

    def test_notify_disabled_when_remind_days_zero(self, invited_user, monkeypatch):
        invited_user.date_expired = timezone.now() + datetime.timedelta(days=3)
        invited_user.save(update_fields=["date_expired"])
        monkeypatch.setattr(account_expiry, "remind_days", lambda: 0)
        assert notify_expiring_accounts() == 0

    def test_disable_expired_accounts(self, invited_user):
        invited_user.date_expired = timezone.now() - datetime.timedelta(hours=1)
        invited_user.save(update_fields=["date_expired"])

        assert disable_expired_accounts() == 1
        invited_user.refresh_from_db()
        assert invited_user.is_active is False
        assert len(mail.outbox) == 1  # 到期通知（站内信 + 邮件）

    def test_expire_job_registration(self):
        import system.tasks  # noqa: F401 —— 显式导入触发周期任务注册（celery autodiscover 同口径）
        from common.celery.decorator import _need_registered_period_tasks

        names = set()
        for item in _need_registered_period_tasks:
            names.update(item.keys())
        assert "system.tasks.account_expiry_job" in names
