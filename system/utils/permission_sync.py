#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""菜单权限点同步内核（扫描 → 规划 → 应用 → 报告）。

设计见 docs/architecture/菜单权限与字段同步补全方案-2026.09.md。核心口径与菜单页
「批量生成权限」同源（复用 system.utils.menu.get_view_permissions），保证：

1. 补齐的权限点 code / 描述 / 模型绑定与 UI 生成结果一致，不会产生重复项；
2. 幂等：已覆盖（精确 `path$` 或运行时的正则回退命中）的端点不重复创建；
3. 校正绑定：CRUD 动作补绑定（角色页字段权限可配置）；导入导出链保持空绑定
   （字段权限按运行时规则回退到 list/create 菜单）。

本模块不打印、不交互，供 management 命令与测试复用。
"""

import json
import re
from collections import Counter
from dataclasses import dataclass
from dataclasses import field as dc_field
from pathlib import Path

from django.conf import settings
from django.db import transaction

from common.core.utils import get_all_url_dict
from common.utils import get_logger
from system.models import Menu, MenuMeta, ModelLabelField, UserRole
from system.utils.menu import get_related_models, get_view_permissions

logger = get_logger(__name__)

# 死端点（前端无消费方）：登记豁免，不生成权限点。
# 2026-09-14：`api/system/tasks/interval` 及其前端死代码已删除，此处清空备查。
DEAD_ENDPOINT_PREFIXES = ()
# 不参与扫描的路由前缀（demo 应用按既定决策不投入）
SKIP_ROUTE_PREFIXES = ("api/demo/",)
# 无同源权限点时，按路由前缀指定父菜单（Menu.name，必须为页面菜单）
PARENT_MENU_MAP = {
    "api/system/dynamic-form-submissions": "FormMySubmission",
    "api/system/approval-delegations": "SystemApprovalDelegation",
}
# 审计豁免（有权限点但不在可扫描路由面内，运行期经权限链正则回退命中，权限点有效）：
# - api/chat/*：不在 PERMISSION_SHOW_PREFIX（框架未纳入菜单生成面），权限点手工维护；
# - api-docs/*、api/flower/*、api/system/global-search：路由以无名 pattern / 代理注册，
#   get_all_url_dict 忽略无名路由，故不出现在扫描面内。
AUDIT_SKIP_PREFIXES = ("api/chat/", "api-docs/", "api/flower/", "api/system/global-search")
# 已知「同端点双权限码」重复点：各自服务不同 UI 入口/动作（非脏数据，不报告、不合并）：
# - tasks/executions GET：任务页「日志」按钮(log:SystemTask) 与任务中心抽屉(list:SystemTaskExecution)；
# - logs/operation GET：操作日志页(list:SystemOperationLog) 与用户页「变更历史」(changeHistory:SystemUser)；
# - tasks/periodic/batch-enable POST：批量启用/停用共用端点（batchEnable / batchDisable 两码，
#   授权不区分方向，属已知边界）。
AUDIT_KNOWN_DUPLICATES = {
    ("api/system/tasks/executions$", "GET"),
    ("api/system/logs/operation$", "GET"),
    ("api/system/tasks/periodic/batch-enable$", "POST"),
}
# 需保持「模型绑定为空」的动作：导入导出链（字段权限回退到 list/create 菜单的口径）
IMPORT_EXPORT_ACTIONS = (
    "export_data",
    "export_async",
    "import_data",
    "import_async",
    "import_validate",
    "import_headers",
)
# 需绑定关联模型的动作（与 get_view_permissions 的 models 口径一致）
CRUD_BIND_ACTIONS = ("list", "create", "retrieve", "partial_update")


@dataclass
class RouteInfo:
    view: str
    name: str
    url: str
    sample: str
    actions: dict
    view_cls: object
    requires_permission: bool


@dataclass
class PlanItem:
    view: str
    url: str
    method: str
    action: str
    code: str
    description: str
    parent_id: object
    parent_name: str
    model_pks: list
    rank: int = 10000
    source: str = "generator"  # generator / fallback

    @property
    def path(self) -> str:
        return self.url


@dataclass
class BindingFix:
    menu: Menu
    action: str
    mode: str  # add / clear
    current: set = dc_field(default_factory=set)
    expected: set = dc_field(default_factory=set)


def sample_path(path: str) -> str:
    """把 URL 正则样例化为可 resolve 的请求路径（与权限判定脚本同口径）。"""
    s = path.replace("[^/.]+", "1").replace("[^/]+", "1").replace("\\d+", "1").replace(".*", "x")
    s = re.sub(r"\(\?P<[^>]+>", "", s)
    return s.replace("(?:", "").replace(")", "").replace("?", "")


def url_to_sample(url: str) -> str:
    return sample_path("/" + url.rstrip("$"))


def path_whitelisted(sample: str, method: str) -> bool:
    return any(
        re.match(w_url, sample) and ("*" in methods or method in methods)
        for w_url, methods in settings.PERMISSION_WHITE_URL.items()
    )


def requires_permission(view_cls) -> bool:
    """视图是否走默认菜单权限链（显式 AllowAny / 空清单 / 自定义权限类不走）。"""
    permission_classes = getattr(view_cls, "permission_classes", None)
    if permission_classes is None:
        return True
    names = [getattr(item, "__name__", "") for item in permission_classes]
    return bool(permission_classes) and "IsAuthenticated" in names and "AllowAny" not in names


def ensure_urlconf_loaded():
    """确保 URLconf 模块已导入（get_all_url_dict 内部走 import_string(ROOT_URLCONF)，
    命令行/测试进程未加载 URLconf 时会 ImportError）。"""
    from django.utils.module_loading import import_module

    import_module(settings.ROOT_URLCONF)


def build_route_index():
    """扫描全量路由（仅 api/ 前缀），解析出视图、方法与是否需要权限点。"""
    from django.urls import resolve
    from django.urls.exceptions import Resolver404

    ensure_urlconf_loaded()

    routes = []
    for item in get_all_url_dict(""):
        url = str(item.get("url") or "")
        if not url.startswith("api/"):
            continue
        if url.startswith(SKIP_ROUTE_PREFIXES):
            continue
        sample = url_to_sample(url)
        resolved = None
        for candidate in (sample, f"{sample}/"):
            try:
                resolved = resolve(candidate)
                break
            except Resolver404:
                continue
        if resolved is None:
            logger.warning(f"route resolve failed, skip: {url}")
            continue
        routes.append(
            RouteInfo(
                view=str(item.get("view")),
                name=str(item.get("name")),
                url=url,
                sample=sample,
                actions=dict(getattr(resolved.func, "actions", {}) or {}),
                view_cls=getattr(resolved.func, "cls", None),
                requires_permission=requires_permission(getattr(resolved.func, "cls", None)),
            )
        )
    return routes


def load_permission_menus():
    return list(Menu.objects.filter(menu_type=Menu.MenuChoices.PERMISSION, deleted_at__isnull=True))


def find_covering(perms, path, method):
    """与运行时 get_menu_pk 同口径：精确 `path$` 优先，其次正则前缀回退。"""
    exact = f"{path}$"
    for perm in perms:
        if (perm.method or "").upper() != method:
            continue
        if perm.path == exact:
            return perm
    target = "/" + path
    for perm in perms:
        if (perm.method or "").upper() != method:
            continue
        try:
            if re.match("/" + perm.path, target):
                return perm
        except re.error:
            continue
    return None


def scan_gaps(routes, perms):
    """扫描需要权限点但未覆盖的端点：返回 [(RouteInfo, METHOD, action)]。"""
    gaps = []
    for route in routes:
        if not route.requires_permission or route.url.startswith(DEAD_ENDPOINT_PREFIXES):
            continue
        path = route.url.rstrip("$")
        for method, action in route.actions.items():
            upper = method.upper()
            if upper == "PUT":  # 生成器设计：ViewSet 忽略 PUT（前端统一用 PATCH）
                continue
            if path_whitelisted(route.sample, upper):
                continue
            if find_covering(perms, path, upper):
                continue
            gaps.append((route, upper, action))
    return gaps


def resolve_view_context(view, view_route_urls, perms, default_parent=None):
    """解析某视图的权限码后缀与父菜单（返回 (suffix, parent, source)）。

    优先复用同视图既有权限点（后缀/父菜单），保证与 UI 生成结果一致；
    无同源权限点时按 PARENT_MENU_MAP 前缀映射，最后回退 default_parent。
    """
    suffixes, parents = [], []
    for perm in perms:
        if perm.path not in view_route_urls and f"{perm.path}$" not in view_route_urls:
            continue
        if ":" in perm.name:
            suffixes.append(perm.name.split(":", 1)[1])
        if perm.parent_id:
            parents.append(perm.parent)
    if suffixes:
        suffix = Counter(suffixes).most_common(1)[0][0]
        parent = Counter(parents).most_common(1)[0][0] if parents else None
        return suffix, parent, "existing"

    for prefix, menu_name in PARENT_MENU_MAP.items():
        if not any(url.startswith(prefix) for url in view_route_urls):
            continue
        parent = Menu.objects.filter(name=menu_name, menu_type=Menu.MenuChoices.MENU, deleted_at__isnull=True).first()
        if parent:
            return parent.name, parent, "prefix-map"

    if default_parent:
        return default_parent.name, default_parent, "default"
    return None, None, "unresolved"


def build_plans(gaps, routes, perms, default_parent=None):
    """把缺口规划为待创建的权限点（不落库）。返回 (plans, unresolved)。"""
    by_view = {}
    for route, method, action in gaps:
        by_view.setdefault(route.view, []).append((route, method, action))

    ensure_urlconf_loaded()
    plans, unresolved = [], []
    for view, items in sorted(by_view.items()):
        all_urls = {r.url for r in routes if r.view == view}
        suffix, parent, source = resolve_view_context(view, all_urls, perms, default_parent)
        if not suffix:
            unresolved.extend(items)
            continue

        canonical = {}
        for entry in get_view_permissions(view, suffix):
            canonical[(entry["url"], str(entry["method"]).upper())] = entry

        for index, (route, method, action) in enumerate(sorted(items, key=lambda x: (x[0].url, x[1]))):
            entry = canonical.get((route.url, method))
            if entry:
                code = entry["code"]
                description = entry["description"] or view
                labels = list(entry.get("models") or [])
                plan_source = "generator"
            else:
                # 兜底：与 get_view_permissions 同规则的 code（视图未产出该路由时）
                code_part = action.title().replace("_", "").replace("-", "")
                code = f"{code_part[0].lower()}{code_part[1:]}:{suffix}"
                description = view
                labels = []
                plan_source = "fallback"

            model_pks = list(
                ModelLabelField.objects.filter(
                    field_type=ModelLabelField.FieldChoices.ROLE, parent=None, name__in=labels
                ).values_list("pk", flat=True)
            )
            plans.append(
                PlanItem(
                    view=view,
                    url=route.url,
                    method=method,
                    action=action,
                    code=code,
                    description=description,
                    parent_id=parent.pk if parent else None,
                    parent_name=parent.name if parent else "",
                    model_pks=model_pks,
                    rank=10000 + index,
                    source=plan_source,
                )
            )
    return plans, unresolved


def apply_plans(plans, user=None):
    """创建权限点（Menu + MenuMeta）。

    同名已存在时：路径一致但方法不同 → 修正方法（如 `batchDestroy:SystemDataMaskRule`
    种子方法误写为 DELETE，实际路由为 POST）；路径也不同 → 记冲突跳过。
    返回 (created, conflicts, method_fixed)。
    """
    created, conflicts, method_fixed = [], [], []
    with transaction.atomic():
        for plan in plans:
            existing = Menu.objects.filter(name=plan.code, deleted_at__isnull=True).first()
            if existing:
                if existing.path == plan.url and (existing.method or "").upper() != plan.method:
                    existing.method = plan.method
                    existing.save(update_fields=["method", "updated_time"])
                    method_fixed.append(existing)
                else:
                    conflicts.append(plan)
                continue
            meta = MenuMeta.objects.create(title=(plan.description or plan.code)[:250], creator=user, modifier=user)
            menu = Menu.objects.create(
                name=plan.code,
                rank=plan.rank,
                path=plan.url,
                method=plan.method,
                menu_type=Menu.MenuChoices.PERMISSION,
                parent_id=plan.parent_id,
                meta=meta,
                is_active=True,
                creator=user,
                modifier=user,
            )
            if plan.model_pks:
                menu.model.set(plan.model_pks)
            created.append(menu)
    return created, conflicts, method_fixed


def related_model_labels(view_cls):
    try:
        model = view_cls.queryset.model
    except Exception as e:  # noqa: BLE001 无 queryset 的视图（APIView/自定义）不参与绑定
        logger.debug(f"view has no queryset, skip model binding: {view_cls} {e}")
        return set()
    try:
        return set(get_related_models(model))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"get related models failed: {view_cls} {e}")
        return set()


def _role_root_pks(labels):
    return set(
        ModelLabelField.objects.filter(
            field_type=ModelLabelField.FieldChoices.ROLE, parent=None, name__in=list(labels)
        ).values_list("pk", flat=True)
    )


def plan_binding_fixes(routes, perms):
    """规划模型绑定校正：CRUD 补绑定（只增不减）；导入导出链清空绑定。"""
    route_by_path = {}
    for route in routes:
        route_by_path[route.url] = route
        route_by_path[route.url.rstrip("$")] = route
    fixes = []
    for perm in perms:
        # 权限点路径两种写法并存（种子多带 `$`，生成器产物也带），两种都试
        route = route_by_path.get(perm.path) or route_by_path.get(f"{perm.path}$")
        if not route:
            continue
        action = (route.actions or {}).get((perm.method or "").lower())
        if not action:
            continue
        current = set(perm.model.values_list("pk", flat=True))
        if action in CRUD_BIND_ACTIONS:
            expected = _role_root_pks(related_model_labels(route.view_cls))
            if expected - current:
                fixes.append(
                    BindingFix(menu=perm, action=action, mode="add", current=current, expected=current | expected)
                )
        elif action in IMPORT_EXPORT_ACTIONS and current:
            fixes.append(BindingFix(menu=perm, action=action, mode="clear", current=current, expected=set()))
    return fixes


def apply_binding_fixes(fixes):
    with transaction.atomic():
        for fix in fixes:
            fix.menu.model.set(fix.expected)
    return len(fixes)


def audit_permission_menus(routes, perms):
    """报告：未匹配任何路由的权限点 / 重复的 (path, method)。

    匹配用「样例化为真实请求路径」的路由地址（正则原文含 `(?P<pk>...)`，
    不能直接做字符串/正则比较）。
    """
    unmatched, duplicates, exempted, known_duplicates = [], [], [], []
    seen = {}
    for perm in perms:
        key = (perm.path, (perm.method or "").upper())
        if key in seen:
            if key in AUDIT_KNOWN_DUPLICATES:
                known_duplicates.append(perm)
            else:
                duplicates.append(perm)
        else:
            seen[key] = perm

        if perm.path.startswith(AUDIT_SKIP_PREFIXES):
            exempted.append(perm)
            continue

        method = (perm.method or "").upper()
        matched = False
        for route in routes:
            if method and method not in {item.upper() for item in route.actions}:
                continue
            sample = route.sample
            if perm.path in (sample.lstrip("/"), f"{sample.lstrip('/')}$"):
                matched = True
                break
            try:
                if re.match("/" + perm.path, sample) or re.match("/" + perm.path, f"{sample}/"):
                    matched = True
                    break
            except re.error:
                continue
        if not matched:
            unmatched.append(perm)
    return unmatched, duplicates, exempted, known_duplicates


def grant_to_roles(created_menus, perms, parent_of_created):
    """把新建权限点授予「已拥有同模块权限点且拥有父菜单」的角色（可选，默认不执行）。"""
    granted = []
    for menu in created_menus:
        parent_id = parent_of_created.get(menu.pk)
        sibling_ids = [perm.pk for perm in perms if perm.parent_id == parent_id]
        if not sibling_ids:
            continue
        roles = UserRole.objects.filter(is_active=True, menu__pk__in=sibling_ids)
        if parent_id:
            roles = roles.filter(menu__pk=parent_id)
        for role in roles.distinct():
            role.menu.add(menu)
            granted.append((role.name, menu.name))
    return granted


def dump_entries(model, objs, exclude_fields=()):
    """把模型实例序列化成 loaddata 兼容的 JSON 结构（与 dump_init_json 同口径）。"""
    from django.core import serializers

    fields = [item.name for item in model._meta.get_fields() if item.name not in exclude_fields]
    return json.loads(serializers.serialize("json", list(objs), fields=fields))


def detect_indent(text, default=1):
    match = re.search(r"\n( +)\S", text)
    return len(match.group(1)) if match else default


def seed_entry_pks(file_path):
    """读取种子文件已有条目的 pk 集合（不存在时返回空集）。"""
    path = Path(file_path)
    if not path.exists():
        return set()
    return {entry["pk"] for entry in json.loads(path.read_text(encoding="utf8"))}


def merge_seed_file(file_path, entries, normalize_creator=True):
    """把条目合并进 loadjson 种子文件（按 pk 原地替换、新条目追加，保持既有缩进）。"""
    path = Path(file_path)
    text = path.read_text(encoding="utf8") if path.exists() else "[]"
    indent = detect_indent(text)
    data = json.loads(text)
    index = {entry["pk"]: position for position, entry in enumerate(data)}

    added = updated = 0
    for entry in entries:
        if normalize_creator:
            entry["fields"]["creator"] = 1
            entry["fields"]["modifier"] = 1
        if entry["pk"] in index:
            data[index[entry["pk"]]] = entry
            updated += 1
        else:
            data.append(entry)
            added += 1

    with path.open("w", encoding="utf8") as fh:
        json.dump(data, fh, indent=indent, ensure_ascii=False)
        fh.write("\n")
    return {"file": str(path), "added": added, "updated": updated}
