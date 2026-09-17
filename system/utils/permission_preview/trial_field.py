#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""权限可视化：字段权限实时试算。"""

from django.conf import settings
from django.db.models import Q
from rest_framework.exceptions import ValidationError

from system.models import FieldPermission, Menu, ModelLabelField, UserInfo

from .labels import _model_label
from .queries import _visible_menu_or_error


def _direct_user_field_matrix(user_obj: UserInfo, menu_pk) -> dict:
    """字段权限生效矩阵直查（与 get_user_field_queryset 同口径，不经 10s 缓存）。

    试算需反映「当前配置」，即便 10s 缓存也会让「刚改完立刻试算」失真；
    多角色/部门角色的白名单按字段并集合并（运行时同款语义）。
    """
    q = Q()
    has_q = False
    roles = list(user_obj.roles.all())
    if roles:
        q |= Q(role__in=roles) & Q(role__is_active=True)
        has_q = True
    if user_obj.dept:
        q |= Q(role__deptinfo=user_obj.dept) & Q(role__deptinfo__is_active=True)
        has_q = True
    data = {}
    if not has_q:
        return data
    queryset = FieldPermission.objects.filter(q).filter(menu=menu_pk)
    for model_name, field_name in queryset.values_list("field__parent__name", "field__name").distinct():
        if not model_name or not field_name:
            continue
        data.setdefault(model_name, set()).add(field_name)
    return data


def _registered_model_fields(model_label: str) -> dict:
    """字段权限注册表（ROLE）里某模型的注册字段 → {name: label}。"""
    node = ModelLabelField.objects.filter(
        field_type=ModelLabelField.FieldChoices.ROLE, parent__isnull=True, name=model_label
    ).first()
    if not node:
        return {}
    return dict(ModelLabelField.objects.filter(parent=node).values_list("name", "label"))


def _validate_draft_fields(fields) -> dict:
    """字段试算草稿校验：模型与字段都必须在字段权限注册表（ROLE）内。

    与字段权限保存路径同口径（白名单只能来自注册表字段），
    保证「试算能过 ⇒ 保存也能过」，不成为绕过校验的后门。
    """
    if not fields:
        return {}
    if not isinstance(fields, dict):
        raise ValidationError("字段试算草稿格式不合法")
    cleaned = {}
    for model_label, names in fields.items():
        node = ModelLabelField.objects.filter(
            field_type=ModelLabelField.FieldChoices.ROLE, parent__isnull=True, name=model_label
        ).first()
        if not node:
            raise ValidationError(f"字段试算草稿含未注册模型：{model_label}")
        if not isinstance(names, (list, tuple, set)):
            raise ValidationError(f"字段试算草稿的字段列表不合法：{model_label}")
        registered = set(_registered_model_fields(model_label))
        picked = {str(name) for name in names if name not in (None, "")}
        invalid = picked - registered
        if invalid:
            raise ValidationError(f"字段试算草稿含未注册字段：{model_label}.{sorted(invalid)[0]}")
        cleaned[model_label] = sorted(picked)
    return cleaned


def _menu_model_labels(menu_obj: Menu) -> dict:
    """菜单关联模型 → 中文名（menu.model 可能指向模型根节点，也可能指向字段节点）。"""
    labels = {}
    for node in menu_obj.model.select_related("parent").all():
        parent = node.parent
        labels[parent.name if parent else node.name] = parent.label if parent else node.label
    return labels


def run_field_trial(user_obj: UserInfo, menu_pk, draft=None) -> dict:
    """字段权限试算：目标用户在某菜单下的生效字段矩阵（只读，不落库）。

    - 生效口径与运行时 get_user_field_queryset 同源（用户角色 ∪ 部门角色，字段并集）；
    - 「未配置白名单 = 字段被裁空」显式标注（configured=False），避免把空矩阵误读成"没限制"；
    - draft.fields（{model: [field]}）模拟「该菜单新增一份白名单」后的效果：经注册表校验后
      与现有生效字段取并集，draft_fields 标出草稿带来的新增字段。
    """
    if not menu_pk:
        raise ValidationError("字段试算必须指定菜单")
    menu_obj = _visible_menu_or_error(user_obj, menu_pk)

    draft_fields = _validate_draft_fields(draft.get("fields") if isinstance(draft, dict) else None)
    current = _direct_user_field_matrix(user_obj, menu_obj.pk)
    if user_obj.is_superuser:
        bypass_reason = "superuser"
    elif not settings.PERMISSION_FIELD_ENABLED:
        bypass_reason = "disabled"
    else:
        bypass_reason = None

    model_labels = _menu_model_labels(menu_obj)
    for model_label in list(current.keys()) + list(draft_fields.keys()):
        model_labels.setdefault(model_label, _model_label(model_label))

    models = []
    for model_label in sorted(model_labels):
        registered = _registered_model_fields(model_label)
        if bypass_reason:
            visible = set(registered)
        else:
            visible = set(current.get(model_label, set()))
        visible |= set(draft_fields.get(model_label, []))
        ordered = [name for name in registered if name in visible]
        # 注册表外的历史字段（脏数据）也如实展示，便于管理员定位
        ordered += sorted(visible - set(registered))
        models.append(
            {
                "model": model_label,
                "model_label": model_labels[model_label],
                "configured": bool(current.get(model_label)),
                "fields": ordered,
                "field_labels": [registered.get(name, name) for name in ordered],
                "draft_fields": list(draft_fields.get(model_label, [])),
                "total_fields": len(registered),
                "total_field_labels": [registered[name] for name in registered],
            }
        )

    unconfigured = [item["model_label"] for item in models if not bypass_reason and not item["configured"]]
    if bypass_reason == "superuser":
        note = "目标用户为超级管理员，字段权限旁路（可见全部字段）"
    elif bypass_reason == "disabled":
        note = "字段权限未启用，试算结果为全量字段"
    elif unconfigured:
        note = f"未配置字段白名单的模型（{'、'.join(unconfigured)}）在运行时会裁空所有字段（未配置=默认拒绝）"
    else:
        note = None
    return {
        "scope": "field",
        "menu": {"pk": str(menu_obj.pk), "title": menu_obj.meta.title if menu_obj.meta else menu_obj.name},
        "enabled": settings.PERMISSION_FIELD_ENABLED,
        "superuser_bypass": user_obj.is_superuser,
        "draft_applied": bool(draft_fields),
        "models": models,
        "note": note,
    }
