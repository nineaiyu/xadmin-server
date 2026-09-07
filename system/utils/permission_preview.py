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
from django.db.models import Q
from rest_framework.exceptions import ValidationError

from common.base.utils import menu_list_to_tree
from common.core.filter import get_filter_queryset
from common.core.permission import get_user_menu_queryset
from common.utils import get_logger
from system.models import DataPermission, DeptInfo, FieldPermission, Menu, ModelLabelField, UserInfo, UserRole
from system.services import ModeTypeAbstract

logger = get_logger(__name__)

# 角色预览持有用户列表采样上限
PREVIEW_USER_SAMPLE_LIMIT = 20

# 14 种数据权限规则类型 → 可读文案（ModelLabelField.KeyChoices 全覆盖）
RULE_TYPE_TEXTS = {
    'value.text': '文本值',
    'value.json': 'JSON 值',
    'value.all': '全部数据',
    'value.datetime': '指定时间',
    'value.datetime.range': '时间范围',
    'value.date': '相对时间窗口',
    'value.user.id': '目标用户本人',
    'value.user.dept.id': '目标用户所在部门',
    'value.user.dept.ids': '目标用户部门及下级',
    'value.dept.ids': '指定部门及下级',
    'value.table.user.ids': '指定用户',
    'value.table.menu.ids': '指定菜单',
    'value.table.role.ids': '指定角色',
    'value.table.dept.ids': '指定部门',
}

# match lookup → 可读文案（与 RelatedManager.get_filter_attrs_qs 支持的匹配符对齐）
MATCH_TEXTS = {
    'exact': '等于',
    'iexact': '忽略大小写等于',
    'contains': '包含',
    'icontains': '忽略大小写包含',
    'in': '属于',
    'gt': '大于',
    'gte': '大于等于',
    'lt': '小于',
    'lte': '小于等于',
    'startswith': '以…开头',
    'istartswith': '忽略大小写以…开头',
    'endswith': '以…结尾',
    'iendswith': '忽略大小写以…结尾',
    'regex': '正则匹配',
    'm2m': '多对多包含任一',
    'm2m_all': '多对多包含全部',
    'all': '不限',
    'ip_in': 'IP 段内',
}

MODE_OR_TEXT = '或模式（满足任一规则即可见）'
MODE_AND_TEXT = '且模式（需同时满足全部规则）'

DATA_PERMISSION_SEMANTIC_NOTE = (
    '部门祖先链各层授权之间为「且」组合（逐级收紧）；同一授权内多规则按该授权的且/或模式组合'
    '（仅一条规则时按或模式）；个人授权与部门链授权的结果做「或」合并；'
    '无任何授权时默认不可见；绑定菜单的授权仅在对应菜单上下文生效。'
)


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


def _model_label(table: str) -> str:
    """model label_lower → 中文模型名（数据权限注册表），未注册回退原名。"""
    node = ModelLabelField.objects.filter(parent__isnull=True, name=table).first()
    return node.label if node else table


def _field_label(table: str, field: str) -> str:
    """数据权限规则字段的中文标签（注册表父子树），未注册回退原名。"""
    node = ModelLabelField.objects.filter(parent__name=table, name=field).first()
    return node.label if node else field


def _pks_of(value) -> list:
    """table.*.ids 规则的 value 兼容解析：['pk', {'pk': ..}] → [pk]。"""
    items = json.loads(value) if isinstance(value, str) else value
    return [(item.get('pk') if isinstance(item, dict) else item) for item in items or []]


