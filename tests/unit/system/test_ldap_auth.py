# -*- coding: utf-8 -*-
"""LdapBindBackend 单元测试（ADR-017）。

覆盖：开关 / 优先级让位 / bind 成功解析与建号 / 复用本地账号 / 回收站占用
fail-closed / 密码错误与目录不可达降级（绝不抛异常阻断本地登录）。
"""

import pytest
from ldap3.core.exceptions import LDAPException

from system.ldap.auth import LdapBindBackend
from system.models import LdapUserBinding, UserInfo

pytestmark = pytest.mark.django_db

ALICE_DN = "cn=alice,ou=people,dc=corp,dc=com"

ALICE_ENTRY = {
    "type": "searchResEntry",
    "dn": ALICE_DN,
    "attributes": {
        "sAMAccountName": "alice",
        "cn": "Alice",
        "mail": "alice@corp.com",
        "telephoneNumber": "13800000001",
        "userAccountControl": 512,
    },
}


class FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def unbind(self):
        pass


class FakeBadConn(FakeConn):
    """模拟用户 DN bind 失败（密码错误）。"""


@pytest.fixture
def ldap_on(settings):
    settings.LDAP_AUTH_ENABLED = True
    settings.LDAP_SERVER_URI = "ldap://fake"
    settings.LDAP_USER_SEARCH_BASE = "dc=corp,dc=com"
    return settings


@pytest.fixture
def stub_directory(monkeypatch):
    """注入假目录：entries 为搜索结果；bind_error 模拟用户密码错误。"""
    holder = {"entries": [], "bind_error": False, "service_error": False, "searched": 0}

    def fake_service_connection():
        if holder["service_error"]:
            raise LDAPException("cannot connect to directory")
        return FakeConn()

    def fake_user_connection(user_dn, password):
        if holder["bind_error"]:
            raise LDAPException("invalid credentials")
        return FakeConn()

    def fake_search(conn, base, search_filter, attributes):
        holder["searched"] += 1
        return holder["entries"]

    monkeypatch.setattr("system.ldap.auth.service_connection", fake_service_connection)
    monkeypatch.setattr("system.ldap.auth.user_connection", fake_user_connection)
    monkeypatch.setattr("system.ldap.auth.paged_search_entries", fake_search)
    return holder


@pytest.fixture
def backend():
    return LdapBindBackend()


class TestDisabled:
    def test_disabled_returns_none_without_directory(self, backend, monkeypatch):
        """LDAP 关闭：不产生任何连接开销，行为与现状一致。"""
        searched = []
        monkeypatch.setattr("system.ldap.auth.service_connection", lambda: searched.append(1))
        assert backend.authenticate(None, username="alice", password="x") is None
        assert searched == []

    def test_empty_credentials(self, backend, ldap_on, stub_directory):
        assert backend.authenticate(None, username="alice", password=None) is None
        assert backend.authenticate(None, username=None, password="x") is None
        assert stub_directory["searched"] == 0


class TestPriority:
    def test_local_first_defers_usable_local_password(self, backend, ldap_on, stub_directory, superuser):
        """local_first：本地可用密码的账号让位 ModelBackend（目录密码不遮蔽本地管理员）。"""
        assert superuser.has_usable_password()
        assert backend.authenticate(None, username="admin", password="whatever") is None
        assert stub_directory["searched"] == 0

    def test_local_first_binds_when_no_local_password(self, backend, ldap_on, stub_directory):
        """local_first：本地不存在该用户 → 尝试目录 bind 并建号。"""
        stub_directory["entries"] = [ALICE_ENTRY]
        user = backend.authenticate(None, username="alice", password="Dir-Pass-1")
        assert user is not None
        assert user.username == "alice"
        assert user._ldap_authenticated is True
        assert not user.has_usable_password()

    def test_ldap_first_binds_even_with_local_password(self, backend, ldap_on, stub_directory, superuser):
        """ldap_first：即使本地有密码也先 bind 目录。"""
        ldap_on.LDAP_AUTH_PRIORITY = "ldap_first"
        stub_directory["entries"] = [
            dict(ALICE_ENTRY, attributes={**ALICE_ENTRY["attributes"], "sAMAccountName": "admin"})
        ]
        user = backend.authenticate(None, username="admin", password="Dir-Pass-1")
        assert user is not None
        assert user.pk == superuser.pk
        assert stub_directory["searched"] == 1


