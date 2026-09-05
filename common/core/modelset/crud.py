#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""CRUD 五个基础 Action：Create / Detail / List / Destroy / Update。

统一将 DRF 原生响应包装为 ApiResponse。拆分自 modelset.py（T2.1）。
"""

from rest_framework import mixins

from common.core.response import ApiResponse
from common.utils import get_logger

logger = get_logger(__name__)


class CreateAction(mixins.CreateModelMixin):
    def create(self, request, *args, **kwargs):
        """添加{cls}数据"""
        data = super().create(request, *args, **kwargs).data
        return ApiResponse(data=data)


class DetailAction(mixins.RetrieveModelMixin):
    def retrieve(self, request, *args, **kwargs):
        """获取{cls}的详情"""
        data = super().retrieve(request, *args, **kwargs).data
        return ApiResponse(data=data)


class ListAction(mixins.ListModelMixin):
    def list(self, request, *args, **kwargs):
        """获取{cls}的列表"""
        data = super().list(request, *args, **kwargs).data
        if isinstance(data, dict) and request.query_params.get("with_meta", "").lower() in ("1", "true", "yes"):
            # T3.2：按 with_meta=1 内联元数据，页面首开把
            # list / search-columns / search-fields 三个请求合并为一个
            self.inline_metadata(request, data)
        return ApiResponse(data=data)

    def inline_metadata(self, request, data: dict) -> None:
        """将 search-columns / search-fields 载荷内联进列表响应。

        仅在视图集混入了对应元数据 Action 时生效；单条元数据构建失败
        只记录日志并降级省略，绝不影响列表本身。
        """
        for action_name, key in (
            ("search_columns", "search_columns"),
            ("search_fields", "search_fields"),
        ):
            action = getattr(self, action_name, None)
            if action is None:
                continue
            try:
                result = action(request)
                payload = result.data.get("data")
                if payload is not None:
                    data[key] = payload
            except Exception as e:
                logger.warning(f"inline metadata {action_name} failed on {self.__class__.__name__}: {e}")


class DestroyAction(mixins.DestroyModelMixin):
    def destroy(self, request, *args, **kwargs):
        """删除{cls}数据"""
        instance = self.get_object()
        self.perform_destroy(instance)
        return ApiResponse()


class UpdateAction(mixins.UpdateModelMixin):
    def update(self, request, *args, **kwargs):
        """整体更新{cls}信息"""
        data = super().update(request, *args, **kwargs).data
        return ApiResponse(data=data)

    def partial_update(self, request, *args, **kwargs):
        """部分更新{cls}信息"""
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)