def _resolve_value_text(rule: dict, user_obj: UserInfo) -> str:
    """按规则类型把 value 解析为可读名称（关联 ID → 用户名/角色名/部门名/菜单标题）。

    注意：value.user.* 类型的原始 value 就是 '*' 占位符（过滤时注入真实 ID），
    必须先按类型解析，仅对自由值类型把 '*' 视为不限。
    """
    f_type, val = rule.get('type'), rule.get('value')
    if f_type == 'value.all':
        return '不限（全部）'
    if f_type == 'value.user.id':
        return f"目标用户本人（{user_obj.username}）"
    if f_type == 'value.user.dept.id':
        dept = user_obj.dept
        return f"目标用户所在部门（{dept.name}）" if dept else "目标用户未绑定部门"
    if f_type == 'value.user.dept.ids':
        if not user_obj.dept:
            return "目标用户未绑定部门"
        pks = [str(pk) for pk in DeptInfo.recursion_dept_info(user_obj.dept.pk)]
        return "、".join(DeptInfo.objects.filter(pk__in=pks).values_list('name', flat=True))
    if f_type == 'value.dept.ids':
        names = []
        for pk in _pks_of(val):
            pks = [str(item) for item in DeptInfo.recursion_dept_info(str(pk))]
            names.extend(DeptInfo.objects.filter(pk__in=pks).values_list('name', flat=True))
        return "、".join(dict.fromkeys(names))
    if f_type == 'value.table.user.ids':
        users = UserInfo.objects.filter(pk__in=_pks_of(val))
        return "、".join(user.nickname or user.username for user in users)
    if f_type == 'value.table.menu.ids':
        menus = Menu.objects.filter(pk__in=_pks_of(val)).select_related('meta')
        return "、".join(menu.meta.title for menu in menus)
    if f_type == 'value.table.role.ids':
        return "、".join(UserRole.objects.filter(pk__in=_pks_of(val)).values_list('name', flat=True))
    if f_type == 'value.table.dept.ids':
        return "、".join(DeptInfo.objects.filter(pk__in=_pks_of(val)).values_list('name', flat=True))
    if f_type == 'value.date':
        seconds = json.loads(val) if isinstance(val, str) else val
        direction = "过去" if int(seconds) < 0 else "未来"
        return f"{direction} {_humanize_seconds(seconds)}内"
    if f_type == 'value.datetime.range':
        return "{} ~ {}".format(*val) if isinstance(val, list) and len(val) == 2 else str(val)
    if val == '*':
        return '不限（全部）'
    return str(val)


def decode_rule(rule: dict, user_obj: UserInfo) -> dict:
    """单条规则 JSON → 可读文案结构（table/field/type/match/value 全部附 label）。"""
    table = rule.get('table')
    field = rule.get('field')
    return {
        'table': table,
        'table_label': '全部表' if table == "*" else _model_label(table),
        'field': field,
        'field_label': '全部字段' if field == "*" else _field_label(table, field),
        'type': rule.get('type'),
        'type_text': RULE_TYPE_TEXTS.get(rule.get('type'), str(rule.get('type'))),
        'match': rule.get('match', 'exact'),
        'match_text': MATCH_TEXTS.get(rule.get('match', 'exact'), str(rule.get('match', 'exact'))),
        'value': rule.get('value'),
        'value_text': _resolve_value_text(copy.deepcopy(rule), user_obj),
        'exclude': bool(rule.get('exclude')),
    }


