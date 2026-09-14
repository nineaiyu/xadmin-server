#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : modelfield
# author : ly_13
# date : 10/24/2024
from django.apps import apps
from django.conf import settings
from django.db import transaction
from django.utils.translation import activate
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel
from common.core.serializers import BaseModelSerializer
from common.utils import get_logger
from system.models import ModelLabelField

logger = get_logger(__name__)


def _prune_stale(field_type, kept_pks, enabled):
    """删除本轮未采集到的同类型行（陈旧字段/模型）。

    **不依赖 updated_time**：种子 loaddata 写入的行 updated_time 为 NULL，
    旧的 `updated_time__lt=now` 清理对这批行恒不成立（NULL 不参与比较），
    导致被删字段长期残留（2026-09 调研：1298 行中 647 行为 NULL）。
    `enabled` 为假（本轮未采集到任何数据）时不做清理，避免误删全表。
    """
    if not enabled:
        return 0
    stale = ModelLabelField.objects.filter(field_type=field_type).exclude(pk__in=kept_pks)
    count = stale.count()
    if count:
        stale.delete()
    return count


def get_sub_serializer_fields():
    """按序列化器定义重建 ROLE 字段树（角色页字段权限勾选的数据源）。

    - 单个序列化器实例化异常只跳过并记录（不再中断整个同步）；
    - 清理口径改用「本轮采集 pk 集合的反向差集」（NULL 安全）。
    """
    cls_list = []
    activate(settings.LANGUAGE_CODE)

    def get_all_subclass(base_cls):
        if base_cls.__subclasses__():
            for cls in base_cls.__subclasses__():
                cls_list.append(cls)
                get_all_subclass(cls)

    get_all_subclass(BaseModelSerializer)

    field_type = ModelLabelField.FieldChoices.ROLE
    kept, processed, failed = set(), 0, []
    for cls in cls_list:
        try:
            instance = cls(ignore_field_permission=True)
        except Exception as e:  # noqa: BLE001 单个坏序列化器不阻断全量同步
            failed.append(cls.__name__)
            logger.warning(f"skip serializer {cls.__name__} in field sync: {e}")
            continue
        model = getattr(getattr(instance, "Meta", None), "model", None)
        if not model:
            continue
        processed += 1
        obj, _created = ModelLabelField.objects.update_or_create(
            name=model._meta.label_lower,
            field_type=field_type,
            parent=None,
            defaults={"label": model._meta.verbose_name},
        )
        kept.add(obj.pk)
        for name, field in instance.fields.items():
            _obj, _created = ModelLabelField.objects.update_or_create(
                name=name, parent=obj, field_type=field_type, defaults={"label": field.label}
            )
            kept.add(_obj.pk)

    deleted = _prune_stale(field_type, kept, enabled=processed > 0)
    logger.info(f"sync role field tree done. kept:{len(kept)} deleted:{deleted} failed:{failed}")
    return {"kept": len(kept), "deleted": deleted, "failed_serializers": failed}


def get_app_model_fields():
    """按 PERMISSION_DATA_AUTH_APPS 重建「模型/字段」数据权限树（NULL 安全清理）。"""
    field_type = ModelLabelField.FieldChoices.DATA
    kept = set()
    root, _created = ModelLabelField.objects.update_or_create(
        name="*", field_type=field_type, parent=None, defaults={"label": _("All tables")}
    )
    kept.add(root.pk)
    all_fields, _created = ModelLabelField.objects.update_or_create(
        name="*", field_type=field_type, parent=root, defaults={"label": _("All fields")}
    )
    kept.add(all_fields.pk)

    for field in DbAuditModel._meta.fields:
        _obj, _created = ModelLabelField.objects.update_or_create(
            name=field.name,
            field_type=field_type,
            parent=root,
            defaults={"label": getattr(field, "verbose_name", field.name)},
        )
        kept.add(_obj.pk)

    processed = 0
    for app_name, app in apps.app_configs.items():
        if app_name not in settings.PERMISSION_DATA_AUTH_APPS:
            continue

        for model in app.models.values():
            # 虚拟 model 判断：不包含 Meta 的模型是系统生成的第三方模型（含 relationship）
            if not hasattr(model, "Meta"):
                continue
            processed += 1
            obj, _created = ModelLabelField.objects.update_or_create(
                name=f"{app_name}.{model._meta.model_name}",
                field_type=field_type,
                parent=None,
                defaults={"label": model._meta.verbose_name},
            )
            kept.add(obj.pk)
            for field in model._meta.fields + model._meta.many_to_many:
                _obj, _created = ModelLabelField.objects.update_or_create(
                    name=field.name, parent=obj, field_type=field_type, defaults={"label": field.verbose_name}
                )
                kept.add(_obj.pk)

    deleted = _prune_stale(field_type, kept, enabled=processed > 0)
    logger.info(f"sync data field tree done. kept:{len(kept)} deleted:{deleted} models:{processed}")
    return {"kept": len(kept), "deleted": deleted, "models": processed}


