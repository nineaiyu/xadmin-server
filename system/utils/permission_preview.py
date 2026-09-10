#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : permission_preview
# author : ly_13
# date : 9/7/2026
"""权限可视化（规划外功能）：三层权限只读预览 + 数据权限实时试算。

设计原则：
- 预览 = 真实计算逻辑的只读复用，全部**直查 DB**，不走 MagicCacheData 权限缓存
  （API 权限 24h / 字段权限 10s 缓存会使预览失真；数据权限 get_filter_queryset
  每次现查规则，天然新鲜），保证"所见即当前配置"。
- 以任意 user 为主语取数，不依赖当前请求（复用 get_user_menu_queryset；
  超管自行走 Menu.objects.filter(is_active=True) 旁路，与 routes 视图口径一致）。
- 试算向目标 user 注入 `menu` 属性模拟菜单上下文，与 IsAuthenticated 写入
  request.user.menu 完全同构（common/core/permission.py L123）。
- 文案为面向管理员的中文直述（同登录限流等既有中文文案惯例），不入 .po。
"""

import copy
import json
import re

from django.apps import apps
from django.conf import settings
from django.core.exceptions import EmptyResultSet
from django.db.models import Q
from rest_framework.exceptions import ValidationError

from common.base.utils import menu_list_to_tree
from common.core.data_scope import validate_rules
from common.core.filter import get_filter_queryset
from common.core.permission import get_user_menu_queryset
from common.utils import get_logger
from system.models import DataPermission, DeptInfo, FieldPermission, Menu, ModelLabelField, UserInfo, UserRole
from system.services import ModeTypeAbstract
from system.utils.rule_meta import (
    DATA_PERMISSION_SEMANTIC_NOTE,
    MATCH_TEXTS,
    MODE_AND_TEXT,
    MODE_OR_TEXT,
    RULE_TYPE_TEXTS,
)

logger = get_logger(__name__)

# 角色预览持有用户列表采样上限
PREVIEW_USER_SAMPLE_LIMIT = 20

# 预览里「关联对象名称」列表的展示上限（大部门/大授权集不拉全量、不撑爆响应）
PREVIEW_VALUE_NAME_LIMIT = 50


def _humanize_seconds(seconds: int) -> str:
    """相对时间秒数 → 可读时长（1 天 / 2 小时 / 30 分钟）。"""
    seconds = abs(int(seconds))
    if seconds % 86400 == 0:
        return f"{seconds // 86400} 天"
    if seconds % 3600 == 0:
        return f"{seconds // 3600} 小时"
    if seconds % 60 == 0:
        return f"{seconds // 60} 分钟"
    return f"{seconds} 秒"


def _model_label(table: str, label_cache: dict | None = None) -> str:
    """model label_lower → 中文模型名（数据权限注册表），未注册回退原名。

    label_cache 为请求内共享缓存（同一次预览里同一张表只查一次），避免逐规则 N+1。
    """
    if label_cache is not None and table in label_cache["model"]:
        return label_cache["model"][table]
    node = ModelLabelField.objects.filter(parent__isnull=True, name=table).first()
    label = node.label if node else table
    if label_cache is not None:
        label_cache["model"][table] = label
    return label


def _field_label(table: str, field: str, label_cache: dict | None = None) -> str:
    """数据权限规则字段的中文标签（注册表父子树），未注册回退原名。"""
    key = f"{table}__{field}"
    if label_cache is not None and key in label_cache["field"]:
        return label_cache["field"][key]
    node = ModelLabelField.objects.filter(parent__name=table, name=field).first()
    label = node.label if node else field
    if label_cache is not None:
        label_cache["field"][key] = label
    return label


def _new_label_cache() -> dict:
    """一次预览内的 label 缓存（decode_data_permission 内部贯穿传递）。"""
    return {"model": {}, "field": {}}


def _pks_of(value) -> list:
    """table.*.ids 规则的 value 兼容解析：['pk', {'pk': ..}] → [pk]；脏值返回空列表。"""
    try:
        items = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return []
    if not isinstance(items, (list, tuple)):
        return []
    return [(item.get("pk") if isinstance(item, dict) else item) for item in items if item is not None]


