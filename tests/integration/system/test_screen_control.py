# -*- coding: utf-8 -*-
"""大屏远程控制：控制态 REST 接口 + 展示端 WS 通道。

覆盖：默认态查询、switch/page/refresh/auto 状态迁移与落态回放、非法指令拒绝、
可见性口径（can_view_screen）与展示通道准入（匿名 4401 / 无可见性 4403）、
连接回放控制态与控制帧转发。
"""

import uuid

import pytest
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from system.models.dataset import Dashboard, Screen
from system.ws_screen import ScreenDisplayNotify, can_view_screen, load_screen_state, screen_group_name

pytestmark = pytest.mark.django_db

SCREEN_URL = "/api/system/screens"


@pytest.fixture
def dashboards(superuser):
    return [
        Dashboard.objects.create(name=f"看板{i}", layout=[], visibility="shared", creator=superuser) for i in (1, 2)
    ]


def _make_screen(creator, dashboards, visibility="shared"):
    # name 有唯一约束：同一用例可能建多块大屏，后缀随机化
    return Screen.objects.create(
        name=f"控制测试大屏-{uuid.uuid4().hex[:8]}",
        dashboards=[str(item.pk) for item in dashboards],
        visibility=visibility,
        creator=creator,
    )


class TestScreenCommandApi:
    def test_get_default_state(self, auth_client, superuser, dashboards):
        screen = _make_screen(superuser, dashboards)
        body = auth_client.get(f"{SCREEN_URL}/{screen.pk}/command").json()
        assert body["code"] == 1000
        assert body["data"]["state"]["mode"] == "auto"
        assert body["data"]["state"]["index"] == 0
        assert body["data"]["dashboards"] == [str(item.pk) for item in dashboards]

    def test_switch_page_refresh_auto_flow(self, auth_client, superuser, dashboards):
        screen = _make_screen(superuser, dashboards)
        switch = auth_client.post(
            f"{SCREEN_URL}/{screen.pk}/command",
            {"command": "switch", "dashboard_pk": str(dashboards[1].pk)},
            format="json",
        ).json()
        assert switch["data"]["state"]["mode"] == "manual"
        assert switch["data"]["state"]["index"] == 1

        paged = auth_client.post(
            f"{SCREEN_URL}/{screen.pk}/command", {"command": "page", "index": 0}, format="json"
        ).json()
        assert paged["data"]["state"]["index"] == 0

        refreshed = auth_client.post(f"{SCREEN_URL}/{screen.pk}/command", {"command": "refresh"}, format="json").json()
        assert refreshed["data"]["state"]["refresh_rev"] == 1
        assert refreshed["data"]["state"]["mode"] == "manual"  # 刷新不改变浏览模式

        resumed = auth_client.post(f"{SCREEN_URL}/{screen.pk}/command", {"command": "auto"}, format="json").json()
        assert resumed["data"]["state"]["mode"] == "auto"
        # 落态可回放：后开的展示端读到同一份控制态
        assert load_screen_state(screen.pk)["mode"] == "auto"
        assert load_screen_state(screen.pk)["refresh_rev"] == 1

    def test_invalid_commands_rejected(self, auth_client, superuser, dashboards):
        screen = _make_screen(superuser, dashboards)
        url = f"{SCREEN_URL}/{screen.pk}/command"
        outside = auth_client.post(url, {"command": "switch", "dashboard_pk": "not-in-list"}, format="json").json()
        assert outside["code"] == 1001
        out_of_range = auth_client.post(url, {"command": "page", "index": 99}, format="json").json()
        assert out_of_range["code"] == 1001
        # 参数缺失 / 未知指令走序列化器 400
        assert auth_client.post(url, {"command": "page"}, format="json").status_code == 400
        assert auth_client.post(url, {"command": "unknown"}, format="json").status_code == 400

    def test_visibility_scope(self, superuser, normal_user, dashboards):
        shared = _make_screen(superuser, dashboards, visibility="shared")
        personal = _make_screen(superuser, dashboards, visibility="personal")
        assert can_view_screen(superuser, shared.pk) is True
        assert can_view_screen(normal_user, shared.pk) is True
        assert can_view_screen(normal_user, personal.pk) is False
        assert can_view_screen(superuser, personal.pk) is True


def _make_consumer(layer, user, pk):
    consumer = ScreenDisplayNotify()
    consumer.channel_layer = layer
    consumer.channel_name = "specific.screen-channel"
    consumer.scope = {"user": user, "url_route": {"kwargs": {"pk": str(pk)}}}
    captured, closed = [], []

    async def fake_send_base_json(action, data=None, **kwargs):
        captured.append({"action": action, "data": data})

    async def fake_close(code=None):
        closed.append(code)

    async def fake_accept():
        captured.append({"action": "accepted"})

    consumer.send_base_json = fake_send_base_json
    consumer.close = fake_close
    consumer.accept = fake_accept
    return consumer, captured, closed


class TestScreenDisplayChannel:
    def test_connect_replays_state_and_forwards_command(self, superuser, dashboards):
        screen = _make_screen(superuser, dashboards)
        layer = get_channel_layer()
        consumer, captured, closed = _make_consumer(layer, superuser, screen.pk)

        async_to_sync(consumer.connect)()

        assert closed == []
        assert captured[0]["action"] == "accepted"
        replay = captured[1]
        assert replay["action"] == "screen_command"
        assert replay["data"]["command"] == "state"
        assert replay["data"]["mode"] == "auto"
        assert consumer.channel_name in layer.groups[screen_group_name(screen.pk)]

        frame = {"command": "switch", "mode": "manual", "index": 1, "refresh_rev": 0, "rev": 1, "ts": ""}
        async_to_sync(consumer.screen_command)({"type": "screen_command", "data": frame})
        assert captured[-1]["data"]["index"] == 1

    def test_anonymous_rejected(self, dashboards, superuser):
        screen = _make_screen(superuser, dashboards)
        consumer, _captured, closed = _make_consumer(get_channel_layer(), None, screen.pk)
        async_to_sync(consumer.connect)()
        assert closed == [4401]

    def test_invisible_screen_rejected(self, normal_user, superuser, dashboards):
        personal = _make_screen(superuser, dashboards, visibility="personal")
        consumer, _captured, closed = _make_consumer(get_channel_layer(), normal_user, personal.pk)
        async_to_sync(consumer.connect)()
        assert closed == [4403]
