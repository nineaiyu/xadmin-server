#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : pagination
# author : ly_13
# date : 6/16/2023
# -*- coding: utf-8 -*-


import base64
import json
from collections import OrderedDict
from datetime import datetime
from typing import Any

from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class PageNumber(PageNumberPagination):
    page_size = 20  # 每页显示多少条
    page_size_query_param = "size"  # URL中每页显示条数的参数
    page_query_param = "page"  # URL中页码的参数
    max_page_size = 100  # 返回最大数据条数

    def get_paginated_response(self, data: Any) -> Any:
        return Response(
            OrderedDict(
                [
                    ("total", self.page.paginator.count),
                    # ('next', self.get_next_link()),
                    # ('previous', self.get_previous_link()),
                    ("results", data),
                ]
            )
        )

    def get_paginated_response_schema(self, schema: Any) -> Any:
        return build_object_type(
            properties={
                "code": build_basic_type(OpenApiTypes.NUMBER),
                "detail": build_basic_type(OpenApiTypes.STR),
                "data": build_object_type(
                    properties={"total": build_basic_type(OpenApiTypes.NUMBER), "results": schema}
                ),
            }
        )


class KeysetPageNumber(PageNumber):
    """审计日志深分页：page 模式兼容，cursor 模式走 (created_time, pk) keyset。

    前端顺序翻页时把上一页的 ``next_cursor`` 作为下一页的 ``cursor`` 传入，
    跳过 COUNT + OFFSET；自定义 ordering 或非法 cursor 时回退/报错。
    仅适用于带 ``created_time`` 审计字段且默认 ``-created_time, -pk`` 排序的模型。
    """

    cursor_query_param = "cursor"
    cursor_ordering = ("-created_time", "-pk")

    def paginate_queryset(self, queryset: Any, request: Any, view: Any = None) -> Any:
        cursor = request.query_params.get(self.cursor_query_param)
        custom_ordering = bool(request.query_params.get("ordering"))
        self._cursor_mode = bool(cursor) and not custom_ordering
        if self._cursor_mode:
            size = self.get_page_size(request)
            rows = list(self._apply_cursor(queryset, cursor)[: size + 1])
            self._has_more = len(rows) > size
            rows = rows[:size]
            self._next_cursor = self._encode(rows[-1]) if rows and self._has_more else None
            self.page = None
            return rows

        # page 模式也固定稳定排序，保证返回的 next_cursor 可续
        if not custom_ordering:
            queryset = queryset.order_by(*self.cursor_ordering)
        page = super().paginate_queryset(queryset, request, view)
        if page is not None and self.page is not None:
            # DRF 的 paginate_queryset 返回 list；分页状态在 self.page
            self._has_more = self.page.has_next()
            self._next_cursor = (
                self._encode(self.page.object_list[-1]) if self._has_more and self.page.object_list else None
            )
        return page

    def _apply_cursor(self, queryset: Any, raw: str) -> Any:
        try:
            payload = json.loads(base64.urlsafe_b64decode(raw.encode()).decode())
            created_time = datetime.fromisoformat(payload["t"])
            pk = queryset.model._meta.pk.to_python(payload["pk"])
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValidationError({"cursor": _("Invalid cursor")}) from exc
        # 默认 -created_time, -pk：游标之后 = 时间更早，或同一时间 pk 更小
        return queryset.filter(Q(created_time__lt=created_time) | Q(created_time=created_time, pk__lt=pk)).order_by(
            *self.cursor_ordering
        )

    @staticmethod
    def _encode(obj: Any) -> str:
        payload = {"t": obj.created_time.isoformat(), "pk": obj.pk}
        return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()

    def get_paginated_response(self, data: Any) -> Any:
        if self._cursor_mode:
            return Response(
                OrderedDict(
                    [
                        ("results", data),
                        ("next_cursor", self._next_cursor),
                        ("has_more", self._has_more),
                    ]
                )
            )
        response = super().get_paginated_response(data)
        response.data["next_cursor"] = self._next_cursor
        response.data["has_more"] = self._has_more
        return response

    def get_paginated_response_schema(self, schema: Any) -> Any:
        return build_object_type(
            properties={
                "code": build_basic_type(OpenApiTypes.NUMBER),
                "detail": build_basic_type(OpenApiTypes.STR),
                "data": build_object_type(
                    properties={
                        "total": build_basic_type(OpenApiTypes.NUMBER),
                        "results": schema,
                        "next_cursor": build_basic_type(OpenApiTypes.STR),
                        "has_more": build_basic_type(OpenApiTypes.BOOL),
                    }
                ),
            }
        )


class DynamicPageNumber:
    def __init__(self, max_page_size: int = 100, page_size: int = 20) -> None:
        self.max_page_size = max_page_size
        self.page_size = page_size

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        instance = PageNumber()
        instance.max_page_size = self.max_page_size
        instance.page_size = self.page_size
        return instance
