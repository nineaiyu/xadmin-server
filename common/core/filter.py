#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : filter
# author : ly_13
# date : 6/2/2023
from types import SimpleNamespace

from django.conf import settings
from django.core.cache import cache
from django.db.models import (
    Q,
    QuerySet,
)
from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from rest_framework.exceptions import NotAuthenticated
from rest_framework.exceptions import ValidationError as RestValidationError
from rest_framework.filters import BaseFilterBackend

from common.base.magic import count_sql_queries, timeit
from common.cache.storage import CommonResourceIDsCache
from common.contracts import DataPermission, DeptInfo, apply_grant_row_scope
from common.core.data_scope import ScopeResult, combine, compile_grant
from common.utils import get_logger

logger = get_logger(__name__)

# 授权池缓存：版本号在数据权限 / 部门 / 授权关系变更时自增（system/signal_handler.py 与
# identity/signal_handler.py），
# 让既有缓存条目立即不可达；TTL 仅兜底。缓存后端异常时静默回落直查，不影响权限结果。
GRANTS_CACHE_VERSION_KEY = "data_permission_grants_version"
GRANTS_CACHE_TTL = 300


def invalidate_data_permission_grants_cache():
    """失效授权池缓存（数据权限 / 部门 / 用户或部门-授权关系变更时调用）。"""
    try:
        cache.incr(GRANTS_CACHE_VERSION_KEY)
    except ValueError:
        # 版本键不存在（尚未初始化 / 被清理）：直接写 2，让可能存在的版本 1 条目全部不可达
        try:
            cache.set(GRANTS_CACHE_VERSION_KEY, 2, None)
        except Exception:
            logger.warning("reset data permission grants cache version failed", exc_info=True)
    except Exception:
        logger.warning("invalidate data permission grants cache failed", exc_info=True)


def _grants_cache_version():
    """当前授权池缓存版本号；缓存不可用时返回 None（调用方跳过缓存直查）。"""
    try:
        version = cache.get(GRANTS_CACHE_VERSION_KEY)
    except Exception:
        logger.warning("read data permission grants cache version failed", exc_info=True)
        return None
    if version is None:
        version = 1
        try:
            cache.set(GRANTS_CACHE_VERSION_KEY, version, None)
        except Exception:
            logger.warning("init data permission grants cache version failed", exc_info=True)
    return version


def _load_grants(user_obj, dq):
    """加载授权池（部门祖先链授权 + 个人授权）。

    单次 OR 查询取代旧实现的「部门 / 个人」两次查询；结果按
    (版本, 用户, 部门, 菜单) 短缓存，命中时零 SQL。同一授权行同时命中部门与个人时由
    distinct 去重——同池 OR 合并对重复授权不敏感，编译结果与旧实现等价。
    """
    dept = user_obj.dept
    dept_pk = dept.pk if dept and dept.pk else ""
    menu_pk = getattr(user_obj, "menu", None) or ""
    version = _grants_cache_version()
    cache_key = f"data_permission_grants_{version}_{user_obj.pk}_{dept_pk}_{menu_pk}"
    if version is not None:
        try:
            cached = cache.get(cache_key)
        except Exception:
            cached = None
            logger.warning("read data permission grants cache failed", exc_info=True)
        if cached is not None:
            # rules / mode_type 是编译所需全部字段：构造轻量对象，避免逐行取 ORM 实例
            return [SimpleNamespace(rules=item["rules"], mode_type=item["mode_type"]) for item in cached]

    conditions = Q(userinfo=user_obj)
    if dept_pk:
        # 递归取祖先链（含自身，树缓存 60s），仅启用部门上的授权生效
        chain = [str(pk) for pk in DeptInfo.recursion_dept_info(dept_pk, is_parent=True)]
        active_chain = [
            str(pk) for pk in DeptInfo.objects.filter(pk__in=chain, is_active=True).values_list("pk", flat=True)
        ]
        if active_chain:
            conditions |= Q(deptinfo__in=active_chain)
    grants = list(DataPermission.objects.filter(is_active=True).filter(conditions).filter(dq).distinct())
    if version is not None:
        try:
            cache.set(cache_key, [{"rules": g.rules, "mode_type": g.mode_type} for g in grants], GRANTS_CACHE_TTL)
        except Exception:
            logger.warning("cache data permission grants failed", exc_info=True)
    return grants


@timeit
@count_sql_queries
def get_filter_queryset(queryset: QuerySet, user_obj, extra_grants=None):
    """数据权限过滤入口（薄壳；规则编译与代数在 common/core/data_scope/ 包）。

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

    # 部门授权与个人授权同池「或」合并（单次查询 + 版本化缓存，见 _load_grants）
    grants = _load_grants(user_obj, dq)
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


def assert_within_data_scope(queryset, user_obj, message) -> None:
    """写侧载荷范围校验：目标对象/归属值必须在数据权限可见范围内。

    与读侧同源（``get_filter_queryset``）：可见即可写、不可见即拒（fail-closed）——
    对象级写侧已由 ``get_object()`` → ``filter_queryset`` 统一拦截（按 pk 定位越界 404），
    本函数补「载荷级」面：创建时的归属字段、改归属、关系字段赋值。

    空目标（无值/空数组）由调用方先行短路；超管与 ``PERMISSION_DATA_ENABLED``
    关闭时由 ``get_filter_queryset`` 自身直通，行为与既有读侧一致。
    """
    if user_obj is None:
        raise RestValidationError(message)
    allowed = get_filter_queryset(queryset, user_obj)
    if allowed is None or not allowed.exists():
        raise RestValidationError(message)


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
        queryset = get_filter_queryset(queryset, request.user)
        # 应用行级授权：AND 叠加在数据权限之后（超管 owner 同样生效）
        return apply_grant_row_scope(request, queryset)


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


# 实现拆至 common.core.controlled_lookup：经模块级 __getattr__ 延迟再导出（保持调用面，避免循环导入）。
_MOVED_EXPORTS = ("ControlledLookupFilterBackend", "PkMultipleChoiceField", "PkMultipleFilter")


def __getattr__(name):
    if name in _MOVED_EXPORTS:
        from importlib import import_module

        return getattr(import_module("common.core.controlled_lookup"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
