#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : modelset
# author : ly_13
# date : 12/24/2023
from django.db.models import Count
from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_object_type, build_basic_type, build_array_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiRequest
from rest_framework.decorators import action

from common.core.config import SysConfig, UserConfig
from common.core.filter import get_filter_queryset
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from system.models import UserRole, DataPermission, SystemConfig
from system.utils.permission_preview import get_dept_preview, get_role_preview, get_user_preview, run_data_trial


def _extract_pks(items):
    """归一化 empower 入参主键：兼容 ``[{"pk": x}, ...]`` 与 ``["x", ...]`` 两种形态。

    历史实现直接对每个元素取 ``.get("pk")``，一旦调用方按 OpenAPI 声明的字符串数组
    传参就会 AttributeError 500；此处统一容错，两类入参都能正确解析。
    """
    pks = []
    for item in items:
        if isinstance(item, dict):
            pk = item.get("pk") or item.get("id")
        else:
            pk = item
        if pk not in (None, ""):
            pks.append(pk)
    return pks


class ChangeRolePermissionAction(object):
    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                required=["roles", "rules"],
                properties={
                    "roles": build_array_type(build_object_type(properties={"pk": build_basic_type(OpenApiTypes.STR)})),
                    "rules": build_array_type(build_object_type(properties={"pk": build_basic_type(OpenApiTypes.STR)})),
                },
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=True)
    def empower(self, request, *args, **kwargs):
        """给{cls}分配角色-数据权限"""
        instance = self.get_object()
        roles = request.data.get("roles")
        rules = request.data.get("rules")
        if roles is not None or rules is not None:
            # 非法入参返回可读业务失败，而不是在遍历时抛 500
            if (roles is not None and not isinstance(roles, (list, tuple))) or (
                rules is not None and not isinstance(rules, (list, tuple))
            ):
                return ApiResponse(code=1004, detail=_("Operation failed. Abnormal data"))
            if roles is not None:
                instance.roles.set(
                    get_filter_queryset(UserRole.objects.filter(pk__in=_extract_pks(roles)), request.user).all()
                )
            if rules is not None:
                # 数据权限按「或」合并（取最宽生效），无需附加模式开关
                instance.rules.set(DataPermission.objects.filter(pk__in=_extract_pks(rules)).all())
            return ApiResponse()
        return ApiResponse(code=1004, detail=_("Operation failed. Abnormal data"))


class PermissionPreviewAction(object):
    """用户权限预览（可见菜单/API 码/数据权限规则解码/字段权限矩阵 + 实时试算）。

    取数全部直查 DB，不经过 24h/10s 权限缓存，确保反映当前配置
    （详见 system/utils/permission_preview.py 模块注释）。
    """

    @extend_schema(request=None, responses=get_default_response_schema())
    @action(methods=["get"], detail=True, url_path="preview")
    def preview(self, request, *args, **kwargs):
        """获取{cls}的权限预览"""
        return ApiResponse(data=get_user_preview(self.get_object()))

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                required=["model"],
                properties={
                    "model": build_basic_type(OpenApiTypes.STR),
                    "menu": build_basic_type(OpenApiTypes.STR),
                },
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=True, url_path="preview/trial")
    def preview_trial(self, request, *args, **kwargs):
        """试算{cls}的数据权限过滤（命中行数 + 最终 SQL）

        draft（可选）：未保存的规则草稿 {rules, mode_type, menu}，用于配置页即时验证影响面；
        草稿经写入侧同一套 validate_rules，不落库。
        """
        return ApiResponse(
            data=run_data_trial(
                self.get_object(),
                request.data.get("model"),
                request.data.get("menu") or None,
                draft=request.data.get("draft") or None,
            )
        )


class DeptPreviewAction(object):
    """部门维度授权预览（挂载角色 / 数据权限 / 字段权限 / 成员采样）。"""

    @extend_schema(request=None, responses=get_default_response_schema())
    @action(methods=["get"], detail=True, url_path="preview")
    def preview(self, request, *args, **kwargs):
        """获取{cls}的授权预览"""
        return ApiResponse(data=get_dept_preview(self.get_object(), request.user))


class RolePreviewAction(object):
    """角色授权预览（授权菜单树 / 字段权限 / 持有用户采样）。"""

    @extend_schema(request=None, responses=get_default_response_schema())
    @action(methods=["get"], detail=True, url_path="preview")
    def preview(self, request, *args, **kwargs):
        """获取{cls}的授权预览"""
        return ApiResponse(data=get_role_preview(self.get_object(), request.user))


class InvalidConfigCacheAction(object):
    @extend_schema(request=None, responses=get_default_response_schema())
    @action(methods=["post"], detail=True)
    def invalid(self, request, *args, **kwargs):
        """使{cls}缓存失效"""
        instance = self.get_object()

        if isinstance(instance, SystemConfig):
            SysConfig.invalid_config_cache(key=instance.key)
            owner = "*"
        else:
            owner = instance.owner
        UserConfig(owner).invalid_config_cache(key=instance.key)
        return ApiResponse()


class AnnotateUserCountMixin(object):
    """
    部门 user_count 预聚合：列表/详情/导出会逐行序列化该字段，不加聚合时为每行一次 COUNT。

    反向查询名固定为 dept_query —— UserInfo.dept 显式声明了 related_query_name，
    覆盖了默认的模型名小写，写成 userinfo 会抛 FieldError。

    仅在会逐行序列化的 action 生效，避免 annotate 影响 update/delete 等写操作。
    """

    def get_queryset(self):
        queryset = super().get_queryset()
        if getattr(self, "action", None) in getattr(self, "auto_prefetch_actions", ()):
            # annotate 产生 GROUP BY 后 Django 不再套用 Meta.ordering，需显式补回，
            # 否则分页顺序不稳定；前端传 ordering 时 OrderingFilter 会在其后覆盖
            ordering = queryset.query.order_by or queryset.model._meta.ordering
            queryset = queryset.annotate(user_count=Count("dept_query"))
            if ordering:
                queryset = queryset.order_by(*ordering)
        return queryset