class TestResolveAndCreate:
    def test_binding_reuses_bound_user(self, backend, ldap_on, stub_directory, normal_user):
        binding = LdapUserBinding.objects.create(user=normal_user, dn=ALICE_DN)
        stub_directory["entries"] = [ALICE_ENTRY]
        user = backend.authenticate(None, username="alice", password="Dir-Pass-1")
        assert user.pk == normal_user.pk
        assert LdapUserBinding.objects.get(pk=binding.pk).user_id == normal_user.pk

    def test_same_username_local_user_gets_binding(self, backend, ldap_on, stub_directory):
        """本地同名但无可用密码（如 OAuth 建号）：复用并自动补绑定（目录接管后续同步）。"""
        local = UserInfo.objects.create_user(username="alice", email="old@corp.com")
        local.set_unusable_password()
        local.save()
        stub_directory["entries"] = [ALICE_ENTRY]
        user = backend.authenticate(None, username="alice", password="Dir-Pass-1")
        assert user.pk == local.pk
        assert LdapUserBinding.objects.filter(user=local, dn=ALICE_DN).exists()

    def test_ldap_first_attaches_binding_for_local_user(self, backend, ldap_on, stub_directory):
        """ldap_first：本地同名有密码账号也可被目录接管并补绑定。"""
        ldap_on.LDAP_AUTH_PRIORITY = "ldap_first"
        local = UserInfo.objects.create_user(username="alice", password="Local-Pass-1")
        stub_directory["entries"] = [ALICE_ENTRY]
        user = backend.authenticate(None, username="alice", password="Dir-Pass-1")
        assert user.pk == local.pk
        assert LdapUserBinding.objects.filter(user=local, dn=ALICE_DN).exists()

    def test_recycled_username_fail_closed(self, backend, ldap_on, stub_directory):
        """同名账号在回收站：username 被占用，拒绝绑定与建号。"""
        local = UserInfo.objects.create_user(username="alice", password="Local-Pass-1")
        local.delete()
        stub_directory["entries"] = [ALICE_ENTRY]
        assert backend.authenticate(None, username="alice", password="Dir-Pass-1") is None
        assert not LdapUserBinding.objects.exists()

    def test_auto_create_off(self, backend, ldap_on, stub_directory):
        ldap_on.LDAP_AUTH_AUTO_CREATE = False
        stub_directory["entries"] = [ALICE_ENTRY]
        assert backend.authenticate(None, username="alice", password="Dir-Pass-1") is None

    def test_email_conflict_cleared_on_create(self, backend, ldap_on, stub_directory):
        """目录邮箱与本地他人冲突：置空邮箱建号，不阻断首次登录。"""
        UserInfo.objects.create_user(username="bob", password="Local-Pass-1", email="alice@corp.com")
        stub_directory["entries"] = [ALICE_ENTRY]
        user = backend.authenticate(None, username="alice", password="Dir-Pass-1")
        assert user is not None
        assert user.email == ""

    def test_disabled_directory_entry_rejected(self, backend, ldap_on, stub_directory):
        disabled = dict(ALICE_ENTRY, attributes={**ALICE_ENTRY["attributes"], "userAccountControl": 514})
        stub_directory["entries"] = [disabled]
        assert backend.authenticate(None, username="alice", password="Dir-Pass-1") is None

    def test_entry_without_username_skipped(self, backend, ldap_on, stub_directory):
        no_name = dict(ALICE_ENTRY, attributes={"cn": "nouser"})
        stub_directory["entries"] = [no_name]
        assert backend.authenticate(None, username="alice", password="Dir-Pass-1") is None


class TestDegradation:
    def test_wrong_password_returns_none(self, backend, ldap_on, stub_directory):
        """密码错误：返回 None 落回 ModelBackend，登录链继续（防爆破逻辑接管）。"""
        stub_directory["entries"] = [ALICE_ENTRY]
        stub_directory["bind_error"] = True
        assert backend.authenticate(None, username="alice", password="wrong") is None

    def test_directory_unreachable_returns_none(self, backend, ldap_on, stub_directory):
        """目录不可达：可读告警 + None，绝不抛异常阻断本地登录。"""
        stub_directory["service_error"] = True
        assert backend.authenticate(None, username="alice", password="x") is None

    def test_user_not_in_directory_falls_back(self, backend, ldap_on, stub_directory):
        stub_directory["entries"] = []
        assert backend.authenticate(None, username="alice", password="x") is None


class TestGetUser:
    def test_get_user_active(self, backend, normal_user):
        assert backend.get_user(normal_user.pk).pk == normal_user.pk

    def test_get_user_inactive_rejected(self, backend, normal_user):
        normal_user.is_active = False
        normal_user.save(update_fields=["is_active"])
        assert backend.get_user(normal_user.pk) is None

    def test_get_user_missing(self, backend):
        assert backend.get_user(99999) is None
