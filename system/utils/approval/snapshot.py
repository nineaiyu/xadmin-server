#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""敏感操作审批：目标对象轻量快照。

建单时对目标对象做「事实对照」快照（主键 / 名称 + 仅变更相关字段的变更前 → 变更后），
供审批详情渲染 diff（审批人看事实，而不是只看申请文字）。

边界：
- 只读（不修改业务对象）；对象不可达（已删除 / 无数据权限）返回 ``{}``，详情页按
  「快照缺失」降级展示；
- 只存变更相关字段（不是整对象），最多 :data:`MAX_SNAPSHOT_FIELDS` 个字段、每值
  截断 :data:`MAX_SNAPSHOT_VALUE` 字符——快照体积可控；
- 多值关系（M2M）不展开（避免一次 N 查询），以「(多值)」占位。
"""

from common.utils import get_logger

from .payload import get_request_object_pk

logger = get_logger(__name__)

MAX_SNAPSHOT_FIELDS = 20
MAX_SNAPSHOT_VALUE = 200
MULTI_VALUE_PLACEHOLDER = "(multi-valued)"


def _field_labels(view) -> dict:
    """序列化器字段名 → 可读 label（拿不到时回落字段名）。"""
    labels = {}
    try:
        serializer_class = view.get_serializer_class()
        for name, field in serializer_class().fields.items():
            label = getattr(field, "label", None)
            if label:
                labels[str(name)] = str(label)
    except Exception:  # noqa: BLE001 拿不到 label 不影响快照主体
        logger.info("target snapshot field labels unavailable", exc_info=True)
    return labels


def _scalar(value) -> str:
    """标量值 → 可读字符串（截断 + 多值占位）。"""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        if len(value) <= 5 and all(isinstance(item, (str, int, float, bool)) for item in value):
            return ", ".join(str(item) for item in value)[:MAX_SNAPSHOT_VALUE]
        return MULTI_VALUE_PLACEHOLDER
    if isinstance(value, dict):
        return MULTI_VALUE_PLACEHOLDER
    text = str(value)
    return text[:MAX_SNAPSHOT_VALUE]


def _iter_changes(obj, request):
    """遍历请求体声明的字段：返回 [(field, old, new)]（仅模型实际字段）。"""
    data = getattr(request, "data", None)
    if not isinstance(data, dict):
        return []
    model = type(obj)
    changes = []
    for field in data:
        if len(changes) >= MAX_SNAPSHOT_FIELDS:
            break
        try:
            model_field = model._meta.get_field(str(field))
        except Exception:  # noqa: BLE001 非模型字段（计算字段/嵌套参数）跳过
            continue
        if model_field.many_to_many:
            old_value, new_value = MULTI_VALUE_PLACEHOLDER, MULTI_VALUE_PLACEHOLDER
        else:
            old_value = _scalar(getattr(obj, str(field), None))
            new_value = _scalar(data.get(field))
        if old_value == new_value:
            continue
        changes.append({"field": str(field), "old": old_value, "new": new_value})
    return changes


def build_target_snapshot(view, request) -> dict:
    """构建目标对象快照；不可用时返回 ``{}``（详情页降级展示）。"""
    if view is None:
        return {}
    pk = get_request_object_pk(view)
    if not pk:
        return {}
    try:
        obj = view.get_queryset().filter(pk=pk).first()
    except Exception:  # noqa: BLE001 主键形态异常/查询失败：快照缺失即可
        logger.info("target snapshot object unavailable. pk:%s", pk, exc_info=True)
        return {}
    if obj is None:
        return {}
    model = type(obj)
    labels = _field_labels(view)
    changes = _iter_changes(obj, request)
    for item in changes:
        item["label"] = labels.get(item["field"], item["field"])
    return {
        "model": model._meta.label_lower,
        "verbose_name": str(model._meta.verbose_name),
        "pk": str(obj.pk),
        "name": str(obj)[:MAX_SNAPSHOT_VALUE],
        "changes": changes,
    }
