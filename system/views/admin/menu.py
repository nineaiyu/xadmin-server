#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : menu
# author : ly_13
# date : 6/6/2023
from django.db import transaction
from django.db.models.signals import post_save
from django.utils import timezone
from django_filters import rest_framework as filters
from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema
from rest_framework.decorators import action

from common.base.magic import temporary_disable_signal
from common.core.filter import BaseFilterSet
from common.core.modelset import (
    BaseModelSet,
    BatchPartialUpdateAction,
    CacheListResponseMixin,
    ChoicesAction,
    ImpactPreviewAction,
    ImportExportDataAction,
    RankAction,
    RecycleBinAction,
)
from common.core.pagination import DynamicPageNumber
from common.core.response import ApiResponse
from common.core.utils import get_all_url_dict
from common.swagger.utils import get_default_response_schema
from identity.services import invalidate_menu_user_caches
from system.models import Menu, MenuMeta, ModelLabelField
from system.serializers.menu import MenuSerializer
from system.signal_handler import clean_cache_handler
from system.utils.platform import permission_sync as sync
from system.utils.platform.menu import get_view_permissions


class MenuFilter(BaseFilterSet):
    name = filters.CharFilter(field_name="name", lookup_expr="icontains")
    component = filters.CharFilter(field_name="component", lookup_expr="icontains")
    title = filters.CharFilter(field_name="meta__title", lookup_expr="icontains")
    path = filters.CharFilter(field_name="path", lookup_expr="icontains")

    class Meta:
        model = Menu
        fields = ["name"]


#: api-url 返回层前缀白名单：仅业务接口路由（api/ 前缀）进入权限点配置面。
#: 考证（xadmin-client 唯二消费方）：权限点表单的接口下拉与「批量生成权限」的
#: 视图下拉，二者只消费业务 API 路由；文档站（api-docs）、任务监控反代
#: （api/flower，虽挂 api/ 前缀但属外部监控工具的代理入口，其权限点由种子维护、
#: 审计豁免，见 permission_sync.AUDIT_SKIP_PREFIXES）与「#」哨兵条目均非业务入口。
#: 与权限点同步内核「仅 api/ 前缀参与扫描」的取面口径一致。过滤只发生在返回层，
#: 不影响路由注册与权限点运行期校验。
API_URL_PREFIX_WHITELIST = ("api/",)
#: 白名单内仍需剔除的基础设施反代入口（非业务 API）
API_URL_INFRA_PROXY_PREFIXES = ("api/flower/",)


