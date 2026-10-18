"""通知公告入参校验回归。

覆盖三类入参缺口：
1. 系统通知（SYSTEM）不允许经管理接口直建——其正常生产路径是后端 SiteMessageUtil
   经 ORM 生成（接收人由代码指定），且创建后不可编辑；手工直建会产出接收对象
   不受控、又永久无法修正的记录。公告（NOTICE）仍走 announcement 端点发布，
   其余类型的创建行为不变；
2. publish / unread 布尔入参收敛为序列化器字段：缺 key / 错型返回 400，不再落到
   IntegrityError 500；合法值与「未传 unread 视为标记未读」的既有默认口径不变；
3. files 仅接受「文件路径字符串数组」（富文本编辑器收集的链接形态），
   畸形结构 400，合法结构照常关联附件。
"""

import pytest

from file.models import UploadFile
from notifications.models import MessageContent, MessageUserRead

pytestmark = pytest.mark.django_db

NOTICE_MSG_URL = "/api/notifications/notice-messages"
USER_READ_MSG_URL = "/api/notifications/user-read-messages"


def _notice_payload(notice_type, user, files=None, **extra):
    payload = {
        "title": "notice-x",
        "message": "m",
        "level": "info",
        "notice_type": notice_type,
        "notice_user": [str(user.pk)],
        "files": [] if files is None else files,
    }
    payload.update(extra)
    return payload


class TestSystemNoticeCreateForbidden:
    def test_system_type_rejected_on_create(self, auth_client, superuser):
        resp = auth_client.post(
            NOTICE_MSG_URL, _notice_payload(MessageContent.NoticeChoices.SYSTEM, superuser), format="json"
        )
        assert resp.status_code == 400
        assert not MessageContent.objects.filter(title="notice-x").exists()

    def test_user_type_create_still_works(self, auth_client, superuser):
        """合法类型（USER）创建不受影响，接收对象照常落库。"""
        resp = auth_client.post(
            NOTICE_MSG_URL, _notice_payload(MessageContent.NoticeChoices.USER, superuser), format="json"
        )
        assert resp.status_code == 200, resp.data
        notice = MessageContent.objects.get(pk=resp.data["data"]["pk"])
        assert notice.notice_type == MessageContent.NoticeChoices.USER
        assert notice.notice_user.filter(pk=superuser.pk).exists()

    def test_announcement_endpoint_still_publishes_notice_type(self, auth_client, superuser):
        """公告端点（前端 / AI 动作共用）不受影响：NOTICE 类型仍可发布，SYSTEM 仍被拒。"""
        resp = auth_client.post(
            f"{NOTICE_MSG_URL}/announcement",
            _notice_payload(
                MessageContent.NoticeChoices.NOTICE,
                superuser,
                notice_user=[],
                notice_dept=[],
                notice_role=[],
                publish=True,
            ),
            format="json",
        )
        assert resp.status_code == 200, resp.data
        assert MessageContent.objects.get(pk=resp.data["data"]["pk"]).notice_type == (
            MessageContent.NoticeChoices.NOTICE
        )


class TestPublishInput:
    def _notice(self):
        return MessageContent.objects.create(
            title="publish-x", message="m", notice_type=MessageContent.NoticeChoices.NOTICE, publish=True
        )

    def test_legal_boolean_toggles_state(self, auth_client):
        notice = self._notice()
        resp = auth_client.patch(f"{NOTICE_MSG_URL}/{notice.pk}/publish", {"publish": False}, format="json")
        assert resp.status_code == 200, resp.data
        notice.refresh_from_db()
        assert notice.publish is False

        resp = auth_client.patch(f"{NOTICE_MSG_URL}/{notice.pk}/publish", {"publish": True}, format="json")
        assert resp.status_code == 200
        notice.refresh_from_db()
        assert notice.publish is True

    def test_missing_key_rejected(self, auth_client):
        notice = self._notice()
        resp = auth_client.patch(f"{NOTICE_MSG_URL}/{notice.pk}/publish", {}, format="json")
        assert resp.status_code == 400
        notice.refresh_from_db()
        assert notice.publish is True

    def test_wrong_type_rejected(self, auth_client):
        notice = self._notice()
        resp = auth_client.patch(f"{NOTICE_MSG_URL}/{notice.pk}/publish", {"publish": "abc"}, format="json")
        assert resp.status_code == 400
        notice.refresh_from_db()
        assert notice.publish is True