def _join_names(names, total: int | None = None) -> str:
    """名称列表 → 「、」连接；超出展示上限时截断并标注总数（预览只读展示，不拉全量）。"""
    names = [str(name) for name in names if name]
    if not names:
        return "（无）"
    if total is not None and total > len(names):
        return "、".join(names) + f" 等 {total} 项"
    return "、".join(names)


def _resolve_value_text(rule: dict, user_obj: UserInfo | None, subject: str = "user") -> str:
    """按规则类型把 value 解析为可读名称（关联 ID → 用户名/角色名/部门名/菜单标题）。

    注意：value.user.* 类型的原始 value 就是 '*' 占位符（过滤时注入真实 ID），
    必须先按类型解析，仅对自由值类型把 '*' 视为不限。
    历史脏数据（非法 JSON / 非预期结构）不应让预览接口 500，统一回退原始值文案。

    subject="dept" 时主语是「部门成员」（部门维度预览没有单一用户上下文），
    注入类类型直接给固定文案、不访问 user_obj。
    """
    try:
        return _resolve_value_text_inner(rule, user_obj, subject)
    except Exception as exc:  # noqa: BLE001 预览是只读展示，任何脏数据都不应 500
        logger.warning("decode rule value failed, fallback to raw. type:%s error:%s", rule.get("type"), exc)
        return str(rule.get("value"))


def _resolve_dept_subject_text(f_type, val) -> str:
    """部门维度：注入类类型的主语是「部门成员」，与具体用户无关。"""
    mapping = {
        "value.user.id": "部门成员本人（各自）",
        "value.user.dept.id": "部门成员所在部门",
        "value.user.dept.ids": "部门成员所在部门及下级",
        "value.leader.dept.ids": "部门成员主管的部门及下级",
        "value.leader.user.ids": "部门成员主管部门的成员",
    }
    if f_type in mapping:
        return mapping[f_type]
    # 与具体用户无关的类型（指定部门/指定用户/时间窗等）复用通用解析
    return _resolve_value_text_inner({"type": f_type, "value": val}, None, "user")