@transaction.atomic
def sync_model_field():
    """同步模型字段数据到数据库（角色字段树 + 数据权限字段树）。

    返回同步摘要（新增语义按 kept 计），供管理命令 / 接口回显：
    ``{"data": {...}, "role": {...}}``
    """
    activate(settings.LANGUAGE_CODE)
    data = get_app_model_fields()
    role = get_sub_serializer_fields()
    return {"data": data, "role": role}


def get_field_lookup_info(fields):
    field_info = {
        "exact": _("Exact match, the field value must be exactly the same as the given value."),
        "iexact": _("Case-insensitive exact match."),
        "contains": _("The field value must contain the given substring (case-sensitive)."),
        "icontains": _(
            "Case-insensitive containment, the field value must contain the given substring (case-insensitive)."
        ),
        "in": _("The field value must be within the given list, tuple, or queryset."),
        "gt": _("Greater than, the field value must be greater than the given value."),
        "gte": _("Greater than or equal to, the field value must be greater than or equal to the given value."),
        "lt": _("Less than, the field value must be less than the given value."),
        "lte": _("Less than or equal to, the field value must be less than or equal to the given value."),
        "startswith": _("The field value must start with the given string (case-sensitive)."),
        "istartswith": _(
            "Case-insensitive start with, the field value must start with the given string (case-insensitive)."
        ),
        "endswith": _("The field value must end with the given string (case-sensitive)."),
        "iendswith": _("Case-insensitive end with, the field value must end with the given string (case-insensitive)."),
        "range": _("Within a range, the field value must be between the two given values (inclusive)."),
        "date": _("Filters only the date part (ignores the time part)."),
        "year": _("Filters by year."),
        "iso_year": _("Filters by ISO year (may not be the same as the Gregorian year)."),
        "month": _("Filters by month."),
        "day": _("Filters by day of the month."),
        "week": _("Filters by week number of the year."),
        "week_day": _("Filters by a specific day of the week (1 = Monday, 7 = Sunday)."),
        "iso_week_day": _("Filters by a specific day of the ISO week (1 = Monday, 7 = Sunday)."),
        "quarter": _("Filters by quarter (1, 2, 3, 4)."),
        "time": _("Filters only the time part (ignores the date part)."),
        "hour": _("Filters by hour."),
        "minute": _("Filters by minute."),
        "second": _("Filters by second."),
        "isnull": _("Checks if the field value is NULL, can be set to True or False."),
        "regex": _("The field value must match the given regular expression (case-sensitive)."),
        "iregex": _("The field value must match the given regular expression (case-insensitive)."),
        "contained_by": _(
            "The field value must be a subset of the given value, typically used with array or JSON fields."
        ),
        "has_any_keys": _(
            "The field value must contain at least one of the given keys, typically used with JSON fields."
        ),
        "has_keys": _("The field value must contain all the given keys, typically used with JSON fields."),
        "has_key": _("The field value must contain the given single key, typically used with JSON fields."),
        # 框架自定义匹配符（不在 Django class lookups 里，见 common/core/data_scope.SPECIAL_MATCHES）
        "m2m": _("Many-to-many: the field contains any of the given values."),
        "m2m_all": _("Many-to-many: the field contains all of the given values."),
        "ip_in": _("IP address is inside the given network / range, * means no restriction."),
    }
    return [{"value": field, "label": field_info.get(field, field)} for field in fields]


def get_extra_field_lookups(field) -> list:
    """按字段类型返回框架自定义匹配符（与 data_scope.SPECIAL_MATCHES 同源）。

    只对适用字段暴露，避免在 CharField 等字段的 match 下拉里出现 m2m_all/ip_in 造成误导。
    """
    from common.core.data_scope import SPECIAL_MATCHES

    extras = []
    if getattr(field, "many_to_many", False):
        extras.extend(("m2m", "m2m_all"))
    if field.get_internal_type() == "GenericIPAddressField":
        extras.append("ip_in")
    return [item for item in extras if item in SPECIAL_MATCHES]
