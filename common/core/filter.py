#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : filter
# author : ly_13
# date : 6/2/2023
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Q, QuerySet
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from django_filters.fields import MultipleChoiceField
from rest_framework.exceptions import NotAuthenticated
from rest_framework.exceptions import ValidationError as RestValidationError
from rest_framework.filters import BaseFilterBackend

from common.base.magic import count_sql_queries, timeit
from common.cache.storage import CommonResourceIDsCache
from common.core.data_scope import ScopeResult, combine, compile_grant
from common.utils import get_logger
from system.services import DataPermission, DeptInfo

logger = get_logger(__name__)


@timeit
@count_sql_queries
def get_filter_queryset(queryset: QuerySet, user_obj, extra_grants=None):
    """数据权限过滤入口（薄壳；规则编译与代数在 common/core/data_scope.py）。

    合并语义（对齐行业「取最宽生效」）：
    - 部门祖先链（含本部门，仅启用部门）与个人授权汇入同一授权池；
    - 池内各授权组的结果统一「或」合并，组内多规则仍按授权自身 mode_type；
    - 菜单上下文：授权 menu 为空 = 通用，否则须匹配当前菜单（menu 属性由
      IsAuthenticated 在权限校验时写入 request.user）；
    - 无任何适用授权 → none()（fail-closed 保留）；
    - 「全部数据」规则 = ALLOW_ALL 哨兵，在任何层级正确生效。

    extra_grants：额外参与本次编译的授权（如试算草稿的未落库 DataPermission 实例）。
    调用方需自行保证其菜单上下文已判定；该参数不改变正常请求路径的行为。
    """
    if not settings.PERMISSION_DATA_ENABLED or queryset is None:
        return queryset

    if user_obj.is_superuser:
        logger.info(f"superuser: {user_obj.username}. return all queryset {queryset.model._meta.label_lower}")
        return queryset

    dq = Q(menu__isnull=True) | Q(menu__isnull=False, menu__pk=getattr(user_obj, "menu", None))

    grants = []
    dept = user_obj.dept
    if dept and dept.pk:
        # 递归取祖先链（含自身，树缓存 60s），仅启用部门上的授权生效
        chain = [str(pk) for pk in DeptInfo.recursion_dept_info(dept.pk, is_parent=True)]
        active_chain = [
            str(pk) for pk in DeptInfo.objects.filter(pk__in=chain, is_active=True).values_list("pk", flat=True)
        ]
        if active_chain:
            grants.extend(
                DataPermission.objects.filter(is_active=True).filter(deptinfo__in=active_chain).filter(dq).distinct()
            )
    # 个人授权与部门授权同池「或」合并
    grants.extend(DataPermission.objects.filter(is_active=True).filter(userinfo=user_obj).filter(dq))
    if extra_grants:
        # 试算草稿等临时授权：不落库，直接参与本次编译
        grants.extend(extra_grants)

    if not grants:
        logger.info(f"get filter end. {queryset.model._meta.label} : no grant, return none")
        return queryset.none()

    model = queryset.model
    results = []
    for dp in grants:
        result = compile_grant(dp, model, user_obj)
        if result is None:
            # 组内规则均与当前模型无关（或全部无效被跳过），该授权不参与
            continue
        results.append(result)
    if not results:
        return queryset.none()

    combined = combine(results)
    if combined.kind == ScopeResult.KIND_ALLOW:
        logger.info(f"{model._meta.label_lower} : all queryset")
        return queryset
    if combined.kind == ScopeResult.KIND_DENY:
        logger.info(f"get filter end. {model._meta.label} : deny all")
        return queryset.none()
    logger.info(f"get filter end. {model._meta.label} : {combined.q}")
    return queryset.filter(combined.q)


class OwnerUserFilter(BaseFilterBackend):
    def filter_queryset(self, request, queryset, view):
        if request.user and request.user.is_authenticated:
            return queryset.filter(owner=request.user)
        raise NotAuthenticated(_("Unauthorized authentication"))


class CreatorUserFilter(BaseFilterBackend):
    def filter_queryset(self, request, queryset, view):
        if request.user and request.user.is_authenticated:
            return queryset.filter(creator=request.user)
        raise NotAuthenticated(_("Unauthorized authentication"))


class BaseDataPermissionFilter(BaseFilterBackend):
    def filter_queryset(self, request, queryset, view):
        return get_filter_queryset(queryset, request.user)


class BaseFilterSet(filters.FilterSet):
    pk = filters.NumberFilter(field_name="id")
    spm = filters.CharFilter(field_name="spm", method="get_spm_filter")
    creator = filters.NumberFilter(field_name="creator")
    modifier = filters.NumberFilter(field_name="modifier")
    dept_belong = filters.UUIDFilter(field_name="dept_belong")
    created_time = filters.DateTimeFromToRangeFilter(field_name="created_time")
    updated_time = filters.DateTimeFromToRangeFilter(field_name="updated_time")
    description = filters.CharFilter(field_name="description", lookup_expr="icontains")

    def get_spm_filter(self, queryset, name, value):
        pks = CommonResourceIDsCache(value).get_storage_cache()
        if pks:
            return queryset.filter(pk__in=pks)
        # spm 缺失/过期必须 fail-closed：selected 范围绝不能静默放行为全量
        # （异步导出重放在队列积压超过 spm TTL 时，曾因此把勾选导出退化成全量导出）
        raise RestValidationError(_("Resource selection has expired, please reselect"))


class PkMultipleChoiceField(MultipleChoiceField):
    def validate(self, value):
        if self.required and not value:
            raise ValidationError(self.error_messages["required"], code="required")


class PkMultipleFilter(filters.MultipleChoiceFilter):
    """
    通过 input_type 来自定义前端展示类型
    """

    field_class = PkMultipleChoiceField

    def __init__(self, **kwargs):
        self.input_type = kwargs.pop("input_type", None)
        super().__init__(**kwargs)
