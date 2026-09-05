# -*- coding: utf-8 -*-
"""通知注册机制单测（T2.4 / TD-10）。

覆盖显式注册表与后端渲染注册：
- @register_message 替代元类隐式收集后的注册行为
- register_backend_msg 后端渲染方法集中注册与回退
"""

import pytest

from notifications.backends import BACKEND
from notifications.notifications import (
    BACKEND_MSG_RENDERERS,
    SYSTEM_MESSAGE_REGISTRY,
    USER_MESSAGE_REGISTRY,
    Message,
    SystemMessage,
    UserMessage,
    register_backend_msg,
    register_message,
)


@pytest.fixture
def _clean_registries():
    """测试期间注册表变动不外泄。"""
    sys_len, user_len = len(SYSTEM_MESSAGE_REGISTRY), len(USER_MESSAGE_REGISTRY)
    renderer_snapshot = dict(BACKEND_MSG_RENDERERS)
    yield
    del SYSTEM_MESSAGE_REGISTRY[sys_len:]
    del USER_MESSAGE_REGISTRY[user_len:]
    BACKEND_MSG_RENDERERS.clear()
    BACKEND_MSG_RENDERERS.update(renderer_snapshot)


class TestRegisterMessage:
    def test_register_system_and_user_message(self, _clean_registries):
        @register_message
        class MockSystemAnnouncement(SystemMessage):
            category = "Mock"
            category_label = "Mock category"
            message_type_label = "Mock announcement"

        @register_message
        class MockUserWelcome(UserMessage):
            category = "Mock"
            category_label = "Mock category"
            message_type_label = "Mock welcome"

            def __init__(self, user):
                self.user = user

        assert SYSTEM_MESSAGE_REGISTRY[-1]["message_type"] == "MockSystemAnnouncement"
        assert SYSTEM_MESSAGE_REGISTRY[-1]["category"] == "Mock"
        assert [m["message_type"] for m in USER_MESSAGE_REGISTRY[-1:]] == ["MockUserWelcome"]
        assert SYSTEM_MESSAGE_REGISTRY[-1]["message_type_label"] == "Mock announcement"
        assert USER_MESSAGE_REGISTRY[-1]["category_label"] == "Mock category"

    def test_register_rejects_non_message(self, _clean_registries):
        with pytest.raises(TypeError):
            register_message(type("Plain", (), {}))

    def test_register_rejects_base_message(self, _clean_registries):
        class Neither(Message):
            category = "x"
            category_label = "x"
            message_type_label = "x"

        # Message 本身既不是 System 也不是 User，不允许注册
        with pytest.raises(TypeError):
            register_message(Neither)


class TestRegisterBackendMsg:
    def test_registered_renderer_used(self, _clean_registries):
        class MockNotify(Message):
            category = "Mock"
            category_label = "Mock"
            message_type_label = "Mock"

            def get_mock_channel_msg(self):
                return {"subject": "mock", "message": "via registry"}

        register_backend_msg(BACKEND.SITE_MSG, "get_mock_channel_msg")
        mapper = MockNotify().get_backend_msg_mapper([])
        # site_msg 始终必发，且走注册的渲染方法而非内置 get_site_msg_msg
        assert mapper[BACKEND.SITE_MSG] == {"subject": "mock", "message": "via registry"}

    def test_unregistered_backend_falls_back_to_common_msg(self, _clean_registries):
        BACKEND_MSG_RENDERERS.pop(BACKEND.SITE_MSG, None)
        mapper = Message.get_common_msg()
        assert mapper == {"subject": "", "message": ""}
        site_mapper = Message()
        result = site_mapper.get_backend_msg_mapper([])
        assert result[BACKEND.SITE_MSG] == {"subject": "", "message": ""}

    def test_subclass_renderer_override_still_works(self, _clean_registries):
        """注册表指向方法名，子类覆写同名方法仍生效。"""

        @register_message
        class MockSystemNotice(SystemMessage):
            category = "Mock"
            category_label = "Mock"
            message_type_label = "Mock"

            def get_site_msg_msg(self):
                return {"subject": "override", "message": "subclass"}

        mapper = MockSystemNotice().get_backend_msg_mapper([])
        assert mapper[BACKEND.SITE_MSG]["subject"] == "override"
