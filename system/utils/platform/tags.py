#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""通用标签中心：白名单资源解析 + 打标读写 + 列表过滤。

- 白名单：``system.models.tag.TAGGABLE_MODELS``（"app_label.model" 小写 → 展示名）；
- 读写形态：``TaggedItem`` 通过 ``content_type + object_id`` 关联目标对象，
  目标模型侧 ``GenericRelation`` 支持 ``prefetch_related("tagged_items__tag")``；
- 打标权限回落业务对象的写权限点（默认 update，白名单可按资源声明 method），标签
  本身的 CRUD 走独立 4 个权限点；
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


def taggable_visit(model) -> tuple[str, str]:
    """对象打标所需的业务权限模板（白名单外返回空串）：返回 (method, path 模板)。"""
    from system.models.tag import TAGGABLE_MODELS

    meta = TAGGABLE_MODELS.get(resource_key(model)) or {}
    return str(meta.get("method") or "PATCH").upper(), str(meta.get("visit") or "")


def ensure_tag_permission(user, model, pk) -> None:
    """打标权限校验：回落业务对象的写权限点（fail-closed，模板见 TAGGABLE_MODELS）。"""
    from ai.utils.ai_actions import user_can_visit

    method, visit = taggable_visit(model)
    if not visit:
        raise DjangoValidationError(_("The object type cannot be tagged"))
    if not user_can_visit(user, method, visit.replace("<pk>", str(pk))):
        raise DjangoValidationError(_("You do not have permission to tag this object"))


def _data_scope_queryset(model, user):
    """默认可见域：全局数据权限过滤（与各域列表页的 BaseDataPermissionFilter 同源）。"""
    from common.core.filter import get_filter_queryset

    return get_filter_queryset(model._default_manager.all(), user)


def _approval_instance_queryset(model, user):
    """审批实例可见域：我发起 ∪ 待我审批 ∪ 我参与过 ∪ 我被抄送（列表页缺省页签同源）。"""
    from approval.utils.approval_flow.queries import visible_instances_for

    return visible_instances_for(user)


#: 资源键 → 可见域工厂（与该域列表页取值域同口径）。打标白名单新增资源时必须在此
#: 登记可见性口径，未登记的类型 fail-closed：对象一律按不可见处理（404），不放宽放行。
VISIBLE_QUERYSETS = {
    "identity.userinfo": _data_scope_queryset,
    "file.uploadfile": _data_scope_queryset,
    "approval.approvalinstance": _approval_instance_queryset,
}


def ensure_object_visible(user, model, pk) -> None:
    """查看级对象校验：目标对象必须落在请求者的用户可见域内（fail-closed）。

    打标读口与写口权限不同口径：写（assign / batch-assign）回落业务对象的更新权限点，
    读只要求「能看到该对象」，与各域列表页同源（普通模型走全局数据权限过滤，审批实例
    走审批域可见域）。对象不可见与不存在同响应（404），不泄露对象存在性；
    主键格式非法同按不可见处理。
    """
    from rest_framework.exceptions import NotFound

    factory = VISIBLE_QUERYSETS.get(resource_key(model))
    if factory is None:
        raise NotFound()
    try:
        visible = factory(model, user).filter(pk=pk).exists()
    except (ValueError, DjangoValidationError):
        # 主键取值与目标模型主键字段不匹配（如 UUID 主键传入任意串）按不可见处理
        visible = False
    if not visible:
        raise NotFound()


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


