# -*- coding: utf-8 -*-
"""Redis 在线统计测试。

覆盖：
1. 反向索引（online:users）驱动 get_online_users / get_online_info，
   不再 SCAN 全库（get_groups 仅作降级路径）；
2. 快照缓存：TTL 内重复查询不再重新统计；
3. 心跳直收：ping 不再绕行 channel layer 队列；
4. 批量推送：一次桥接完成全部在线用户推送，且只读一次用户配置；
5. 组名解析防御：非个人组名（聊天室）不混入结果、不再抛 ValueError。
"""

import time

import pytest
from django.core.cache import cache

from message import utils as msg_utils
from message.utils import get_online_info, get_online_users, parse_online_user_pk
from notifications.message import SiteMessageUtil
from notifications.models import MessageContent

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_snapshot():
    cache.delete(msg_utils.ONLINE_INFO_CACHE_KEY)
    yield
    cache.delete(msg_utils.ONLINE_INFO_CACHE_KEY)


@pytest.fixture
def layer(settings):
    """测试用内存 channel layer（tests/channel_layer.py 提供同名方法）。"""
    from channels.layers import get_channel_layer

    layer = get_channel_layer()
    layer._online_users = {}
    # layer 实例在进程内复用，必须清理上一条用例遗留的 group
    if hasattr(layer, "groups") and hasattr(layer.groups, "clear"):
        layer.groups.clear()
    yield layer
    layer._online_users = {}
    if hasattr(layer, "groups") and hasattr(layer.groups, "clear"):
        layer.groups.clear()


def _beat(layer, user_pk, channel="chan"):
    """模拟一次前端心跳（每 10s 一次的 ping）。"""
    group = msg_utils.get_user_layer_group_name(user_pk)
    layer.groups.setdefault(group, set()).add(channel)
    layer._online_users[user_pk] = time.time()


class TestOnlineReverseIndex:
    def test_online_users_from_reverse_index(self, layer):
        _beat(layer, 1, "c1")
        _beat(layer, 2, "c2")
        layer._online_users[3] = time.time() - 999  # 超过 30s 未心跳，视为离线

        assert sorted(get_online_users()) == [1, 2]

    def test_get_groups_not_called_when_reverse_index_ready(self, layer, monkeypatch):
        _beat(layer, 7, "c7")

        def boom():
            raise AssertionError("反向索引可用时不应 SCAN 全库")

        monkeypatch.setattr(layer, "get_groups", boom)
        assert get_online_users() == [7]

    def test_get_online_info_returns_sockets(self, layer):
        group = msg_utils.get_user_layer_group_name(11)
        layer.groups[group] = {"chan-1": 0, "chan-2": 0}
        layer._online_users = {11: time.time()}

        pks, sockets = get_online_info()
        assert pks == [11]
        assert sorted(sockets) == ["chan-1", "chan-2"]

    def test_online_info_snapshot_cached(self, layer):
        group = msg_utils.get_user_layer_group_name(21)
        layer.groups[group] = {"chan-a": 0}
        layer._online_users = {21: time.time()}

        first = get_online_info()
        layer._online_users[22] = time.time()  # 新心跳，但快照仍在有效期内
        second = get_online_info()
        assert first == second == ([21], ["chan-a"])
        assert cache.get(msg_utils.ONLINE_INFO_CACHE_KEY) is not None

    def test_falls_back_to_groups_when_index_empty(self, layer):
        group = msg_utils.get_user_layer_group_name(31)
        layer.groups[group] = {"chan-b": 0}
        # 反向索引为空（Redis 重启后首个心跳尚未到来的窗口）-> 降级走 get_groups 重建
        result = get_online_info()
        assert result[0] == [31]
        assert result[1] == ["chan-b"]

    def test_chat_room_group_never_in_results(self, layer):
        """旧实现 int(group.split('_')[-1]) 会把聊天室解析成 pk=0 混入结果"""
        layer.groups["message_system_default_0"] = {"chan-room": 0}
        pks, sockets = get_online_info()
        assert 0 not in pks
        assert pks == []


