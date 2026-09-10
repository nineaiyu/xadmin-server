#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""批量操作 Action：排序（rank）与批量删除（batch-destroy）。

拆分自 modelset.py。
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

# 排序入参上限：Case/When 的 WHEN 数随列表线性增长，超大列表会撑爆 SQL 参数/表达式上限
RANK_MAX_ITEMS = 1000


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
        # 入参必须是主键列表：dict 会被 list() 解包成键列表，非法形态直接返回可读错误
        if not isinstance(request.data, (list, tuple)):
            return ApiResponse(code=1004, detail=_("Operation failed. Abnormal data"))
        pks = [pk for pk in request.data if pk not in (None, "")]
        if len(pks) > RANK_MAX_ITEMS:
            return ApiResponse(code=1004, detail=_("Too many items to sort (max {})").format(RANK_MAX_ITEMS))
        if pks:
            # Case/When 单条批量 UPDATE，替代逐条 filter(pk=pk).update(rank=rank)
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

        queryset = self.filter_queryset(self.get_queryset()).filter(pk__in=request.data)
        if not self._needs_rowwise_delete():
            # 模型无逐行副作用时直接走批量 delete()，单条 SQL 完成
            # （旧实现逐行 instance.delete()，N 行 = N 次级联删除事务）
            deleted, _rows_count = queryset.delete()
            return ApiResponse(detail=_("Operation successful. Batch deleted {} data").format(deleted))

        # 软删模型需要触发 delete() 中的 save() 信号（权限缓存失效依赖此链路）；
        # 带文件字段的模型需要触发模型 delete() 以清理底层文件。先收集再统一删除
        count = 0
        for instance in queryset:
            try:
                result = self.perform_destroy(instance)
                # Django delete() 返回 (total, per_model_dict) 元组；
                # 软删模型的 delete() 返回标记行数（int）
                deleted = result[0] if isinstance(result, tuple) else (result or 0)
                if deleted:
                    count += 1
            except Exception as e:
                logger.error(f"failed to destroy instance {instance} with error {e}")
        return ApiResponse(detail=_("Operation successful. Batch deleted {} data").format(count))

    def _needs_rowwise_delete(self):
        """是否需要逐行 delete() 以触发模型级副作用。

        - 继承 AutoCleanFileMixin 且确实存在文件/附件关联的模型，delete()
          才有批量 delete() 覆盖不到的文件清理副作用；
        - 软删模型（SoftDeleteModel）的 delete() 走 save()，post_save 信号
          （权限缓存失效）必须触发，单 SQL update 会绕过信号。
        """
        model = getattr(getattr(self, "queryset", None), "model", None)
        if model is None:
            return True
        from common.core.models import AutoCleanFileMixin, SoftDeleteModel

        if issubclass(model, SoftDeleteModel):
            return True
        return issubclass(model, AutoCleanFileMixin) and AutoCleanFileMixin.has_file_cleanup(model)
