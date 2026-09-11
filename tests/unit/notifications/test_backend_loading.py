# -*- coding: utf-8 -*-
"""通知后端约定式加载与渠道过滤的容错测试。"""

from types import ModuleType
from unittest import mock

from notifications.backends import BACKEND, client_name_mapper, load_backend_clients
from notifications.notifications import Message


class MockNotice(Message):
    def get_common_msg(self) -> dict:
        return {"subject": "s", "message": "m"}


class TestLoadBackendClients:
    def test_loads_all_registered_backends(self):
        """约定加载覆盖枚举内全部渠道：backends/<name>.py 暴露模块级 backend。"""
        try:
            client_name_mapper.clear()
            load_backend_clients()
            assert set(client_name_mapper) == set(BACKEND)
        finally:
            load_backend_clients()

    def test_tolerates_import_failure(self, caplog):
        """单个渠道模块缺失只告警跳过，不阻断通知模块启动。"""
        try:
            client_name_mapper.clear()
            with mock.patch("notifications.backends.importlib.import_module", side_effect=ImportError("boom")):
                load_backend_clients()
            assert client_name_mapper == {}
            assert any("skip it" in record.message for record in caplog.records)
        finally:
            load_backend_clients()

    def test_tolerates_module_without_backend_attr(self, caplog):
        """模块未暴露 `backend` 属性时告警跳过，不写入映射。"""
        fake_module = ModuleType("notifications.backends.fake")
        try:
            client_name_mapper.clear()
            with mock.patch("notifications.backends.importlib.import_module", return_value=fake_module):
                load_backend_clients([BACKEND.EMAIL])
            assert BACKEND.EMAIL not in client_name_mapper
            assert any("skip it" in record.message for record in caplog.records)
        finally:
            load_backend_clients()


class TestFilterEnableBackends:
    def test_filters_disabled_backend(self, settings):
        settings.EMAIL_ENABLED = False
        assert BACKEND.filter_enable_backends(["email", "site_msg"]) == ["site_msg"]

    def test_tolerates_unknown_backend_value(self, settings):
        """存量订阅数据里的已下线渠道（dingtalk）只跳过，不让历史数据打断发布。"""
        settings.EMAIL_ENABLED = True
        assert BACKEND.filter_enable_backends(["email", "dingtalk"]) == ["email"]


class TestMsgMapperUnknownBackend:
    def test_mapper_skips_unknown_backend(self):
        """渲染映射对未知渠道取值兜底：站内信恒在，未知渠道告警跳过。"""
        mapper = MockNotice().get_backend_msg_mapper(["dingtalk"])
        assert set(mapper) == {BACKEND.SITE_MSG}
