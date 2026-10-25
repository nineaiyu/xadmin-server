# -*- coding: utf-8 -*-
"""系统消息「用户自选订阅」守护测试。

背景：主机资源阈值告警（ServerPerformanceMessage）开放个人订阅页自选订阅——
系统订阅页的管理员兜底收件不变，publish 时并入 UserMsgSubscription 里勾选了
渠道的自选者，按各自渠道集合分组投递。未勾选渠道（空 receive_backends，
个人订阅页首访会批量补齐空行）视为不订阅，避免「补齐行即全员广播」。
"""

from types import SimpleNamespace
from unittest import mock

import pytest
from django.test import override_settings

import common.notifications  # noqa: F401  导入即注册消息类型
from common.contracts import BACKEND, SystemMsgSubscription
from notifications.models import UserMsgSubscription
from notifications.notifications import SYSTEM_MESSAGE_REGISTRY, Message

pytestmark = pytest.mark.django_db

USER_SUB_URL = "/api/notifications/user-msg-subscription"
MESSAGE_TYPE = "ServerPerformanceMessage"


def _make_msg():
    from common.notifications import ServerPerformanceMessage

    return ServerPerformanceMessage([(SimpleNamespace(name="CPU"), ["usage 92% >= 90%"])])


def _set_system_subscription(users, backends):
    sub, _ = SystemMsgSubscription.objects.get_or_create(message_type=MESSAGE_TYPE)
    sub.users.set(users)
    sub.receive_backends = backends
    sub.save()
    return sub


def _sent_groups(fake_send):
    """收集每次发送的（渠道集合, 收件人 pk 集合）；站内信在 mapper 内恒发叠加。"""
    return [(frozenset(call.args[1].keys()), frozenset(call.args[0])) for call in fake_send.call_args_list]


class TestUserSubscribableFlag:
    def test_server_performance_open_for_user_subscription(self):
        info = next(m for m in SYSTEM_MESSAGE_REGISTRY if m["message_type"] == MESSAGE_TYPE)
        assert info["user_subscribable"] is True

    def test_other_system_messages_stay_closed(self):
        """其余系统消息默认不开放（并入逻辑零行为变化面）。"""
        others = [m for m in SYSTEM_MESSAGE_REGISTRY if m["message_type"] != MESSAGE_TYPE]
        assert others
        assert all(info["user_subscribable"] is False for info in others)