class MenuViewSet(
    BatchPartialUpdateAction,
    RecycleBinAction,
    BaseModelSet,
    ImpactPreviewAction,
    RankAction,
    ImportExportDataAction,
    ChoicesAction,
    CacheListResponseMixin,
):
    """菜单"""

    queryset = Menu.objects.order_by("rank").all()
    serializer_class = MenuSerializer
    pagination_class = DynamicPageNumber(1000)
    ordering_fields = ["updated_time", "name", "created_time", "rank"]
    filterset_class = MenuFilter
    # 批量更新白名单：批量启停（与行内启停同一字段口径，逐项走序列化器校验）
    batch_update_fields = ("is_active",)

    def get_recycle_restore_queryset(self, pks):
        """成组恢复：目录删除时后代被标记同一 deleted_at，按时间戳成组恢复。"""
        selected = self.filter_queryset(Menu.all_objects.filter(deleted_at__isnull=False, pk__in=pks))
        timestamps = list(selected.values_list("deleted_at", flat=True))
        return Menu.all_objects.filter(deleted_at__in=timestamps)

    def get_recycle_purge_queryset(self, pks):
        """成组清除：后代先于父级物理清除，避免父级删除后子级被外键置空悬挂。"""
        queryset = super().get_recycle_purge_queryset(pks)
        instances = []
        for directory in queryset:
            instances.extend(directory.get_deleted_descendants().order_by("-pk"))
            instances.append(directory)
        return instances

    @extend_schema(
        responses=get_default_response_schema(
            {
                "data": build_array_type(
                    build_object_type(
                        properties={
                            "name": build_basic_type(OpenApiTypes.STR),
                            "url": build_basic_type(OpenApiTypes.STR),
                        }
                    )
                )
            }
        )
    )
    @action(methods=["get"], detail=False, url_path="api-url")
    def api_url(self, request, *args, **kwargs):
        """获取后端API列表（仅业务接口路由，供菜单/权限点配置选择）"""
        urls = [
            item
            for item in get_all_url_dict("")
            if (url := str(item.get("url") or "")).startswith(API_URL_PREFIX_WHITELIST)
            and not url.startswith(API_URL_INFRA_PROXY_PREFIXES)
        ]
        return ApiResponse(data=urls)

    @staticmethod
    def _suggest_permission_code(suffix, action):
        """按「批量生成权限」同一 code 规则给出建议权限码（无法归属视图时为空）。"""
        if not suffix or not action:
            return ""
        code = action.title().replace("_", "").replace("-", "")
        return f"{code[0].lower()}{code[1:]}:{suffix}"

    def _serialize_gap_items(self, gaps, routes, perms):
        """正向缺口：代码有路由、库内无权限点。

        ``resolve_view_context`` 逐视图解析一次后缀与父菜单（同源权限点优先），
        避免逐条缺口查库；缺口仅在补权限前出现，正常库为空。
        """
        by_view: dict[str, list] = {}
        for route, method, gap_action in gaps:
            by_view.setdefault(route.view, []).append((route, method, gap_action))
        view_urls: dict[str, set] = {}
        for route in routes:
            view_urls.setdefault(route.view, set()).add(route.url)

        items = []
        for view, entries in by_view.items():
            suffix, parent, _source = sync.resolve_view_context(view, view_urls.get(view, set()), perms)
            for route, method, gap_action in entries:
                items.append(
                    {
                        "problem": "missing",
                        "code": self._suggest_permission_code(suffix, gap_action),
                        "method": method,
                        "path": route.url,
                        "menu": parent.name if parent else "",
                        "pk": None,
                        "view": view.rsplit(".", 1)[-1],
                        "suggestion": "generate",
                    }
                )
        return items

    @staticmethod
    def _serialize_perm_items(perms, problem, suggestion):
        """游离/重复权限点：字段取自库内菜单，父菜单经 select_related 预取不触发逐条查询。"""
        return [
            {
                "problem": problem,
                "code": perm.name,
                "method": (perm.method or "").upper(),
                "path": perm.path,
                "menu": perm.parent.name if perm.parent_id else "",
                "pk": str(perm.pk),
                "view": "",
                "suggestion": suggestion,
            }
            for perm in perms
        ]

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="permission-audit")
    def permission_audit(self, request, *args, **kwargs):
        """菜单权限检测：只读报告代码路由与库内权限点之间的四类问题。

        复用同步内核（``scan_gaps`` / ``audit_permission_menus`` /
        ``audit_field_permissions``），仅报告不落库：正向缺口、游离权限点（无对应路由）、
        重复权限码，以及「角色已获模型权限点但未配置字段权限」（该组合下接口输出空对象，
        见 audit 内核注释）。
        """
        routes = sync.build_route_index()
        # select_related 一次性取回父菜单，供游离/重复项直接读 name，避免 N+1
        perms = list(
            Menu.objects.filter(menu_type=Menu.MenuChoices.PERMISSION, deleted_at__isnull=True).select_related("parent")
        )
        gaps = sync.scan_gaps(routes, perms)
        unmatched, duplicates, _exempted, _known = sync.audit_permission_menus(routes, perms)

        missing = self._serialize_gap_items(gaps, routes, perms)
        orphan = self._serialize_perm_items(unmatched, "orphan", "verify")
        duplicate = self._serialize_perm_items(duplicates, "duplicate", "merge")
        field_unconfigured = self._serialize_field_audit_items(sync.audit_field_permissions())
        return ApiResponse(
            data={
                "summary": {
                    "missing": len(missing),
                    "orphan": len(orphan),
                    "duplicate": len(duplicate),
                    "field_unconfigured": len(field_unconfigured),
                    "total": len(missing) + len(orphan) + len(duplicate) + len(field_unconfigured),
                    "routes": len(routes),
                    "permissions": len(perms),
                },
                "missing": missing,
                "orphan": orphan,
                "duplicate": duplicate,
                "field_unconfigured": field_unconfigured,
            }
        )

    @staticmethod
    def _serialize_field_audit_items(items):
        """角色字段权限缺口条目：多一列 role（同一权限点可能多个角色未配置）。"""
        return [
            {
                "problem": "field_unconfigured",
                "code": menu.name,
                "method": (menu.method or "").upper(),
                "path": menu.path,
                "menu": menu.parent.name if menu.parent_id else "",
                "role": role.name,
                "pk": str(menu.pk),
                "view": "",
                "suggestion": "configure",
            }
            for role, menu in items
        ]

    def _build_permission_items(self, instance, permissions, skip_existing):
        """构造待写入的权限点（只读，不落库）。

        返回 ``(action, 已有菜单或 None, 数据)`` 列表：``action=create`` 为新建（标题前缀 C-），
        ``update`` 为覆盖既有权限点（前缀 U-）；``skip_existing`` 命中时整体跳过——
        预览与执行共用本方法，保证「所见即所得」。

        既有权限点与角色模型清单各以一次 ``__in`` 批量查询取回，避免逐权限点回表；
        meta 一并 select_related，供覆盖更新时直接改标题。
        """
        permissions = list(permissions)
        if not permissions:
            return []
        existing = {
            menu.name: menu
            for menu in self.get_queryset()
            .filter(
                menu_type=Menu.MenuChoices.PERMISSION,
                name__in=[permission.get("code") for permission in permissions],
            )
            .select_related("meta")
        }
        role_models: dict[str, list] = {}
        role_labels = {label for permission in permissions for label in permission.get("models")}
        if role_labels:
            fields = ModelLabelField.objects.filter(field_type=ModelLabelField.FieldChoices.ROLE, name__in=role_labels)
            for field in fields:
                role_models.setdefault(field.name, []).append(field)

        items = []
        rank = 10000
        for permission in permissions:
            rank += 1
            models = [field for label in permission.get("models") for field in role_models.get(label, [])]
            data = {
                "rank": rank,
                "is_active": True,
                "menu_type": Menu.MenuChoices.PERMISSION,
                "name": permission.get("code"),
                "parent": instance,
                "path": permission.get("url"),
                "method": permission.get("method"),
                "model": models,
                "meta": {"title": permission.get("description")[:250]},
            }
            permission_menu = existing.get(data["name"])
            if permission_menu and skip_existing:
                continue
            action = "update" if permission_menu else "create"
            data["meta"]["title"] = ("U-" if permission_menu else "C-") + data["meta"]["title"]
            items.append((action, permission_menu, data))
        return items

    @staticmethod
    def _serialize_permission_items(items):
        """预览载荷：逐条给出动作、权限码、接口与标题，附新建/覆盖计数。"""
        results = [
            {
                "action": action,
                "name": data["name"],
                "path": data["path"],
                "method": data["method"],
                "title": data["meta"]["title"],
            }
            for action, _target, data in items
        ]
        return {
            "results": results,
            "create_count": len([item for item in results if item["action"] == "create"]),
            "update_count": len([item for item in results if item["action"] == "update"]),
        }

    @temporary_disable_signal(post_save, receiver=clean_cache_handler, sender=Menu)
    def _save_permission_items(self, items):
        """批量落库权限点并返回落库实例（信号临时禁用，失效由调用方统一执行）。

        覆盖更新与新创建各走一支批量写入，整个落库过程原子提交。若逐条保存触发信号，
        会按每个权限点各自扫一遍用户/角色/部门（同一批内重复扫描）；改为收集实例后
        一次精确失效，保证「被覆盖更新的子权限点」也进失效集——只失效父菜单会让
        这些用户最长 24h 持旧权限（路由缓存 TTL）。creator/modifier 原由全局 pre_save
        信号按请求用户补齐，批量写入不触发信号，按同一口径显式赋值。
        """
        user = getattr(self.request, "user", None)
        if user is not None and not user.is_authenticated:
            user = None
        # 同一批出现重复权限码时按顺序收敛：覆盖更新后写生效（与逐条保存一致），新建先到先得
        updates: dict = {}
        creates: dict = {}
        for _action, permission_menu, data in items:
            if permission_menu is not None:
                updates[permission_menu.pk] = (permission_menu, data)
            else:
                creates.setdefault(data["name"], data)
        with transaction.atomic():
            saved = self._update_permission_items(list(updates.values()), user)
            saved += self._create_permission_items(list(creates.values()), user)
        return saved

    @staticmethod
    def _update_permission_items(updates, user):
        """覆盖既有权限点：菜单行与 meta 标题各一次批量 UPDATE，角色模型按差集同步。"""
        if not updates:
            return []
        updated_time = timezone.now()
        metas = []
        for permission_menu, data in updates:
            for field in ("rank", "is_active", "menu_type", "name", "parent", "path", "method"):
                setattr(permission_menu, field, data[field])
            permission_menu.modifier = user
            permission_menu.updated_time = updated_time
            meta = permission_menu.meta
            meta.title = data["meta"]["title"]
            meta.modifier = user
            meta.updated_time = updated_time
            metas.append(meta)
        menus = [menu for menu, _data in updates]
        Menu.objects.bulk_update(
            menus,
            fields=["rank", "is_active", "menu_type", "name", "parent", "path", "method", "modifier", "updated_time"],
        )
        MenuMeta.objects.bulk_update(metas, fields=["title", "modifier", "updated_time"])
        MenuViewSet._sync_permission_models([(menu, data["model"]) for menu, data in updates])
        return menus

    @staticmethod
    def _create_permission_items(creates, user):
        """新建权限点：meta 先批量落库取得主键回填 meta_id，菜单行随后批量落库。

        活跃行名称唯一约束兜底并发冲突，冲突行被数据库跳过；按主键回读实际
        落库实例，角色模型关联只建给真实存在的菜单行。
        """
        if not creates:
            return []
        dept = getattr(user, "dept", None)
        models_by_name = {data["name"]: data["model"] for data in creates}
        metas = MenuMeta.objects.bulk_create(
            [MenuMeta(title=data["meta"]["title"], creator=user, modifier=user, dept_belong=dept) for data in creates]
        )
        menus = Menu.objects.bulk_create(
            [
                Menu(
                    rank=data["rank"],
                    is_active=data["is_active"],
                    menu_type=data["menu_type"],
                    name=data["name"],
                    parent=data["parent"],
                    path=data["path"],
                    method=data["method"],
                    meta=meta,
                    creator=user,
                    modifier=user,
                    dept_belong=dept,
                )
                for data, meta in zip(creates, metas, strict=True)
            ],
            ignore_conflicts=True,
        )
        # ignore_conflicts 无法区分被跳过的冲突行，按主键回读实际落库实例
        saved = list(Menu.objects.filter(pk__in=[menu.pk for menu in menus]).select_related("meta"))
        if saved:
            Menu.model.through.objects.bulk_create(
                [
                    Menu.model.through(menu=menu, modellabelfield=field)
                    for menu in saved
                    for field in models_by_name.get(menu.name, [])
                ]
            )
        return saved

    @staticmethod
    def _sync_permission_models(menu_model_pairs):
        """把 ``[(menu, models)]`` 的角色模型关联同步进中间表（等价于逐实例 ``set()``）。

        现有关联一次读回后按差集增删，避免每权限点各查一遍。
        """
        if not menu_model_pairs:
            return
        through = Menu.model.through
        menu_ids = [menu.pk for menu, _models in menu_model_pairs]
        rows = list(through.objects.filter(menu_id__in=menu_ids).values_list("pk", "menu_id", "modellabelfield_id"))
        desired = {(menu.pk, field.pk) for menu, models in menu_model_pairs for field in models}
        current = {(menu_id, field_id) for _pk, menu_id, field_id in rows}
        stale_pks = [pk for pk, menu_id, field_id in rows if (menu_id, field_id) not in desired]
        fresh_pairs = desired - current
        if stale_pks:
            through.objects.filter(pk__in=stale_pks).delete()
        if fresh_pairs:
            through.objects.bulk_create(
                [through(menu_id=menu_id, modellabelfield_id=field_id) for menu_id, field_id in fresh_pairs]
            )

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "views": build_array_type(build_basic_type(OpenApiTypes.STR) or {}),
                    "component": build_basic_type(OpenApiTypes.STR),
                    "skip_existing": build_basic_type(OpenApiTypes.BOOL),
                    "dry_run": build_basic_type(OpenApiTypes.BOOL),
                },
                required=["views"],
                description="dry_run=true 仅预览将新建/覆盖的权限点，不落库",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=True, url_path="permissions")
    def permissions(self, request, *args, **kwargs):
        """自动添加API权限"""
        views = request.data.get("views")
        component = request.data.get("component")
        skip_existing = request.data.get("skip_existing")
        dry_run = request.data.get("dry_run")
        if isinstance(views, list) and len(views) > 0:
            instance = self.get_object()
            items = []
            for view in views:
                code_suffix = view.split(".")[-1].replace("ViewSet", " ").replace("APIView", " ")
                if len(views) == 1 and component:
                    code_suffix = component
                items.extend(
                    self._build_permission_items(instance, get_view_permissions(view, code_suffix), skip_existing)
                )

            if dry_run:
                return ApiResponse(data=self._serialize_permission_items(items))

            saved = self._save_permission_items(items)
            # 保存数据，触发刷新缓存信号（父菜单）；本次变更的子权限点一并精确失效
            instance.save(update_fields=["is_active"])
            invalidate_menu_user_caches([instance, *saved])
            return ApiResponse()
        return ApiResponse(code=1001)