def decode_data_permission(dp: DataPermission, user_obj: UserInfo) -> dict:
    """DataPermission → 可读授权组（含生效模式与总述文案）。

    与 get_filter_q_base 对齐：仅一条规则时强制或模式；value.all 在或模式下短路全放行、
    在且模式下该规则被忽略。deepcopy 防止解码过程改写 JSONField 内存值。
    """
    rules = [rule for rule in copy.deepcopy(dp.rules) if isinstance(rule, dict)]
    effective_mode = ModeTypeAbstract.ModeChoices.OR if len(rules) == 1 else dp.mode_type
    decoded = []
    for rule in rules:
        item = decode_rule(rule, user_obj)
        if rule.get('type') == 'value.all' and effective_mode == ModeTypeAbstract.ModeChoices.AND:
            # 且模式存在 value.all：该规则被忽略（get_filter_q_base L40-41 语义）
            item['value_text'] = '且模式下被忽略'
        decoded.append(item)
    rule_text = "; ".join(
        f"【{item['table_label']}】{item['field_label']} {item['match_text']} {item['value_text']}"
        for item in decoded
    )
    mode_text = MODE_OR_TEXT if effective_mode == ModeTypeAbstract.ModeChoices.OR else MODE_AND_TEXT
    return {
        'pk': str(dp.pk),
        'name': dp.name,
        'is_active': dp.is_active,
        'mode_type': dp.mode_type,
        'menus': [{'pk': str(menu.pk), 'title': menu.meta.title} for menu in dp.menu.all()],
        'rules': decoded,
        'rule_text': f"{mode_text}: {rule_text}",
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
    """
    if not menus:
        return []
    data = [
        {
            'pk': str(menu.pk),
            'name': menu.name,
            'rank': menu.rank,
            'path': menu.path,
            'menu_type': menu.menu_type,
            'parent': {'pk': str(menu.parent.pk), 'name': menu.parent.name} if menu.parent else None,
            'title': menu.meta.title if menu.meta else menu.name,
        }
        for menu in menus.select_related('meta', 'parent')
    ]
    data.sort(key=lambda item: item.get('rank') or 0)
    return menu_list_to_tree(data, 'parent')


def get_user_api_permissions(user_obj: UserInfo) -> list:
    """API 权限码：可见菜单中 menu_type=PERMISSION 的全量码（不经 24h 缓存）。"""
    menu_queryset = get_user_menu_queryset_for_preview(user_obj)
    if not menu_queryset:
        return []
    return [
        {
            'menu_pk': str(menu.pk),
            'code': menu.name,
            'method': menu.method,
            'path': menu.path,
            'title': menu.meta.title,
        }
        for menu in menu_queryset.filter(menu_type=Menu.MenuChoices.PERMISSION).select_related('meta')
    ]


def get_user_data_permissions(user_obj: UserInfo) -> dict:
    """数据权限明细：个人授权 + 部门祖先链逐层分组（语义对齐 get_filter_queryset）。"""
    personal = [
        decode_data_permission(dp, user_obj)
        for dp in DataPermission.objects.filter(is_active=True).filter(userinfo=user_obj).prefetch_related('menu__meta')
    ]
    dept_chain = []
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
            dept_chain.append({
                'dept': {'pk': str(dept.pk), 'name': dept.name},
                'relation': 'self' if dept.pk == user_obj.dept.pk else 'ancestor',
                'permissions': [
                    decode_data_permission(dp, user_obj)
                    for dp in DataPermission.objects.filter(is_active=True).filter(deptinfo=dept)
                    .prefetch_related('menu__meta')
                ],
            })
    has_any_grant = bool(personal) or any(item['permissions'] for item in dept_chain)
    return {
        'enabled': settings.PERMISSION_DATA_ENABLED,
        'superuser_bypass': user_obj.is_superuser,
        'has_any_grant': has_any_grant,
        'personal': personal,
        'dept_chain': dept_chain,
        'semantic_note': DATA_PERMISSION_SEMANTIC_NOTE,
    }


def _field_groups(field_permission: FieldPermission) -> list:
    """FieldPermission.field M2M → 按父模型分组的字段结构。"""
    models = {}
    for field in field_permission.field.all().select_related('parent'):
        parent = field.parent
        if not parent:
            continue
        group = models.setdefault(parent.name, {
            'model': parent.name, 'model_label': parent.label, 'fields': [], 'field_labels': [],
        })
        group['fields'].append(field.name)
        group['field_labels'].append(field.label)
    return list(models.values())


def get_user_field_matrix(user_obj: UserInfo) -> list:
    """字段权限矩阵（菜单 × 角色 × 模型 → 字段白名单）。

    复刻 get_user_field_queryset 的取数范围（用户角色 ∪ 部门挂载角色），
    但直查并保留完整关联关系，不经 10s 缓存。
    """
    q = Q()
    has_q = False
    if user_obj.roles.exists():
        q |= (Q(role__in=user_obj.roles.all()) & Q(role__is_active=True))
        has_q = True
    if user_obj.dept:
        q |= (Q(role__deptinfo=user_obj.dept) & Q(role__deptinfo__is_active=True))
        has_q = True
    if not has_q:
        return []
    rows = []
    queryset = FieldPermission.objects.filter(q).select_related('menu__meta', 'role').prefetch_related('field__parent')
    for fp in queryset:
        for group in _field_groups(fp):
            rows.append({
                'menu': {'pk': str(fp.menu.pk), 'title': fp.menu.meta.title},
                'role': {'pk': str(fp.role.pk), 'name': fp.role.name},
                **group,
            })
    return rows


def get_trial_candidates() -> list:
    """试算模型候选：数据权限注册表（ModelLabelField DATA 根节点）+ 规则命中标记。"""
    registered_tables = set()
    for rules_json in DataPermission.objects.filter(is_active=True).values_list('rules', flat=True):
        for rule in rules_json or []:
            if isinstance(rule, dict) and rule.get('table'):
                registered_tables.add(rule['table'])
    return [
        {
            'label': node.name,
            'display': f"{node.label} ({node.name})",
            'has_rules': node.name in registered_tables,
        }
        for node in ModelLabelField.objects.filter(
            field_type=ModelLabelField.FieldChoices.DATA, parent__isnull=True
        ).exclude(name='*')
    ]


def run_data_trial(user_obj: UserInfo, model_label, menu_pk) -> dict:
    """以目标用户为主语试算数据权限过滤（只读，不落库）。

    安全设计：
    1. 模型白名单 = 数据权限注册表（防任意表扫描 / 防非法 label 注入 apps.get_model）；
    2. 菜单上下文必须属于目标用户可见页面菜单（防任意构造上下文）；
    3. 只做 count 与 SQL 文本展示，SQL 不执行；count 为单条 SELECT COUNT(*)。
    """
    if not model_label:
        raise ValidationError("试算模型不能为空")
    allowed = set(
        ModelLabelField.objects.filter(
            field_type=ModelLabelField.FieldChoices.DATA, parent__isnull=True
        ).exclude(name='*').values_list('name', flat=True)
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

    # 与 IsAuthenticated 的 request.user.menu 注入同构（UserInfo 无 menu 字段，纯属性）
    user_obj.menu = menu_ctx
    queryset = get_filter_queryset(model.objects.all(), user_obj)
    note = None
    if user_obj.is_superuser:
        note = "目标用户为超级管理员，试算返回全量"
    elif not settings.PERMISSION_DATA_ENABLED:
        note = "数据权限未启用，试算结果为全量"
    return {
        'model': model_label,
        'menu': menu_ctx,
        'count': queryset.count(),
        'sql': str(queryset.query),
        'is_superuser': user_obj.is_superuser,
        'data_enabled': settings.PERMISSION_DATA_ENABLED,
        'note': note,
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
        'user': {
            'pk': str(user_obj.pk),
            'username': user_obj.username,
            'nickname': user_obj.nickname,
            'is_active': user_obj.is_active,
            'is_superuser': user_obj.is_superuser,
            'dept': {'pk': str(user_obj.dept.pk), 'name': user_obj.dept.name} if user_obj.dept else None,
            'roles': [
                {'pk': str(role.pk), 'name': role.name, 'code': role.code, 'is_active': role.is_active}
                for role in user_obj.roles.all()
            ],
        },
        'menu_tree': menu_tree,
        'api_permissions': api_permissions,
        'data_permissions': data_permissions,
        'field_permissions': field_permissions,
        'field_permission_enabled': settings.PERMISSION_FIELD_ENABLED,
        'trial_candidates': trial_candidates,
        'summary': {
            'menu_count': len(menu_tree),
            'api_code_count': len(api_permissions),
            'data_permission_count': len(data_permissions['personal']) + sum(
                len(item['permissions']) for item in data_permissions['dept_chain']),
            'field_permission_count': len(field_permissions),
        },
    }


def get_role_preview(role_obj: UserRole, operator: UserInfo) -> dict:
    """角色维度授权预览载荷（授权菜单树 / 字段授权 / 持有用户采样）。

    持有用户列表经调用者数据权限过滤（不泄漏调用者不可见的用户）。
    """
    menu_tree = _serialize_menu_tree(role_obj.menu.all())
    field_permissions = []
    queryset = FieldPermission.objects.filter(role=role_obj).select_related('menu__meta').prefetch_related(
        'field__parent')
    for fp in queryset:
        field_permissions.append({
            'menu': {'pk': str(fp.menu.pk), 'title': fp.menu.meta.title},
            'models': _field_groups(fp),
        })

    users_queryset = get_filter_queryset(UserInfo.objects.filter(roles=role_obj), operator)
    total = users_queryset.count()
    users = [
        {
            'pk': str(user.pk),
            'username': user.username,
            'nickname': user.nickname,
            'dept': {'pk': str(user.dept.pk), 'name': user.dept.name} if user.dept else None,
            'is_active': user.is_active,
        }
        for user in users_queryset[:PREVIEW_USER_SAMPLE_LIMIT]
    ]
    return {
        'role': {
            'pk': str(role_obj.pk),
            'name': role_obj.name,
            'code': role_obj.code,
            'is_active': role_obj.is_active,
        },
        'menu_tree': menu_tree,
        'field_permissions': field_permissions,
        'users': {
            'total': total,
            'truncated': total > PREVIEW_USER_SAMPLE_LIMIT,
            'sample_limit': PREVIEW_USER_SAMPLE_LIMIT,
            'list': users,
        },
    }
