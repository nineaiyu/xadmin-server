# -*- coding: utf-8 -*-
"""LDAP 组 → 角色映射（sync._sync_roles）集成测试。"""

import pytest
from django.test import override_settings

from system.ldap.sync import _group_matches, _sync_roles, get_group_role_map
from system.models import UserInfo, UserRole

pytestmark = pytest.mark.django_db


@pytest.fixture
def user(db):
    return UserInfo.objects.create_user(username="ldap_role_user", password="Test@123456")


@pytest.fixture
def ops_role(db):
    return UserRole.objects.create(name="OPS", code="ops_role", is_active=True)


class TestSyncRoles:
    def test_group_role_mapping_applied(self, user, ops_role):
        summary = {"roles_added": 0, "roles_removed": 0}
        with override_settings(LDAP_GROUP_ROLE_MAP={"ops": "ops_role"}):
            _sync_roles(user, {"memberOf": ["CN=OPS,OU=Groups,DC=example,DC=com"]}, summary)
        assert user.roles.filter(code="ops_role").exists()
        assert summary["roles_added"] == 1

    def test_role_revoked_when_group_left(self, user, ops_role):
        user.roles.add(ops_role)
        summary = {"roles_added": 0, "roles_removed": 0}
        with override_settings(LDAP_GROUP_ROLE_MAP={"ops": "ops_role"}):
            _sync_roles(user, {"memberOf": ["CN=Others,OU=Groups,DC=example,DC=com"]}, summary)
        assert not user.roles.filter(code="ops_role").exists()
        assert summary["roles_removed"] == 1

    def test_disabled_when_mapping_empty(self, user, ops_role):
        user.roles.add(ops_role)
        summary = {"roles_added": 0, "roles_removed": 0}
        with override_settings(LDAP_GROUP_ROLE_MAP={}):
            _sync_roles(user, {"memberOf": []}, summary)
        assert user.roles.filter(code="ops_role").exists()
        assert summary["roles_removed"] == 0

    def test_manual_role_untouched(self, user, ops_role):
        """只管理映射中出现的角色：手工授予的其他角色不受影响。"""
        other = UserRole.objects.create(name="OTHER", code="other_role", is_active=True)
        user.roles.add(other)
        summary = {"roles_added": 0, "roles_removed": 0}
        with override_settings(LDAP_GROUP_ROLE_MAP={"ops": "ops_role"}):
            _sync_roles(user, {"memberOf": []}, summary)
        assert user.roles.filter(code="other_role").exists()

    def test_full_dn_key_matches(self):
        assert _group_matches("cn=ops,ou=groups,dc=example,dc=com", ["cn=ops,ou=groups,dc=example,dc=com"])
        assert _group_matches("ops", ["cn=ops,ou=groups,dc=example,dc=com"])
        assert not _group_matches("ops", ["cn=ops2,ou=groups,dc=example,dc=com"])

    def test_get_group_role_map_normalizes(self):
        with override_settings(LDAP_GROUP_ROLE_MAP={" OPS ": " ops_role "}):
            assert get_group_role_map() == {"ops": "ops_role"}
