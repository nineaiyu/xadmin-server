# -*- coding: utf-8 -*-
"""Markdown 公告服务端中和 HTML：存储侧剥离标签，任何渲染器都不执行作者 HTML。"""

import pytest

from common.utils.sanitize import sanitize_markdown
from notifications.models import MessageContent

pytestmark = pytest.mark.django_db

NOTICE_MSG_URL = "/api/notifications/notice-messages"


class TestSanitizeMarkdown:
    def test_script_and_event_handlers_removed(self):
        content = '# 标题\n\n**加粗** 与 `代码`\n\n<script>alert(1)</script>\n<img src="x" onerror="alert(1)">\n'
        cleaned = sanitize_markdown(content)
        assert "# 标题" in cleaned
        assert "**加粗**" in cleaned and "`代码`" in cleaned
        assert "<script" not in cleaned and "<img" not in cleaned
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in cleaned

    def test_fenced_code_html_is_escaped_not_executed(self):
        cleaned = sanitize_markdown('```html\n<div onclick="x">hi</div>\n```')
        assert "<div" not in cleaned
        assert "&lt;div" in cleaned and "hi" in cleaned


class TestNoticeMarkdownStorage:
    def test_markdown_notice_stored_without_tags(self, auth_client, superuser):
        resp = auth_client.post(
            NOTICE_MSG_URL,
            {
                "title": "markdown-x",
                "message": "# 公告\n\n<script>alert(1)</script>正文",
                "message_format": MessageContent.MessageFormat.MARKDOWN,
                "level": "info",
                "notice_type": MessageContent.NoticeChoices.USER,
                "notice_user": [str(superuser.pk)],
                "files": [],
            },
            format="json",
        )
        assert resp.status_code == 200, resp.data
        stored = MessageContent.objects.get(title="markdown-x").message
        assert stored.startswith("# 公告")
        assert "正文" in stored
        assert "<script" not in stored
