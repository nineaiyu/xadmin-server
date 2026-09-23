#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : filter
# author : ly_13
# date : 6/2/2023
from types import SimpleNamespace

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db.models import BooleanField, ManyToManyField, Q, QuerySet
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

# 授权池缓存：版本号在数据权限 / 部门 / 授权关系变更时自增（system/signal_handler.py），
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
        from system.utils.api_grant import apply_grant_row_scope

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


class ControlledLookupFilterBackend(BaseFilterBackend):
    """受控 lookup 透传：在视图已声明过滤器的字段面上放开常用 lookup。

    开启方式（显式 opt-in，未开启的视图零变化）::

        class XxxViewSet(...):
            controlled_lookup = True
            extra_filter_class = [ControlledLookupFilterBackend]

    参数形态 ``field__lookup=value``（多个条件 AND 组合）：

    - 字段白名单 = ``filterset_class`` 已声明过滤器的 ``field_name`` 集合
      （可再用视图 ``controlled_lookup_fields`` 显式追加）——不扩大字段面，只放开 lookup；
    - lookup 限定 ``exact / icontains / startswith / in / gte / lte / isnull / ne``
      （``ne`` = 取反）；**禁跨关系嵌套**（``a__b__icontains`` 直接 400）；
    - **字段可见性 fail-closed**：非超管必须命中 ``request.fields`` 字段权限白名单，
      否则 400 —— 过滤不能成为无权字段的探测侧信道；
    - 值按模型字段 ``to_python`` 转换（失败 400，不落成 500）；``in`` 为逗号分隔多值；
      M2M 字段只允许 ``exact / in / ne``；
    - 条件数上限 ``max_conditions``（防参数滥用）。
    """

    allowed_lookups = ("exact", "icontains", "startswith", "in", "gte", "lte", "isnull", "ne")
    m2m_lookups = ("exact", "in", "ne")
    max_conditions = 20

    def filter_queryset(self, request, queryset, view):
        if not getattr(view, "controlled_lookup", False):
            return queryset
        keys = [key for key in request.query_params if "__" in key]
        if not keys:
            return queryset
        if len(keys) > self.max_conditions:
            raise RestValidationError(
                _("Too many filter conditions (at most %(count)s)") % {"count": self.max_conditions}
            )
        allowed_fields = self._allowed_fields(view)
        model = queryset.model
        model_label = model._meta.label_lower
        include = Q()
        exclude = Q()
        for key in keys:
            field_name, _separator, lookup = key.rpartition("__")
            if not field_name or "__" in field_name or lookup not in self.allowed_lookups:
                raise RestValidationError(_("Unsupported filter expression: %(key)s") % {"key": key})
            model_field = self._model_field(model, field_name)
            if model_field is None or field_name not in allowed_fields:
                raise RestValidationError(_("Unsupported filter field: %(key)s") % {"key": key})
            if isinstance(model_field, ManyToManyField) and lookup not in self.m2m_lookups:
                raise RestValidationError(_("Unsupported filter expression: %(key)s") % {"key": key})
            if not self._field_visible(request, model_label, field_name):
                raise RestValidationError(_("No permission to filter by field: %(field)s") % {"field": field_name})
            value = request.query_params.get(key)
            if lookup == "ne":
                exclude &= Q(**{field_name: self._coerce(model_field, value)})
                continue
            lookup_expr = field_name if lookup == "exact" else f"{field_name}__{lookup}"
            include &= Q(**{lookup_expr: self._coerce(model_field, value, lookup)})
        if exclude:
            queryset = queryset.exclude(exclude)
        return queryset.filter(include) if include else queryset

    @staticmethod
    def _allowed_fields(view) -> set:
        filterset_class = getattr(view, "filterset_class", None)
        fields = {"pk"}  # pk 恒可用（列表接口本就返回主键，不属于字段权限收敛面）
        if filterset_class is not None:
            for filter_obj in filterset_class.get_filters().values():
                field_name = getattr(filter_obj, "field_name", "") or ""
                if field_name and "__" not in field_name:
                    fields.add(field_name)
        fields |= set(getattr(view, "controlled_lookup_fields", ()) or ())
        return fields

    @staticmethod
    def _model_field(model, field_name):
        if field_name == "pk":
            return model._meta.pk
        try:
            return model._meta.get_field(field_name)
        except FieldDoesNotExist:
            return None

    @staticmethod
    def _field_visible(request, model_label: str, field_name: str) -> bool:
        """与序列化器字段裁剪同口径：超管全量；其余按 request.fields（fail-closed）。"""
        if not settings.PERMISSION_FIELD_ENABLED:
            return True
        if field_name == "pk":
            return True
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_superuser", False):
            return True
        allowed = getattr(request, "fields", None)
        if not isinstance(allowed, dict):
            return False
        return field_name in (allowed.get(model_label) or ())

    def _coerce(self, model_field, value, lookup: str = "exact"):
        if lookup == "isnull":
            return str(value).strip().lower() in ("1", "true", "yes", "on")
        if lookup == "in":
            values = [item.strip() for item in str(value).split(",") if item.strip()]
            if not values:
                raise RestValidationError(_("Invalid filter value for %(field)s") % {"field": model_field.name})
            return [self._to_python(model_field, item) for item in values]
        return self._to_python(model_field, value)

    @staticmethod
    def _to_python(model_field, value):
        try:
            if isinstance(model_field, ManyToManyField):
                return model_field.target_field.to_python(value)
            if isinstance(model_field, BooleanField):
                # 布尔值容错：API 侧常用小写 true/false（Django 原生只认 True/False/"True"/"1"）
                normalized = str(value).strip().lower()
                if normalized in ("1", "true", "yes", "on"):
                    return True
                if normalized in ("0", "false", "no", "off"):
                    return False
                raise ValueError(f"invalid boolean: {value!r}")
            return model_field.to_python(value)
        except Exception as exc:
            raise RestValidationError(_("Invalid filter value for %(field)s") % {"field": model_field.name}) from exc


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
