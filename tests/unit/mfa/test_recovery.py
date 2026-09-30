# -*- coding: utf-8 -*-
"""OTP 恢复码单元测试：生成 / 哈希落库 / 一次性消费 / 后端注册语义。"""

import hashlib

import pytest

from mfa import recovery
from mfa.backends import get_backend, get_enabled_backends
from mfa.backends.recovery import RecoveryCodeBackend
from mfa.models import MfaRecoveryCode

pytestmark = pytest.mark.django_db


class TestCodeFormat:
    def test_normalize_strips_separators_and_case(self):
        assert recovery.normalize_code(" ABCD-EF12 ") == "abcdef12"
        assert recovery.normalize_code("a b c d") == "abcd"
        assert recovery.normalize_code(None) == ""

    def test_hash_is_sha256_of_normalized_code(self):
        assert recovery.hash_code("ABCD-EF12") == hashlib.sha256(b"abcdef12").hexdigest()

    def test_format_groups_five_five(self):
        assert recovery.format_code("abcdefghij") == "abcde-fghij"


class TestGenerate:
    def test_generates_ten_unique_codes(self, normal_user):
        codes = recovery.generate_codes(normal_user)
        assert len(codes) == recovery.RECOVERY_CODE_COUNT
        assert len(set(codes)) == recovery.RECOVERY_CODE_COUNT
        for code in codes:
            assert len(code) == 11 and code[5] == "-"

    def test_stores_hash_not_plaintext(self, normal_user):
        codes = recovery.generate_codes(normal_user)
        rows = MfaRecoveryCode.objects.filter(user=normal_user)
        assert rows.count() == recovery.RECOVERY_CODE_COUNT
        stored = {row.code_hash for row in rows}
        assert stored == {recovery.hash_code(code) for code in codes}
        for code in codes:
            assert code not in stored

    def test_regenerate_invalidates_old_batch(self, normal_user):
        old = recovery.generate_codes(normal_user)
        new = recovery.generate_codes(normal_user)
        assert old != new
        assert MfaRecoveryCode.objects.filter(user=normal_user).count() == recovery.RECOVERY_CODE_COUNT
        ok, _ = recovery.verify_and_consume(normal_user, old[0])
        assert not ok


class TestVerifyAndConsume:
    def test_correct_code_claims_once(self, normal_user):
        codes = recovery.generate_codes(normal_user)
        ok, msg = recovery.verify_and_consume(normal_user, codes[0])
        assert ok, msg
        ok, _ = recovery.verify_and_consume(normal_user, codes[0])
        assert not ok
        assert recovery.remaining_count(normal_user) == recovery.RECOVERY_CODE_COUNT - 1

    def test_entry_form_tolerant(self, normal_user):
        codes = recovery.generate_codes(normal_user)
        codes[0] = codes[0].upper().replace("-", " ")
        ok, msg = recovery.verify_and_consume(normal_user, codes[0])
        assert ok, msg

    def test_unknown_or_empty_rejected(self, normal_user):
        recovery.generate_codes(normal_user)
        assert recovery.verify_and_consume(normal_user, "zzzzz-zzzzz")[0] is False
        assert recovery.verify_and_consume(normal_user, "  ")[0] is False

    def test_invalid_and_used_share_message(self, normal_user):
        """无效与已使用同文案，不暴露「码存在但已用」的判定面。"""
        codes = recovery.generate_codes(normal_user)
        recovery.verify_and_consume(normal_user, codes[0])
        _, used_msg = recovery.verify_and_consume(normal_user, codes[0])
        _, invalid_msg = recovery.verify_and_consume(normal_user, "zzzzz-zzzzz")
        assert used_msg == invalid_msg


class TestRecoveryBackend:
    def test_active_follows_remaining_count(self, normal_user):
        assert get_backend(normal_user, "recovery") is None
        recovery.generate_codes(normal_user)
        assert get_backend(normal_user, "recovery") is not None

    def test_check_code_delegates_to_consume(self, normal_user):
        codes = recovery.generate_codes(normal_user)
        backend = RecoveryCodeBackend(normal_user)
        assert backend.check_code(codes[0]) == (True, "")
        assert backend.check_code(codes[0])[0] is False

    def test_global_enabled_tied_to_otp(self, settings):
        settings.SECURITY_MFA_CONFIRM_BACKENDS = ["password"]
        assert RecoveryCodeBackend.global_enabled() is False
        settings.SECURITY_MFA_CONFIRM_BACKENDS = ["otp", "password"]
        assert RecoveryCodeBackend.global_enabled() is True

    def test_policy_narrows_recovery_like_other_backends(self, normal_user):
        """策略层收窄（角色/用户白名单）对 recovery 同口径生效。"""
        recovery.generate_codes(normal_user)
        normal_user.allowed_mfa_types = ["otp"]
        normal_user.save(update_fields=["allowed_mfa_types"])
        assert get_backend(normal_user, "recovery") is None
        assert "recovery" not in [b.name for b in get_enabled_backends(normal_user)]
