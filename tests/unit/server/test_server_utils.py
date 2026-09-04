# -*- coding: utf-8 -*-
"""server.utils 单元测试（thread-local 请求上下文、DB 表前缀）。"""
from types import SimpleNamespace

import pytest
from django.test import override_settings

from server.utils import add_db_prefix, get_current_request, set_current_request

pytestmark = pytest.mark.django_db


class TestCurrentRequest:
    def test_round_trip(self):
        assert get_current_request() is None
        set_current_request("fake-request")
        assert get_current_request() == "fake-request"
        set_current_request(None)
        assert get_current_request() is None


class TestAddDbPrefix:
    def _make_sender(self, db_table="system_userinfo"):
        meta = SimpleNamespace(
            managed=True,
            app_label="system",
            label_lower="system.userinfo",
            label="system.UserInfo",
            db_table=db_table,
        )
        return SimpleNamespace(_meta=meta)

    @override_settings(DB_PREFIX="px_")
    def test_apply_string_prefix(self):
        sender = self._make_sender()
        add_db_prefix(sender)
        assert sender._meta.db_table == "px_system_userinfo"

    @override_settings(DB_PREFIX="px_")
    def test_no_duplicate_prefix(self):
        sender = self._make_sender(db_table="px_system_userinfo")
        add_db_prefix(sender)
        assert sender._meta.db_table == "px_system_userinfo"

    @override_settings(DB_PREFIX="")
    def test_no_prefix_is_noop(self):
        sender = self._make_sender()
        add_db_prefix(sender)
        assert sender._meta.db_table == "system_userinfo"

    @override_settings(DB_PREFIX={"system.userinfo": "abc_"})
    def test_dict_prefix_by_label_lower(self):
        sender = self._make_sender()
        add_db_prefix(sender)
        assert sender._meta.db_table == "abc_system_userinfo"

    @override_settings(DB_PREFIX={"other.model": "abc_"})
    def test_dict_prefix_fallback_default(self):
        sender = self._make_sender()
        add_db_prefix(sender)
        assert sender._meta.db_table == "system_userinfo"