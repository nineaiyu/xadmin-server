# -*- coding: utf-8 -*-
"""LDAP 客户端轻封装单元测试（ADR-017）：DN 规范化 / 属性归一 / 禁用位判定。"""

import pytest

from system.ldap.client import entry_to_attrs, first_attr, get_attr_map, is_entry_disabled, normalize_dn


class TestNormalizeDn:
    def test_lower_and_trim(self):
        assert normalize_dn("CN=Alice, OU=People,DC=Corp,DC=Com") == "cn=alice,ou=people,dc=corp,dc=com"

    def test_escaped_comma_kept(self):
        dn = r"cn=Smith\, John,ou=people,dc=corp,dc=com"
        # 转义逗号不切分；值内空格保留（属 RDN 值的一部分）
        assert normalize_dn(dn) == r"cn=smith\, john,ou=people,dc=corp,dc=com"

    def test_empty(self):
        assert normalize_dn("") == ""
        assert normalize_dn(None) == ""


class TestEntryAttrs:
    def test_scalar_and_list(self):
        attrs = entry_to_attrs({"attributes": {"cn": "Alice", "objectClass": ["top", "person"], "mail": None}})
        assert attrs["cn"] == "Alice"
        assert attrs["objectclass"] == ["top", "person"]
        # None 单值视为缺失
        assert "mail" not in attrs

    def test_first_attr_case_insensitive(self):
        attrs = {"samaccountname": "alice"}
        assert first_attr(attrs, "sAMAccountName") == "alice"

    def test_first_attr_list_takes_first(self):
        assert first_attr({"mail": ["a@x.com", "b@x.com"]}, "mail") == "a@x.com"
        assert first_attr({"mail": []}, "mail") is None
        assert first_attr({}, "mail") is None


class TestDisabledBit:
    @pytest.mark.parametrize(
        "uac,disabled", [(514, True), (66050, True), (512, False), (66048, False), (None, False), ("abc", False)]
    )
    def test_user_account_control(self, uac, disabled):
        attrs = {"userAccountControl": uac} if uac is not None else {}
        assert is_entry_disabled(attrs) is disabled

    def test_missing_attr_means_active(self):
        assert is_entry_disabled({}) is False


class TestAttrMap:
    def test_defaults(self, settings):
        assert get_attr_map() == {
            "username": "sAMAccountName",
            "nickname": "cn",
            "email": "mail",
            "phone": "telephoneNumber",
        }

    def test_openldap_overrides(self, settings):
        settings.LDAP_ATTR_USERNAME = "uid"
        settings.LDAP_ATTR_NICKNAME = "givenName"
        attr_map = get_attr_map()
        assert attr_map["username"] == "uid"
        assert attr_map["nickname"] == "givenName"
