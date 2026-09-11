# -*- coding: utf-8 -*-
"""密码安全套件：泄露密码库 / 密码历史 / 密码过期 / 改密链路留存。

四项能力默认关闭（风险对策：先灰度观察再默认开启），用例内按需开启；
登录链路异常登录提醒（异地/新设备/新 IP）已有 test_login_alert.py 覆盖。
"""

import datetime
from types import SimpleNamespace

import pytest
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from common.base.utils import AESCipherV2
from settings.services import (
    check_history_password,
    check_leak_password,
    is_password_expired,
    record_password_hash,
)
from system.serializers.user import ResetPasswordSerializer
from system.serializers.userinfo import ChangePasswordSerializer

pytestmark = pytest.mark.django_db

BASIC_LOGIN_URL = "/api/system/login/basic"


class TestLeakPassword:
    def test_disabled_always_pass(self, normal_user, settings):
        settings.SECURITY_PASSWORD_LEAK_CHECK_ENABLED = False
        assert check_leak_password("123456") is False

    def test_weak_password_hit(self, normal_user, settings):
        settings.SECURITY_PASSWORD_LEAK_CHECK_ENABLED = True
        assert check_leak_password("123456") is True
        assert check_leak_password("password") is True

    def test_strong_password_pass(self, normal_user, settings):
        settings.SECURITY_PASSWORD_LEAK_CHECK_ENABLED = True
        assert check_leak_password("X7#mKq2$vLp9") is False


class TestPasswordHistory:
    def test_disabled_when_count_zero(self, normal_user, settings):
        settings.SECURITY_PASSWORD_HISTORY_COUNT = 0
        record_password_hash(normal_user, normal_user.password)
        assert check_history_password(normal_user, "anything") is False

    def test_hit_recent_history(self, normal_user, settings):
        settings.SECURITY_PASSWORD_HISTORY_COUNT = 1
        record_password_hash(normal_user, "New@Password123")
        from django.contrib.auth.hashers import make_password

        assert check_history_password(normal_user, "New@Password123") is False
        # record 存的是哈希：明文校验需与 make_password 后的哈希比对
        record_password_hash(normal_user, make_password("Old@Password456"))
        assert check_history_password(normal_user, "Old@Password456") is True

    def test_record_refreshes_timestamp(self, normal_user):
        assert normal_user.date_password_updated is None
        record_password_hash(normal_user, normal_user.password)
        normal_user.refresh_from_db()
        assert normal_user.date_password_updated is not None
        assert normal_user.password_histories.count() == 1


class TestPasswordExpiration:
    def test_disabled_when_days_zero(self, normal_user, settings):
        settings.SECURITY_PASSWORD_EXPIRATION_DAYS = 0
        normal_user.date_password_updated = timezone.now() - datetime.timedelta(days=999)
        assert is_password_expired(normal_user) is False

    def test_grace_period_for_legacy_users(self, normal_user, settings):
        settings.SECURITY_PASSWORD_EXPIRATION_DAYS = 30
        # date_password_updated 为空 = 存量用户宽限期，不拦截
        assert is_password_expired(normal_user) is False

    def test_expired_when_over_days(self, normal_user, settings):
        settings.SECURITY_PASSWORD_EXPIRATION_DAYS = 1
        normal_user.date_password_updated = timezone.now() - datetime.timedelta(days=2)
        assert is_password_expired(normal_user) is True

    def test_login_blocked_when_expired(self, api_client, normal_user, settings):
        settings.SECURITY_PASSWORD_EXPIRATION_DAYS = 1
        settings.SECURITY_LOGIN_CAPTCHA_ENABLED = False
        settings.SECURITY_LOGIN_ENCRYPTED_ENABLED = False
        settings.SECURITY_LOGIN_TEMP_TOKEN_ENABLED = False
        normal_user.date_password_updated = timezone.now() - datetime.timedelta(days=2)
        normal_user.save(update_fields=["date_password_updated"])

        resp = api_client.post(
            BASIC_LOGIN_URL,
            {"username": "zhangsan", "password": "Test@123456"},
            format="json",
        )
        assert resp.status_code == 400, resp.data
        assert "密码已过期" in str(resp.data) or "expired" in str(resp.data)

    def test_login_allowed_when_not_expired(self, api_client, normal_user, settings):
        settings.SECURITY_PASSWORD_EXPIRATION_DAYS = 30
        settings.SECURITY_LOGIN_CAPTCHA_ENABLED = False
        settings.SECURITY_LOGIN_ENCRYPTED_ENABLED = False
        settings.SECURITY_LOGIN_TEMP_TOKEN_ENABLED = False
        normal_user.date_password_updated = timezone.now()
        normal_user.save(update_fields=["date_password_updated"])

        resp = api_client.post(
            BASIC_LOGIN_URL,
            {"username": "zhangsan", "password": "Test@123456"},
            format="json",
        )
        assert resp.status_code == 200, resp.data


