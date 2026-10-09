#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : modelset
# author : ly_13
# date : 12/24/2023
from typing import TYPE_CHECKING, Any

from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema
from rest_framework.decorators import action

from common.core.config import SysConfig, UserConfig
from common.core.filter import get_filter_queryset
from common.core.modelset import RelationCountMixin
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from identity.services import UserRole
from system.models import DataPermission, SystemConfig
from system.utils.platform.permission_preview import (
    get_dept_preview,
    get_post_preview,
    get_role_preview,
    get_user_preview,
    run_data_trial,
    run_field_trial,
)


def _extract_pks(items: Any) -> Any:
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


class ChangeRolePermissionAction:
    if TYPE_CHECKING:  # 宿主 ViewSet 提供的接口（mixin 模式）

        def get_object(self, *args: Any, **kwargs: Any) -> Any: ...

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
    @action(methods=["post"], detail=True)  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def empower(self, request: Any, *args: Any, **kwargs: Any) -> Any:
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
                # 数据权限按「或」合并（取最宽生效），无需附加模式开关；
                # 与 roles 同口径过行级数据权限：可指派的规则必须在调用者取值域内，
                # 否则持有 empower 权限点但无数据权限管理权限的用户可挂任意全量规则
                instance.rules.set(
                    get_filter_queryset(DataPermission.objects.filter(pk__in=_extract_pks(rules)), request.user).all()
                )
            return ApiResponse()
        return ApiResponse(code=1004, detail=_("Operation failed. Abnormal data"))


class PermissionPreviewAction:
    if TYPE_CHECKING:  # 宿主 ViewSet 提供的接口（mixin 模式）

        def get_object(self, *args: Any, **kwargs: Any) -> Any: ...

    """用户权限预览（可见菜单/API 码/数据权限规则解码/字段权限矩阵 + 实时试算）。

    取数全部直查 DB，不经过 24h/10s 权限缓存，确保反映当前配置
    （详见 system/utils/platform/permission_preview/ 包模块注释）。
    """

    @extend_schema(request=None, responses=get_default_response_schema())
    @action(methods=["get"], detail=True, url_path="preview")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def preview(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """获取{cls}的权限预览"""
        return ApiResponse(data=get_user_preview(self.get_object()))

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "scope": build_basic_type(OpenApiTypes.STR),
                    "model": build_basic_type(OpenApiTypes.STR),
                    "menu": build_basic_type(OpenApiTypes.STR),
                    "draft": build_basic_type(OpenApiTypes.OBJECT),
                },
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=True, url_path="preview/trial")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def preview_trial(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """试算{cls}的数据权限/字段权限（只读 dry-run，不落库）

        scope=data（默认）：数据权限试算 → 命中行数 + 样本行 + 授权诊断 + 最终 SQL；
        scope=field：字段权限试算 → 指定菜单下的生效字段矩阵（含未配置=裁空提示）。
        draft（可选，两作用域通用）：未保存的草稿（数据规则 {rules, mode_type, menu} /
        字段白名单 {fields}），经写入侧同一套校验，仅参与本次试算。
        """
        draft = request.data.get("draft") or None
        if (request.data.get("scope") or "data") == "field":
            return ApiResponse(data=run_field_trial(self.get_object(), request.data.get("menu") or None, draft=draft))
        return ApiResponse(
            data=run_data_trial(
                self.get_object(),
                request.data.get("model"),
                request.data.get("menu") or None,
                draft=draft,
            )
        )


class DeptPreviewAction:
    if TYPE_CHECKING:  # 宿主 ViewSet 提供的接口（mixin 模式）

        def get_object(self, *args: Any, **kwargs: Any) -> Any: ...

    """部门维度授权预览（挂载角色 / 数据权限 / 字段权限 / 成员采样）。"""

    @extend_schema(request=None, responses=get_default_response_schema())
    @action(methods=["get"], detail=True, url_path="preview")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def preview(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """获取{cls}的授权预览"""
        return ApiResponse(data=get_dept_preview(self.get_object(), request.user))


class RolePreviewAction:
    if TYPE_CHECKING:  # 宿主 ViewSet 提供的接口（mixin 模式）

        def get_object(self, *args: Any, **kwargs: Any) -> Any: ...

    """角色授权预览（授权菜单树 / 字段权限 / 持有用户采样）。"""

    @extend_schema(request=None, responses=get_default_response_schema())
    @action(methods=["get"], detail=True, url_path="preview")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def preview(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """获取{cls}的授权预览"""
        return ApiResponse(data=get_role_preview(self.get_object(), request.user))


class PostPreviewAction:
    if TYPE_CHECKING:  # 宿主 ViewSet 提供的接口（mixin 模式）

        def get_object(self, *args: Any, **kwargs: Any) -> Any: ...

    """岗位维度预览（岗位信息 / 持有用户采样；岗位不参与权限判定，无授权段）。"""

    @extend_schema(request=None, responses=get_default_response_schema())
    @action(methods=["get"], detail=True, url_path="preview")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def preview(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """获取{cls}的授权预览"""
        return ApiResponse(data=get_post_preview(self.get_object(), request.user))


class InvalidConfigCacheAction:
    if TYPE_CHECKING:  # 宿主 ViewSet 提供的接口（mixin 模式）

        def get_object(self, *args: Any, **kwargs: Any) -> Any: ...

    def _invalidate_config_cache(self, instance: Any) -> None:
        """按实例类型清理对应配置缓存（invalid 动作与 destroy 删除前复用同一份逻辑）。"""
        if isinstance(instance, SystemConfig):
            SysConfig.invalid_config_cache(key=instance.key)
            # 系统级变更还要清「全部用户」对该 key 的个人缓存：owner="*" 是
            # UserConfig 的通配哨兵（px="user_*"，del_many 按模式批删）——个人
            # 缓存里可能固化过含系统回退的合成值
            owner = "*"
        else:
            owner = instance.owner
        UserConfig(owner).invalid_config_cache(key=instance.key)

    @extend_schema(request=None, responses=get_default_response_schema())
    @action(methods=["post"], detail=True)  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def invalid(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """使{cls}缓存失效"""
        self._invalidate_config_cache(self.get_object())
        return ApiResponse()


# 部门 user_count 预聚合的语义化别名（起由通用 RelationCountMixin +
# DeptSerializer.relation_count_fields（Count("dept_query")）驱动），保留本名以兼容
# 既有视图与测试的导入路径。反向查询名固定为 dept_query —— UserInfo.dept 显式声明了
# related_query_name，覆盖了默认的模型名小写，写成 userinfo 会抛 FieldError。
AnnotateUserCountMixin = RelationCountMixin
