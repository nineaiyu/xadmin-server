# -*- coding: utf-8 -*-
"""LDAP 同步服务单元测试：建号/更新/冲突审计/消失策略/部门树/恢复。

同步契约：逐条 savepoint 隔离、冲突跳过并落 OperationLog(LDAP:conflict)、
摘要落 OperationLog(LDAP:sync)、有动作时通知超管。
"""

import json
from unittest import mock

import pytest

from system.ldap import sync as ldap_sync
from system.models import DeptInfo, LdapUserBinding, OperationLog, UserInfo

pytestmark = pytest.mark.django_db

PEOPLE_BASE = "ou=people,dc=corp,dc=com"
DEPT_BASE = "ou=depts,dc=corp,dc=com"


def user_entry(dn, username, **attrs):
    payload = {
        "sAMAccountName": username,
        "cn": attrs.pop("cn", username.title()),
        "mail": attrs.pop("mail", f"{username}@corp.com"),
        "telephoneNumber": attrs.pop("phone", ""),
        "userAccountControl": attrs.pop("uac", 512),
    }
    payload.update(attrs)
    return {"type": "searchResEntry", "dn": dn, "attributes": payload}


def ou_entry(dn, name):
    return {"type": "searchResEntry", "dn": dn, "attributes": {"ou": name, "name": name}}


class FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.fixture
def sync_on(settings):
    settings.LDAP_SYNC_ENABLED = True
    settings.LDAP_SERVER_URI = "ldap://fake"
    settings.LDAP_USER_SEARCH_BASE = PEOPLE_BASE
    settings.LDAP_DEPT_ENABLED = False
    settings.LDAP_DEPT_SEARCH_BASE = ""
    return settings


@pytest.fixture
def stub_directory(monkeypatch):
    """按 search_base 路由假搜索结果。"""
    holder = {"by_base": {}}

    def fake_search(conn, search_base, search_filter, attributes):
        return holder["by_base"].get(search_base, [])

    monkeypatch.setattr(ldap_sync, "service_connection", lambda: FakeConn())
    monkeypatch.setattr(ldap_sync, "paged_search_entries", fake_search)
    return holder


class TestGates:
    def test_disabled_skips(self, settings, stub_directory):
        settings.LDAP_SYNC_ENABLED = False
        summary = ldap_sync.run_ldap_sync()
        assert summary == {"skipped": True, "reason": "LDAP_SYNC_ENABLED is off"}


class TestUserSync:
    def test_creates_user_with_binding(self, sync_on, stub_directory):
        stub_directory["by_base"][PEOPLE_BASE] = [user_entry("cn=alice,ou=people,dc=corp,dc=com", "alice")]
        summary = ldap_sync.run_ldap_sync()
        assert summary["created_users"] == 1
        user = UserInfo.objects.get(username="alice")
        assert not user.has_usable_password()  # 目录用户无本地密码
        assert user.email == "alice@corp.com"
        binding = LdapUserBinding.objects.get(user=user)
        assert binding.dn == "cn=alice,ou=people,dc=corp,dc=com"
        assert binding.synced_at is not None

    def test_updates_bound_user_and_status(self, sync_on, stub_directory):
        user = UserInfo.objects.create_user(username="alice", nickname="Old")
        user.set_unusable_password()
        user.save()
        LdapUserBinding.objects.create(user=user, dn="cn=alice,ou=people,dc=corp,dc=com")
        stub_directory["by_base"][PEOPLE_BASE] = [
            user_entry("cn=alice,ou=people,dc=corp,dc=com", "alice", cn="Alice New", uac=514)
        ]
        summary = ldap_sync.run_ldap_sync()
        assert summary["updated_users"] == 1
        user.refresh_from_db()
        assert user.nickname == "Alice New"
        assert user.is_active is False  # 目录禁用位同步

    def test_username_conflict_skipped_and_audited(self, sync_on, stub_directory):
        """用户名被本地无绑定账号占用：跳过 + 逐条冲突审计，绝不冒名更新。"""
        local = UserInfo.objects.create_user(username="alice", nickname="Local")
        local.set_password("Local-Pass-1")
        local.save()
        stub_directory["by_base"][PEOPLE_BASE] = [user_entry("cn=alice,ou=people,dc=corp,dc=com", "alice")]
        summary = ldap_sync.run_ldap_sync()
        assert summary["conflict_users"] == 1
        local.refresh_from_db()
        assert local.nickname == "Local"
        assert local.has_usable_password()
        log = OperationLog.objects.get(module="LDAP:conflict")
        assert "local user" in log.changes

    def test_recycled_username_conflict(self, sync_on, stub_directory):
        user = UserInfo.objects.create_user(username="alice", password="Local-Pass-1")
        user.delete()
        stub_directory["by_base"][PEOPLE_BASE] = [user_entry("cn=alice,ou=people,dc=corp,dc=com", "alice")]
        summary = ldap_sync.run_ldap_sync()
        assert summary["conflict_users"] == 1
        assert "recycled" in OperationLog.objects.get(module="LDAP:conflict").changes

    def test_email_conflict_cleared(self, sync_on, stub_directory):
        UserInfo.objects.create_user(username="bob", password="Local-Pass-1", email="alice@corp.com")
        stub_directory["by_base"][PEOPLE_BASE] = [user_entry("cn=alice,ou=people,dc=corp,dc=com", "alice")]
        summary = ldap_sync.run_ldap_sync()
        assert summary["created_users"] == 1
        assert UserInfo.objects.get(username="alice").email == ""
        # 冲突被清理也要留痕
        assert OperationLog.objects.filter(module="LDAP:conflict").exists()

    def test_sync_auto_create_off(self, sync_on, stub_directory):
        sync_on.LDAP_SYNC_AUTO_CREATE = False
        stub_directory["by_base"][PEOPLE_BASE] = [user_entry("cn=alice,ou=people,dc=corp,dc=com", "alice")]
        summary = ldap_sync.run_ldap_sync()
        assert summary["skipped_users"] == 1
        assert not UserInfo.objects.filter(username="alice").exists()


