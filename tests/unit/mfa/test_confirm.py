# -*- coding: utf-8 -*-
"""敏感操作二次确认框架（mfa/confirm.py）单元测试。

覆盖三种接入方式的校验分支：DRF 权限类 / 手动校验 / 装饰器，
以及确认状态缓存命中与未命中、装饰器定位 request 的三种形态。
"""

from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser

from mfa.cache import UserConfirmStateCache
from mfa.confirm import (
    UserConfirmation,
    check_user_confirm,
    ensure_user_confirmed,
    require_user_confirmation,
)
from mfa.const import ConfirmType
from mfa.exceptions import MFAConfirmRequired

pytestmark = pytest.mark.django_db


class TestCheckUserConfirm:
    def test_disabled_bypasses_check(self, settings, normal_user):
        settings.SECURITY_MFA_CONFIRM_ENABLED = False
        check_user_confirm(normal_user, ConfirmType.MFA)

    def test_without_state_raises(self, settings, normal_user):
        settings.SECURITY_MFA_CONFIRM_ENABLED = True
        with pytest.raises(MFAConfirmRequired):
            check_user_confirm(normal_user, ConfirmType.MFA)

    def test_with_valid_state_passes(self, settings, normal_user):
        settings.SECURITY_MFA_CONFIRM_ENABLED = True
        UserConfirmStateCache(normal_user).set(ConfirmType.MFA, "otp")
        check_user_confirm(normal_user, ConfirmType.MFA)


class TestUserConfirmationPermission:
    def test_anonymous_passthrough(self):
        """未认证用户交给认证层处理，这里放行。"""
        permission = UserConfirmation()
        assert permission.has_permission(SimpleNamespace(user=None), SimpleNamespace()) is True
        assert permission.has_permission(SimpleNamespace(user=AnonymousUser()), SimpleNamespace()) is True

    def test_authenticated_user_requires_state(self, settings, normal_user):
        settings.SECURITY_MFA_CONFIRM_ENABLED = True
        permission = UserConfirmation()
        request = SimpleNamespace(user=normal_user)

        with pytest.raises(MFAConfirmRequired):
            permission.has_permission(request, SimpleNamespace())

        UserConfirmStateCache(normal_user).set(ConfirmType.MFA, "otp")
        assert permission.has_permission(request, SimpleNamespace()) is True

    def test_require_generates_level_classes(self):
        mfa_cls = UserConfirmation.require(ConfirmType.MFA)
        pwd_cls = UserConfirmation.require(ConfirmType.PASSWORD)
        assert mfa_cls.min_type == ConfirmType.MFA
        assert pwd_cls.min_type == ConfirmType.PASSWORD
        assert mfa_cls.__name__ != pwd_cls.__name__


class TestEnsureUserConfirmed:
    def test_request_without_user_rejected(self):
        with pytest.raises(MFAConfirmRequired):
            ensure_user_confirmed(object(), ConfirmType.MFA)

    def test_unauthenticated_rejected(self):
        with pytest.raises(MFAConfirmRequired):
            ensure_user_confirmed(SimpleNamespace(user=AnonymousUser()), ConfirmType.MFA)

    def test_valid_state_passes(self, settings, normal_user):
        settings.SECURITY_MFA_CONFIRM_ENABLED = True
        UserConfirmStateCache(normal_user).set(ConfirmType.MFA, "otp")
        ensure_user_confirmed(SimpleNamespace(user=normal_user), ConfirmType.MFA)


class TestRequireUserConfirmationDecorator:
    def test_locates_request_in_args(self, settings, normal_user):
        settings.SECURITY_MFA_CONFIRM_ENABLED = True
        UserConfirmStateCache(normal_user).set(ConfirmType.MFA, "otp")

        @require_user_confirmation(ConfirmType.MFA)
        def op(request, value=0):
            return ("ok", value)

        assert op(SimpleNamespace(user=normal_user), value=3) == ("ok", 3)

    def test_locates_request_in_kwargs(self, settings, normal_user):
        settings.SECURITY_MFA_CONFIRM_ENABLED = True
        UserConfirmStateCache(normal_user).set(ConfirmType.MFA, "otp")

        @require_user_confirmation(ConfirmType.MFA)
        def op(request):
            return "ok"

        assert op(request=SimpleNamespace(user=normal_user)) == "ok"

    def test_without_request_rejected(self, settings):
        settings.SECURITY_MFA_CONFIRM_ENABLED = True

        @require_user_confirmation(ConfirmType.MFA)
        def op():
            return "ok"

        with pytest.raises(MFAConfirmRequired):
            op()

    def test_state_required_before_call(self, settings, normal_user):
        settings.SECURITY_MFA_CONFIRM_ENABLED = True

        @require_user_confirmation(ConfirmType.MFA)
        def op(request):
            return "ok"

        with pytest.raises(MFAConfirmRequired):
            op(SimpleNamespace(user=normal_user))


class TestConfirmStateLevelSemantics:
    """确认状态按请求级别写入：验证方式级别不放大确认窗口（TTL 与解锁面）。"""

    def test_state_records_request_level_not_method_level(self, settings, normal_user):
        import pyotp

        from mfa.services import verify_user_confirm

        settings.SECURITY_MFA_CONFIRM_BACKENDS = ["otp", "password"]
        secret = pyotp.random_base32()
        normal_user.otp_secret_key = secret
        normal_user.save(update_fields=["otp_secret_key"])
        code = pyotp.TOTP(secret).now()
        # 用 OTP（MFA 级方式）确认 PASSWORD 级请求
        ok, msg = verify_user_confirm(normal_user, "otp", code, confirm_type=ConfirmType.PASSWORD)
        assert ok, msg
        state_cache = UserConfirmStateCache(normal_user)
        assert state_cache.is_valid_for(ConfirmType.PASSWORD) is True
        # 不因验证方式级别高而解锁 MFA 级窗口
        assert state_cache.is_valid_for(ConfirmType.MFA) is False

    def test_malformed_cache_state_is_invalid_not_error(self, settings, normal_user):
        from django.core.cache import cache as django_cache

        from mfa.views import _state_expire_at

        state_cache = UserConfirmStateCache(normal_user)
        key = state_cache.cache_key
        # 缺 type / time 的旧版或损坏结构：按不满足处理，不抛 KeyError
        django_cache.set(key, {"level": 3})
        assert state_cache.is_valid_for(ConfirmType.MFA) is False
        assert _state_expire_at({"level": 3}) is None
        # 合法结构：过期时间可正常计算
        assert _state_expire_at({"type": ConfirmType.MFA, "time": 1000.0}) is not None
