# -*- coding: utf-8 -*-
"""会话列表批量预取守护：查询数不随房间数增长（对端 / 群成员预览 / 成员数）。

历史实现逐房间查询：每私聊 1 次（对端）、每群 2 次（成员预览 + 计数），
房间数即查询数；现统一为「整页 3 次批量查询」。
"""

import pytest

from message import chat as chat_service
from message.models import ChatRoom

pytestmark = pytest.mark.django_db

ROOM_URL = "/api/chat/room"


def _business_queries(ctx):
    return [q for q in ctx.captured_queries if "SAVEPOINT" not in q["sql"]]


@pytest.fixture
def peers(db):
    from identity.models import UserInfo

    return [
        UserInfo.objects.create_user(username=f"peer_{index}", password="Test@123456", nickname=f"对端{index}")
        for index in range(6)
    ]


class TestRoomListQueryBudget:
    def test_query_count_independent_of_room_count(self, auth_client, superuser, peers):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        # 首页只建 1 个私聊
        chat_service.get_or_create_private_room(superuser, peers[0])
        auth_client.get(ROOM_URL)  # 预热（权限/配置缓存）
        with CaptureQueriesContext(connection) as small:
            assert auth_client.get(ROOM_URL).status_code == 200
        small_count = len(_business_queries(small))

        # 追加 5 个私聊 + 2 个群（各 3 名成员）：整页统一 3 次批量查询
        for peer in peers[1:]:
            chat_service.get_or_create_private_room(superuser, peer)
        for index in range(2):
            chat_service.create_group(
                superuser, f"群-{index}", [peers[index].pk, peers[index + 1].pk, peers[index + 2].pk]
            )
        with CaptureQueriesContext(connection) as large:
            resp = auth_client.get(ROOM_URL)
        assert resp.status_code == 200
        large_count = len(_business_queries(large))

        assert small_count > 0
        assert large_count <= small_count + 4, f"small={small_count} large={large_count}"

    def test_group_preview_and_count_shared_in_batch(self, auth_client, superuser, peers):
        """批量预取结果正确：群成员预览与成员数、私聊对端一次性带出。"""
        room = chat_service.get_or_create_private_room(superuser, peers[0])
        chat_service.create_message(room, peers[0], "hi")  # 私聊只有产生消息后才入列表
        chat_service.create_group(superuser, "批量群", [peers[0].pk, peers[1].pk])

        rooms = auth_client.get(ROOM_URL).json()["data"]["rooms"]
        private = next(item for item in rooms if item["room_type"] == ChatRoom.RoomType.PRIVATE)
        group = next(item for item in rooms if item["room_type"] == ChatRoom.RoomType.GROUP)

        assert private["peer"]["username"] == "peer_0"
        assert "online" in private["peer"]
        # 群成员 = 群主 + 2 名成员
        assert group["member_count"] == 3
        assert len(group["members"]) == 3
        assert {item["username"] for item in group["members"]} == {"admin", "peer_0", "peer_1"}