class TestMissingPolicy:
    BINDING_DN = "cn=ghost,ou=people,dc=corp,dc=com"

    def _prepare(self, settings_fixture):
        user = UserInfo.objects.create_user(username="ghost", nickname="Ghost")
        user.set_unusable_password()
        user.save()
        LdapUserBinding.objects.create(user=user, dn=self.BINDING_DN)
        return user

    def test_deactivate(self, sync_on, stub_directory):
        user = self._prepare(sync_on)
        stub_directory["by_base"][PEOPLE_BASE] = []  # 目录侧已消失
        summary = ldap_sync.run_ldap_sync()
        assert summary["deactivated_users"] == 1
        user.refresh_from_db()
        assert user.is_active is False
        assert user.deleted_at is None

    def test_soft_delete_to_recycle_bin(self, sync_on, stub_directory):
        sync_on.LDAP_SYNC_MISSING_POLICY = "soft_delete"
        user = self._prepare(sync_on)
        stub_directory["by_base"][PEOPLE_BASE] = []
        summary = ldap_sync.run_ldap_sync()
        assert summary["soft_deleted_users"] == 1
        user.refresh_from_db()
        assert user.deleted_at is not None

    def test_ignore(self, sync_on, stub_directory):
        sync_on.LDAP_SYNC_MISSING_POLICY = "ignore"
        user = self._prepare(sync_on)
        stub_directory["by_base"][PEOPLE_BASE] = []
        ldap_sync.run_ldap_sync()
        user.refresh_from_db()
        assert user.is_active is True

    def test_reappear_restores_soft_deleted(self, sync_on, stub_directory):
        """目录重新出现：软删用户自动恢复并重新启用。"""
        user = self._prepare(sync_on)
        user.delete()
        stub_directory["by_base"][PEOPLE_BASE] = [user_entry(self.BINDING_DN, "ghost")]
        summary = ldap_sync.run_ldap_sync()
        assert summary["restored_users"] == 1
        user.refresh_from_db()
        assert user.deleted_at is None
        assert user.is_active is True

    def test_local_users_without_binding_untouched(self, sync_on, stub_directory):
        """无绑定的本地账号永远不受同步策略影响。"""
        local = UserInfo.objects.create_user(username="purelocal", password="Local-Pass-1")
        stub_directory["by_base"][PEOPLE_BASE] = []
        ldap_sync.run_ldap_sync()
        local.refresh_from_db()
        assert local.is_active is True
        assert local.deleted_at is None