class TestPublishUserSubscribers:
    @override_settings(EMAIL_ENABLED=True)
    def test_user_subscriber_receives_via_own_backends(self, superuser, normal_user):
        """自选者按勾选渠道分组：与系统订阅渠道不同则分两次发送。"""
        # 系统订阅收件人保留但渠道清空（仅站内信），自选者勾选 email —— 两组不同
        _set_system_subscription([superuser], [])
        UserMsgSubscription.objects.create(
            user=normal_user,
            message_type=MESSAGE_TYPE,
            receive_backends=[BACKEND.EMAIL],
        )
        with mock.patch.object(Message, "send_msg") as fake_send:
            _make_msg().publish()

        groups = _sent_groups(fake_send)
        assert fake_send.call_count == 2
        assert {backends for backends, _ in groups} == {
            frozenset({BACKEND.SITE_MSG}),
            frozenset({BACKEND.SITE_MSG, BACKEND.EMAIL}),
        }
        recipients = {user for _, users in groups for user in users}
        assert recipients == {superuser.pk, normal_user.pk}

    @override_settings(EMAIL_ENABLED=True)
    def test_both_registered_same_backends_single_group(self, superuser, normal_user):
        """系统订阅与自选渠道相同的用户并为一组一次发送。"""
        _set_system_subscription([superuser, normal_user], [BACKEND.EMAIL])
        UserMsgSubscription.objects.create(
            user=normal_user,
            message_type=MESSAGE_TYPE,
            receive_backends=[BACKEND.EMAIL],
        )
        with mock.patch.object(Message, "send_msg") as fake_send:
            _make_msg().publish()

        assert fake_send.call_count == 1
        call = fake_send.call_args_list[0]
        assert set(call.args[1].keys()) == {BACKEND.SITE_MSG, BACKEND.EMAIL}
        assert set(call.args[0]) == {superuser.pk, normal_user.pk}

    @override_settings(EMAIL_ENABLED=True)
    def test_same_user_merges_backends_from_both_sides(self, superuser, normal_user):
        """同一用户在系统订阅与自选两边登记时渠道取并集。"""
        from notifications.backends.sms import SMS

        _set_system_subscription([superuser, normal_user], [BACKEND.EMAIL])
        UserMsgSubscription.objects.create(
            user=normal_user,
            message_type=MESSAGE_TYPE,
            receive_backends=[BACKEND.SMS],
        )
        # 短信渠道测试环境无平台配置（is_enable 恒 False），patch 放行以构造并集
        with (
            mock.patch.object(SMS, "is_enable", return_value=True),
            mock.patch.object(Message, "send_msg") as fake_send,
        ):
            _make_msg().publish()

        groups = _sent_groups(fake_send)
        by_user = {next(iter(users)): backends for backends, users in groups}
        assert by_user[superuser.pk] >= {BACKEND.SITE_MSG, BACKEND.EMAIL}
        assert by_user[normal_user.pk] >= {
            BACKEND.SITE_MSG,
            BACKEND.EMAIL,
            BACKEND.SMS,
        }

    @override_settings(EMAIL_ENABLED=True)
    def test_empty_backends_means_not_subscribed(self, superuser, normal_user):
        """空 receive_backends（个人订阅页补齐行）不并入：默认不订阅。"""
        _set_system_subscription([superuser], [BACKEND.EMAIL])
        UserMsgSubscription.objects.create(user=normal_user, message_type=MESSAGE_TYPE, receive_backends=[])
        with mock.patch.object(Message, "send_msg") as fake_send:
            _make_msg().publish()

        assert fake_send.call_count == 1
        assert set(fake_send.call_args_list[0].args[0]) == {superuser.pk}

    @override_settings(EMAIL_ENABLED=True)
    def test_self_healed_admins_do_not_drop_user_subscribers(self, superuser, normal_user):
        """系统收件人自愈（回补超管）不影响自选者并入：两者都收到。"""
        # 不预建系统订阅行：publish 自愈补超管并落默认 email 渠道
        UserMsgSubscription.objects.create(
            user=normal_user,
            message_type=MESSAGE_TYPE,
            receive_backends=[BACKEND.EMAIL],
        )
        with mock.patch.object(Message, "send_msg") as fake_send:
            _make_msg().publish()

        assert fake_send.call_count >= 1
        recipients = {user for call in fake_send.call_args_list for user in call.args[0]}
        assert recipients == {superuser.pk, normal_user.pk}


class TestSubscriptionView:
    def test_user_subscription_list_includes_open_system_messages(self, auth_client):
        """个人订阅页列出开放自选的系统消息并补齐订阅行。"""
        resp = auth_client.get(USER_SUB_URL)
        assert resp.status_code == 200
        tree = resp.data["data"]
        all_types = [child["message_type"] for node in tree for child in node["children"]]
        assert MESSAGE_TYPE in all_types
        assert UserMsgSubscription.objects.filter(message_type=MESSAGE_TYPE).exists()

    def test_open_system_message_patch_roundtrip(self, auth_client, superuser):
        """个人订阅页可勾选渠道（PATCH 订阅行）——完整订阅闭环。"""
        auth_client.get(USER_SUB_URL)  # 先补齐行
        resp = auth_client.patch(
            f"{USER_SUB_URL}/{MESSAGE_TYPE}",
            {"receive_backends": [BACKEND.EMAIL]},
            format="json",
        )
        assert resp.status_code == 200
        sub = UserMsgSubscription.objects.get(user=superuser, message_type=MESSAGE_TYPE)
        assert sub.receive_backends == [BACKEND.EMAIL]