def _resolve_value_text_inner(rule: dict, user_obj: UserInfo | None, subject: str = "user") -> str:
    f_type, val = rule.get("type"), rule.get("value")
    if f_type == "value.all":
        return "不限（全部）"
    if subject == "dept":
        return _resolve_dept_subject_text(f_type, val)
    if f_type == "value.user.id":
        return f"目标用户本人（{user_obj.username}）"
    if f_type == "value.user.dept.id":
        dept = user_obj.dept
        return f"目标用户所在部门（{dept.name}）" if dept else "目标用户未绑定部门"
    if f_type == "value.user.dept.ids":
        if not user_obj.dept:
            return "目标用户未绑定部门"
        pks = [str(pk) for pk in DeptInfo.recursion_dept_info(user_obj.dept.pk)]
        return _join_names(DeptInfo.objects.filter(pk__in=pks).values_list("name", flat=True))
    if f_type == "value.dept.ids":
        names = []
        for pk in _pks_of(val):
            pks = [str(item) for item in DeptInfo.recursion_dept_info(str(pk))]
            names.extend(DeptInfo.objects.filter(pk__in=pks).values_list("name", flat=True))
        return _join_names(dict.fromkeys(names))
    if f_type == "value.leader.dept.ids":
        led = getattr(user_obj, "leader_depts", None)
        if led is None or not led.exists():
            return "目标用户不是任何部门主管"
        names = []
        for dept in led.filter(is_active=True):
            pks = [str(pk) for pk in DeptInfo.recursion_dept_info(dept.pk)]
            names.extend(DeptInfo.objects.filter(pk__in=pks).values_list("name", flat=True))
        return _join_names(dict.fromkeys(names))
    if f_type == "value.leader.user.ids":
        led = getattr(user_obj, "leader_depts", None)
        if led is None or not led.exists():
            return "目标用户不是任何部门主管"
        dept_pks = []
        for dept in led.filter(is_active=True):
            dept_pks.extend(str(pk) for pk in DeptInfo.recursion_dept_info(dept.pk))
        queryset = UserInfo.objects.filter(dept__in=dept_pks)
        rows = list(queryset.values_list("nickname", "username")[:PREVIEW_VALUE_NAME_LIMIT])
        return _join_names([nickname or username for nickname, username in rows], total=queryset.count())
    if f_type == "value.table.user.ids":
        queryset = UserInfo.objects.filter(pk__in=_pks_of(val))
        rows = list(queryset.values_list("nickname", "username")[:PREVIEW_VALUE_NAME_LIMIT])
        return _join_names([nickname or username for nickname, username in rows], total=queryset.count())
    if f_type == "value.table.menu.ids":
        queryset = Menu.objects.filter(pk__in=_pks_of(val))
        titles = queryset.values_list("meta__title", flat=True)[:PREVIEW_VALUE_NAME_LIMIT]
        return _join_names(titles, total=queryset.count())
    if f_type == "value.table.role.ids":
        queryset = UserRole.objects.filter(pk__in=_pks_of(val))
        names = queryset.values_list("name", flat=True)[:PREVIEW_VALUE_NAME_LIMIT]
        return _join_names(names, total=queryset.count())
    if f_type == "value.table.dept.ids":
        queryset = DeptInfo.objects.filter(pk__in=_pks_of(val))
        names = queryset.values_list("name", flat=True)[:PREVIEW_VALUE_NAME_LIMIT]
        return _join_names(names, total=queryset.count())
    if f_type == "value.date":
        seconds = json.loads(val) if isinstance(val, str) else val
        direction = "过去" if int(seconds) < 0 else "未来"
        return f"{direction} {_humanize_seconds(seconds)}内"
    if f_type == "value.datetime.range":
        return "{} ~ {}".format(*val) if isinstance(val, list) and len(val) == 2 else str(val)
    if val == "*":
        return "不限（全部）"
    return str(val)


def decode_rule(rule: dict, user_obj: UserInfo | None, label_cache: dict | None = None, subject: str = "user") -> dict:
    """单条规则 JSON → 可读文案结构（table/field/type/match/value 全部附 label）。"""
    table = rule.get("table")
    field = rule.get("field")
    return {
        "table": table,
        "table_label": "全部表" if table == "*" else _model_label(table, label_cache),
        "field": field,
        "field_label": "全部字段" if field == "*" else _field_label(table, field, label_cache),
        "type": rule.get("type"),
        "type_text": RULE_TYPE_TEXTS.get(rule.get("type"), str(rule.get("type"))),
        "match": rule.get("match", "exact"),
        "match_text": MATCH_TEXTS.get(rule.get("match", "exact"), str(rule.get("match", "exact"))),
        "value": rule.get("value"),
        "value_text": _resolve_value_text(copy.deepcopy(rule), user_obj, subject),
        "exclude": bool(rule.get("exclude")),
    }


