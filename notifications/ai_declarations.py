#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""通知域的 AI 动作声明（系统公告 / 我的站内信）。

公告发布复用 ``AnnouncementSerializer`` 的校验、净化与推送信号；站内信只暴露
「我的未读」与「全部已读」，不跨用户操作。
"""

from django.utils.translation import gettext_lazy as _

from ai.utils.ai_api_actions import IN_QUERY, api_action

AI_ACTIONS = {
    "notice.publish": api_action(
        key="notice.publish",
        label=_("Publish a system announcement"),
        description=_("Publish a system-wide announcement visible to all users"),
        # 复用公告管理页的 announcement 端点（序列化校验 + 净化 + 发布推送信号），
        # 权限点 announcement:SystemNotice
        method="POST",
        path="/api/notifications/notice-messages/announcement",
        params={
            "title": {"type": "string", "required": True, "in": "body", "description": "Announcement title"},
            "message": {
                "type": "string",
                "required": True,
                "in": "body",
                "description": "Announcement body in Markdown (headings, lists, tables and fenced code supported)",
            },
            "level": {
                "type": "enum",
                "values": ["info", "primary", "success", "danger"],
                "default": "info",
                "in": "body",
                "description": "Notice level, default info",
            },
        },
        # 公告端点的隐含必填字段（业务接口既有契约，原样满足）：全员公告类型 +
        # 立即可见 + 无指定接收人/附件；模型输出天然是 Markdown，故固定按
        # markdown 格式落库（展示端按格式渲染，避免把 **/# 原样显示）
        defaults={
            "notice_type": 1,
            "publish": True,
            "message_format": "markdown",
            "notice_user": [],
            "notice_dept": [],
            "notice_role": [],
            "files": [],
        },
    ),
    "notice.list": api_action(
        key="notice.list",
        label=_("List system announcements"),
        description=_("List published system announcements, optionally filtered by title keyword"),
        method="GET",
        path="/api/notifications/notice-messages",
        params={
            "title": {"type": "string", "required": False, "in": IN_QUERY, "description": "Title keyword (fuzzy)"},
            # 服务端固定：只看「系统公告」类型（notice_type=1），不接受模型指定
            "notice_type": {"const": 1, "in": IN_QUERY},
        },
    ),
    "message.unread_list": api_action(
        key="message.unread_list",
        label=_("List my unread messages"),
        description=_("Current user's unread notifications and announcements (with totals)"),
        method="GET",
        path="/api/notifications/site-messages/unread",
        params={},
    ),
    "message.mark_all_read": api_action(
        key="message.mark_all_read",
        label=_("Mark all my messages as read"),
        description=_("Mark every unread message of the current user as read"),
        method="PATCH",
        path="/api/notifications/site-messages/all-read",
        params={},
    ),
    "notice.detail": api_action(
        key="notice.detail",
        label=_("Show an announcement"),
        description=_("One announcement with its full body and publishing metadata"),
        method="GET",
        path="/api/notifications/notice-messages/<pk>",
        params={"pk": {"type": "pk", "required": True, "in": "path", "description": "Notice id (from notice.list)"}},
    ),
    "message.list": api_action(
        key="message.list",
        label=_("List my messages"),
        description=_("Messages of the current user with read state and source channel"),
        method="GET",
        path="/api/notifications/site-messages",
        params={},
    ),
    "message_template.list": api_action(
        key="message_template.list",
        label=_("List notification templates"),
        description=_("Notification templates per channel and event (name, channel, updated time)"),
        method="GET",
        path="/api/notifications/message-templates",
        params={},
    ),
}
