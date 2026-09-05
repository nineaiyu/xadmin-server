#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""批量操作 Action：排序（rank）与批量删除（batch-destroy）。

拆分自 modelset.py（T2.1）。
"""

from typing import Callable

from django.db.models import Case, IntegerField, Value, When
from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_array_type, build_basic_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiRequest
from rest_framework.decorators import action

from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger

logger = get_logger(__name__)


class RankAction(object):
    filter_queryset: Callable
    get_queryset: Callable

    @extend_schema(
        request=OpenApiRequest(build_array_type(build_basic_type(OpenApiTypes.STR))),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="rank")
    def rank(self, request, *args, **kwargs):
        """{cls}排序"""
        pks = list(request.data)
        if pks:
            # PERF-12：Case/When 单条批量 UPDATE，替代逐条 filter(pk=pk).update(rank=rank)
            queryset = self.filter_queryset(self.get_queryset()).filter(pk__in=pks)
            queryset.update(
                rank=Case(
                    *[When(pk=pk, then=Value(index)) for index, pk in enumerate(pks, start=1)],
                    output_field=IntegerField(),
                )
            )
        return ApiResponse(detail=_("Sorting saved successfully"))


class BatchDestroyAction(object):
    filter_queryset: Callable
    get_queryset: Callable
    perform_destroy: Callable

    @extend_schema(
        request=OpenApiRequest(build_array_type(build_basic_type(OpenApiTypes.STR))),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="batch-destroy")
    def batch_destroy(self, request, *args, **kwargs):
        """批量删除{cls}"""

        # response = run_view_by_celery_task(self, request, kwargs, request.data, batch_length=30)
        # if response:
        #     return response

        queryset = self.filter_queryset(self.get_queryset()).filter(pk__in=request.data)
        if not self._has_file_cleanup():
            # PERF-19：模型无文件字段时无需逐行触发文件清理，直接走批量 delete()，
            # 单条 SQL 完成（旧实现逐行 instance.delete()，N 行 = N 次级联删除事务）
            deleted, _rows_count = queryset.delete()
            return ApiResponse(detail=_("Operation successful. Batch deleted {} data").format(deleted))

        # 带文件字段的模型需要触发模型 delete() 以清理底层文件；先收集再统一删除
        count = 0
        for instance in queryset:
            try:
                deleted, _rows_count = self.perform_destroy(instance)
                if deleted:
                    count += 1
            except Exception as e:
                logger.error(f"failed to destroy instance {instance} with error {e}")
        return ApiResponse(detail=_("Operation successful. Batch deleted {} data").format(count))

    def _has_file_cleanup(self):
        """PERF-19：模型是否需要逐行 delete() 以清理文件/附件。

        只有继承 AutoCleanFileMixin 且确实存在文件/附件关联的模型，其 delete()
        才有批量 delete() 覆盖不到的副作用，需要逐行触发。
        """
        model = getattr(getattr(self, "queryset", None), "model", None)
        if model is None:
            return True
        from common.core.models import AutoCleanFileMixin

        return issubclass(model, AutoCleanFileMixin) and AutoCleanFileMixin.has_file_cleanup(model)
