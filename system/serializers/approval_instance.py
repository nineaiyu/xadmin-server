#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""审批流实例与节点任务序列化器（自 approval_flow.py 拆出，仅行数门禁）。

- ApprovalInstanceSerializer：实例只读展示 + 发起申请写入（flow/title/form_data）；
  列表附带 my_task（当前用户在当前节点的待办任务），供待办面板直接发起审批动作；
  详情附带 related_object（关联业务对象当前状态卡片，U-1）；
- ApprovalNodeTaskSerializer：节点任务（审批轨迹，含处理人显示名快照）。
"""

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.core.serializers import BaseModelSerializer
from system.models.approval import ApprovalFlow, ApprovalInstance, ApprovalNodeTask
from system.serializers.fields import DictChoiceField
from system.serializers.tag import TaggedObjectSerializerMixin
from system.serializers.task import DisplayRelatedField


def _username(value):
    return getattr(value, "username", str(value))


class ApprovalNodeTaskSerializer(BaseModelSerializer):
    assignee = DisplayRelatedField(read_only=True, allow_null=True, label=_("Assignee"), label_builder=_username)
    actor = DisplayRelatedField(read_only=True, allow_null=True, label=_("Actor"), label_builder=_username)
    # 委托代审来源：assignee 为代理人时非空（原审批人），详情页标注「由 X 代理」
    delegate_from = DisplayRelatedField(
        read_only=True, allow_null=True, label=_("Delegate from"), label_builder=_username
    )
    status = DictChoiceField(
        dict_code="approval_status",
        fallback_choices=ApprovalNodeTask.Status.choices,
        merge_fallback=True,
        read_only=True,
    )

    class Meta:
        model = ApprovalNodeTask
        fields = [
            "pk",
            "node_name",
            "node_order",
            "assignee",
            # U-1：处理人显示名快照（用户删除/改名后审批轨迹仍可读）
            "assignee_display",
            "delegate_from",
            "actor",
            "actor_display",
            "status",
            "comment",
            "acted_at",
            "is_added",
            "created_time",
        ]
        read_only_fields = fields


class ApprovalInstanceCommentSerializer(BaseModelSerializer):
    """审批讨论区评论（F-5）。"""

    creator = DisplayRelatedField(read_only=True, allow_null=True, label=_("Author"), label_builder=_username)

    class Meta:
        from system.models.approval import ApprovalInstanceComment

        model = ApprovalInstanceComment
        fields = ["pk", "creator", "author_display", "content", "mentions", "created_time"]
        read_only_fields = fields


class ApprovalInstanceSerializer(TaggedObjectSerializerMixin, BaseModelSerializer):
    flow = DisplayRelatedField(queryset=ApprovalFlow.objects.all(), label=_("Flow"), label_builder=lambda v: v.name)
    creator = DisplayRelatedField(read_only=True, allow_null=True, label=_("Applicant"), label_builder=_username)
    # P-1 通用标签：只读回显（打标走 /api/system/tags/assign）
    tags = serializers.SerializerMethodField(label=_("Tags"))
    status = DictChoiceField(
        dict_code="approval_status",
        fallback_choices=ApprovalInstance.Status.choices,
        merge_fallback=True,
        read_only=True,
    )
    current_node_name = serializers.SerializerMethodField(label=_("Current node"))
    current_assignees = serializers.SerializerMethodField(label=_("Current approvers"))
    my_task = serializers.SerializerMethodField(label=_("My task"))
    node_progress = serializers.SerializerMethodField(label=_("Node progress"))
    tasks = ApprovalNodeTaskSerializer(many=True, read_only=True)
    # 表单字段定义快照：详情页按 key 渲染 label（实例列表已 select_related flow，无额外查询）
    form_schema = serializers.SerializerMethodField(label=_("Form schema"))
    # U-1：关联业务对象当前状态（biz_type 白名单渲染；无关联返回 null，前端不渲染卡片）
    related_object = serializers.SerializerMethodField(label=_("Related object"))
    # F-5 抄送人（只读回显；发起时经 create_instance 的 cc_users 参数追加）
    cc_users = DisplayRelatedField(
        read_only=True, many=True, label=_("CC users"), label_builder=lambda v: v.nickname or v.username
    )
    # F-5 讨论区评论（仅详情返回；列表不返回避免 N+1）
    comments = serializers.SerializerMethodField(label=_("Comments"))

    class Meta:
        model = ApprovalInstance
        fields = [
            "pk",
            "flow",
            "flow_name",
            "title",
            "form_data",
            "status",
            "current_node_name",
            "current_assignees",
            "my_task",
            "node_progress",
            "tasks",
            "form_schema",
            "biz_type",
            "biz_id",
            "related_object",
            "cc_users",
            "comments",
            "reason",
            "tags",
            "creator",
            "finished_at",
            "created_time",
            "updated_time",
        ]
        table_fields = [
            "title",
            "flow_name",
            "status",
            "current_node_name",
            "current_assignees",
            "creator",
            "finished_at",
            "created_time",
        ]
        read_only_fields = [
            "pk",
            "flow_name",
            "status",
            "reason",
            "creator",
            "finished_at",
            "created_time",
            "updated_time",
            "biz_type",
            "biz_id",
            "related_object",
        ]

    def get_current_node_name(self, obj) -> str:
        return getattr(obj.current_node, "name", "") or ""

    def get_node_progress(self, obj):
        """当前节点进度（比例会签达标线预览）：复用列表 prefetch 的 tasks，避免 N+1。"""
        from system.utils.approval_flow import node_progress_for

        return node_progress_for(obj, tasks=obj.tasks.all())

    def get_current_assignees(self, obj) -> str:
        """当前节点的待办处理人（昵称，逗号分隔）：巡看「申请卡在谁那里」用。

        非 PENDING 实例返回空串；只取当前节点 PENDING 任务（加签者一并纳入）；
        优先用处理人显示名快照（U-1），无快照回落实时用户名。
        """
        if obj.status != ApprovalInstance.Status.PENDING:
            return ""
        return ", ".join(
            task.assignee_display or task.assignee.nickname or task.assignee.username
            for task in obj.tasks.all()
            if task.status == ApprovalNodeTask.Status.PENDING and task.assignee_id
        )

    def get_form_schema(self, obj) -> list:
        return list(getattr(obj.flow, "form_schema", None) or []) if obj.flow_id else []

    def get_related_object(self, obj):
        """关联业务对象当前状态（U-1 白名单渲染器）。"""
        from system.utils.approval_flow.biz import biz_summary

        return biz_summary(obj)

    def get_comments(self, obj) -> list:
        """F-5 讨论区评论：仅详情（retrieve）返回，列表零额外查询。"""
        action = getattr(self.context.get("view"), "action", "")
        if action != "retrieve":
            return []
        rows = obj.comments.select_related("creator").all()
        return ApprovalInstanceCommentSerializer(rows, many=True).data

    def get_my_task(self, obj):
        """当前用户在当前节点的待办任务（仅 PENDING 实例有意义；非待办返回 null）。"""
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or not getattr(user, "is_authenticated", False) or obj.status != ApprovalInstance.Status.PENDING:
            return None
        for task in obj.tasks.all():
            if task.assignee_id == user.pk and task.status == ApprovalNodeTask.Status.PENDING:
                return {"pk": str(task.pk), "node_name": task.node_name, "node_order": task.node_order}
        return None

    def validate(self, attrs):
        flow = attrs.get("flow")
        if flow is None:
            raise serializers.ValidationError({"flow": _("Flow is required")})
        if not flow.is_active:
            raise serializers.ValidationError({"flow": _("The flow is disabled")})
        if not (attrs.get("title") or "").strip():
            raise serializers.ValidationError({"title": _("Title is required")})
        return attrs


class ApprovalInstanceExportSerializer(BaseModelSerializer):
    """导出专用（轻量）：仅表格列，剔除 tasks / my_task / form_schema / node_progress 等重字段。

    导出复用 list 链路（支持筛选参数），沿用主序列化器会把每条实例的全部任务与表单快照
    写进文件（体积大且不可读）；此处与 ``Meta.table_fields`` 同口径。
    """

    creator = DisplayRelatedField(read_only=True, allow_null=True, label=_("Applicant"), label_builder=_username)
    status = DictChoiceField(
        dict_code="approval_status",
        fallback_choices=ApprovalInstance.Status.choices,
        merge_fallback=True,
        read_only=True,
    )
    current_node_name = serializers.SerializerMethodField(label=_("Current node"))
    current_assignees = serializers.SerializerMethodField(label=_("Current approvers"))

    class Meta:
        model = ApprovalInstance
        fields = [
            "pk",
            "title",
            "flow_name",
            "status",
            "current_node_name",
            "current_assignees",
            "creator",
            "reason",
            "finished_at",
            "created_time",
        ]

    def get_current_node_name(self, obj) -> str:
        return getattr(obj.current_node, "name", "") or ""

    def get_current_assignees(self, obj) -> str:
        if obj.status != ApprovalInstance.Status.PENDING:
            return ""
        return ", ".join(
            task.assignee_display or task.assignee.nickname or task.assignee.username
            for task in obj.tasks.all()
            if task.status == ApprovalNodeTask.Status.PENDING and task.assignee_id
        )
