# -*- coding: utf-8 -*-
"""common.core.db.prefix 表前缀信号（自 server/utils.py 归位后的本体行为）。"""

import weakref
from types import SimpleNamespace

from django.db.models.signals import class_prepared
from django.test import override_settings

import common.core.db.prefix as db_prefix


def _make_sender(db_table="system_userinfo"):
    meta = SimpleNamespace(
        managed=True,
        app_label="system",
        label_lower="identity.userinfo",
        label="system.UserInfo",
        db_table=db_table,
    )
    return SimpleNamespace(_meta=meta)


class TestAddDbPrefix:
    @override_settings(DB_PREFIX="px_")
    def test_apply_string_prefix(self):
        sender = _make_sender()
        db_prefix.add_db_prefix(sender)
        assert sender._meta.db_table == "px_system_userinfo"

    @override_settings(DB_PREFIX="px_")
    def test_no_duplicate_prefix(self):
        sender = _make_sender(db_table="px_system_userinfo")
        db_prefix.add_db_prefix(sender)
        assert sender._meta.db_table == "px_system_userinfo"

    @override_settings(DB_PREFIX="")
    def test_no_prefix_is_noop(self):
        sender = _make_sender()
        db_prefix.add_db_prefix(sender)
        assert sender._meta.db_table == "system_userinfo"

    @override_settings(DB_PREFIX={"identity.userinfo": "abc_"})
    def test_dict_prefix_by_label_lower(self):
        sender = _make_sender()
        db_prefix.add_db_prefix(sender)
        assert sender._meta.db_table == "abc_system_userinfo"

    @override_settings(DB_PREFIX={"other.model": "abc_"})
    def test_dict_prefix_fallback_default(self):
        sender = _make_sender()
        db_prefix.add_db_prefix(sender)
        assert sender._meta.db_table == "system_userinfo"


class TestSignalWiring:
    def test_add_db_prefix_connected_to_class_prepared(self):
        """import 本模块即连接 class_prepared（连接时机 = 首次 import，见模块 docstring）。"""
        # Signal.receivers 对函数持弱引用（条目 (lookup_key, receiver) 二元组），解包后按身份断言
        registered = set()
        for entry in class_prepared.receivers:
            for item in entry:
                if isinstance(item, weakref.ref):
                    resolved = item()
                    if resolved is not None:
                        registered.add(resolved)
                elif callable(item):
                    registered.add(item)
        assert db_prefix.add_db_prefix in registered
