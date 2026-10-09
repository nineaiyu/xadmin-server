#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""数据集执行服务：白名单校验 + ORM 查询构建 + 数据权限过滤。

安全边界（保存与执行双侧校验，禁原生 SQL）：
- 模型白名单 = ModelLabelField DATA 根节点（与数据权限编辑器同源）；
- 字段白名单 = 对应模型的 DATA 子节点；过滤字段/排序字段/聚合字段同样受限；
- op 白名单固定九种；聚合 metric 限 count/sum/avg，sum/avg 仅数值字段；
- 行级过滤走既有入口 `get_filter_queryset`（fail-closed：无授权 → none()）；
- 输出列叠加浏览者字段权限白名单。
"""

from typing import Any

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db.models import BooleanField, F
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger
from dataset.utils.columns import (
    annotations_for,
    json_fields_of_bound_model,
    parse_column,
    resolve_columns,
)

logger = get_logger(__name__)

ALLOWED_OPS = ("exact", "in", "gte", "gt", "lte", "lt", "contains", "startswith", "isnull")
ALLOWED_METRICS = ("count", "sum", "avg")
ALLOWED_DATE_TRUNC = ("day", "month")
AGGREGATE_BUCKET_LIMIT = 365
ROW_LIMIT_CAP = 5000
NUMERIC_FIELD_CLASSES = ("IntegerField", "BigIntegerField", "SmallIntegerField", "FloatField", "DecimalField")

# 设计器元数据短缓存（meta 端点）：载荷只读 ModelLabelField 与模型内省，与请求用户
# 无关；一次下发全部白名单模型的字段面回表成本高。白名单仅随管理端字段同步/编辑
# 变化（低频），滞后至多一个 TTL；需立即生效可 cache.delete(DATASET_META_CACHE_KEY)
# 或迭代键内版本号（v1 → v2）换 key。
DATASET_META_CACHE_KEY = "dataset:designer_meta:v1"
DATASET_META_CACHE_TTL = 60


def designer_meta_payload() -> dict[str, Any]:
    """设计器元数据载荷：模型白名单 / 逐模型字段清单 / JSON 路径根字段（短 TTL 缓存）。"""
    from django.core.cache import cache

    def _load() -> dict[str, Any]:
        models = available_models()
        return {
            "models": models,
            "fields": {name: available_fields(name) for name in models},
            "json_fields": {name: json_fields_of_bound_model(name) for name in models},
        }

    payload: dict[str, Any] = cache.get_or_set(DATASET_META_CACHE_KEY, _load, DATASET_META_CACHE_TTL)
    return payload


def _request_memo(key: Any, loader: Any) -> Any:
    """请求级 memo：白名单与字段权限在同一请求内不变（多卡片同屏只查一次）。

    容器挂在当前请求对象上随请求销毁，跨请求零残留；无请求上下文
    （Celery 定时报表、管理命令、测试直调）不缓存，行为与改造前一致。
    """
    from server.utils import get_current_request

    request = get_current_request()
    if request is None:
        return loader()
    store = getattr(request, "_dataset_whitelist_memo", None)
    if store is None:
        store = {}
        request._dataset_whitelist_memo = store
    if key not in store:
        store[key] = loader()
    return store[key]


def available_models() -> list[Any]:
    """模型白名单：ModelLabelField DATA 根节点（label_lower 列表）。"""
    from system.models import ModelLabelField

    def _load() -> Any:
        return tuple(
            ModelLabelField.objects.filter(
                field_type=ModelLabelField.FieldChoices.DATA, parent__isnull=True
            ).values_list("name", flat=True)
        )

    return list(_request_memo("available_models", _load))


def available_fields(bound_model: str) -> list[Any]:
    """模型字段白名单：该模型 DATA 节点的子节点字段名。"""
    from system.models import ModelLabelField

    def _load() -> Any:
        return tuple(
            ModelLabelField.objects.filter(
                field_type=ModelLabelField.FieldChoices.DATA, parent__name=bound_model
            ).values_list("name", flat=True)
        )

    return list(_request_memo(("available_fields", bound_model), _load))


def get_whitelisted_model(bound_model: str) -> Any:
    """白名单校验 + 取模型类；越权模型 raise ValidationError。"""
    if bound_model not in available_models():
        raise ValidationError(_("Model {} is not available for datasets").format(bound_model))
    try:
        return apps.get_model(*bound_model.split(".", 1))
    except (LookupError, ValueError) as exc:
        raise ValidationError(_("Model {} is not available for datasets").format(bound_model)) from exc


def _check_fields(bound_model: str, fields: Any, allow_empty: Any = True) -> None:
    """列清单校验：模型字段（白名单）或 JSON 路径（根字段白名单 + JSONField）。"""
    model = get_whitelisted_model(bound_model)
    whitelist = set(available_fields(bound_model))
    for field in fields or []:
        parse_column(model, field, whitelist)
    if not allow_empty and not fields:
        raise ValidationError(_("Dataset columns cannot be empty"))


def validate_filters(bound_model: str, filters: Any) -> None:
    """filters: [{field, op, value}]；field 在白名单、op 在白名单、value 形态合法。"""
    if not filters:
        return
    if not isinstance(filters, list):
        raise ValidationError(_("Invalid dataset filters"))
    model = get_whitelisted_model(bound_model)
    whitelist = set(available_fields(bound_model))
    for item in filters:
        if not isinstance(item, dict) or not item.get("field") or not item.get("op"):
            raise ValidationError(_("Invalid dataset filters"))
        parse_column(model, item["field"], whitelist)
        if item["op"] not in ALLOWED_OPS:
            raise ValidationError(_("Filter op {} is not allowed").format(item["op"]))
        if item["op"] == "in" and not isinstance(item.get("value"), (list, tuple)):
            raise ValidationError(_("Filter op in requires a list value"))
        if item["op"] == "isnull" and not isinstance(item.get("value"), bool):
            raise ValidationError(_("Filter op isnull requires a boolean value"))


def validate_dataset(instance: Any) -> None:
    """保存侧整体校验（模型/列/过滤/排序/limit/config）。"""
    model = get_whitelisted_model(instance.bound_model)
    whitelist = set(available_fields(instance.bound_model))
    _check_fields(instance.bound_model, instance.columns, allow_empty=False)
    validate_filters(instance.bound_model, instance.filters)
    if instance.ordering:
        field = instance.ordering.lstrip("-")
        if field not in (instance.columns or []):
            raise ValidationError(_("Ordering field must be in columns"))
        parse_column(model, field, whitelist)
    if not (0 < int(instance.row_limit) <= ROW_LIMIT_CAP):
        raise ValidationError(_("Row limit must be between 1 and {}").format(ROW_LIMIT_CAP))
    date_field = str((instance.config or {}).get("date_field") or "")
    if not date_field:
        return
    spec = parse_column(model, date_field, whitelist)
    if spec.is_json:
        # JSON 趋势字段必须带 |date 标注且在 columns 内（与聚合分桶要求同源）
        if spec.value_type != "date":
            raise ValidationError(_("JSON trend field requires |date type: {}").format(spec.raw))
        if spec.raw not in (instance.columns or []):
            raise ValidationError(_("Trend field must be in columns"))
    elif date_field not in whitelist:
        raise ValidationError(_("Field {}.{} is not available for datasets").format(instance.bound_model, date_field))


def _group_label(value: Any, model_field: Any = None) -> str:
    """分组名：布尔与枚举码走可读文案（i18n / choices display）；仅 None 落空串。

    直接 ``str(value)`` 会把原始值画进图例：布尔字段出现两个同名「True」、
    枚举码（如性别）只显示裸码「0」。False/0 等合法 falsy 分组值在映射后保留。
    """
    if value is None:
        return ""
    if model_field is not None:
        if isinstance(model_field, BooleanField):
            return str(_("Enabled") if value else _("Disabled"))
        try:
            label = dict(model_field.flatchoices or []).get(value)
        except TypeError:  # 分组值不可哈希（JSON 等复杂类型）→ 回落原始字符串
            label = None
        if label is not None:
            return str(label)
    return str(value)


def numeric_columns_of(dataset: Any) -> list[Any]:
    """数据集列中的数值字段（sum/avg 聚合候选）：供前端 value_field 选择器使用。

    含 JSON 路径列（``data.amount|number`` 的类型标注即候选）；历史列在模型演进后
    可能失配（字段被删/改名）：逐列静默跳过，不阻断列表读取。
    """
    try:
        model = apps.get_model(*str(dataset.bound_model or "").split(".", 1))
    except (LookupError, ValueError):
        return []
    if model is None:
        return []
    numeric = []
    for field in dataset.columns or []:
        try:
            spec = parse_column(model, field)
        except ValidationError:  # 非法声明 / 历史列失配 → 不影响其余列
            continue
        if spec.is_json:
            if spec.value_type == "number":
                numeric.append(spec.raw)
            continue
        try:
            model_field = model._meta.get_field(spec.raw)
        except Exception:  # noqa: BLE001 历史列失配 → 不影响其余列
            continue
        if model_field.__class__.__name__ in NUMERIC_FIELD_CLASSES:
            numeric.append(spec.raw)
    return numeric


def _check_numeric(model: Any, field: str, whitelist: Any = None) -> Any:
    """sum/avg 取值字段：模型数值字段，或带 ``|number`` 标注的 JSON 路径。"""
    spec = parse_column(model, field, whitelist)
    if spec.is_json:
        if spec.value_type != "number":
            raise ValidationError(_("Field {} is not numeric, cannot aggregate").format(spec.raw))
        return spec
    try:
        model_field = model._meta.get_field(spec.raw)
    except Exception as exc:
        raise ValidationError(_("Field {} is not available for datasets").format(spec.raw)) from exc
    if model_field.__class__.__name__ not in NUMERIC_FIELD_CLASSES:
        raise ValidationError(_("Field {} is not numeric, cannot aggregate").format(spec.raw))
    return spec


def build_queryset(dataset: Any, user_obj: Any, extra_filters: Any = None) -> Any:
    """执行侧查询构建：白名单复核 → JSON 列注解 → filters → 排序 → 数据权限过滤。

    JSON 路径列统一注解为 ``json_<根>_<键>`` 别名：筛选 / 排序 / 分组全部走别名，
    使 ``|number`` 标注列的比较与聚合作用于 Cast 表达式（跨库语义一致）。
    数据权限过滤 fail-closed（无授权 → none()）。
    """
    model = get_whitelisted_model(dataset.bound_model)
    whitelist = set(available_fields(dataset.bound_model))
    specs = resolve_columns(model, dataset.columns, whitelist)
    validate_filters(dataset.bound_model, dataset.filters)

    columns = [spec.raw for spec in specs]
    queryset = model.objects.all()
    annotations = annotations_for(specs)
    if annotations:
        queryset = queryset.annotate(**annotations)
    conditions = list(dataset.filters or []) + list(extra_filters or [])
    for item in conditions:
        alias = parse_column(model, item["field"], whitelist).alias
        queryset = queryset.filter(**{f"{alias}__{item['op']}": item.get("value")})
    if dataset.ordering:
        ordering = str(dataset.ordering)
        descending = ordering.startswith("-")
        alias = parse_column(model, ordering.lstrip("-"), whitelist).alias
        # 显式 NULLS LAST：PG 对 DESC 默认 NULLS FIRST、sqlite 把 NULL 当最小值排最后，
        # 两侧默认相反（nightly PG 档首轮暴露）。JSON 缺键行的契约是「缺键不参与数值
        # 列」，排序必须与缺省方向解耦；sqlite ≥3.30 起支持 NULLS FIRST/LAST。
        direction = F(alias).desc(nulls_last=True) if descending else F(alias).asc(nulls_last=True)
        queryset = queryset.order_by(direction)
    # 行级数据权限：fail-closed 继承数据权限编译器（无授权 → none()）
    from common.core.filter import get_filter_queryset

    return get_filter_queryset(queryset, user_obj), model, columns


def viewer_visible_fields(bound_model: str, user_obj: Any) -> Any:
    """浏览者对 bound_model 的字段权限白名单。

    角色解析与 `common.core.permission.get_user_field_queryset` 同口径
    （用户直挂角色 + 部门挂角色，均要求角色启用）；字段权限配置本身跨菜单取并集——
    数据集执行无菜单上下文，逐菜单裁剪在此无意义。

    返回值语义：
    - ``None``：超管，或浏览者的角色**没有任何**该模型字段权限配置 → 不裁剪（全量）。
      字段权限是显式授权行为（白名单制），而数据集执行不走菜单序列化裁剪链路；
      若在此 fail-closed 全裁，所有未配置字段权限的普通用户的仪表盘都会被裁空；
    - ``set``：白名单字段名集合（执行/聚合列与其求交集后输出）。

    结果按请求级 memo 缓存（键含用户 pk）：同请求多卡片同屏只查一次。
    """

    def _load() -> Any:
        from django.db.models import Q

        from system.models import FieldPermission

        if getattr(user_obj, "is_superuser", False):
            return None
        conditions = Q()
        has_role = False
        roles = list(user_obj.roles.all())
        if roles:
            conditions |= Q(role__in=roles) & Q(role__is_active=True)
            has_role = True
        if getattr(user_obj, "dept", None):
            conditions |= Q(role__deptinfo=user_obj.dept) & Q(role__deptinfo__is_active=True)
            has_role = True
        if not has_role:
            return None
        fields = set(
            FieldPermission.objects.filter(conditions)
            .filter(field__parent__name=bound_model)
            .values_list("field__name", flat=True)
            .distinct()
        )
        return frozenset(fields) if fields else None

    cached = _request_memo(("viewer_visible_fields", bound_model, getattr(user_obj, "pk", None)), _load)
    return set(cached) if cached is not None else None


# 执行 / 聚合 / 卡片级布局过滤拆至 dataset_query（仅行数门禁）：经模块级 __getattr__
# 再导出，保持既有 ``from dataset.utils.dataset import execute_dataset`` 调用面
# （实现模块反向依赖本模块的白名单与查询构建，直接 import 会成环）。
_EXECUTION_EXPORTS = ("execute_dataset", "aggregate_dataset", "filter_layout_for_user")


def __getattr__(name: Any) -> Any:
    if name in _EXECUTION_EXPORTS:
        from dataset.utils import dataset_query

        return getattr(dataset_query, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
