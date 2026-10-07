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
    cache.delete(msg_utils.ONLINE_LAYERS_CACHE_KEY)
    yield
    cache.delete(msg_utils.ONLINE_INFO_CACHE_KEY)
    cache.delete(msg_utils.ONLINE_LAYERS_CACHE_KEY)


@pytest.fixture
def layer(settings):
    """channel layer（InMemory 档 / 真 Redis 层通用，清理走 tests/channel_layer helper）。"""
    from channels.layers import get_channel_layer

    from tests.channel_layer import reset_layer_state

    layer = get_channel_layer()
    reset_layer_state(layer)
    yield layer
    reset_layer_state(layer)


def _beat(layer, user_pk, channel="chan"):
    """模拟一次前端心跳（每 10s 一次的 ping）：双栈同名的公开 API。"""
    from tests.channel_layer import beat_layer

    beat_layer(layer, user_pk, channel)


class TestOnlineReverseIndex:
    def test_online_users_from_reverse_index(self, layer):
        from tests.channel_layer import inject_online_user

        _beat(layer, 1, "c1")
        _beat(layer, 2, "c2")
        inject_online_user(layer, 3, at=time.time() - 999)  # 超过 30s 未心跳，视为离线

        assert sorted(get_online_users()) == [1, 2]

    def test_get_groups_not_called_when_reverse_index_ready(self, layer, monkeypatch):
        _beat(layer, 7, "c7")

        def boom():
            raise AssertionError("反向索引可用时不应 SCAN 全库")

        monkeypatch.setattr(layer, "get_groups", boom)
        assert get_online_users() == [7]

    def test_get_online_info_returns_sockets(self, layer):
        from tests.channel_layer import inject_group_channels, inject_online_user

        group = msg_utils.get_user_layer_group_name(11)
        inject_group_channels(layer, group, ["chan-1", "chan-2"])
        inject_online_user(layer, 11)

        pks, sockets = get_online_info()
        assert pks == [11]
        assert sorted(sockets) == ["chan-1", "chan-2"]

    def test_online_info_snapshot_cached(self, layer):
        from tests.channel_layer import inject_group_channels, inject_online_user

        group = msg_utils.get_user_layer_group_name(21)
        inject_group_channels(layer, group, ["chan-a"])
        inject_online_user(layer, 21)

        first = get_online_info()
        inject_online_user(layer, 22)  # 新心跳，但快照仍在有效期内
        second = get_online_info()
        assert first == second == ([21], ["chan-a"])
        assert cache.get(msg_utils.ONLINE_INFO_CACHE_KEY) is not None

    def test_falls_back_to_groups_when_index_empty(self, layer):
        from tests.channel_layer import inject_group_channels

        group = msg_utils.get_user_layer_group_name(31)
        inject_group_channels(layer, group, ["chan-b"])
        # 反向索引为空（Redis 重启后首个心跳尚未到来的窗口）-> 降级走 get_groups 重建
        result = get_online_info()
        assert result[0] == [31]
        assert result[1] == ["chan-b"]

    def test_chat_room_group_never_in_results(self, layer):
        """旧实现 int(group.split('_')[-1]) 会把聊天室解析成 pk=0 混入结果"""
        from tests.channel_layer import inject_group_channels

        inject_group_channels(layer, "message_system_default_0", ["chan-room"])
        pks, sockets = get_online_info()
        assert 0 not in pks
        assert pks == []


class TestOnlineLayersBatch:
    """批量在线 channel 查询：展示口径走 5s 快照，动作口径（强制下线）强制实时。"""

    def test_snapshot_builds_once_and_serves_from_cache(self, layer):
        _beat(layer, 1, "c1")
        _beat(layer, 2, "c2")
        calls = []
        original = layer.get_layers_for_groups

        async def spy(groups):
            calls.append(list(groups))
            return await original(groups)

        layer.get_layers_for_groups = spy

        first = msg_utils.get_online_users_layers([1, 2, 1])  # 重复 pk 自动去重
        second = msg_utils.get_online_users_layers([2])
        assert first == {1: ["c1"], 2: ["c2"]}
        assert second == {2: ["c2"]}
        assert len(calls) == 1, "5s 快照窗口内的重复查询不应再次访问 channel layer"
        assert cache.get(msg_utils.ONLINE_LAYERS_CACHE_KEY) is not None

    def test_snapshot_rebuilds_after_expiry(self, layer):
        _beat(layer, 3, "c3")
        assert msg_utils.get_online_users_layers([3]) == {3: ["c3"]}
        cache.delete(msg_utils.ONLINE_LAYERS_CACHE_KEY)  # 等价 TTL 到期
        _beat(layer, 4, "c4")
        assert msg_utils.get_online_users_layers([3, 4]) == {3: ["c3"], 4: ["c4"]}

    def test_snapshot_falls_back_to_groups_when_index_empty(self, layer):
        from tests.channel_layer import inject_group_channels

        group = msg_utils.get_user_layer_group_name(6)
        inject_group_channels(layer, group, ["chan-6"])
        # 反向索引为空（Redis 重启后首个心跳尚未到来）→ 降级 SCAN 重建
        assert msg_utils.get_online_users_layers([6]) == {6: ["chan-6"]}

    def test_realtime_path_queries_each_call_without_snapshot(self, layer):
        from tests.channel_layer import inject_group_channels

        _beat(layer, 5, "c5")
        first = msg_utils.get_online_users_layers([5], use_snapshot=False)
        inject_group_channels(layer, msg_utils.get_user_layer_group_name(5), ["c6"])  # 新连接
        second = msg_utils.get_online_users_layers([5], use_snapshot=False)
        assert first == {5: ["c5"]}
        assert sorted(second[5]) == ["c5", "c6"], "实时口径必须拿到最新 channel 明细"
        assert cache.get(msg_utils.ONLINE_LAYERS_CACHE_KEY) is None, "实时口径不写快照"

    def test_falls_back_to_per_user_query(self, monkeypatch):
        class LegacyLayer:
            async def get_layers(self, group):
                return ["legacy"]

        monkeypatch.setattr(msg_utils, "channel_layer", LegacyLayer())
        assert msg_utils.get_online_users_layers([5], use_snapshot=False) == {5: ["legacy"]}

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
        # 与 gettext 同源取值（本地有 .mo 时为中文译文、CI 无 .mo 时为 msgid 原文）
        assert result["label"] == str(_("Test label"))
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


class TestLogoutKick:
    """强退踢线：返回是否命中在线 channel，已下线会话不再投递（未命中可被调用方区分）。"""

    def test_hit_pushes_logout_and_discards_group(self, layer):
        from asgiref.sync import async_to_sync

        from message.utils import get_user_layer_group_name, send_logout_msg

        _beat(layer, 11, "ch-live")
        assert send_logout_msg(11, ["ch-live"]) is True
        # 命中即逐 channel 推送并退组：组内清空
        assert async_to_sync(layer.get_layers)(get_user_layer_group_name(11)) == []

    def test_stale_channel_misses_without_push(self, layer):
        """已下线会话（channel 不在推送组）：计为未命中，不再做无效投递。"""
        from message.utils import send_logout_msg

        assert send_logout_msg(12, ["ch-gone"]) is False

    def test_auto_resolve_group_channels_reports_hit(self, layer):
        """不传 channel 名时取推送组存活 channel：在线命中、离线未命中。"""
        from message.utils import send_logout_msg

        _beat(layer, 13, "ch-auto")
        assert send_logout_msg(13) is True
        assert send_logout_msg(13) is False  # 上一次命中已退组，组内无 channel