def decode_data_permission(
    dp: DataPermission, user_obj: UserInfo | None, label_cache: dict | None = None, subject: str = "user"
) -> dict:
    """DataPermission → 可读授权组（含生效模式与总述文案）。

    与 common/core/data_scope.py 组内语义对齐：value.all 在或模式下短路全放行、
    在且模式下该规则被忽略（代数下即 ALLOW 单位元）。deepcopy 防止解码过程改写 JSONField 内存值。

    menu_scoped=True 表示该授权绑定了菜单，只在对应菜单上下文生效（通用列表接口下不生效），
    展示层据此提示，避免「预览显示有授权、实际看不到」的误读。
    """
    rules = [rule for rule in copy.deepcopy(dp.rules) if isinstance(rule, dict)]
    effective_mode = ModeTypeAbstract.ModeChoices.OR if len(rules) == 1 else dp.mode_type
    decoded = []
    for rule in rules:
        item = decode_rule(rule, user_obj, label_cache, subject)
        if rule.get("type") == "value.all" and effective_mode == ModeTypeAbstract.ModeChoices.AND:
            # 且模式存在 value.all：该规则被忽略（代数下 ALLOW 是单位元）
            item["value_text"] = "且模式下被忽略"
        decoded.append(item)
    rule_text = "; ".join(
        f"【{item['table_label']}】{item['field_label']} {item['match_text']} {item['value_text']}" for item in decoded
    )
    mode_text = MODE_OR_TEXT if effective_mode == ModeTypeAbstract.ModeChoices.OR else MODE_AND_TEXT
    menus = [{"pk": str(menu.pk), "title": menu.meta.title} for menu in dp.menu.all()]
    return {
        "pk": str(dp.pk),
        "name": dp.name,
        "is_active": dp.is_active,
        "mode_type": dp.mode_type,
        "menus": menus,
        "menu_scoped": bool(menus),
        "rules": decoded,
        "rule_text": f"{mode_text}: {rule_text}",
    }


def get_user_menu_queryset_for_preview(user_obj: UserInfo):
    """预览用可见菜单：超管全量旁路（与 routes 视图一致），无角色/部门 → None。"""
    if user_obj.is_superuser:
        return Menu.objects.filter(is_active=True)
    return get_user_menu_queryset(user_obj)


def _serialize_menu_tree(menus) -> list:
    """页面菜单（目录/菜单）→ 前端只读树（按 rank 排序）。

    刻意不走 RouteSerializer（BaseModelSerializer 会按请求者字段权限裁剪，
    无白名单时字段被裁空导致树构建失败），改为手动投影固定字段。
    menu_list_to_tree 要求父节点在同一集合内，否则孤儿子节点会被当成根，
    故先按全量 pk→parent_id 映射补齐祖先（一次查询，避免逐层 N+1）。
    """
    if not menus:
        return []
    ordered = list(menus.select_related("meta", "parent"))
    parent_map = dict(Menu.objects.values_list("pk", "parent_id"))
    pks = {str(menu.pk) for menu in ordered}
    missing = set()
    for menu in ordered:
        current = parent_map.get(menu.pk)
        while current is not None and str(current) not in pks and str(current) not in missing:
            missing.add(str(current))
            current = parent_map.get(current)
    if missing:
        ordered.extend(Menu.objects.filter(pk__in=missing).select_related("meta", "parent"))
    data = [
        {
            "pk": str(menu.pk),
            "name": menu.name,
            "rank": menu.rank,
            "path": menu.path,
            "menu_type": menu.menu_type,
            "parent": {"pk": str(menu.parent.pk), "name": menu.parent.name} if menu.parent else None,
            "title": menu.meta.title if menu.meta else menu.name,
        }
        for menu in ordered
    ]
    data.sort(key=lambda item: item.get("rank") or 0)
    return menu_list_to_tree(data, "parent")


def _count_menu_nodes(tree: list) -> int:
    """菜单树节点总数（含各层子节点；summary.menu_count 需与页面显示一致）。"""
    return sum(1 + _count_menu_nodes(node.get("children") or []) for node in tree)


def get_user_api_permissions(user_obj: UserInfo) -> list:
    """API 权限码：可见菜单中 menu_type=PERMISSION 的全量码（不经 24h 缓存）。"""
    menu_queryset = get_user_menu_queryset_for_preview(user_obj)
    if not menu_queryset:
        return []
    return [
        {
            "menu_pk": str(menu.pk),
            "code": menu.name,
            "method": menu.method,
            "path": menu.path,
            "title": menu.meta.title,
        }
        for menu in menu_queryset.filter(menu_type=Menu.MenuChoices.PERMISSION).select_related("meta")
    ]