class TestUserReadStateInput:
    @staticmethod
    def _read_row(user, notice_type):
        msg = MessageContent.objects.create(title="read-x", message="m", notice_type=notice_type)
        msg.notice_user.add(user)  # through 表即 MessageUserRead，默认 unread=True
        return MessageUserRead.objects.get(owner=user, notice=msg)

    def test_legal_boolean_marks_read(self, auth_client, superuser):
        row = self._read_row(superuser, MessageContent.NoticeChoices.USER)
        resp = auth_client.patch(f"{USER_READ_MSG_URL}/{row.pk}/state", {"unread": False}, format="json")
        assert resp.status_code == 200, resp.data
        row.refresh_from_db()
        assert row.unread is False

    def test_missing_key_defaults_to_unread(self, auth_client, superuser):
        """既有口径：未传 unread 视为标记未读。"""
        row = self._read_row(superuser, MessageContent.NoticeChoices.USER)
        row.unread = False
        row.save(update_fields=["unread"])

        resp = auth_client.patch(f"{USER_READ_MSG_URL}/{row.pk}/state", {}, format="json")
        assert resp.status_code == 200, resp.data
        row.refresh_from_db()
        assert row.unread is True

    def test_wrong_type_rejected(self, auth_client, superuser):
        row = self._read_row(superuser, MessageContent.NoticeChoices.USER)
        resp = auth_client.patch(f"{USER_READ_MSG_URL}/{row.pk}/state", {"unread": "abc"}, format="json")
        assert resp.status_code == 400
        row.refresh_from_db()
        assert row.unread is True

    def test_announcement_read_row_still_deleted(self, auth_client, superuser):
        """公告类已读记录经 state 仍删除，不受入参收敛影响。"""
        row = self._read_row(superuser, MessageContent.NoticeChoices.NOTICE)
        resp = auth_client.patch(f"{USER_READ_MSG_URL}/{row.pk}/state", {"unread": False}, format="json")
        assert resp.status_code == 200, resp.data
        assert not MessageUserRead.objects.filter(pk=row.pk).exists()


class TestFilesShapeValidation:
    def test_legal_path_list_links_attachment(self, auth_client, superuser):
        upload = UploadFile.objects.create(
            filepath="upload/report.pdf", filename="report.pdf", filesize=1, mime_type="application/pdf", md5sum="m"
        )
        resp = auth_client.post(
            NOTICE_MSG_URL,
            _notice_payload(MessageContent.NoticeChoices.USER, superuser, files=[f"/media/{upload.filepath}"]),
            format="json",
        )
        assert resp.status_code == 200, resp.data
        notice = MessageContent.objects.get(pk=resp.data["data"]["pk"])
        assert notice.file.filter(pk=upload.pk).exists()

    def test_empty_list_allowed(self, auth_client, superuser):
        resp = auth_client.post(
            NOTICE_MSG_URL,
            _notice_payload(MessageContent.NoticeChoices.USER, superuser, files=[]),
            format="json",
        )
        assert resp.status_code == 200, resp.data
        notice = MessageContent.objects.get(pk=resp.data["data"]["pk"])
        assert notice.file.count() == 0

    @pytest.mark.parametrize(
        "files",
        [
            "/media/upload/report.pdf",  # 字符串而非数组：旧实现被逐字符拆分
            {"url": "/media/upload/report.pdf"},  # 对象而非数组：旧实现按 key 迭代
            [1, 2],  # 元素非字符串
            [{"name": "report.pdf", "url": "/media/upload/report.pdf"}],  # 元素为对象
            [None],
        ],
    )
    def test_malformed_shapes_rejected(self, auth_client, superuser, files):
        resp = auth_client.post(
            NOTICE_MSG_URL, _notice_payload(MessageContent.NoticeChoices.USER, superuser, files=files), format="json"
        )
        assert resp.status_code == 400
        assert not MessageContent.objects.filter(title="notice-x").exists()