class TestOnlineLayersBatch:
    """批量在线 channel 查询：走 layer 的批量接口（单 Redis 部署一次往返）。"""

    def test_uses_batch_interface_once(self, monkeypatch):
        calls = []

        class SpyLayer:
            async def get_layers_for_groups(self, groups):
                calls.append(list(groups))
                return {group: [f"chan-{group}"] for group in groups}

        monkeypatch.setattr(msg_utils, "channel_layer", SpyLayer())
        result = msg_utils.get_online_users_layers([1, 2, 1])  # 重复 pk 自动去重
        expected = {
            1: [f"chan-{msg_utils.get_user_layer_group_name(1)}"],
            2: [f"chan-{msg_utils.get_user_layer_group_name(2)}"],
        }
        assert result == expected
        assert len(calls) == 1, "应合并为一次批量调用（而非逐用户往返）"

    def test_falls_back_to_per_user_query(self, monkeypatch):
        class LegacyLayer:
            async def get_layers(self, group):
                return ["legacy"]

        monkeypatch.setattr(msg_utils, "channel_layer", LegacyLayer())
        assert msg_utils.get_online_users_layers([5]) == {5: ["legacy"]}

    def test_empty_input_no_call(self, monkeypatch):
        class Boom:
            async def get_layers_for_groups(self, groups):
                raise AssertionError("空入参不应触发查询")

        monkeypatch.setattr(msg_utils, "channel_layer", Boom())
        assert msg_utils.get_online_users_layers([]) == {}


class TestGroupNameParsing:
    def test_parse_user_group(self):
        assert parse_online_user_pk("websocket_group_123") == 123

    def test_parse_chat_room_group_is_ignored(self):
        assert parse_online_user_pk("message_system_default_0") is None

    def test_parse_invalid_group_is_ignored(self):
        assert parse_online_user_pk("websocket_group_abc") is None
        assert parse_online_user_pk("") is None


class TestBatchPush:
    def _make_message(self, user=None):
        msg = MessageContent.objects.create(title="t", message="m", notice_type=MessageContent.NoticeChoices.USER)
        if user:
            msg.notice_user.add(user)
        return msg

    def test_push_notice_messages_batched(self, monkeypatch):
        msg = self._make_message()

        monkeypatch.setattr("notifications.message.get_online_users", lambda: [1, 2, 3])
        pushes = []
        monkeypatch.setattr(
            "message.utils.push_messages",
            lambda pks, message, message_type="push_message": pushes.append((list(pks), message)),
        )
        monkeypatch.setattr(
            "notifications.message.batch_user_config", lambda pks, key, default=None: {pk: True for pk in pks}
        )

        SiteMessageUtil.push_notice_messages(msg, [1, 2, 5])

        assert len(pushes) == 1
        assert pushes[0][0] == [1, 2]
        assert pushes[0][1]["message_type"] == "notify_message"

    def test_push_notice_messages_skips_disabled_users(self, monkeypatch):
        msg = self._make_message()

        monkeypatch.setattr("notifications.message.get_online_users", lambda: [1, 2])
        pushes = []
        monkeypatch.setattr(
            "message.utils.push_messages", lambda pks, message, message_type="push_message": pushes.append(list(pks))
        )
        # 推送任务内 from-import message.utils.push_messages，patch 源头模块命名空间
        monkeypatch.setattr(
            "notifications.message.batch_user_config", lambda pks, key, default=None: {1: False, 2: True}
        )

        SiteMessageUtil.push_notice_messages(msg, [1, 2])
        assert pushes == [[2]]

    def test_push_notice_messages_no_online_user_no_push(self, monkeypatch):
        msg = self._make_message()
        monkeypatch.setattr("notifications.message.get_online_users", lambda: [])
        pushes = []
        monkeypatch.setattr(
            "message.utils.push_messages", lambda pks, message, message_type="push_message": pushes.append(list(pks))
        )

        SiteMessageUtil.push_notice_messages(msg, [1, 2])
        assert pushes == []

    def test_push_notice_messages_uses_single_bridge(self, monkeypatch):
        """批量推送只调用一次 push_messages，而非每用户一次桥接"""
        msg = self._make_message()
        monkeypatch.setattr("notifications.message.get_online_users", lambda: list(range(50)))
        calls = {"push_messages": 0}
        monkeypatch.setattr(
            "message.utils.push_messages",
            lambda pks, message, message_type="push_message": calls.__setitem__(
                "push_messages", calls["push_messages"] + 1
            ),
        )
        monkeypatch.setattr(
            "notifications.message.batch_user_config", lambda pks, key, default=None: {pk: True for pk in pks}
        )

        SiteMessageUtil.push_notice_messages(msg, list(range(50)))
        assert calls == {"push_messages": 1}

    def test_push_notice_messages_dispatches_celery_job(self, monkeypatch):
        """全量扇出改由 Celery 任务投递（请求线程不再直接逐人推送），参数为 JSON 原生类型。"""
        import json
        from types import SimpleNamespace

        msg = self._make_message()
        monkeypatch.setattr("notifications.message.get_online_users", lambda: [1, 2])
        monkeypatch.setattr(
            "notifications.message.batch_user_config", lambda pks, key, default=None: {pk: True for pk in pks}
        )
        dispatched = []
        monkeypatch.setattr(
            "notifications.message.push_messages_job",
            SimpleNamespace(delay=lambda pks, message, message_type="push_message": dispatched.append((pks, message))),
        )

        SiteMessageUtil.push_notice_messages(msg, [1, 2])

        assert len(dispatched) == 1
        pks, message = dispatched[0]
        assert pks == [1, 2]
        assert message["message_type"] == "notify_message"
        json.dumps([pks, message])  # celery JSON 序列化安全（无非原生类型）


