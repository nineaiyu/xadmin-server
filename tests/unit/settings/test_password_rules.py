# -*- coding: utf-8 -*-
"""密码规则工具（settings/utils/password.py）盲区补测。

覆盖：泄露密码库加载失败兜底 / 开关、历史哈希留存的容错语义（绝不阻断改密主流程）、
规则组装的超管替换与 falsy 跳过、复杂度正则的组合分支。
"""

import pytest

from settings.utils import password as pwd
from system.models.password import PasswordHistory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _reset_leak_cache():
    """泄露库进程内 lru_cache：测试前后清空，避免污染其他用例。"""
    pwd._load_leak_passwords.cache_clear()
    yield
    pwd._load_leak_passwords.cache_clear()


class TestLeakPassword:
    def test_missing_file_treated_as_empty(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pwd, "LEAK_PASSWORDS_FILE", tmp_path / "missing.txt")
        assert pwd._load_leak_passwords() == frozenset()

    def test_check_respects_switch(self, monkeypatch, tmp_path, settings):
        lib = tmp_path / "leak.txt"
        lib.write_text("Password@123\n\n  \nqwerty\n", encoding="utf-8")
        monkeypatch.setattr(pwd, "LEAK_PASSWORDS_FILE", lib)

        settings.SECURITY_PASSWORD_LEAK_CHECK_ENABLED = True
        assert pwd.check_leak_password("Password@123") is True
        assert pwd.check_leak_password("Unknown@1") is False

        settings.SECURITY_PASSWORD_LEAK_CHECK_ENABLED = False
        assert pwd.check_leak_password("Password@123") is False


class TestRecordPasswordHash:
    def test_skips_invalid_input(self, normal_user):
        pwd.record_password_hash(None, "hash")
        pwd.record_password_hash(normal_user, "")
        assert PasswordHistory.objects.count() == 0

    def test_persists_and_refreshes_timestamp(self, normal_user):
        assert normal_user.date_password_updated is None
        pwd.record_password_hash(normal_user, "hashed-1")
        assert PasswordHistory.objects.filter(user=normal_user, password="hashed-1").exists()
        assert normal_user.date_password_updated is not None

    def test_history_failure_does_not_block(self, normal_user, monkeypatch):
        def boom(**kwargs):
            raise RuntimeError("db down")

        monkeypatch.setattr(PasswordHistory.objects, "create", boom)
        normal_user.must_change_password = True
        normal_user.save(update_fields=["must_change_password"])

        pwd.record_password_hash(normal_user, "hashed-2")

        assert normal_user.must_change_password is False
        assert normal_user.date_password_updated is not None
        assert PasswordHistory.objects.count() == 0


class TestPasswordRules:
    def test_get_rules_swaps_admin_min_length_and_skips_falsy(self, django_user_model, settings):
        settings.SECURITY_PASSWORD_RULES = [
            "SECURITY_PASSWORD_MIN_LENGTH",
            "SECURITY_PASSWORD_SPECIAL_CHAR",
            "SECURITY_PASSWORD_UPPER_CASE",
        ]
        settings.SECURITY_PASSWORD_MIN_LENGTH = 8
        settings.SECURITY_ADMIN_USER_PASSWORD_MIN_LENGTH = 12
        settings.SECURITY_PASSWORD_SPECIAL_CHAR = False
        settings.SECURITY_PASSWORD_UPPER_CASE = True

        normal = django_user_model.objects.create_user(username="u-rule", password="x")
        assert pwd.get_password_check_rules(normal) == [
            {"key": "SECURITY_PASSWORD_MIN_LENGTH", "value": 8},
            {"key": "SECURITY_PASSWORD_UPPER_CASE", "value": True},
        ]

        admin = django_user_model.objects.create_superuser(
            username="a-rule", email="a-rule@example.com", password="Admin@123456"
        )
        rules = {r["key"]: r["value"] for r in pwd.get_password_check_rules(admin)}
        assert rules["SECURITY_ADMIN_USER_PASSWORD_MIN_LENGTH"] == 12

    def test_check_password_rules_combinations(self, settings):
        settings.SECURITY_PASSWORD_UPPER_CASE = True
        settings.SECURITY_PASSWORD_LOWER_CASE = True
        settings.SECURITY_PASSWORD_NUMBER = True
        settings.SECURITY_PASSWORD_SPECIAL_CHAR = True
        settings.SECURITY_PASSWORD_MIN_LENGTH = 8
        settings.SECURITY_ADMIN_USER_PASSWORD_MIN_LENGTH = 12

        assert pwd.check_password_rules("Abcdef@1") is True
        assert pwd.check_password_rules("abcdef@1") is False  # 缺大写
        assert pwd.check_password_rules("ABCDEF@1") is False  # 缺小写
        assert pwd.check_password_rules("Abcdefg1") is False  # 缺特殊字符
        assert pwd.check_password_rules("Abc@1") is False  # 长度不足
        assert pwd.check_password_rules("Abcdef@12345", is_super_admin=True) is True
        assert pwd.check_password_rules("Abcdef@1", is_super_admin=True) is False  # 管理员 12 位
