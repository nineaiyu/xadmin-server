#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""全量审批流引擎一期序列化器。

- ApprovalFlowSerializer：流程定义 + 节点列表嵌套写入（nodes 整体替换式更新）。
  改版走版本化路径（收口当前生效行 + 新版本落行，见 docs/adr/ADR-073-in-flight-
  flow-versioning.md）：有 PENDING 实例时同样允许改节点/回滚——在途实例按自身
  ``flow_version`` 过滤节点集，定义变更只影响之后发起的新单。
- ApprovalInstanceSerializer：实例只读展示 + 发起申请写入（flow/title/form_data）；
  列表附带 my_task（当前用户在当前节点的待办任务），供待办面板直接发起审批动作。
- ApprovalNodeTaskSerializer：节点任务（审批轨迹）。
"""

from django.db import transaction
from django.db.models import Count, Q
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from approval.models.approval import (
    ApprovalFlow,
    ApprovalFlowNode,
)
from approval.serializers.approval_instance import (  # noqa: F401 实例/任务序列化器拆出后保持既有导入面
    ApprovalInstanceExportSerializer,
    ApprovalInstanceSerializer,
    ApprovalNodeTaskSerializer,
)
from approval.utils.approval_flow import CONDITION_OPS, MAX_FLOW_NODES
from approval.utils.approval_flow.versioning import apply_definition, build_snapshot
from common.core.serializers import BaseModelSerializer
from system.services import DisplayRelatedField

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
            # 超时自动动作（none/approve/reject/transfer_up；仅在 timeout_hours>0 时生效）
            "timeout_action",
            # 节点级默认抄送人（用户 pk 列表）
            "cc_users",
        ]
        extra_kwargs = {"order": {"required": False}}


class ApprovalFlowSerializer(BaseModelSerializer):
    nodes = ApprovalFlowNodeSerializer(many=True, required=False)
    creator = DisplayRelatedField(read_only=True, allow_null=True, label=_("Creator"), label_builder=_username)
    node_count = serializers.SerializerMethodField(label=_("Node count"))
    # 表单字段编辑锁：流程被 dform 绑定后 form_schema 由绑定表单单向投影
    # （dataset/utils/dform_flow.py::project_flow_form_schema），流程侧只读
    form_schema_locked = serializers.SerializerMethodField(label=_("Form schema locked"))

    # 关联计数声明（注解名与字段名一致）：列表/详情/导出由 RelationCountMixin
    # 预聚合，避免逐行 COUNT；单对象序列化（无注解）回退为单次 COUNT/EXISTS。
    # filter 限定当前生效行：历史版本节点不计入「节点数」展示；form_schema_locked
    # 以 Count(filter) 预聚合、取值侧转布尔（O11-3 抽样实测定位的逐行 EXISTS N+1）。
    # 两处均 distinct：同查询带两处反向关联 join，不 distinct 会交叉膨胀计数。
    relation_count_fields = {
        "node_count": Count("nodes", filter=Q(nodes__version_to__isnull=True), distinct=True),
        "form_schema_locked": Count("bound_forms", filter=Q(bound_forms__is_template=False), distinct=True),
    }

    class Meta:
        model = ApprovalFlow
        fields = [
            "pk",
            "name",
            "code",
            "form_schema",
            "form_schema_locked",
            "is_active",
            "nodes",
            "node_count",
            "creator",
            "created_time",
            "updated_time",
        ]
        table_fields = ["name", "code", "node_count", "is_active", "creator", "created_time"]

    def get_node_count(self, obj) -> int:
        annotated = getattr(obj, "node_count", None)
        return annotated if annotated is not None else obj.nodes.count()

    def get_form_schema_locked(self, obj) -> bool:
        """是否被 dform 绑定（绑定期 form_schema 由表单侧单向投影维护）。

        列表/详情/导出走 ``relation_count_fields`` 预聚合（注解为 Count，转布尔）；
        单对象序列化（无注解）回退为单次 EXISTS。
        """
        annotated = getattr(obj, "form_schema_locked", None)
        if annotated is not None:
            return annotated > 0
        return obj.bound_forms.filter(is_template=False).exists()

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
        """节点校验：数量上限；至少 1 个；order 缺省按顺序补齐且不可重复；审批人配置与条件表达式合法。"""
        if value is None:
            return value
        if not value:
            raise serializers.ValidationError(_("The flow requires at least one node"))
        if len(value) > MAX_FLOW_NODES:
            raise serializers.ValidationError(_("A flow supports at most {} nodes").format(MAX_FLOW_NODES))
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

        # 环检测（colors: 0 未访问 1 在栈 2 完成）：显式栈迭代，深链不触发 Python 递归上限
        color = {order: 0 for order in orders}
        for start in edges:
            if color.get(start, 0) != 0:
                continue
            stack = [(start, False)]
            while stack:
                order, exiting = stack.pop()
                if exiting:
                    color[order] = 2
                    continue
                state = color.get(order, 0)
                if state == 2:
                    continue
                if state == 1:  # 防御：入栈时已拦同类边，此处兜底
                    raise serializers.ValidationError(_("Branch routes contain a loop"))
                color[order] = 1
                stack.append((order, True))
                for target in edges.get(order, []):
                    target_state = color.get(target, 0)
                    if target_state == 1:
                        raise serializers.ValidationError(_("Branch routes contain a loop"))
                    if target_state == 0:
                        stack.append((target, False))

    @transaction.atomic
    def create(self, validated_data):
        nodes = validated_data.pop("nodes", [])
        flow = super().create(validated_data)
        apply_definition(flow, nodes, remark=_("Initial version"))
        return flow

    @transaction.atomic
    def update(self, instance, validated_data):
        nodes = validated_data.pop("nodes", None)
        # 编辑锁：绑定期 form_schema 由绑定表单单向投影，客户端改动忽略不落库
        # （响应带 form_schema_locked=true 供前端禁用编辑；防手滑覆盖投影结果）
        if "form_schema" in validated_data and self.get_form_schema_locked(instance):
            validated_data.pop("form_schema")
        # 行锁：并发改版时「版本号分配 + 旧行收口 + 新行落库」串行化
        # （版本快照的 (flow, version) 唯一约束仍作二层兜底）
        flow = ApprovalFlow.objects.select_for_update().get(pk=instance.pk)
        flow = super().update(flow, validated_data)
        # 定义（节点或表单）有实质变化才落新版本；无变化时节点行保持原样（主键不变）
        if nodes is not None and self._definition_changed(flow, nodes):
            apply_definition(flow, nodes, remark=_("Nodes updated"))
        return flow

    def _definition_changed(self, flow, nodes) -> bool:
        """与最新版本快照比较：nodes 或 form_schema 有变化返回 True。

        历史快照缺 `timeout_action`（字段后补）时按默认值回填再比较，避免
        语义未变的存量定义被误判为「有变化」而多落一个版本。
        """
        latest = flow.versions.order_by("-version").values_list("snapshot", flat=True).first()
        if latest is None:
            return True
        import json

        latest = json.loads(json.dumps(latest))
        for node in latest.get("nodes") or []:
            if isinstance(node, dict):
                node.setdefault("timeout_action", ApprovalFlowNode.TimeoutAction.NONE)
        return json.dumps(latest, sort_keys=True, ensure_ascii=False) != json.dumps(
            build_snapshot(flow, nodes), sort_keys=True, ensure_ascii=False
        )

    def rollback_to_version(self, flow, version: int, remark=""):
        """回滚到历史版本：快照写入活定义（节点/表单）并落新版本。返回 (ok, detail)。

        与改节点同口径：在途实例按自身 ``flow_version`` 推进，回滚只影响之后发起的新单，
        因此不再受 PENDING 实例限制。
        """
        snapshot = flow.versions.filter(version=version).values_list("snapshot", flat=True).first()
        if snapshot is None:
            return False, str(_("The flow version does not exist"))
        with transaction.atomic():
            flow = ApprovalFlow.objects.select_for_update().get(pk=flow.pk)
            flow.form_schema = snapshot.get("form_schema") or []
            flow.save(update_fields=["form_schema", "updated_time"])
            apply_definition(
                flow,
                snapshot.get("nodes") or [],
                remark=remark or str(_("Rollback from version {}").format(version)),
            )
        return True, None