def get_user_data_permissions(user_obj: UserInfo) -> dict:
    """数据权限明细：个人授权 + 部门祖先链逐层分组（语义对齐 get_filter_queryset）。

    与运行时口径对齐的两个关键点（否则会出现「预览显示有授权、实际看不到」）：
    1. 只有**启用部门**上的授权参与过滤（filter.py 只把 active 部门加入 active_chain），
       停用层仍列出以便定位，但标记 is_active/effective=False 且不计入 has_any_grant；
    2. 绑定菜单的授权（menu_scoped）只在对应菜单上下文生效，通用列表接口下不生效，
       同样不计入 has_any_grant，由展示层提示。
    """
    label_cache = _new_label_cache()
    personal = [
        decode_data_permission(dp, user_obj, label_cache)
        for dp in DataPermission.objects.filter(is_active=True).filter(userinfo=user_obj).prefetch_related("menu__meta")
    ]
    dept_chain = []
    has_any_grant = any(not item["menu_scoped"] for item in personal)
    if user_obj.dept:
        # 与 filter.py 的祖先链口径一致（含自身，向上递归）；按部门链顺序（自身→上级）展示
        chain = [user_obj.dept]
        seen = {str(user_obj.dept.pk)}
        current = user_obj.dept
        while current.parent and str(current.parent.pk) not in seen:
            current = current.parent
            seen.add(str(current.pk))
            chain.append(current)
        for dept in chain:
            permissions = [
                decode_data_permission(dp, user_obj, label_cache)
                for dp in DataPermission.objects.filter(is_active=True)
                .filter(deptinfo=dept)
                .prefetch_related("menu__meta")
            ]
            effective = bool(dept.is_active)
            if effective and any(not item["menu_scoped"] for item in permissions):
                has_any_grant = True
            dept_chain.append(
                {
                    "dept": {"pk": str(dept.pk), "name": dept.name},
                    "relation": "self" if dept.pk == user_obj.dept.pk else "ancestor",
                    "is_active": effective,
                    "effective": effective,
                    "permissions": permissions,
                }
            )
    return {
        "enabled": settings.PERMISSION_DATA_ENABLED,
        "superuser_bypass": user_obj.is_superuser,
        "has_any_grant": has_any_grant,
        "personal": personal,
        "dept_chain": dept_chain,
        "semantic_note": DATA_PERMISSION_SEMANTIC_NOTE,
    }


def _field_groups(field_permission: FieldPermission) -> list:
    """FieldPermission.field M2M → 按父模型分组的字段结构。"""
    models = {}
    for field in field_permission.field.all().select_related("parent"):
        parent = field.parent
        if not parent:
            continue
        group = models.setdefault(
            parent.name,
            {
                "model": parent.name,
                "model_label": parent.label,
                "fields": [],
                "field_labels": [],
            },
        )
        group["fields"].append(field.name)
        group["field_labels"].append(field.label)
    return list(models.values())


def get_user_field_matrix(user_obj: UserInfo) -> list:
    """字段权限矩阵（菜单 × 角色 × 模型 → 字段白名单）。

    复刻 get_user_field_queryset 的取数范围（用户角色 ∪ 部门挂载角色），
    但直查并保留完整关联关系，不经 10s 缓存。
    """
    q = Q()
    has_q = False
    if user_obj.roles.exists():
        q |= Q(role__in=user_obj.roles.all()) & Q(role__is_active=True)
        has_q = True
    if user_obj.dept:
        q |= Q(role__deptinfo=user_obj.dept) & Q(role__deptinfo__is_active=True)
        has_q = True
    if not has_q:
        return []
    rows = []
    queryset = FieldPermission.objects.filter(q).select_related("menu__meta", "role").prefetch_related("field__parent")
    for fp in queryset:
        for group in _field_groups(fp):
            rows.append(
                {
                    "menu": {"pk": str(fp.menu.pk), "title": fp.menu.meta.title},
                    "role": {"pk": str(fp.role.pk), "name": fp.role.name},
                    **group,
                }
            )
    return rows


