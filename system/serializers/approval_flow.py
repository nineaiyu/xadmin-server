#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""全量审批流引擎一期序列化器（ADR-012）。

- ApprovalFlowSerializer：流程定义 + 节点列表嵌套写入（nodes 整体替换式更新）；
  有 PENDING 实例的流程禁止改动节点（避免在途实例指向被删节点）。
- ApprovalInstanceSerializer：实例只读展示 + 发起申请写入（flow/title/form_data）；
  列表附带 my_task（当前用户在当前节点的待办任务），供待办面板直接发起审批动作。
- ApprovalNodeTaskSerializer：节点任务（审批轨迹）。
"""

from django.db import transaction
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.core.serializers import BaseModelSerializer
from system.models.approval import ApprovalFlow, ApprovalFlowNode, ApprovalInstance, ApprovalNodeTask
from system.serializers.fields import DictChoiceField
from system.serializers.task import DisplayRelatedField
from system.utils.approval_flow import CONDITION_OPS

FORM_FIELD_TYPES = ("text", "textarea", "number", "date", "select")


def _username(value):
    return getattr(value, "username", str(value))


class ApprovalFlowNodeSerializer(BaseModelSerializer):
    class Meta:
        model = ApprovalFlowNode
        fields = [
            "pk",
            "name",
            "order",
            "approve_type",
            "assignee_type",
            "assignee_value",
            "condition",
            "timeout_hours",
        ]
        extra_kwargs = {"order": {"required": False}}


class ApprovalFlowSerializer(BaseModelSerializer):
    nodes = ApprovalFlowNodeSerializer(many=True, required=False)
    creator = DisplayRelatedField(read_only=True, allow_null=True, label=_("Creator"), label_builder=_username)
    node_count = serializers.SerializerMethodField(label=_("Node count"))

    class Meta:
        model = ApprovalFlow
        fields = [
            "pk",
            "name",
            "code",
            "form_schema",
            "is_active",
            "nodes",
            "node_count",
            "creator",
            "created_time",
            "updated_time",
        ]
        table_fields = ["name", "code", "node_count", "is_active", "creator", "created_time"]

    def get_node_count(self, obj) -> int:
        annotated = getattr(obj, "nodes_count", None)
        return annotated if annotated is not None else obj.nodes.count()

    def validate_form_schema(self, value):
        """表单字段定义校验：key/label/type 必填，type 白名单，select 需 options。"""
        if value in (None, ""):
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError(_("Form schema must be a list"))
        seen = set()
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                raise serializers.ValidationError(_("Form schema item must be an object"))
            key = (item.get("key") or "").strip()
            if not key:
                raise serializers.ValidationError(_("Form field key is required (item {})").format(index + 1))
            if key in seen:
                raise serializers.ValidationError(_("Form field key {} is duplicated").format(key))
            seen.add(key)
            field_type = item.get("type") or "text"
            if field_type not in FORM_FIELD_TYPES:
                raise serializers.ValidationError(_("Unsupported form field type: {}").format(field_type))
            if field_type == "select" and not isinstance(item.get("options"), list):
                raise serializers.ValidationError(_("Select field {} requires options").format(key))
        return value

    def validate_nodes(self, value):
        """节点校验：至少 1 个；order 缺省按顺序补齐且不可重复；审批人配置与条件表达式合法。"""
        if value is None:
            return value
        if not value:
            raise serializers.ValidationError(_("The flow requires at least one node"))
        orders = []
        for index, node in enumerate(value):
            order = node.get("order")
            node["order"] = order if order not in (None, "") else index + 1
            if int(node["order"]) < 1:
                raise serializers.ValidationError(_("Node order must be greater than 0"))
            orders.append(int(node["order"]))
        if len(set(orders)) != len(orders):
            raise serializers.ValidationError(_("Node order is duplicated"))
        for node in value:
            if not (node.get("name") or "").strip():
                raise serializers.ValidationError(_("Node name is required"))
            if (
                node.get("assignee_type") != ApprovalFlowNode.AssigneeType.LEADER
                and not (node.get("assignee_value") or "").strip()
            ):
                raise serializers.ValidationError(_("Assignee value is required for node {}").format(node["name"]))
            hours = int(node.get("timeout_hours") or 0)
            if hours < 0:
                raise serializers.ValidationError(_("Timeout hours cannot be negative"))
            condition = node.get("condition") or {}
            if condition:
                if not isinstance(condition, dict) or not (condition.get("field") or "").strip():
                    raise serializers.ValidationError(_("Condition requires a field"))
                if (condition.get("op") or "eq") not in CONDITION_OPS:
                    raise serializers.ValidationError(
                        _("Unsupported condition operator: {}").format(condition.get("op"))
                    )
        return value

    def validate_code(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError(_("Flow code is required"))
        return value

    def _assert_nodes_mutable(self, instance):
        if instance.instances.filter(status=ApprovalInstance.Status.PENDING).exists():
            raise serializers.ValidationError(_("The flow has pending applications, nodes cannot be changed"))

    @transaction.atomic
    def create(self, validated_data):
        nodes = validated_data.pop("nodes", [])
        flow = super().create(validated_data)
        self._replace_nodes(flow, nodes)
        return flow

    @transaction.atomic
    def update(self, instance, validated_data):
        nodes = validated_data.pop("nodes", None)
        flow = super().update(instance, validated_data)
        if nodes is not None:
            self._assert_nodes_mutable(flow)
            flow.nodes.all().delete()
            self._replace_nodes(flow, nodes)
        return flow

    def _replace_nodes(self, flow, nodes):
        for index, node in enumerate(nodes or []):
            ApprovalFlowNode.objects.create(
                flow=flow,
                name=(node.get("name") or "").strip()[:64],
                order=int(node.get("order") or index + 1),
                approve_type=node.get("approve_type") or ApprovalFlowNode.ApproveType.OR,
                assignee_type=node.get("assignee_type") or ApprovalFlowNode.AssigneeType.ROLE,
                assignee_value=(node.get("assignee_value") or "").strip()[:255],
                condition=node.get("condition") or {},
                timeout_hours=int(node.get("timeout_hours") or 0),
            )


class ApprovalNodeTaskSerializer(BaseModelSerializer):
    assignee = DisplayRelatedField(read_only=True, allow_null=True, label=_("Assignee"), label_builder=_username)
    actor = DisplayRelatedField(read_only=True, allow_null=True, label=_("Actor"), label_builder=_username)
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
            "actor",
            "status",
            "comment",
            "acted_at",
            "is_added",
            "created_time",
        ]
        read_only_fields = fields


class ApprovalInstanceSerializer(BaseModelSerializer):
    flow = DisplayRelatedField(queryset=ApprovalFlow.objects.all(), label=_("Flow"), label_builder=lambda v: v.name)
    creator = DisplayRelatedField(read_only=True, allow_null=True, label=_("Applicant"), label_builder=_username)
    status = DictChoiceField(
        dict_code="approval_status",
        fallback_choices=ApprovalInstance.Status.choices,
        merge_fallback=True,
        read_only=True,
    )
    current_node_name = serializers.SerializerMethodField(label=_("Current node"))
    my_task = serializers.SerializerMethodField(label=_("My task"))
    tasks = ApprovalNodeTaskSerializer(many=True, read_only=True)
    # 表单字段定义快照：详情页按 key 渲染 label（实例列表已 select_related flow，无额外查询）
    form_schema = serializers.SerializerMethodField(label=_("Form schema"))

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
            "my_task",
            "tasks",
            "form_schema",
            "reason",
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
        ]

    def get_current_node_name(self, obj) -> str:
        return getattr(obj.current_node, "name", "") or ""

    def get_form_schema(self, obj) -> list:
        return list(getattr(obj.flow, "form_schema", None) or []) if obj.flow_id else []

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
