#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""审批实例讨论区（评论 CRUD + @ 提醒，自 approval_instance_actions.py 平移）。

对外的既有端点面不变：组合 ApprovalInstanceActionMixin 的 ViewSet 经 MRO 继承本 mixin。
"""

import re
from typing import TYPE_CHECKING, Any

from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied

from approval.models.approval import ApprovalInstanceComment
from approval.serializers.approval_instance import ApprovalInstanceCommentSerializer
from approval.utils.approval.display import user_display
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema

# 评论 @ 提及：与聊天室提及同口径（用户名，允许 . - _）
MENTION_PATTERN = re.compile(r"@([\w.\-]+)")
COMMENT_MAX_LENGTH = 2000


class ApprovalInstanceCommentMixin:
    """讨论区端点（self 由组合它的 ViewSet 提供：get_object 等）。"""

    if TYPE_CHECKING:  # 宿主 ViewSet 提供的接口（mixin 模式）

        def get_object(self, *args, **kwargs) -> Any: ...

    def _resolve_comment_mentions(self, content, exclude_user):
        """解析评论中的 @用户名 → 启用用户列表（排除自己，去重保序）。"""
        from identity.models import UserInfo

        names = []
        for name in MENTION_PATTERN.findall(content or ""):
            if name and name not in names:
                names.append(name)
        if not names:
            return []
        users = []
        for user in UserInfo.objects.filter(username__in=names[:20], is_active=True):
            if user.pk != exclude_user.pk:
                users.append(user)
        return users

    def _notify_comment_mentions(self, instance, users, content):
        """评论 @ 提醒（默认只提醒被提及者；失败只记日志）。"""
        if not users:
            return
        from system.notifications import ApprovalFlowMessage

        for user in users:
            try:
                ApprovalFlowMessage(user, "mentioned", instance, extra=(content or "")[:120]).publish(is_async=True)
            except Exception:  # noqa: BLE001 通知失败不影响评论
                pass

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=True, url_path="comments")
    def comments(self, request, *args, **kwargs):
        """讨论区评论列表"""
        instance = self.get_object()
        rows = instance.comments.select_related("creator").all()
        return ApiResponse(data=ApprovalInstanceCommentSerializer(rows, many=True).data)

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={"content": build_basic_type(OpenApiTypes.STR)},
                required=["content"],
                description="评论内容（支持 @用户名 提醒）",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=True, url_path="comment")
    def add_comment(self, request, *args, **kwargs):
        """发表评论（参与人 = 可见域内用户；@ 提及者收提醒）"""
        instance = self.get_object()
        content = str(request.data.get("content") or "").strip()
        if not content:
            return ApiResponse(code=1004, detail=_("Comment content is required"))
        if len(content) > COMMENT_MAX_LENGTH:
            return ApiResponse(
                code=1004, detail=_("Comment is too long (max {} characters)").format(COMMENT_MAX_LENGTH)
            )
        mentioned = self._resolve_comment_mentions(content, request.user)
        comment = ApprovalInstanceComment.objects.create(
            instance=instance,
            content=content,
            creator=request.user,
            author_display=user_display(request.user),
            mentions=[str(user.pk) for user in mentioned],
        )
        self._notify_comment_mentions(instance, mentioned, content)
        return ApiResponse(data=ApprovalInstanceCommentSerializer(comment).data, detail=_("Comment published"))

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={"pk": build_basic_type(OpenApiTypes.STR)},
                required=["pk"],
                description="评论主键",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=True, url_path="comment/delete")
    def delete_comment(self, request, *args, **kwargs):
        """删除评论（作者本人或超管）"""
        instance = self.get_object()
        comment = instance.comments.filter(pk=request.data.get("pk")).first()
        if comment is None:
            return ApiResponse(code=1004, detail=_("Comment does not exist"))
        if comment.creator_id != request.user.pk and not request.user.is_superuser:
            raise PermissionDenied(_("You can only delete your own comments"))
        comment.delete()
        return ApiResponse(detail=_("Deleted successfully"))