def get_trial_candidates() -> list:
    """试算模型候选：数据权限注册表（ModelLabelField DATA 根节点）+ 规则命中标记。"""
    registered_tables = set()
    for rules_json in DataPermission.objects.filter(is_active=True).values_list("rules", flat=True):
        for rule in rules_json or []:
            if isinstance(rule, dict) and rule.get("table"):
                registered_tables.add(rule["table"])
    return [
        {
            "label": node.name,
            "display": f"{node.label} ({node.name})",
            "has_rules": node.name in registered_tables,
        }
        for node in ModelLabelField.objects.filter(
            field_type=ModelLabelField.FieldChoices.DATA, parent__isnull=True
        ).exclude(name="*")
    ]


def _build_draft_grant(draft, menu_ctx):
    """试算草稿 → 未落库的 DataPermission 实例；不适用当前菜单上下文时返回 None。

    草稿只用于本次试算（不落库），但仍走写入侧同一套 validate_rules，
    保证「试算能过的规则保存也能过」，不成为绕过校验的后门。
    """
    if not draft or not isinstance(draft, dict):
        return None
    rules = draft.get("rules")
    if not rules:
        return None
    validate_rules(rules)
    raw_mode = draft.get("mode_type")
    try:
        mode = int(raw_mode) if raw_mode is not None else ModeTypeAbstract.ModeChoices.OR
    except (TypeError, ValueError):
        raise ValidationError("试算草稿的模式不合法")
    if mode not in (ModeTypeAbstract.ModeChoices.OR, ModeTypeAbstract.ModeChoices.AND):
        raise ValidationError("试算草稿的模式不合法")
    draft_menu = draft.get("menu")
    if draft_menu and str(draft_menu) != str(menu_ctx):
        # 草稿绑定了菜单：仅在该菜单上下文下参与试算，否则与运行期语义不符
        return None
    return DataPermission(name="__draft__", rules=rules, mode_type=mode, is_active=True)


def run_data_trial(user_obj: UserInfo, model_label, menu_pk, draft=None) -> dict:
    """以目标用户为主语试算数据权限过滤（只读，不落库）。

    安全设计：
    1. 模型白名单 = 数据权限注册表（防任意表扫描 / 防非法 label 注入 apps.get_model）；
    2. 菜单上下文必须属于目标用户可见页面菜单（防任意构造上下文）；
    3. 只做 count 与 SQL 文本展示，SQL 不执行；count 为单条 SELECT COUNT(*)；
    4. draft（可选）为「未保存的规则草稿」，用于配置页即时验证影响面，同样经写入校验。
    """
    if not model_label:
        raise ValidationError("试算模型不能为空")
    allowed = set(
        ModelLabelField.objects.filter(field_type=ModelLabelField.FieldChoices.DATA, parent__isnull=True)
        .exclude(name="*")
        .values_list("name", flat=True)
    )
    if model_label not in allowed or not re.fullmatch(r"[a-z_]+\.[a-z_]+", model_label):
        raise ValidationError("不支持的试算模型")
    app_label, model_name = model_label.split(".", 1)
    try:
        model = apps.get_model(app_label, model_name)
    except LookupError:
        raise ValidationError("不支持的试算模型")

    menu_ctx = None
    if menu_pk:
        menu_queryset = get_user_menu_queryset_for_preview(user_obj)
        valid = menu_queryset and menu_queryset.filter(pk=menu_pk, menu_type=Menu.MenuChoices.MENU).exists()
        if not valid:
            raise ValidationError("菜单不在目标用户可见范围")
        menu_ctx = str(menu_pk)

    draft_grant = _build_draft_grant(draft, menu_ctx)
    # 与 IsAuthenticated 的 request.user.menu 注入同构（UserInfo 无 menu 字段，纯属性）
    user_obj.menu = menu_ctx
    queryset = get_filter_queryset(model.objects.all(), user_obj, extra_grants=[draft_grant] if draft_grant else None)
    note = None
    if user_obj.is_superuser:
        note = "目标用户为超级管理员，试算返回全量"
    elif not settings.PERMISSION_DATA_ENABLED:
        note = "数据权限未启用，试算结果为全量"
    try:
        sql = str(queryset.query)
    except EmptyResultSet:
        # 无任何适用授权时 queryset 被短路为空结果集，编译 SQL 会抛 EmptyResultSet；
        # 这是正常的 fail-closed 结果，不该让「试算」这个诊断工具 500
        sql = "-- 无任何适用授权：查询被短路为空结果集，不产生 SQL"
    return {
        "model": model_label,
        "menu": menu_ctx,
        "count": queryset.count(),
        "sql": sql,
        "is_superuser": user_obj.is_superuser,
        "data_enabled": settings.PERMISSION_DATA_ENABLED,
        "draft_applied": bool(draft_grant),
        "note": note,
    }


