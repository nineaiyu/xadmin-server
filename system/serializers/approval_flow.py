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
from system.models.approval import (
    ApprovalFlow,
    ApprovalFlowNode,
    ApprovalFlowVersion,
    ApprovalInstance,
    ApprovalNodeTask,
)
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
            "approve_ratio",
            "assignee_type",
            "assignee_value",
            "condition",
            "routes",
            "layout",
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
                self._validate_condition(condition)
            approve_type = node.get("approve_type") or ApprovalFlowNode.ApproveType.OR
            if approve_type == ApprovalFlowNode.ApproveType.RATIO:
                ratio = int(node.get("approve_ratio") or 0)
                if not 1 <= ratio <= 100:
                    raise serializers.ValidationError(
                        _("Approve ratio must be between 1 and 100 for node {}").format(node["name"])
                    )
            routes = node.get("routes") or []
            if not isinstance(routes, list):
                raise serializers.ValidationError(_("Branch routes must be a list"))
            for route in routes:
                if not isinstance(route, dict):
                    raise serializers.ValidationError(_("Branch route item must be an object"))
                condition = route.get("condition") or {}
                if condition:
                    self._validate_condition(condition)
        return value

    def _validate_condition(self, condition):
        """条件表达式校验：field 必填、op 白名单（routes 与节点条件共用）。"""
        if not isinstance(condition, dict) or not (condition.get("field") or "").strip():
            raise serializers.ValidationError(_("Condition requires a field"))
        if (condition.get("op") or "eq") not in CONDITION_OPS:
            raise serializers.ValidationError(_("Unsupported condition operator: {}").format(condition.get("op")))

    def validate_code(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError(_("Flow code is required"))
        return value

    def validate(self, attrs):
        nodes = attrs.get("nodes") or []
        if nodes:
            self._validate_routes_graph(nodes)
        return attrs

    def _validate_routes_graph(self, nodes):
        """跨节点路由校验：target 必须是同流程有效 order 且非自环；显式回跳环 DFS 拦截
        （线性隐式边按 order 递增天然无环，仅需检测 routes 边）。"""
        orders = {int(node["order"]) for node in nodes}
        edges = {}
        for node in nodes:
            order = int(node["order"])
            targets = []
            for route in node.get("routes") or []:
                target = route.get("target")
                if target is None:
                    raise serializers.ValidationError(
                        _("Branch route target is required for node {}").format(node["name"])
                    )
                target = int(target)
                if target not in orders:
                    raise serializers.ValidationError(_("Branch route target {} does not exist").format(target))
                if target == order:
                    raise serializers.ValidationError(
                        _("Branch route cannot point to itself (node {})").format(node["name"])
                    )
                targets.append(target)
            if targets:
                edges[order] = targets

        # DFS 环检测（colors: 0 未访问 1 在栈 2 完成）
        color = {order: 0 for order in orders}

        def _visit(order):
            if color.get(order, 0) == 1:
                raise serializers.ValidationError(_("Branch routes contain a loop"))
            if color.get(order, 0) == 2:
                return
            color[order] = 1
            for target in edges.get(order, []):
                _visit(target)
            color[order] = 2

        for order in edges:
            _visit(order)

    def _assert_nodes_mutable(self, instance):
        if instance.instances.filter(status=ApprovalInstance.Status.PENDING).exists():
            raise serializers.ValidationError(_("The flow has pending applications, nodes cannot be changed"))

    @transaction.atomic
    def create(self, validated_data):
        nodes = validated_data.pop("nodes", [])
        flow = super().create(validated_data)
        self._replace_nodes(flow, nodes)
        self._snapshot_version(flow, nodes, remark=_("Initial version"))
        return flow

    @transaction.atomic
    def update(self, instance, validated_data):
        nodes = validated_data.pop("nodes", None)
        flow = super().update(instance, validated_data)
        if nodes is not None:
            self._assert_nodes_mutable(flow)
            flow.nodes.all().delete()
            self._replace_nodes(flow, nodes)
        # 定义（节点或表单）有实质变化才落版本快照（改 name 等元数据不算）
        if nodes is not None and self._definition_changed(flow, nodes):
            self._snapshot_version(flow, nodes, remark=_("Nodes updated"))
        return flow

    def _definition_changed(self, flow, nodes) -> bool:
        """与最新版本快照比较：nodes 或 form_schema 有变化返回 True。"""
        latest = flow.versions.order_by("-version").values_list("snapshot", flat=True).first()
        if latest is None:
            return True
        current = self._build_snapshot(flow, nodes)
        import json

        return json.dumps(latest, sort_keys=True, ensure_ascii=False) != json.dumps(
            current, sort_keys=True, ensure_ascii=False
        )

    def _build_snapshot(self, flow, nodes) -> dict:
        return {
            "name": flow.name,
            "code": flow.code,
            "is_active": flow.is_active,
            "form_schema": flow.form_schema or [],
            "nodes": [
                {
                    "name": (node.get("name") or "").strip()[:64],
                    "order": int(node.get("order") or index + 1),
                    "approve_type": node.get("approve_type") or ApprovalFlowNode.ApproveType.OR,
                    "approve_ratio": int(node.get("approve_ratio") or 100),
                    "assignee_type": node.get("assignee_type") or ApprovalFlowNode.AssigneeType.ROLE,
                    "assignee_value": (node.get("assignee_value") or "").strip()[:255],
                    "condition": node.get("condition") or {},
                    "routes": node.get("routes") or [],
                    "layout": node.get("layout") or {},
                    "timeout_hours": int(node.get("timeout_hours") or 0),
                }
                for index, node in enumerate(nodes or [])
            ],
        }

    def _snapshot_version(self, flow, nodes, remark):
        """版本号 +1 并落全量快照（ADR-016 §2）。"""
        flow.version = (flow.version or 0) + 1
        flow.save(update_fields=["version", "updated_time"])
        ApprovalFlowVersion.objects.create(
            flow=flow, version=flow.version, snapshot=self._build_snapshot(flow, nodes or []), remark=str(remark)
        )

    def rollback_to_version(self, flow, version: int, remark=""):
        """回滚到历史版本：快照写入活定义（节点/表单）并落新版本。返回 (ok, detail)。"""
        snapshot = flow.versions.filter(version=version).values_list("snapshot", flat=True).first()
        if snapshot is None:
            return False, str(_("The flow version does not exist"))
        try:
            self._assert_nodes_mutable(flow)
        except serializers.ValidationError as exc:
            return False, str(exc.detail[0] if isinstance(exc.detail, list) else exc.detail)
        nodes = snapshot.get("nodes") or []
        flow.form_schema = snapshot.get("form_schema") or []
        flow.save(update_fields=["form_schema", "updated_time"])
        flow.nodes.all().delete()
        self._replace_nodes(flow, nodes)
        self._snapshot_version(flow, nodes, remark=remark or str(_("Rollback from version {}").format(version)))
        return True, None

    def _replace_nodes(self, flow, nodes):
        for index, node in enumerate(nodes or []):
            ApprovalFlowNode.objects.create(
                flow=flow,
                name=(node.get("name") or "").strip()[:64],
                order=int(node.get("order") or index + 1),
                approve_type=node.get("approve_type") or ApprovalFlowNode.ApproveType.OR,
                approve_ratio=int(node.get("approve_ratio") or 100),
                assignee_type=node.get("assignee_type") or ApprovalFlowNode.AssigneeType.ROLE,
                assignee_value=(node.get("assignee_value") or "").strip()[:255],
                condition=node.get("condition") or {},
                routes=node.get("routes") or [],
                layout=node.get("layout") or {},
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
