#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""通用标签中心（P-1）：白名单资源解析 + 打标读写 + 列表过滤。

- 白名单：``system.models.tag.TAGGABLE_MODELS``（"app_label.model" 小写 → 展示名）；
- 读写形态：``TaggedItem`` 通过 ``content_type + object_id`` 关联目标对象，
  目标模型侧 ``GenericRelation`` 支持 ``prefetch_related("tagged_items__tag")``；
- 打标权限回落业务对象的 update 权限点（由调用方/视图声明），标签本身的 CRUD 走
  独立 4 个权限点；
- 过滤：``?tag=<id|name>``（多值 AND 语义），与数据权限编译器叠加。
"""

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger

logger = get_logger(__name__)

#: 一次最多替换的标签数（防误传超大列表）
MAX_TAGS_PER_OBJECT = 20


def taggable_model(resource: str):
    """资源键 → 模型类（非白名单返回 None，fail-closed）。"""
    from django.apps import apps

    from system.models.tag import TAGGABLE_MODELS

    key = str(resource or "").strip().lower()
    if key not in TAGGABLE_MODELS:
        return None
    try:
        return apps.get_model(key)
    except LookupError:  # pragma: no cover - 白名单与模型注册不一致时的兜底
        logger.warning("taggable model not found: %s", key)
        return None


def resource_key(model) -> str:
    return f"{model._meta.app_label}.{model._meta.model_name}".lower()


def taggable_resources() -> list:
    """白名单资源清单（前端选择器数据源）。"""
    from system.models.tag import TAGGABLE_MODELS

    return [{"key": key, "label": str(meta["label"])} for key, meta in TAGGABLE_MODELS.items()]


def taggable_visit_path(model) -> str:
    """对象打标所需的业务权限路径模板（白名单外返回空串）。"""
    from system.models.tag import TAGGABLE_MODELS

    meta = TAGGABLE_MODELS.get(resource_key(model)) or {}
    return str(meta.get("visit") or "")


def ensure_tag_permission(user, model, pk) -> None:
    """打标权限校验：回落业务对象的 update 权限点（fail-closed）。"""
    from system.utils.ai_actions import user_can_visit

    visit = taggable_visit_path(model)
    if not visit:
        raise DjangoValidationError(_("The object type cannot be tagged"))
    if not user_can_visit(user, "PATCH", visit.replace("<pk>", str(pk))):
        raise DjangoValidationError(_("You do not have permission to tag this object"))


def tag_brief(tag) -> dict:
    return {"pk": str(tag.pk), "name": tag.name, "color": tag.color or ""}


def tags_for_instance(obj) -> list:
    """单对象标签列表（优先走预取缓存，列表页零 N+1）。"""
    prefetched = getattr(obj, "_prefetched_objects_cache", None)
    if prefetched is not None and "tagged_items" in prefetched:
        return [tag_brief(item.tag) for item in prefetched["tagged_items"] if item.tag_id]
    return [tag_brief(item.tag) for item in obj.tagged_items.select_related("tag").all() if item.tag_id]


def object_tags(model, pk) -> list:
    """按主键取标签（详情/打标后回显）。"""
    from system.models.tag import TaggedItem

    if not pk:
        return []
    content_type = ContentType.objects.get_for_model(model)
    rows = (
        TaggedItem.objects.filter(content_type=content_type, object_id=str(pk))
        .select_related("tag")
        .order_by("tag__name")
    )
    return [tag_brief(row.tag) for row in rows if row.tag_id]


def set_object_tags(model, pk, tag_pks, user=None) -> list:
    """全量替换对象标签（返回最新标签列表）；标签不存在即拒绝（fail-closed）。"""
    from django.db import transaction

    from system.models.tag import Tag, TaggedItem

    if pk in (None, ""):
        raise DjangoValidationError(_("The target object is required"))
    wanted = [str(item) for item in (tag_pks or []) if str(item or "").strip()]
    if len(wanted) > MAX_TAGS_PER_OBJECT:
        raise DjangoValidationError(_("Too many tags (max {})").format(MAX_TAGS_PER_OBJECT))
    tags = list(Tag.objects.filter(pk__in=wanted))
    missing = set(wanted) - {str(tag.pk) for tag in tags}
    if missing:
        raise DjangoValidationError(_("Some tags no longer exist; refresh and retry"))
    content_type = ContentType.objects.get_for_model(model)
    with transaction.atomic():
        TaggedItem.objects.filter(content_type=content_type, object_id=str(pk)).delete()
        TaggedItem.objects.bulk_create(
            [
                TaggedItem(
                    tag=tag,
                    content_type=content_type,
                    object_id=str(pk),
                    creator=user if getattr(user, "pk", None) else None,
                )
                for tag in tags
            ]
        )
    return object_tags(model, pk)


def _ids_for_token(model, token: str) -> set:
    """单个过滤词（标签主键或名称）→ 该模型下已打标对象的 id 集合。"""
    from system.models.tag import Tag, TaggedItem

    token = str(token or "").strip()
    if not token:
        return set()
    tag = Tag.objects.filter(pk=token).first() if _looks_like_pk(token) else None
    if tag is None:
        tag = Tag.objects.filter(name=token).first()
    if tag is None:
        return set()
    content_type = ContentType.objects.get_for_model(model)
    return set(TaggedItem.objects.filter(content_type=content_type, tag=tag).values_list("object_id", flat=True))


def _looks_like_pk(token: str) -> bool:
    return len(token) >= 32 and "-" in token


def filter_by_tags(queryset, model, tokens: list):
    """按标签过滤（多词 AND 语义）：未命中任何标签时返回空集（而不是全量）。"""
    ids = None
    for token in tokens:
        current = _ids_for_token(model, token)
        ids = current if ids is None else (ids & current)
    if ids is None:
        return queryset
    return queryset.filter(pk__in=list(ids))


def tag_choice_options(limit: int = 200) -> list:
    """标签下拉选项（元数据 choices 数据源；60s 短缓存，避免每次请求查表）。"""
    from django.core.cache import cache

    from system.models.tag import Tag

    key = f"tag_choice_options_{limit}"
    try:
        cached = cache.get(key)
    except Exception:  # noqa: BLE001 缓存不可用直接查库
        cached = None
    if cached is not None:
        return cached
    data = [(tag.name, tag.name) for tag in Tag.objects.all()[:limit]]
    try:
        cache.set(key, data, 60)
    except Exception:  # noqa: BLE001
        logger.debug("cache tag options failed", exc_info=True)
    return data


def invalidate_tag_options_cache() -> None:
    """标签定义变更后失效下拉缓存（新建/改名/删除后元数据立即反映）。"""
    from django.core.cache import cache

    for limit in (200,):
        try:
            cache.delete(f"tag_choice_options_{limit}")
        except Exception:  # noqa: BLE001
            logger.debug("invalidate tag options failed", exc_info=True)


def filter_by_tag_name(queryset, value):
    """django-filter 方法体：``value`` 支持逗号分隔多标签（AND 语义）。"""
    tokens = [item.strip() for item in str(value or "").split(",") if item.strip()]
    if not tokens:
        return queryset
    return filter_by_tags(queryset, queryset.model, tokens)


class TagChoiceFilter:
    """标签筛选字段工厂：``tag`` 下拉（choices 动态取自标签表，供元数据渲染）。

    用法（可打标对象的 FilterSet）：``tag = TagChoiceFilter(method="filter_tag")``
    + ``Meta.fields`` 加入 ``"tag"`` + 视图 ``filter_backends`` 挂 ``TagFilterBackend``。
    """

    def __new__(cls, *args, **kwargs):
        from django import forms
        from django.utils.translation import gettext_lazy as _
        from django_filters import rest_framework as filters

        class _TagValueField(forms.ChoiceField):
            """校验放宽：值可为「标签名 / 标签主键 / 逗号分隔组合」。

            与 ``filter_by_tag_name`` → ``_ids_for_token`` 的解析口径一致（主键与名称都支持、
            多标签 AND 语义）；未知标签由过滤器返回空集（fail-closed），不在此处 400——
            否则「下拉里没有的取值」（新建标签 60s 缓存窗口内、深链带主键、多标签组合）会被拦。
            """

            def valid_value(self, value):
                return True

        class _TagChoiceFilter(filters.ChoiceFilter):
            def __init__(self, *filter_args, **filter_kwargs):
                filter_kwargs.setdefault("label", _("Tag"))
                filter_kwargs.setdefault("method", "filter_tag")
                super().__init__(*filter_args, **filter_kwargs)

            @property
            def field(self):
                # 每次取值重建：标签随时可新增，元数据下拉保持最新
                return _TagValueField(label=self.label, choices=tag_choice_options(), required=False)

        return _TagChoiceFilter(*args, **kwargs)


class TaggedPrefetchMixin:
    """可打标视图集的标签预取：仅在逐行序列化的 action（列表/详情/导出）预取。

    用 mixin 而非 ``prefetch_related_fields`` 是因为后者对**所有** action 生效
    （写操作/单对象操作会多一次无谓查询）；预取后 ``tags`` 字段零额外查询。
    """

    #: 需要标签数据的 action（与 BaseViewSet.auto_prefetch_actions 同口径）
    tagged_prefetch_actions = ("list", "retrieve", "export_data")

    def optimize_queryset(self, queryset):
        from django.db.models import QuerySet

        queryset = super().optimize_queryset(queryset)
        if not isinstance(queryset, QuerySet):
            return queryset
        if getattr(self, "action", None) in self.tagged_prefetch_actions:
            return queryset.prefetch_related("tagged_items__tag")
        return queryset


class TagFilterBackend:
    """列表过滤后端：``?tag=<id|name>``（可多值，AND 语义）。

    仅对可打标模型生效（其他视图集调用即返回原 queryset，零变化）；
    与数据权限叠加执行，不绕过任何既有过滤链。
    """

    def filter_queryset(self, request, queryset, view):
        from system.models.tag import TAGGABLE_MODELS

        params = request.query_params
        raw = params.getlist("tag") if hasattr(params, "getlist") else [params.get("tag")]
        # 两种写法都要支持：`?tag=A&tag=B`（多值）与 `?tag=A,B`（逗号分隔）。
        # 注意 getlist 对 `?tag=A,B` 返回的是单个未切分元素，必须逐个再切分，
        # 否则多标签会被当成一个 token（与 filterset 的 filter_by_tag_name 口径不一致，
        # 交集恒为空集）。
        tokens = [token.strip() for item in raw for token in str(item or "").split(",") if token.strip()]
        if not tokens:
            return queryset
        model = queryset.model
        if resource_key(model) not in TAGGABLE_MODELS:
            return queryset
        return filter_by_tags(queryset, model, tokens)


class TagFilterMixin:
    """可打标对象 FilterSet 的标签筛选混入：``?tag=<名称>``（多值逗号分隔，AND 语义）。

    用法：``class XxxFilter(TagFilterMixin, BaseFilterSet)`` + ``Meta.fields`` 加 ``"tag"``
    + 视图 ``extra_filter_class = [TagFilterBackend]``（元数据下拉来自 TagChoiceFilter）。
    """

    tag = TagChoiceFilter()

    def filter_tag(self, queryset, name, value):
        return filter_by_tag_name(queryset, value)