def set_object_tags_batch(model, pks, tag_pks, mode="add", user=None, guard=None) -> tuple[list, list]:
    """多对象批量打标：``mode`` 与批量端点同语义（add 合并去重 / remove 剔除 / replace 全量）。

    校验口径与单对象 ``set_object_tags`` 一致（目标主键必填、单对象标签数上限、标签必须
    已存在），数据访问批量化：现有关联一次读、标签存在性一次读、写关联一个事务
    （先删后 ``bulk_create(ignore_conflicts=True)``，同对象同标签的唯一约束兜底并发重复）、
    最新标签一次读回。逐对象先执行 ``guard(pk)``（打标权限回落由调用方定义）再做校验，
    任一对象失败只记入 ``failed`` 不阻断其余对象。返回 ``(changed, failed)``，
    条目顺序与 ``pks`` 一致；``changed`` 条目为 ``{"pk", "tags"}``，``failed`` 为 ``{"pk", "reason"}``。
    """
    from django.db import transaction

    from system.models.tag import Tag, TaggedItem

    raw_pks = list(pks or [])
    if not raw_pks:
        return [], []
    incoming = [str(item) for item in (tag_pks or []) if str(item or "").strip()]

    # 现有关联按对象一次读回（replace 全量替换不依赖现值，免读）
    current_map: dict = {}
    if mode != "replace":
        content_type = ContentType.objects.get_for_model(model)
        rows = TaggedItem.objects.filter(content_type=content_type, object_id__in=[str(pk) for pk in raw_pks])
        for row in rows.order_by("object_id", "tag__name"):
            current_map.setdefault(row.object_id, []).append(str(row.tag_id))

    # 逐对象计算目标标签集（add 去重合并 / remove 剔除 / 其余按全量替换）
    wanted_map: dict = {}
    for pk in (str(pk) for pk in raw_pks):
        current = current_map.get(pk, [])
        if mode == "add":
            wanted_map[pk] = list(dict.fromkeys([*current, *incoming]))
        elif mode == "remove":
            wanted_map[pk] = [item for item in current if item not in set(incoming)]
        else:
            wanted_map[pk] = incoming

    # 标签存在性整批一次校验（取代逐对象查库；replace 入参可能重复，集合口径不受影响）
    wanted_tag_ids = {item for wanted in wanted_map.values() for item in wanted}
    existing_tag_ids = (
        {str(item) for item in Tag.objects.filter(pk__in=wanted_tag_ids).values_list("pk", flat=True)}
        if wanted_tag_ids
        else set()
    )

    changed, failed, valid_pks = [], [], []
    for raw_pk in raw_pks:
        pk = str(raw_pk)
        try:
            if guard is not None:
                guard(raw_pk)
            if raw_pk in (None, ""):
                raise DjangoValidationError(_("The target object is required"))
            wanted = wanted_map[pk]
            if len(wanted) > MAX_TAGS_PER_OBJECT:
                raise DjangoValidationError(_("Too many tags (max {})").format(MAX_TAGS_PER_OBJECT))
            if set(wanted) - existing_tag_ids:
                raise DjangoValidationError(_("Some tags no longer exist; refresh and retry"))
        except DjangoValidationError as exc:
            failed.append({"pk": pk, "reason": "; ".join(getattr(exc, "messages", None) or [str(exc)])})
            continue
        valid_pks.append(pk)

    if valid_pks:
        content_type = ContentType.objects.get_for_model(model)
        creator = user if getattr(user, "pk", None) else None
        with transaction.atomic():
            TaggedItem.objects.filter(content_type=content_type, object_id__in=valid_pks).delete()
            TaggedItem.objects.bulk_create(
                [
                    TaggedItem(tag_id=tag_id, content_type=content_type, object_id=pk, creator=creator)
                    for pk in valid_pks
                    for tag_id in dict.fromkeys(wanted_map[pk])  # replace 入参可重复，落库按集合去重
                ],
                ignore_conflicts=True,
                # 批量入口的目标对象数不受限，显式分批避免单条 INSERT 超出后端绑定参数上限
                batch_size=500,
            )
        # 最新标签一次读回（按 tag 名排序，与单对象打标回显同序）
        latest: dict = {}
        rows = (
            TaggedItem.objects.filter(content_type=content_type, object_id__in=valid_pks)
            .select_related("tag")
            .order_by("object_id", "tag__name")
        )
        for row in rows:
            latest.setdefault(row.object_id, []).append(tag_brief(row.tag))
        changed = [{"pk": pk, "tags": latest.get(pk, [])} for pk in valid_pks]
    return changed, failed


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

        queryset = super().optimize_queryset(queryset)  # type: ignore[misc]  # 宿主 mixin 未声明同名优化钩子
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
