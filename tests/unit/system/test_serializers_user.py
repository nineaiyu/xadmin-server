# -*- coding: utf-8 -*-
"""system 核心序列化器单元测试（必填字段 / 唯一性 / 密码规则 / 保存）。"""

import pytest
from django.test import RequestFactory

from identity.models import UserInfo
from identity.serializers.department import DeptSerializer
from identity.serializers.role import RoleSerializer
from identity.serializers.user import UserSerializer
from server.utils import set_current_request
from tests.unit.common.test_aes_cipher_v2 import _encrypt_v2

pytestmark = pytest.mark.django_db


@pytest.fixture
def post_request(superuser):
    request = RequestFactory().post("/api/identity/user", {}, content_type="application/json")
    request.user = superuser
    request.fields = {}
    set_current_request(request)
    return request


class TestUserSerializer:
    # 本类用明文密码直接提交（导入/脚本等非浏览器客户端的形态），
    # 属建号密码加密开关关闭的明文模式
    @pytest.fixture(autouse=True)
    def _plaintext_password_mode(self, settings):
        settings.SECURITY_USER_PASSWORD_ENCRYPTED_ENABLED = False

    def test_missing_required_fields(self, post_request, superuser):
        serializer = UserSerializer(data={}, ignore_field_permission=True)
        assert not serializer.is_valid()
        assert "username" in serializer.errors

    def test_duplicate_username_invalid(self, post_request, superuser):
        UserInfo.objects.create_user(username="lisi", password="Test@123456")
        serializer = UserSerializer(
            data={"username": "lisi", "nickname": "重复", "password": "Test@123456"},
            ignore_field_permission=True,
        )
        assert not serializer.is_valid()
        assert "username" in serializer.errors

    def test_weak_password_invalid(self, post_request, superuser):
        serializer = UserSerializer(
            data={"username": "weakpwd", "nickname": "弱密码", "password": "123"},
            ignore_field_permission=True,
        )
        assert not serializer.is_valid()
        assert "non_field_errors" in serializer.errors

    def test_valid_payload_saves_user(self, post_request, superuser):
        serializer = UserSerializer(
            data={"username": "savetest", "nickname": "保存", "password": "Test@123456"},
            ignore_field_permission=True,
        )
        assert serializer.is_valid(), serializer.errors
        instance = serializer.save()
        assert instance.username == "savetest"
        assert UserInfo.objects.filter(username="savetest").exists()


class TestUserSerializerPasswordTransport:
    """建号密码传输口径（SECURITY_USER_PASSWORD_ENCRYPTED_ENABLED）。

    密文模式（默认）：前端提交 AESCipherV2(username) 加密串，解密失败直接拒绝
    （不把密文/明文误落；拒绝点的审计与业务码应答由视图层承担，见集成测试）；
    明文模式：解密失败视为提交值本身是明文，保持导入/脚本等场景的兼容行为。
    """

    USERNAME = "transport"

    @pytest.fixture(autouse=True)
    def _encrypted_password_mode(self, settings):
        settings.SECURITY_USER_PASSWORD_ENCRYPTED_ENABLED = True

    def test_encrypted_mode_accepts_valid_ciphertext(self, post_request, superuser):
        serializer = UserSerializer(
            data={"username": self.USERNAME, "nickname": "密文", "password": _encrypt_v2(self.USERNAME, "Test@123456")},
            ignore_field_permission=True,
        )
        assert serializer.is_valid(), serializer.errors
        instance = serializer.save()
        assert instance.check_password("Test@123456")

    def test_encrypted_mode_rejects_undecryptable_payload(self, post_request, superuser):
        payload = "v2:!!!not-a-valid-ciphertext!!!"
        serializer = UserSerializer(
            data={"username": self.USERNAME, "nickname": "坏密文", "password": payload},
            ignore_field_permission=True,
        )
        assert not serializer.is_valid()
        assert "解密" in str(serializer.errors) or "decrypt" in str(serializer.errors)
        assert not UserInfo.objects.filter(username=self.USERNAME).exists()

    def test_encrypted_mode_rejects_plaintext_submission(self, post_request, superuser):
        """密文模式下明文提交不再被当作密码落库（旧口径仅告警后照落）。"""
        serializer = UserSerializer(
            data={"username": self.USERNAME, "nickname": "明文", "password": "Test@123456"},
            ignore_field_permission=True,
        )
        assert not serializer.is_valid()
        assert not UserInfo.objects.filter(username=self.USERNAME).exists()

    def test_plaintext_mode_accepts_submitted_password(self, post_request, superuser, settings):
        settings.SECURITY_USER_PASSWORD_ENCRYPTED_ENABLED = False
        serializer = UserSerializer(
            data={"username": self.USERNAME, "nickname": "明文", "password": "Test@123456"},
            ignore_field_permission=True,
        )
        assert serializer.is_valid(), serializer.errors
        instance = serializer.save()
        assert instance.check_password("Test@123456")

    def test_plaintext_mode_still_decrypts_ciphertext(self, post_request, superuser, settings):
        settings.SECURITY_USER_PASSWORD_ENCRYPTED_ENABLED = False
        serializer = UserSerializer(
            data={"username": self.USERNAME, "nickname": "密文", "password": _encrypt_v2(self.USERNAME, "Test@123456")},
            ignore_field_permission=True,
        )
        assert serializer.is_valid(), serializer.errors
        instance = serializer.save()
        assert instance.check_password("Test@123456")

    def test_ciphertext_without_matching_key_rejected(self, post_request, superuser):
        """密钥与提交用户名不符（合法密文但解密失败）→ 拒绝，密文不落库。"""
        serializer = UserSerializer(
            data={"username": self.USERNAME, "nickname": "错钥", "password": _encrypt_v2("otheruser", "Test@123456")},
            ignore_field_permission=True,
        )
        assert not serializer.is_valid()
        assert not UserInfo.objects.filter(username=self.USERNAME).exists()


class TestRoleSerializer:
    def test_fields_required(self, post_request):
        serializer = RoleSerializer(data={"name": "角色", "code": "role"}, ignore_field_permission=True)
        assert not serializer.is_valid()
        assert "fields" in serializer.errors

    def test_valid_with_empty_fields(self, post_request):
        serializer = RoleSerializer(data={"name": "角色", "code": "role", "fields": {}}, ignore_field_permission=True)
        assert serializer.is_valid(), serializer.errors

    def test_duplicate_code_invalid(self, post_request):
        from identity.models import UserRole

        UserRole.objects.create(name="已有", code="dup")
        serializer = RoleSerializer(data={"name": "新角色", "code": "dup", "fields": {}}, ignore_field_permission=True)
        assert not serializer.is_valid()
        assert "code" in serializer.errors


class TestDeptSerializer:
    def test_missing_required_fields(self, post_request, superuser):
        serializer = DeptSerializer(data={}, ignore_field_permission=True)
        assert not serializer.is_valid()
        assert "name" in serializer.errors

    def test_valid_create_defaults_parent_to_user_dept(self, post_request, superuser):
        serializer = DeptSerializer(data={"name": "测试部门", "code": "test_dept"}, ignore_field_permission=True)
        assert serializer.is_valid(), serializer.errors
        instance = serializer.save()
        # 未传 parent 时，validate 会落到 request.user.dept（超管无部门 → None）
        assert instance.parent is None
