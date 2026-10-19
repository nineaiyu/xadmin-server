# -*- coding: utf-8 -*-
"""存量富文本公告转 Markdown 命令：默认 dry-run，--apply 才写库。"""

from io import StringIO

import pytest
from django.core.management import call_command

from notifications.models import MessageContent

pytestmark = pytest.mark.django_db


def _notice(title: str, message: str, message_format: str = MessageContent.MessageFormat.HTML) -> MessageContent:
    return MessageContent.objects.create(
        title=title,
        message=message,
        message_format=message_format,
        notice_type=MessageContent.NoticeChoices.NOTICE,
    )


def _run(*args: str) -> str:
    stdout = StringIO()
    call_command("convert_notice_markdown", *args, stdout=stdout)
    return stdout.getvalue()


class TestConvertNoticeMarkdown:
    def test_dry_run_reports_without_writing(self):
        notice = _notice("html-x", "<h2>标题</h2><ul><li>第一项</li></ul>")
        output = _run("--dry-run")

        assert "would convert: 1" in output
        notice.refresh_from_db()
        assert notice.message_format == MessageContent.MessageFormat.HTML
        assert notice.message == "<h2>标题</h2><ul><li>第一项</li></ul>"

    def test_apply_converts_html_notice(self):
        notice = _notice("html-y", "<h2>标题</h2><ul><li>第一项</li></ul>")
        output = _run("--apply")

        assert "converted: 1" in output
        notice.refresh_from_db()
        assert notice.message_format == MessageContent.MessageFormat.MARKDOWN
        assert "## 标题" in notice.message
        assert "* 第一项" in notice.message or "- 第一项" in notice.message

    def test_markdown_notice_is_not_touched(self):
        notice = _notice("md-z", "## 已是源码", message_format=MessageContent.MessageFormat.MARKDOWN)
        output = _run("--apply")

        assert "converted: 0" in output
        notice.refresh_from_db()
        assert notice.message == "## 已是源码"

    def test_ids_filter_limits_scope(self):
        first = _notice("html-a", "<p>a</p>")
        second = _notice("html-b", "<p>b</p>")
        _run("--apply", "--ids", str(first.pk))

        first.refresh_from_db()
        second.refresh_from_db()
        assert first.message_format == MessageContent.MessageFormat.MARKDOWN
        assert second.message_format == MessageContent.MessageFormat.HTML
