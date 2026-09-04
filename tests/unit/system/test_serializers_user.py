# -*- coding: utf-8 -*-
"""system 核心序列化器单元测试（必填字段 / 唯一性 / 密码规则 / 保存）。"""
import pytest
from django.test import RequestFactory

from server.utils import set_current_request
from system.models import UserInfo
from system.serializers.department import DeptSerializer
from system.serializers.role import RoleSerializer
from system.serializers.user import UserSerializer

pytestmark = pytest.mark.django_db


@pytest.fixture
def post_request(superuser):
    request = RequestFactory().post("/api/system/user", {}, content_type="application/json")
    request.user = superuser
    request.fields = {}
    set_current_request(request)
    return request


class TestUserSerializer:
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


class TestRoleSerializer:
    def test_fields_required(self, post_request):
        serializer = RoleSerializer(data={"name": "角色", "code": "role"}, ignore_field_permission=True)
        assert not serializer.is_valid()
        assert "fields" in serializer.errors

    def test_valid_with_empty_fields(self, post_request):
        serializer = RoleSerializer(
            data={"name": "角色", "code": "role", "fields": {}}, ignore_field_permission=True
        )
        assert serializer.is_valid(), serializer.errors

    def test_duplicate_code_invalid(self, post_request):
        from system.models import UserRole

        UserRole.objects.create(name="已有", code="dup")
        serializer = RoleSerializer(
            data={"name": "新角色", "code": "dup", "fields": {}}, ignore_field_permission=True
        )
        assert not serializer.is_valid()
        assert "code" in serializer.errors


class TestDeptSerializer:
    def test_missing_required_fields(self, post_request, superuser):
        serializer = DeptSerializer(data={}, ignore_field_permission=True)
        assert not serializer.is_valid()
        assert "name" in serializer.errors

    def test_valid_create_defaults_parent_to_user_dept(self, post_request, superuser):
        serializer = DeptSerializer(
            data={"name": "测试部门", "code": "test_dept"}, ignore_field_permission=True
        )
        assert serializer.is_valid(), serializer.errors
        instance = serializer.save()
        # 未传 parent 时，validate 会落到 request.user.dept（超管无部门 → None）
        assert instance.parent is None