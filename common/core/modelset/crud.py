#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""CRUD 五个基础 Action：Create / Detail / List / Destroy / Update。

统一将 DRF 原生响应包装为 ApiResponse。拆分自 modelset.py（T2.1）。
"""

from rest_framework import mixins

from common.core.response import ApiResponse


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
        return ApiResponse(data=data)


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
