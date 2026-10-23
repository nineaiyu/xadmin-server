"""通知接收列表的类型契约回归。

前端按 `notice_info.notice_type.value` 区分公告族（state 即删除）与用户族
（可回写已读/未读）。该字段由嵌套序列化器的 `LabeledChoiceField` 输出为
`{value, label}`，一旦退化成展示文案，前端判定会静默失效并把「移除」当成
「标为未读」渲染，故在此锁定契约形状。
"""

import pytest

from notifications.models import MessageContent, MessageUserRead

pytestmark = pytest.mark.django_db

USER_READ_MSG_URL = "/api/notifications/user-read-messages"


def test_read_row_exposes_notice_type_code(auth_client, superuser):
    notice = MessageContent.objects.create(
        title="read-contract",
        message="m",
        notice_type=MessageContent.NoticeChoices.NOTICE,
        publish=True,
    )
    MessageUserRead.objects.create(owner=superuser, notice=notice, unread=False)

    resp = auth_client.get(USER_READ_MSG_URL, {"size": 20})

    assert resp.status_code == 200, resp.data
    rows = resp.data["data"]["results"]
    row = next(item for item in rows if int(item["notice_info"]["pk"]) == notice.pk)
    assert row["notice_info"]["notice_type"]["value"] == MessageContent.NoticeChoices.NOTICE
    assert row["notice_info"]["notice_type"]["label"]


def test_all_read_only_marks_private_messages(auth_client, superuser):
    """全部已读只回写私信，不删除公告族接收记录（公告由「移除」单独处置）"""
    private = MessageContent.objects.create(
        title="all-read-private",
        message="m",
        notice_type=MessageContent.NoticeChoices.USER,
        publish=True,
    )
    private.notice_user.add(superuser)
    announcement = MessageContent.objects.create(
        title="all-read-announcement",
        message="m",
        notice_type=MessageContent.NoticeChoices.NOTICE,
        publish=True,
    )
    MessageUserRead.objects.create(owner=superuser, notice=announcement, unread=False)

    resp = auth_client.patch(f"{USER_READ_MSG_URL}/all-read", {}, format="json")

    assert resp.status_code == 200, resp.data
    assert resp.data["data"]["updated"] == 1
    assert MessageUserRead.objects.get(owner=superuser, notice=private).unread is False
    assert MessageUserRead.objects.filter(owner=superuser, notice=announcement).exists()