class TestPushMessagesJob:
    def test_job_calls_batch_push_once(self, monkeypatch):
        from notifications.tasks import push_messages_job

        pushes = []

        def fake_push(pks, message, message_type="push_message"):
            pushes.append((list(pks), message, message_type))

        monkeypatch.setattr("message.utils.push_messages", fake_push)
        assert push_messages_job([1, 2], {"a": 1}) == 2
        assert pushes == [([1, 2], {"a": 1}, "push_message")]

    def test_job_empty_targets_skipped(self, monkeypatch):
        from notifications.tasks import push_messages_job

        monkeypatch.setattr(
            "message.utils.push_messages", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError)
        )
        assert push_messages_job([], {}) == 0

    def test_json_safe_normalizes_non_native_types(self):
        import uuid

        from django.utils.translation import gettext_lazy as _

        from notifications.tasks import json_safe

        result = json_safe({"pk": uuid.UUID(int=1), "label": _("Test label"), "items": [uuid.UUID(int=2)]})
        assert result["pk"] == str(uuid.UUID(int=1))
        assert result["label"] == "Test label"
        assert result["items"] == [str(uuid.UUID(int=2))]

    def test_batch_user_config_single_get_many(self, monkeypatch):
        """N 个用户的配置读取只有一次 get_many"""
        from django.core import cache as django_cache_mod

        calls = {"get_many": 0}
        original = django_cache_mod.cache.get_many

        def counting_get_many(keys):
            calls["get_many"] += 1
            return original(keys)

        monkeypatch.setattr(django_cache_mod.cache, "get_many", counting_get_many)
        result = msg_utils  # noqa: F841  保持导入
        import common.core.config as config_mod

        assert config_mod.batch_user_config([1, 2, 3], "PUSH_MESSAGE_NOTICE", True) == {1: True, 2: True, 3: True}
        assert calls["get_many"] == 1

    def test_batch_user_config_empty(self):
        import common.core.config as config_mod

        assert config_mod.batch_user_config([], "PUSH_MESSAGE_NOTICE") == {}

    def test_batch_user_config_uses_personal_value(self):
        """用户单独配置时优先取个人值，不回退系统默认"""
        import common.core.config as config_mod
        from common.cache.storage import UserSystemConfigCache

        UserSystemConfigCache("user_9_PUSH_MESSAGE_NOTICE").set_storage_cache(
            {"key": "PUSH_MESSAGE_NOTICE", "value": False, "access": True}
        )
        try:
            assert config_mod.batch_user_config([9], "PUSH_MESSAGE_NOTICE", True) == {9: False}
        finally:
            UserSystemConfigCache("user_9_PUSH_MESSAGE_NOTICE").del_storage_cache()