class TestChangePasswordFlow:
    def _change_password(self, user, old_raw, new_raw):
        # ChangePasswordSerializer 仅需 request.user 作 modifier：用 SimpleNamespace 承载
        cipher = AESCipherV2(user.username)
        return ChangePasswordSerializer(
            instance=user,
            data={
                "old_password": cipher.encrypt(old_raw.encode("utf-8")).decode(),
                "sure_password": cipher.encrypt(new_raw.encode("utf-8")).decode(),
            },
            context={"request": SimpleNamespace(user=user)},
        )

    def test_change_records_history(self, normal_user, settings):
        settings.SECURITY_PASSWORD_HISTORY_COUNT = 3
        serializer = self._change_password(normal_user, "Test@123456", "New@Password123")
        assert serializer.is_valid(), serializer.errors
        instance = serializer.save()
        instance.refresh_from_db()
        assert instance.check_password("New@Password123")
        assert instance.password_histories.count() == 1
        assert instance.date_password_updated is not None

    def test_change_rejects_history_password(self, normal_user, settings):
        settings.SECURITY_PASSWORD_HISTORY_COUNT = 3
        # 序列：A→B（留存历史）→ B→A（A 不在历史，放行并留存）→ A→B（B 在最近 N 次内，被拒）
        old_raw, mid_raw = "Test@123456", "New@Password123"
        serializer = self._change_password(normal_user, old_raw, mid_raw)
        assert serializer.is_valid(), serializer.errors
        serializer.save()

        serializer = self._change_password(normal_user, mid_raw, old_raw)
        assert serializer.is_valid(), serializer.errors
        serializer.save()

        serializer = self._change_password(normal_user, old_raw, mid_raw)
        assert serializer.is_valid()
        with pytest.raises(ValidationError) as exc:
            serializer.save()
        assert "密码不能与最近" in str(exc.value)

    def test_change_rejects_leak_password(self, normal_user, settings):
        settings.SECURITY_PASSWORD_LEAK_CHECK_ENABLED = True
        # 构造满足强度规则（≥10 位 + 大写 + 数字）且命中内置泄露库的密码
        serializer = self._change_password(normal_user, "Test@123456", "Password@123")
        assert serializer.is_valid()
        with pytest.raises(ValidationError) as exc:
            serializer.save()
        assert "泄露库" in str(exc.value)


class TestAdminResetFlow:
    def _reset_password(self, user, new_raw):
        # ResetPasswordSerializer 仅需 request.user 作 modifier：用 SimpleNamespace 承载
        cipher = AESCipherV2(user.username)
        return ResetPasswordSerializer(
            instance=user,
            data={"password": cipher.encrypt(new_raw.encode("utf-8")).decode()},
            context={"request": SimpleNamespace(user=user)},
        )

    def test_reset_records_history_and_checks(self, normal_user, settings):
        settings.SECURITY_PASSWORD_HISTORY_COUNT = 3
        serializer = self._reset_password(normal_user, "New@Password123")
        assert serializer.is_valid(), serializer.errors
        instance = serializer.save()
        instance.refresh_from_db()
        assert instance.check_password("New@Password123")
        assert instance.password_histories.count() == 1

    def test_reset_rejects_leak_password(self, normal_user, settings):
        settings.SECURITY_PASSWORD_LEAK_CHECK_ENABLED = True
        serializer = self._reset_password(normal_user, "Password@123")
        assert serializer.is_valid()
        with pytest.raises(ValidationError) as exc:
            serializer.save()
        assert "泄露库" in str(exc.value)