class TestMessageFormat:
    """正文格式分流：html 净化、markdown 保留源码、创建后不可切换。"""

    MARKDOWN_BODY = (
        "## 维护窗口\n\n"
        "- 时间：今晚 23:00\n"
        "- 预计 30 分钟\n\n"
        "| 指标 | 值 |\n| --- | --- |\n| 时长 | 30m |\n\n"
        "```bash\nsystemctl restart xadmin\n```"
    )

    def _create(self, auth_client, superuser, message_format, message):
        payload = _notice_payload(MessageContent.NoticeChoices.USER, superuser, message=message)
        payload["message_format"] = message_format
        return auth_client.post(NOTICE_MSG_URL, payload, format="json")

    def test_markdown_source_preserved(self, auth_client, superuser):
        resp = self._create(auth_client, superuser, MessageContent.MessageFormat.MARKDOWN, self.MARKDOWN_BODY)
        assert resp.status_code == 200, resp.data
        notice = MessageContent.objects.get(pk=resp.data["data"]["pk"])
        assert notice.message_format == MessageContent.MessageFormat.MARKDOWN
        # 源码中的 # / ** / 表格 / 围栏代码块必须原样保留（不跑 bleach）
        assert notice.message == self.MARKDOWN_BODY

    def test_html_is_sanitized_by_default(self, auth_client, superuser):
        """未传格式按 html 处理，仍走白名单净化（存量行为不变）。"""
        resp = auth_client.post(
            NOTICE_MSG_URL,
            _notice_payload(
                MessageContent.NoticeChoices.USER,
                superuser,
                message='<p onclick="x()">a<script>b</script></p>',
            ),
            format="json",
        )
        assert resp.status_code == 200, resp.data
        notice = MessageContent.objects.get(pk=resp.data["data"]["pk"])
        assert notice.message_format == MessageContent.MessageFormat.HTML
        assert "onclick" not in notice.message
        assert "<script" not in notice.message

    def test_invalid_format_rejected(self, auth_client, superuser):
        resp = self._create(auth_client, superuser, "bogus", "m")
        assert resp.status_code == 400
        assert not MessageContent.objects.filter(title="notice-x").exists()

    def test_update_cannot_change_format(self, auth_client, superuser):
        created = self._create(auth_client, superuser, MessageContent.MessageFormat.MARKDOWN, self.MARKDOWN_BODY)
        pk = created.data["data"]["pk"]
        resp = auth_client.patch(f"{NOTICE_MSG_URL}/{pk}", {"message_format": "html"}, format="json")
        assert resp.status_code == 400
        assert "message_format" in resp.data
        notice = MessageContent.objects.get(pk=pk)
        assert notice.message_format == MessageContent.MessageFormat.MARKDOWN

    def test_update_message_uses_instance_format(self, auth_client, superuser):
        """PATCH 未传格式：按实例当前格式校验，Markdown 正文不被 HTML 净化改写。"""
        created = self._create(auth_client, superuser, MessageContent.MessageFormat.MARKDOWN, self.MARKDOWN_BODY)
        pk = created.data["data"]["pk"]
        resp = auth_client.patch(f"{NOTICE_MSG_URL}/{pk}", {"message": "<b>x</b>"}, format="json")
        assert resp.status_code == 200, resp.data
        notice = MessageContent.objects.get(pk=pk)
        assert notice.message_format == MessageContent.MessageFormat.MARKDOWN
        assert notice.message == "<b>x</b>"

    def test_markdown_too_long_rejected(self, auth_client, superuser):
        from notifications.serializers.message import MAX_MARKDOWN_LENGTH

        resp = self._create(
            auth_client, superuser, MessageContent.MessageFormat.MARKDOWN, "a" * (MAX_MARKDOWN_LENGTH + 1)
        )
        assert resp.status_code == 400
        assert not MessageContent.objects.filter(title="notice-x").exists()

    def test_announcement_persists_markdown_format(self, auth_client, superuser):
        payload = _notice_payload(
            MessageContent.NoticeChoices.NOTICE,
            superuser,
            message=self.MARKDOWN_BODY,
            message_format=MessageContent.MessageFormat.MARKDOWN,
            notice_user=[],
            notice_dept=[],
            notice_role=[],
            publish=True,
        )
        resp = auth_client.post(f"{NOTICE_MSG_URL}/announcement", payload, format="json")
        assert resp.status_code == 200, resp.data
        notice = MessageContent.objects.get(pk=resp.data["data"]["pk"])
        assert notice.notice_type == MessageContent.NoticeChoices.NOTICE
        assert notice.message_format == MessageContent.MessageFormat.MARKDOWN
        assert notice.message == self.MARKDOWN_BODY
