#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""应用授权目录与写入校验（自 ``api_grant`` 拆出，仅因行数门禁；行为不变）。

- 目录派生：模型标签树（``ModelLabelField``）× 启用权限菜单（``_catalogs``），
  或按用户权限面派生（``grant_options_for_user``，开放平台授权页下拉的数据源）；
- 写入校验：``validate_grant_payload`` 保证「模型 × 动作 × 字段 × 行」四级配置在
  保存时即合法，且非超管管理员的校验面收敛到本人可授权面（与展示面同源）。

运行时判定（匹配 / 字段收敛 / 行过滤）仍在 ``system/utils/identity/api_grant.py``。
"""

from django.core.cache import cache
from django.utils.translation import gettext_lazy as _

from system.services import Menu, ModelLabelField

ANY = "*"

# 动作段 → 中文展示名（管理面目录用；未命中回退动作段原文）
ACTION_LABELS = {
    "list": _("List"),
    "retrieve": _("Retrieve"),
    "create": _("Create"),
    "partialUpdate": _("Update"),
    "update": _("Update"),
    "destroy": _("Delete"),
    "batchDestroy": _("Batch delete"),
    "exportData": _("Export"),
    "exportAsync": _("Async export"),
    "importData": _("Import"),
    "importValidate": _("Import validate"),
    "importHeaders": _("Import headers"),
    "importAsync": _("Async import"),
}


def action_of_code(code) -> str:
    """权限点 code（``list:SystemUser``）→ 动作段（``list``）；无法解析返回空串。"""
    name = str(code or "").strip()
    if ":" not in name:
        return ""
    return name.split(":", 1)[0].strip()


CATALOG_CACHE_KEY = "api_grant_catalog_v1"
CATALOG_CACHE_TTL = 60


def _catalogs():
    """(模型→动作段集合, 模型→字段集合) 目录（从启用权限菜单与字段标签树派生，短缓存）。"""
    try:
        cached = cache.get(CATALOG_CACHE_KEY)
    except Exception:  # noqa: BLE001
        cached = None
    if cached is not None:
        return cached["actions"], cached["fields"]
    actions: dict[str, set] = {}
    menus = (
        Menu.objects.filter(menu_type=Menu.MenuChoices.PERMISSION, is_active=True)
        .prefetch_related("model")
        .only("pk", "name")
    )
    for menu in menus:
        action = action_of_code(menu.name)
        if not action:
            continue
        for label_field in menu.model.all():
            if label_field.name:
                actions.setdefault(label_field.name, set()).add(action)
    fields: dict[str, set] = {}
    nodes = list(ModelLabelField.objects.filter(parent__isnull=True).values("pk", "name"))
    node_names = {node["pk"]: node["name"] for node in nodes}
    for row in ModelLabelField.objects.filter(parent_id__in=node_names.keys()).values("parent_id", "name"):
        label = node_names.get(row["parent_id"])
        if label and row["name"]:
            fields.setdefault(label, set()).add(row["name"])
    try:
        cache.set(CATALOG_CACHE_KEY, {"actions": actions, "fields": fields}, CATALOG_CACHE_TTL)
    except Exception:  # noqa: BLE001
        pass
    return actions, fields


def _validation_catalogs(user):
    """写入校验目录（模型→动作 / 模型→字段）。

    普通用户收敛到**本人可授权面**（``grant_options_for_user``，与授权页下拉同源）：
    非超管管理员不得保存超出自身权限面的模型/动作/字段规则——运行时虽仍受 owner
    菜单权限交集兜底（不构成提权），但管理面口径必须与展示面一致。超管或无用户
    上下文（管理命令/种子）使用全量目录。
    """
    if user is None or getattr(user, "is_superuser", False):
        return _catalogs()
    options = grant_options_for_user(user)
    actions: dict[str, set] = {}
    fields: dict[str, set] = {}
    for item in options.get("models", []):
        label = item.get("value")
        if not label or label == ANY:
            continue
        actions[label] = {action["value"] for action in item.get("actions") or []}
        fields[label] = {field["value"] for field in item.get("fields") or []}
    return actions, fields


def validate_grant_payload(model_label, actions, fields, row_filter, user=None):
    """授权规则写入校验（serializer 调用）：非法配置在保存时被拒。

    规则（与运行时判定同口径）：
    - ``model`` 必须是模型标签树中的模型或 ``*``；
    - ``actions`` 必须是该模型真实存在的动作段（从权限菜单派生）或 ``*``；
    - ``model="*"`` 时不得配置 ``fields`` / ``row_filter``（通配模型下两者语义不成立）；
    - ``fields`` 必须在该模型字段集内（空 = 全部字段）；
    - ``row_filter`` 走 data_scope 同源校验（table 强制注入为当前模型）；
    - ``user`` 非超管时，以上「真实存在」收敛为「该用户可授权的面」（见
      ``_validation_catalogs``）。
    """
    from rest_framework.exceptions import ValidationError

    from common.core.data_scope import validate_rules

    model_label = str(model_label or "").strip()
    if model_label != ANY:
        known_actions, known_fields = _validation_catalogs(user)
        if model_label not in known_actions and model_label not in known_fields:
            raise ValidationError(_("Unknown model: %(model)s") % {"model": model_label})
    actions = actions or []
    if not isinstance(actions, list) or not actions:
        raise ValidationError(_("Actions cannot be empty"))
    actions = [str(item).strip() for item in actions if str(item).strip()]
    if not actions:
        raise ValidationError(_("Actions cannot be empty"))
    fields = [str(item).strip() for item in (fields or []) if str(item).strip()]
    row_filter = row_filter or []
    if model_label == ANY:
        if fields or row_filter:
            raise ValidationError(_("Wildcard model does not support fields or row filter"))
        return actions, fields, row_filter
    known_actions, known_fields = _validation_catalogs(user)
    allowed_actions = known_actions.get(model_label, set())
    unknown = [action for action in actions if action != ANY and action not in allowed_actions]
    if unknown:
        raise ValidationError(
            _("Unknown actions for %(model)s: %(actions)s") % {"model": model_label, "actions": ", ".join(unknown)}
        )
    if fields:
        allowed_fields = known_fields.get(model_label, set())
        unknown_fields = [field for field in fields if field not in allowed_fields]
        if unknown_fields:
            raise ValidationError(
                _("Unknown fields for %(model)s: %(fields)s")
                % {"model": model_label, "fields": ", ".join(unknown_fields)}
            )
    if row_filter:
        rules = []
        for rule in row_filter:
            if not isinstance(rule, dict):
                raise ValidationError(_("Row filter must be a list of rule objects"))
            item = dict(rule)
            item["table"] = model_label
            rules.append(item)
        validate_rules(rules)
    return actions, fields, row_filter


def grant_options_for_user(user) -> dict:
    """应用授权目录：模型 →（动作段、字段），粒度与 scope-options 同口径。

    - 数据源：模型/字段标签树（``ModelLabelField``）× 用户可授权的权限菜单；
    - 普通用户只列出本人有权限的模型/动作；超管为全部启用权限菜单；
    - ``*``（全部模型 / 全部动作）作为第一项始终可选（仅对超管可见模型全集）。
    """
    from identity.utils.pat_scope import _iter_scope_menus  # 延迟导入：绕开权限层模块循环

    model_actions: dict[str, set] = {}
    for _method, menu in _iter_scope_menus(user):
        action = action_of_code(menu.name)
        if not action:
            continue
        for label_field in menu.model.all():
            if label_field.name:
                model_actions.setdefault(label_field.name, set()).add(action)

    nodes = list(ModelLabelField.objects.filter(parent__isnull=True).values("pk", "name", "label"))
    node_pks = [node["pk"] for node in nodes]
    children: dict = {}
    for row in ModelLabelField.objects.filter(parent_id__in=node_pks).values("parent_id", "name", "label"):
        children.setdefault(row["parent_id"], []).append({"value": row["name"], "label": row["label"] or row["name"]})

    models = []
    merged: dict[str, dict] = {}
    for node in nodes:
        label = node["name"]
        if label not in model_actions:
            continue
        actions = [
            {"value": action, "label": ACTIONS_DISPLAY.get(action, action)}
            for action in sorted(model_actions[label], key=_action_sort_key)
        ]
        fields = sorted(children.get(node["pk"], []), key=lambda item: item["value"])
        item = merged.get(label)
        if item is None:
            item = {
                "value": label,
                "label": node["label"] or label,
                "actions": actions,
                "fields": fields,
            }
            merged[label] = item
            models.append(item)
        else:
            known = {action["value"] for action in item["actions"]}
            item["actions"].extend(action for action in actions if action["value"] not in known)
            known_fields = {field["value"] for field in item["fields"]}
            item["fields"].extend(field for field in fields if field["value"] not in known_fields)
    models.sort(key=lambda item: item["value"])
    models.insert(
        0,
        {
            "value": ANY,
            "label": _("All models"),
            "actions": [{"value": ANY, "label": _("All actions")}],
            "fields": [],
        },
    )
    return {"total": len(models), "models": models}


def _action_sort_key(action: str):
    order = list(ACTION_LABELS)
    try:
        return (0, order.index(action), action)
    except ValueError:
        return (1, 0, action)


# 前端展示用（延迟求值：gettext_lazy 在渲染时翻译）
ACTIONS_DISPLAY = ACTION_LABELS
