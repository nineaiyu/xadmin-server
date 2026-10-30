# -*- coding: utf-8 -*-
"""身份域契约门面：用户/角色自由文本解析、主键取值与批量用户名映射。"""

import pytest

from identity.contracts import get_role, get_user, resolve_role, resolve_user, username_map
from identity.models import UserInfo, UserRole

pytestmark = pytest.mark.django_db


@pytest.fixture
def user(db):
    return UserInfo.objects.create_user(username="contract_u", password="Test@123456", nickname="契约用户")


@pytest.fixture
def role(db):
    return UserRole.objects.create(name="contract_role")


class TestResolveUser:
    def test_resolves_by_username_then_nickname_then_pk(self, user):
        assert resolve_user("contract_u").pk == user.pk
        assert resolve_user("契约用户").pk == user.pk
        assert resolve_user(str(user.pk)).pk == user.pk

    def test_invalid_input_returns_none(self, user):
        assert resolve_user("") is None
        assert resolve_user(None) is None
        assert resolve_user("no-such-user") is None
        assert resolve_user("not-a-uuid") is None  # 非法主键形态静默跳过


class TestGetUser:
    def test_by_pk_and_invalid(self, user):
        assert get_user(user.pk).pk == user.pk
        assert get_user("not-a-uuid") is None
        assert get_user("00000000-0000-0000-0000-000000000000") is None


class TestResolveRole:
    def test_resolves_by_pk_then_name(self, role):
        assert resolve_role(str(role.pk)).pk == role.pk
        assert resolve_role("contract_role").pk == role.pk

    def test_invalid_input_returns_none(self):
        assert resolve_role("") is None
        assert resolve_role("no-such-role") is None


class TestGetRole:
    def test_by_pk_and_invalid(self, role):
        assert get_role(role.pk).pk == role.pk
        assert get_role("garbage") is None


class TestUsernameMap:
    def test_maps_and_skips_empty(self, user):
        mapping = username_map([user.pk, "", None])
        assert mapping == {str(user.pk): "contract_u"}
