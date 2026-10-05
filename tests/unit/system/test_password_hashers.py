# -*- coding: utf-8 -*-
"""密码哈希 argon2id 迁移（渐进升级）：
1. 新设密码一律 argon2id（PASSWORD_HASHERS 首位生效）；
2. 存量 PBKDF2 哈希跨 hasher 校验通过（登录不中断）；
3. 校验通过后自动以 argon2id 重哈希落库（AbstractBaseUser.check_password 的 setter），
   无需批量迁移脚本；
4. 依赖清单：argon2-cffi 已随 pyproject 声明（缺失时 Argon2PasswordHasher 抛 ImproperlyConfigured）。

哈希长度边界：argon2id 字符串约 100 字符，PasswordHistory.password(max_length=128) 与
UserInfo.password(128) 均可承载（守护断言防上游参数膨胀）。
"""

import ast
from pathlib import Path

import pytest
from django.contrib.auth.hashers import check_password, get_hasher, identify_hasher, make_password
from django.test import override_settings

pytestmark = pytest.mark.django_db

ARGON2_FIRST = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

LIB_SETTINGS = Path(__file__).resolve().parents[3] / "server" / "settings" / "libs.py"


def _production_hashers() -> list[str]:
    """从 server/settings/libs.py 源码解析生产 PASSWORD_HASHERS（测试运行时被 MD5 快速哈希覆盖）。

    密码哈希与会话引擎随其余框架级配置（DRF/JWT/CORS）落在 libs.py；base.py 保留指针注释。
    """
    tree = ast.parse(LIB_SETTINGS.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "PASSWORD_HASHERS" for target in node.targets
        ):
            assert isinstance(node.value, (ast.List, ast.Tuple))
            return [element.value for element in node.value.elts]
    raise AssertionError("server/settings/libs.py 未找到 PASSWORD_HASHERS 声明（守护口径失效，请更新测试）")


class TestArgon2Migration:
    def test_settings_declare_argon2_first(self):
        hashers = _production_hashers()
        assert hashers[0] == "django.contrib.auth.hashers.Argon2PasswordHasher"
        # PBKDF2 必须保留在回退链上（存量哈希仍要能校验）
        assert any("PBKDF2" in item for item in hashers)

    @override_settings(PASSWORD_HASHERS=ARGON2_FIRST)
    def test_new_password_uses_argon2id(self):
        encoded = make_password("Str0ng!Passw0rd")
        hasher = identify_hasher(encoded)
        assert hasher.algorithm == "argon2"
        # Django 存储形态：`argon2$` 前缀 + 原生 argon2id 编码串
        assert "argon2id$" in encoded

    @override_settings(PASSWORD_HASHERS=ARGON2_FIRST)
    def test_legacy_pbkdf2_still_validates(self):
        legacy = make_password("Str0ng!Passw0rd", hasher="pbkdf2_sha256")
        assert identify_hasher(legacy).algorithm == "pbkdf2_sha256"
        # argon2 首位的配置下，PBKDF2 哈希校验不受影响
        assert check_password("Str0ng!Passw0rd", legacy) is True
        assert check_password("wrong", legacy) is False

    @override_settings(PASSWORD_HASHERS=ARGON2_FIRST)
    def test_check_password_setter_upgrades_hash(self):
        from identity.models import UserInfo

        user = UserInfo.objects.create_user(username="hash-upgrade", password="Legacy@Pass1")
        # 预置存量 PBKDF2 哈希（迁移前登记的老用户形态）
        user.password = make_password("Legacy@Pass1", hasher="pbkdf2_sha256")
        user.save(update_fields=["password"])
        assert identify_hasher(user.password).algorithm == "pbkdf2_sha256"

        # 登录同款路径：check_password 命中 setter → 自动以 argon2id 重哈希落库
        assert user.check_password("Legacy@Pass1") is True
        user.refresh_from_db()
        assert identify_hasher(user.password).algorithm == "argon2"
        # 重哈希后密码仍正确（同一明文两次校验一致）
        assert user.check_password("Legacy@Pass1") is True

    @override_settings(PASSWORD_HASHERS=ARGON2_FIRST)
    def test_argon2_hash_fits_password_columns(self):
        """哈希串长度必须落在 password 列的 128 上限内（上游参数膨胀会被本测试拦下）。"""
        get_hasher("argon2")  # 确认 argon2-cffi 可用（缺失会 ImproperlyConfigured）
        encoded = make_password("x", hasher="argon2")
        assert len(encoded) <= 128, f"argon2 哈希串超出 password 列 128 上限：{len(encoded)}"