def get_user_preview(user_obj: UserInfo) -> dict:
    """用户维度三层权限预览全量载荷。"""
    menu_queryset = get_user_menu_queryset_for_preview(user_obj)
    page_menus = None
    if menu_queryset:
        page_menus = menu_queryset.filter(menu_type__in=[Menu.MenuChoices.DIRECTORY, Menu.MenuChoices.MENU])
    menu_tree = _serialize_menu_tree(page_menus)
    api_permissions = get_user_api_permissions(user_obj)
    data_permissions = get_user_data_permissions(user_obj)
    field_permissions = get_user_field_matrix(user_obj)
    trial_candidates = get_trial_candidates()
    return {
        "user": {
            "pk": str(user_obj.pk),
            "username": user_obj.username,
            "nickname": user_obj.nickname,
            "is_active": user_obj.is_active,
            "is_superuser": user_obj.is_superuser,
            "dept": {"pk": str(user_obj.dept.pk), "name": user_obj.dept.name} if user_obj.dept else None,
            "roles": [
                {"pk": str(role.pk), "name": role.name, "code": role.code, "is_active": role.is_active}
                for role in user_obj.roles.all()
            ],
        },
        "menu_tree": menu_tree,
        "api_permissions": api_permissions,
        "data_permissions": data_permissions,
        "field_permissions": field_permissions,
        "field_permission_enabled": settings.PERMISSION_FIELD_ENABLED,
        "trial_candidates": trial_candidates,
        "summary": {
            "menu_count": _count_menu_nodes(menu_tree),
            "api_code_count": len(api_permissions),
            "data_permission_count": len(data_permissions["personal"])
            + sum(len(item["permissions"]) for item in data_permissions["dept_chain"]),
            "field_permission_count": len(field_permissions),
        },
    }


DEPT_PREVIEW_NOTES = [
    "部门上的数据权限作用于「该部门及其下级的成员」，规则中的「本人 / 本部门 / 主管部门」均以成员自身为准。",
    "成员最终可见的菜单与字段还叠加各自的个人角色与个人授权，本页只呈现部门侧的授权。",
    "成员列表为直属成员采样，且已按调用者的数据权限过滤。",
]


