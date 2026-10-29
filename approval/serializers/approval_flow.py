#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""全量审批流引擎一期序列化器。

- ApprovalFlowSerializer：流程定义 + 节点列表嵌套写入（nodes 整体替换式更新）。
  改版走版本化路径（收口当前生效行 + 新版本落行
  flow-versioning.md）：有 PENDING 实例时同样允许改节点/回滚——在途实例按自身
  ``flow_version`` 过滤节点集，定义变更只影响之后发起的新单。
  列表裁剪 nodes/form_schema（编辑走 retrieve 取全量）；写入支持
  base_updated_time 乐观锁基线，拦截并发编辑的后写覆盖。
- ApprovalInstanceSerializer：实例只读展示 + 发起申请写入（flow/title/form_data）；
  列表附带 my_task（当前用户在当前节点的待办任务），供待办面板直接发起审批动作。
- ApprovalNodeTaskSerializer：节点任务（审批轨迹）。
"""

from typing import Any

from django.db.models import Count, Q
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from approval.models.approval import (
    ApprovalFlow,
    ApprovalFlowNode,
)
from approval.serializers.approval_instance import (  # noqa: F401 实例/任务序列化器拆出后保持既有导入面
    ApprovalInstanceExportSerializer as ApprovalInstanceExportSerializer,  # noqa: F401 显式再导出（PEP 484 语义）
)
from approval.serializers.approval_instance import (
    ApprovalInstanceSerializer as ApprovalInstanceSerializer,  # noqa: F401 显式再导出（PEP 484 语义）
)
from approval.serializers.approval_instance import (
    ApprovalNodeTaskSerializer as ApprovalNodeTaskSerializer,  # noqa: F401 显式再导出（PEP 484 语义）
)
from approval.services.approval_flow import CONDITION_OPS, MAX_FLOW_NODES
from approval.services.approval_flow.definition import (
    create_flow,
    update_flow,
)
from common.core.fields import DisplayRelatedField
from common.core.serializers import BaseModelSerializer

FORM_FIELD_TYPES = ("text", "textarea", "number", "date", "select")
# 数值比较运算符：条件值必须可转数值。引擎对转换失败一律按不命中处理（节点静默跳过、
# 流程走向改变且无告警），保存期不拦会把配错静默带进流程
NUMERIC_CONDITION_OPS = ("gt", "gte", "lt", "lte")


def _username(value: Any) -> Any:
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
    # 乐观锁基线：客户端编辑抽屉带回它所见行的 updated_time，保存时与当前行比对；
    # 两管理员并发编辑时后写不再静默覆盖先写（否则先写内容无提示丢失且各落一版）
    base_updated_time = serializers.CharField(write_only=True, required=False, allow_blank=True)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # 仅 list 裁剪（实例侧 LIST_EXCLUDED_FIELDS 同款口径）：列表页只渲染 table_fields，
        # nodes / form_schema 随节点数与字段数线性膨胀；编辑走 retrieve 独立取全量
        if getattr(self.context.get("view"), "action", None) != "list":
            return
        for name in ("nodes", "form_schema"):
            self.fields.pop(name, None)

    # 关联计数声明（注解名与字段名一致）：列表/详情/导出由 RelationCountMixin
    # 预聚合，避免逐行 COUNT；单对象序列化（无注解）回退为单次 COUNT/EXISTS。
    # filter 限定当前生效行：历史版本节点不计入「节点数」展示；form_schema_locked
    # 以 Count(filter) 预聚合、取值侧转布尔（抽样实测定位的逐行 EXISTS N+1）。
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
            "initiator_roles",
            "nodes",
            "node_count",
            "creator",
            "created_time",
            "updated_time",
            "base_updated_time",
        ]
        table_fields = ["name", "code", "node_count", "is_active", "creator", "created_time"]

    def get_node_count(self, obj: Any) -> int:
        annotated = getattr(obj, "node_count", None)
        count: int = annotated if annotated is not None else obj.nodes.count()
        return count

    def get_form_schema_locked(self, obj: Any) -> bool:
        """是否被 dform 绑定（绑定期 form_schema 由表单侧单向投影维护）。

        列表/详情/导出走 ``relation_count_fields`` 预聚合（注解为 Count，转布尔）；
        单对象序列化（无注解）回退为单次 EXISTS。
        """
        annotated = getattr(obj, "form_schema_locked", None)
        if annotated is not None:
            locked: bool = annotated > 0
            return locked
        bound: bool = obj.bound_forms.filter(is_template=False).exists()
        return bound

    def validate_initiator_roles(self, value: Any) -> Any:
        """发起范围：角色 code 列表；空 = 公开。未知/停用角色直接拒绝（避免静默不可发起）。"""
        if value in (None, ""):
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError(_("Initiator roles must be a list"))
        codes: list[str] = []
        for item in value:
            code = str(item or "").strip()
            if code and code not in codes:
                codes.append(code)
        if len(codes) > 100:
            raise serializers.ValidationError(_("Too many initiator roles"))
        if codes:
            from identity.models import UserRole

            valid = set(UserRole.objects.filter(code__in=codes, is_active=True).values_list("code", flat=True))
            unknown = [code for code in codes if code not in valid]
            if unknown:
                raise serializers.ValidationError(
                    _("Unknown or disabled role codes: %(codes)s") % {"codes": ", ".join(unknown)}
                )
        return codes

    def validate_form_schema(self, value: Any) -> Any:
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

    def validate_nodes(self, value: Any) -> Any:
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

    def _validate_condition(self, condition: Any) -> None:
        """条件表达式校验：field 必填、op 白名单、数值运算符的值必须可转数值（routes 与节点条件共用）。"""
        if not isinstance(condition, dict) or not (condition.get("field") or "").strip():
            raise serializers.ValidationError(_("Condition requires a field"))
        op = condition.get("op") or "eq"
        if op not in CONDITION_OPS:
            raise serializers.ValidationError(_("Unsupported condition operator: {}").format(condition.get("op")))
        if op in NUMERIC_CONDITION_OPS:
            value = condition.get("value")
            if value is None:
                # 缺值与不可转数值同拒绝口径（float(None) 不可转）
                raise serializers.ValidationError(_("Condition value for operator {} must be a number").format(op))
            try:
                float(value)
            except (TypeError, ValueError):
                raise serializers.ValidationError(
                    _("Condition value for operator {} must be a number").format(op)
                ) from None

    def validate_code(self, value: Any) -> Any:
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError(_("Flow code is required"))
        return value

    def validate(self, attrs: Any) -> Any:
        nodes = attrs.get("nodes") or []
        if nodes:
            self._validate_routes_graph(nodes)
        return attrs

    def _validate_routes_graph(self, nodes: Any) -> None:
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

    def create(self, validated_data: Any) -> Any:
        # 事务编排（主表 + 节点初始版本）在 services 层收口，本层只保留字段校验
        return create_flow(validated_data)

    def update(self, instance: Any, validated_data: Any) -> Any:
        # 事务编排（编辑锁 / 行锁 / 乐观锁 / 改版落版本）在 services 层收口
        return update_flow(instance, validated_data)
