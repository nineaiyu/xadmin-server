#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : menu
# author : ly_13
# date : 6/6/2023
from typing import Any

from django.db.models.signals import post_save
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema
from rest_framework.decorators import action

from common.base.decorators import temporary_disable_signal
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
from common.core.url_inventory import get_all_url_dict
from common.swagger.utils import get_default_response_schema
from identity.services import invalidate_menu_user_caches
from system.models import Menu
from system.serializers.menu import MenuSerializer
from system.services import permission_sync as sync
from system.signal_handler import clean_cache_handler
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

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "pks": build_array_type(build_basic_type(OpenApiTypes.STR) or {}),
                    "fields": build_object_type(),
                },
                required=["pks", "fields"],
                description="批量更新：主键列表 + 待修改字段（白名单校验）",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="batch-update")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def batch_update(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """批量修改菜单

        权限点与菜单/目录的启停语义不同：权限点代表动作授权，由角色与字段权限
        链路独立消费，停用权限点会让授权面静默失效。两类节点混批一律拒绝，
        避免前端级联或第三方客户端把权限点随目录一起停用。
        """
        payload = request.data if isinstance(request.data, dict) else {}
        pks = payload.get("pks") or []
        if isinstance(pks, (list, tuple)) and pks:
            types = set(Menu.objects.filter(pk__in=pks).values_list("menu_type", flat=True))
            if Menu.MenuChoices.PERMISSION in types and len(types) > 1:
                return ApiResponse(
                    code=1004,
                    detail=_("Permission codes cannot be batch updated together with menus or directories"),
                )
        return super().batch_update(request, *args, **kwargs)

    def get_recycle_restore_queryset(self, pks: Any) -> Any:
        """成组恢复：目录删除时后代被标记同一 deleted_at，按时间戳成组恢复。"""
        selected = self.filter_queryset(Menu.all_objects.filter(deleted_at__isnull=False, pk__in=pks))
        timestamps = list(selected.values_list("deleted_at", flat=True))
        return Menu.all_objects.filter(deleted_at__in=timestamps)

    def get_recycle_purge_queryset(self, pks: Any) -> Any:
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
    @action(methods=["get"], detail=False, url_path="api-url")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def api_url(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """获取后端API列表

        仅业务接口路由，供菜单/权限点配置选择
        """
        urls = [
            item
            for item in get_all_url_dict("")
            if (url := str(item.get("url") or "")).startswith(API_URL_PREFIX_WHITELIST)
            and not url.startswith(API_URL_INFRA_PROXY_PREFIXES)
        ]
        return ApiResponse(data=urls)

    @staticmethod
    def _suggest_permission_code(suffix: Any, action: Any) -> Any:
        """按「批量生成权限」同一 code 规则给出建议权限码（无法归属视图时为空）。"""
        if not suffix or not action:
            return ""
        code = action.title().replace("_", "").replace("-", "")
        return f"{code[0].lower()}{code[1:]}:{suffix}"

    def _serialize_gap_items(self, gaps: Any, routes: Any, perms: Any) -> Any:
        """正向缺口：代码有路由、库内无权限点。

        ``resolve_view_context`` 逐视图解析一次后缀与父菜单（同源权限点优先），
        避免逐条缺口查库；缺口仅在补权限前出现，正常库为空。
        """
        by_view: dict[str, list[Any]] = {}
        for route, method, gap_action in gaps:
            by_view.setdefault(route.view, []).append((route, method, gap_action))
        view_urls: dict[str, set[Any]] = {}
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
    def _serialize_perm_items(perms: Any, problem: Any, suggestion: Any) -> Any:
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
    @action(methods=["get"], detail=False, url_path="permission-audit")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def permission_audit(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """菜单权限检测

        只读报告代码路由与库内权限点之间的四类问题

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
        custom_action = self._serialize_perm_items(sync.audit_custom_permissions(perms), "custom_action", "verify")
        field_unconfigured = self._serialize_field_audit_items(sync.audit_field_permissions())
        return ApiResponse(
            data={
                "summary": {
                    "missing": len(missing),
                    "orphan": len(orphan),
                    "duplicate": len(duplicate),
                    "custom_action": len(custom_action),
                    "field_unconfigured": len(field_unconfigured),
                    "total": len(missing) + len(orphan) + len(duplicate) + len(field_unconfigured),
                    "routes": len(routes),
                    "permissions": len(perms),
                },
                "missing": missing,
                "orphan": orphan,
                "duplicate": duplicate,
                "custom_action": custom_action,
                "field_unconfigured": field_unconfigured,
            }
        )

    @staticmethod
    def _serialize_field_audit_items(items: Any) -> Any:
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

    def _build_permission_items(self, instance: Any, permissions: Any, skip_existing: Any) -> Any:
        """构建待写入权限点（内核在 permission_sync.generate，此处薄委托）。

        预览与执行共用同一构建（所见即所得）；保留同名私有方法供既有用例
        脱离 HTTP 层直驱视图集。
        """
        return sync.build_permission_items(instance, permissions, skip_existing)

    @temporary_disable_signal(post_save, receiver=clean_cache_handler, sender=Menu)
    def _save_permission_items(self, items: Any) -> Any:
        """批量落库权限点（内核在 permission_sync.generate；请求用户经此注入审计字段）。"""
        user = getattr(self.request, "user", None)
        if user is not None and not user.is_authenticated:
            user = None
        return sync.save_permission_items(items, user)

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
    @action(methods=["post"], detail=True, url_path="permissions")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def permissions(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """自动添加API权限"""
        views = request.data.get("views")
        component = request.data.get("component")
        skip_existing = request.data.get("skip_existing")
        dry_run = request.data.get("dry_run")
        if isinstance(views, list) and len(views) > 0:
            instance = self.get_object()
            items = []
            matched_views = 0
            for view in views:
                code_suffix = view.split(".")[-1].replace("ViewSet", " ").replace("APIView", " ")
                if len(views) == 1 and component:
                    code_suffix = component
                permissions = get_view_permissions(view, code_suffix)
                if not permissions:
                    continue
                matched_views += 1
                items.extend(self._build_permission_items(instance, permissions, skip_existing))

            # 视图名过期/拼错时 get_view_permissions 返回空：不能以「0 条新增」静默成功，
            # 否则管理员以为权限点已生成。全部视图零匹配才报错；匹配到但被 skip_existing
            # 全部跳过仍属正常幂等结果。
            if matched_views == 0:
                return ApiResponse(code=1001, detail=_("No matched api views for permission generation"))

            if dry_run:
                return ApiResponse(data=sync.serialize_permission_items(items))

            saved = self._save_permission_items(items)
            # 保存数据，触发刷新缓存信号（父菜单）；本次变更的子权限点一并精确失效
            instance.save(update_fields=["is_active"])
            invalidate_menu_user_caches([instance, *saved])
            return ApiResponse()
        return ApiResponse(code=1001, detail=_("No matched api views for permission generation"))