def get_dept_preview(dept_obj: DeptInfo, operator: UserInfo) -> dict:
    """部门维度授权预览：部门信息 + 挂载角色 / 数据权限 / 字段权限 + 成员采样。

    与用户/角色预览同源：菜单树复用 _serialize_menu_tree（自动补齐祖先），
    数据权限复用 decode_data_permission（subject="dept"：主语是「部门成员」，
    因为部门维度没有单一用户上下文），成员列表经调用者数据权限过滤（防水平越权）。
    """
    label_cache = _new_label_cache()
    roles = list(dept_obj.roles.all())
    data_permissions = [
        decode_data_permission(dp, None, label_cache, subject="dept")
        for dp in DataPermission.objects.filter(is_active=True).filter(deptinfo=dept_obj).prefetch_related("menu__meta")
    ]

    # 部门挂载角色带来的页面菜单并集（成员实际可见菜单还叠加各自的个人角色）
    menu_pks = set()
    for role in roles:
        menu_pks.update(role.menu.values_list("pk", flat=True))
    page_menus = Menu.objects.filter(
        pk__in=menu_pks, is_active=True, menu_type__in=[Menu.MenuChoices.DIRECTORY, Menu.MenuChoices.MENU]
    )
    menu_tree = _serialize_menu_tree(page_menus)

    field_permissions = []
    if roles:
        queryset = (
            FieldPermission.objects.filter(role__in=roles)
            .select_related("menu__meta", "role")
            .prefetch_related("field__parent")
        )
        for fp in queryset:
            field_permissions.append(
                {
                    "menu": {"pk": str(fp.menu.pk), "title": fp.menu.meta.title},
                    "role": {"pk": str(fp.role.pk), "name": fp.role.name},
                    "models": _field_groups(fp),
                }
            )

    users_queryset = get_filter_queryset(UserInfo.objects.filter(dept=dept_obj), operator)
    total = users_queryset.count()
    users = [
        {
            "pk": str(user.pk),
            "username": user.username,
            "nickname": user.nickname,
            "is_active": user.is_active,
        }
        for user in users_queryset[:PREVIEW_USER_SAMPLE_LIMIT]
    ]

    children = DeptInfo.objects.filter(parent=dept_obj)
    return {
        "dept": {
            "pk": str(dept_obj.pk),
            "name": dept_obj.name,
            "code": dept_obj.code,
            "is_active": dept_obj.is_active,
            "rank": dept_obj.rank,
            "parent": ({"pk": str(dept_obj.parent.pk), "name": dept_obj.parent.name} if dept_obj.parent else None),
            "leader": (
                {
                    "pk": str(dept_obj.leader.pk),
                    "username": dept_obj.leader.username,
                    "nickname": dept_obj.leader.nickname,
                }
                if dept_obj.leader
                else None
            ),
            "child_count": children.count(),
            "active_child_count": children.filter(is_active=True).count(),
        },
        "roles": [
            {"pk": str(role.pk), "name": role.name, "code": role.code, "is_active": role.is_active} for role in roles
        ],
        "menu_tree": menu_tree,
        "data_permissions": {
            "enabled": settings.PERMISSION_DATA_ENABLED,
            "has_any_grant": bool(data_permissions),
            "rules": data_permissions,
        },
        "field_permissions": field_permissions,
        "field_permission_enabled": settings.PERMISSION_FIELD_ENABLED,
        "users": {
            "total": total,
            "truncated": total > PREVIEW_USER_SAMPLE_LIMIT,
            "sample_limit": PREVIEW_USER_SAMPLE_LIMIT,
            "list": users,
        },
        "notes": DEPT_PREVIEW_NOTES,
    }


def get_role_preview(role_obj: UserRole, operator: UserInfo) -> dict:
    """角色维度授权预览载荷（授权菜单树 / 字段授权 / 持有用户采样）。

    持有用户列表经调用者数据权限过滤（不泄漏调用者不可见的用户）。
    """
    menu_tree = _serialize_menu_tree(role_obj.menu.all())
    field_permissions = []
    queryset = (
        FieldPermission.objects.filter(role=role_obj).select_related("menu__meta").prefetch_related("field__parent")
    )
    for fp in queryset:
        field_permissions.append(
            {
                "menu": {"pk": str(fp.menu.pk), "title": fp.menu.meta.title},
                "models": _field_groups(fp),
            }
        )

    users_queryset = get_filter_queryset(UserInfo.objects.filter(roles=role_obj), operator)
    total = users_queryset.count()
    users = [
        {
            "pk": str(user.pk),
            "username": user.username,
            "nickname": user.nickname,
            "dept": {"pk": str(user.dept.pk), "name": user.dept.name} if user.dept else None,
            "is_active": user.is_active,
        }
        for user in users_queryset[:PREVIEW_USER_SAMPLE_LIMIT]
    ]
    return {
        "role": {
            "pk": str(role_obj.pk),
            "name": role_obj.name,
            "code": role_obj.code,
            "is_active": role_obj.is_active,
        },
        "menu_tree": menu_tree,
        "field_permissions": field_permissions,
        "users": {
            "total": total,
            "truncated": total > PREVIEW_USER_SAMPLE_LIMIT,
            "sample_limit": PREVIEW_USER_SAMPLE_LIMIT,
            "list": users,
        },
    }