class TestDeptSync:
    def test_ou_tree_created_with_namespace_code(self, sync_on, stub_directory):
        sync_on.LDAP_DEPT_ENABLED = True
        sync_on.LDAP_DEPT_SEARCH_BASE = DEPT_BASE
        stub_directory["by_base"][DEPT_BASE] = [
            ou_entry("ou=rd,ou=depts,dc=corp,dc=com", "研发部"),
            ou_entry("ou=backend,ou=rd,ou=depts,dc=corp,dc=com", "后端组"),
        ]
        summary = ldap_sync.run_ldap_sync()
        assert summary["created_depts"] == 2
        parent = DeptInfo.objects.get(code="ldap:ou=rd,ou=depts,dc=corp,dc=com")
        child = DeptInfo.objects.get(code="ldap:ou=backend,ou=rd,ou=depts,dc=corp,dc=com")
        assert child.parent_id == parent.pk
        assert child.name == "后端组"

    def test_user_assigned_to_nearest_ancestor_dept(self, sync_on, stub_directory):
        sync_on.LDAP_DEPT_ENABLED = True
        sync_on.LDAP_DEPT_SEARCH_BASE = DEPT_BASE
        stub_directory["by_base"][DEPT_BASE] = [ou_entry("ou=rd,ou=depts,dc=corp,dc=com", "研发部")]
        stub_directory["by_base"][PEOPLE_BASE] = [user_entry("cn=alice,ou=rd,ou=depts,dc=corp,dc=com", "alice")]
        ldap_sync.run_ldap_sync()
        user = UserInfo.objects.get(username="alice")
        assert user.dept.code == "ldap:ou=rd,ou=depts,dc=corp,dc=com"

    def test_missing_dept_deactivated(self, sync_on, stub_directory):
        sync_on.LDAP_DEPT_ENABLED = True
        sync_on.LDAP_DEPT_SEARCH_BASE = DEPT_BASE
        stale = DeptInfo.objects.create(name="旧部门", code="ldap:ou=old,ou=depts,dc=corp,dc=com")
        stub_directory["by_base"][DEPT_BASE] = []
        summary = ldap_sync.run_ldap_sync()
        assert summary["deactivated_depts"] == 1
        stale.refresh_from_db()
        assert stale.is_active is False

    def test_manual_dept_codes_untouched(self, sync_on, stub_directory):
        """无 ldap: 前缀的手工部门不被同步触碰。"""
        sync_on.LDAP_DEPT_ENABLED = True
        sync_on.LDAP_DEPT_SEARCH_BASE = DEPT_BASE
        manual = DeptInfo.objects.create(name="手工部", code="dev")
        stub_directory["by_base"][DEPT_BASE] = []
        ldap_sync.run_ldap_sync()
        manual.refresh_from_db()
        assert manual.is_active is True


class TestAuditAndNotify:
    def test_summary_audit_written(self, sync_on, stub_directory):
        stub_directory["by_base"][PEOPLE_BASE] = [user_entry("cn=alice,ou=people,dc=corp,dc=com", "alice")]
        ldap_sync.run_ldap_sync()
        log = OperationLog.objects.get(module="LDAP:sync")
        changes = json.loads(log.changes)
        assert changes["created_users"] == 1
        assert log.auth_type == OperationLog.AuthType.LDAP

    def test_notify_on_actions(self, sync_on, stub_directory):
        """有建号动作时发布超管摘要通知。"""
        stub_directory["by_base"][PEOPLE_BASE] = [user_entry("cn=alice,ou=people,dc=corp,dc=com", "alice")]
        with mock.patch("system.notifications.LdapSyncMessage.publish") as publish:
            ldap_sync.run_ldap_sync()
        assert publish.called

    def test_no_notify_when_idle(self, sync_on, stub_directory):
        stub_directory["by_base"][PEOPLE_BASE] = []
        with mock.patch("system.notifications.LdapSyncMessage.publish") as publish:
            ldap_sync.run_ldap_sync()
        assert not publish.called


class TestConnectionTest:
    def test_counts_users_and_depts(self, sync_on, stub_directory):
        sync_on.LDAP_DEPT_ENABLED = True
        sync_on.LDAP_DEPT_SEARCH_BASE = DEPT_BASE
        stub_directory["by_base"][PEOPLE_BASE] = [user_entry("cn=a,dc=corp,dc=com", "a")]
        stub_directory["by_base"][DEPT_BASE] = [ou_entry("ou=x,dc=corp,dc=com", "x")]
        result = ldap_sync.test_ldap_connection()
        assert result == {"user_count": 1, "dept_count": 1}

    def test_missing_uri_raises_config_error(self, settings):
        settings.LDAP_SERVER_URI = ""
        with pytest.raises(ldap_sync.LdapConfigError):
            ldap_sync.test_ldap_connection()
