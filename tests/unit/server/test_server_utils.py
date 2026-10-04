# -*- coding: utf-8 -*-
"""server.utils 兼容层回归（归位后保留一个版本周期）。

本体已迁 common.local / common.core.db.prefix（本体测试见 tests/unit/common/）；
本文件守护兼容 re-export 与归位实现同源、兼容路径行为不变。归位期结束后
随 server/utils.py 一并删除。
"""

from types import SimpleNamespace

from django.test import override_settings

import common.core.db.prefix as db_prefix
import common.local as common_local
from server.utils import add_db_prefix, get_current_request, set_current_request


class TestShimReexports:
    def test_reexports_are_canonical_objects(self):
        """re-export 与归位实现对象同一（非拷贝），行为漂移不可能发生。"""
        assert get_current_request is common_local.get_current_request
        assert set_current_request is common_local.set_current_request
        assert add_db_prefix is db_prefix.add_db_prefix


class TestCurrentRequestViaShim:
    def test_round_trip(self):
        assert get_current_request() is None
        set_current_request("fake-request")
        assert get_current_request() == "fake-request"
        set_current_request(None)
        assert get_current_request() is None


class TestAddDbPrefixViaShim:
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
