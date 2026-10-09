#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""元数据 Action：choices 聚合 / search-fields / search-columns。

前端 RePlusPage 注册表渲染依赖的三大元数据接口。拆分自 modelset.py。
"""

from hashlib import md5
from typing import TYPE_CHECKING, Any

from django.core.cache import cache
from django.forms.widgets import DateTimeInput, SelectMultiple
from django.utils.translation import gettext_lazy as _
from django_filters.utils import get_model_field
from django_filters.widgets import DateRangeWidget
from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action

from common.base.utils import get_choices_dict
from common.core.fields import get_search_choices_max_count
from common.core.modelset.input_types import get_format_intput_type
from common.core.modelset.metadata_cache import cached_payload
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger

logger = get_logger(__name__)

#: 元数据载荷缓存时长：前端每个列表页都会取（首开还经 with_meta=1 内联进列表响应），
#: 现场重建 filterset + 逐字段求值是 k6 最慢用例之一。缓存键含用户主键（字段权限按
#: 用户不同）；载荷级缓存（而非响应级）保证 with_meta 内联路径读到的仍是普通响应对象。
METADATA_CACHE_TIMEOUT = 60 * 5


def metadata_cache_key(view_instance: Any, method_name: Any, request: Any) -> str:
    """载荷缓存键：视图集 + 方法 + 用户主键（+ ``?fields=`` 摘要 + 视图集扩展槽位）。

    ``?fields=`` 会收窄 search-columns 的序列化器字段（BaseViewSet.get_serializer），
    因此并入键；其余查询参数（分页/排序/搜索）不影响元数据输出，不入键——独立元数据
    接口与列表内联调用因此共用同一份缓存。

    序列化器字段面还可能依赖**其他查询参数**（如 setting 类视图集按
    ``?channel=`` / ``?category=`` 收敛序列化器）：视图集可覆写
    ``metadata_extra_cache_key(request)`` 返回额外键段，缺省空串不入键。
    """
    user_pk = getattr(getattr(request, "user", None), "pk", "anonymous")
    query = getattr(request, "query_params", None)
    if query is None:
        query = getattr(request, "GET", {}) or {}
    raw_fields = query.get("fields") if hasattr(query, "get") else None
    digest = f"_{md5(str(raw_fields).encode('utf-8')).hexdigest()[:12]}" if raw_fields else ""
    extra_hook = getattr(view_instance, "metadata_extra_cache_key", None)
    extra = f"_x{md5(str(extra_hook(request)).encode('utf-8')).hexdigest()[:12]}" if callable(extra_hook) else ""
    return f"metadata_payload_{view_instance.__class__.__name__}_{method_name}_{user_pk}{digest}{extra}"


def invalidate_metadata_payload_cache() -> None:
    """嵌入元数据的可变引用数据（如标签选项）变更后整族失效载荷缓存。

    载荷缓存键（用户 / ?fields= / 视图集扩展槽位）不随引用数据变化——标签等
    选项在构建时被固化进载荷，不失效则「新建标签最长 TTL 内不可选」。键族小、
    重建廉价，按信号整族失效即可；调用量大的引用数据应改走独立接口而非内嵌。
    """
    try:
        cache.delete_pattern("metadata_payload_*")
    except Exception:  # noqa: BLE001 缓存不可用时依赖 TTL 自愈
        logger.debug("invalidate metadata payload cache failed", exc_info=True)


def metadata_cache_bypass(request: Any) -> bool:
    """``?no_cache=1`` 旁路：与 cache_response 的刷新口径一致（读跳过、也不回写）。"""
    query = getattr(request, "query_params", None)
    if query is None:
        query = getattr(request, "GET", {}) or {}
    if getattr(request, "no_cache", False):
        return True
    return bool(hasattr(query, "get") and query.get("no_cache") in ("1", "true"))


class ChoicesAction:
    choices_models: list[Any] = []

    if TYPE_CHECKING:  # 宿主 ViewSet 提供的接口（mixin 模式）
        queryset: Any

    @extend_schema(
        responses=get_default_response_schema(
            {
                "choices_dict": build_object_type(
                    properties={
                        "key": build_array_type(
                            build_object_type(
                                properties={
                                    "value": build_basic_type(OpenApiTypes.STR),
                                    "label": build_basic_type(OpenApiTypes.STR),
                                }
                            )
                        )
                    }
                )
            }
        )
    )
    @action(methods=["get"], detail=False, url_path="choices")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def choices_dict(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """获取{cls}的字段选择"""
        result = {}
        models = getattr(self, "choices_models", None)
        if not models:
            models = [self.queryset.model]
        for model in models:
            for field in model._meta.fields:
                choices = field.choices
                if choices:
                    result[field.name] = get_choices_dict(choices)
        return ApiResponse(choices_dict=result)


class SearchFieldsAction:
    filterset_class: Any

    @extend_schema(
        responses=get_default_response_schema(
            {
                "data": build_array_type(
                    build_object_type(
                        properties={
                            "key": build_basic_type(OpenApiTypes.STR),
                            "label": build_basic_type(OpenApiTypes.STR),
                            "help_text": build_basic_type(OpenApiTypes.STR),
                            "default": build_basic_type(OpenApiTypes.ANY),
                            "input_type": build_basic_type(OpenApiTypes.STR),
                            "choices": build_array_type(
                                build_object_type(
                                    properties={
                                        "pk": build_basic_type(OpenApiTypes.STR),
                                        "value": build_basic_type(OpenApiTypes.STR),
                                        "label": build_basic_type(OpenApiTypes.STR),
                                    }
                                )
                            ),
                            "choices_truncated": build_basic_type(OpenApiTypes.BOOL),
                        }
                    )
                )
            }
        )
    )
    @action(methods=["get"], detail=False, url_path="search-fields")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def search_fields(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """获取{cls}的查询字段"""
        cache_key = metadata_cache_key(self, "search_fields", request)
        cache_bypass = metadata_cache_bypass(request)
        results = cached_payload(cache_key, METADATA_CACHE_TIMEOUT, self._build_search_fields, bypass=cache_bypass)
        if results is None:
            # 整体无法构建（filterset 配置异常等）：返回失败码，
            # 而不是 HTTP 200 + code=1000 的"成功但残缺"元数据让前端静默降级
            return ApiResponse(code=500, detail=_("Failed to get search fields"))
        return ApiResponse(data=results)

    def _build_search_fields(self) -> Any:
        """构建查询字段元数据；返回 None 表示构建失败（调用方转失败码，且失败不入缓存）。"""
        if getattr(self, "filterset_class", None) is None:
            # 非模型视图集（内存 queryset / 未声明 filterset，如 IP 拦截名单）：
            # 「没有可筛选字段」是正常语义，返回空元数据
            return []
        try:
            filterset_class = self.filterset_class.get_filters()
            filter_fields = self.filterset_class.get_fields().keys()
        except Exception as e:
            # 整体无法构建（filterset 配置异常等）：由调用方转失败码
            logger.error(f"get search-field failed {e}")
            return None
        results = []
        for field_name, value in filterset_class.items():
            if field_name not in filter_fields:
                continue
            # 单字段异常只跳过该字段并记录，不牵连其余字段的元数据
            try:
                widget = value.field.widget
                if isinstance(widget, SelectMultiple):
                    widget.input_type = "select-multiple"
                if isinstance(widget, DateRangeWidget):
                    widget.input_type = "datetimerange"
                if isinstance(widget, DateTimeInput):
                    widget.input_type = "datetime"
                # if hasattr(value.field, 'queryset'):  # 将一些具有关联的字段的数据置空
                #     widget.input_type = 'text'
                #     widget.choices = []
                widget.input_type = get_format_intput_type(value, widget.input_type)
                choices = list(getattr(widget, "choices", []))
                if choices and len(choices) > 0 and choices[0][0] == "":
                    choices.pop(0)
                # 关联字段的 widget.choices 同样会全量求值，这里做同样的行数上限
                max_choices = get_search_choices_max_count()
                choices_truncated = False
                if max_choices and len(choices) > max_choices:
                    choices = choices[:max_choices]
                    choices_truncated = True
                field = get_model_field(self.filterset_class._meta.model, value.field_name)
                results.append(
                    {
                        "key": field_name,
                        "label": value.label
                        if value.label
                        else (getattr(field, "verbose_name", field.name) if field else field_name),
                        "help_text": value.field.help_text
                        if value.field.help_text
                        else getattr(field, "help_text", None),
                        "input_type": widget.input_type,
                        "choices": get_choices_dict(choices),
                        "default": [] if "multiple" in widget.input_type else "",
                        **({"choices_truncated": True} if choices_truncated else {}),
                    }
                )
            except Exception as e:
                logger.error(f"get search-field failed. field:{field_name} error:{e}")
                continue
        try:
            order_choices = []
            ordering_fields = list(getattr(self, "ordering_fields", []))
            for choice in ordering_fields:
                is_des = False
                if choice.startswith("-"):
                    choice = choice[1:]
                    is_des = True
                label = choice
                field = get_model_field(self.filterset_class._meta.model, choice)
                if field:
                    label = getattr(field, "verbose_name", choice)
                des = (f"-{choice}", f"{label} descending")
                ase = (choice, f"{label} ascending")
                if is_des:
                    des, ase = ase, des
                order_choices.extend([des, ase])
            if order_choices:
                results.append(
                    {
                        "label": "ordering",
                        "key": "ordering",
                        "input_type": "select-ordering",
                        "choices": get_choices_dict(order_choices),
                        "default": order_choices[0][0],
                    }
                )
        except Exception as e:
            # ordering 段失败不影响已收集的字段元数据
            logger.error(f"get search-field ordering failed {e}")
        return results


# 实现拆至 common.core.modelset.metadata_columns：经模块级 __getattr__ 延迟再导出（保持调用面，避免循环导入）。
_MOVED_EXPORTS = ("SearchColumnsAction",)


def __getattr__(name: str) -> Any:
    if name in _MOVED_EXPORTS:
        from importlib import import_module

        return getattr(import_module("common.core.modelset.metadata_columns"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
