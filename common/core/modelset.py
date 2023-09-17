#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : modelset
# author : ly_13
# date : 6/2/2023
import json

from rest_framework import mixins
from rest_framework.decorators import action
from rest_framework.viewsets import GenericViewSet, ModelViewSet

from common.core.response import ApiResponse


class OwnerModelSet(mixins.RetrieveModelMixin, mixins.UpdateModelMixin, GenericViewSet):
    def retrieve(self, request, *args, **kwargs):
        data = super().retrieve(request, *args, **kwargs).data
        return ApiResponse(data=data)

    def update(self, request, *args, **kwargs):
        data = super().update(request, *args, **kwargs).data
        return ApiResponse(data=data)


class OnlyListModelSet(mixins.ListModelMixin, GenericViewSet):
    def list(self, request, *args, **kwargs):
        data = super().list(request, *args, **kwargs).data
        return ApiResponse(data=data)


class BaseModelSet(ModelViewSet):

    def create(self, request, *args, **kwargs):
        data = super().create(request, *args, **kwargs).data
        return ApiResponse(data=data)

    def retrieve(self, request, *args, **kwargs):
        data = super().retrieve(request, *args, **kwargs).data
        return ApiResponse(data=data)

    def list(self, request, *args, **kwargs):
        data = super().list(request, *args, **kwargs).data
        return ApiResponse(data=data)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        return ApiResponse()

    def update(self, request, *args, **kwargs):
        data = super().update(request, *args, **kwargs).data
        return ApiResponse(data=data)

    @action(methods=['delete'], detail=False)
    def many_delete(self, request, *args, **kwargs):
        pks = request.query_params.get('pks', None)
        if not pks:
            return ApiResponse(code=1003, detail="数据异常，批量操作id不存在")
        pks = json.loads(pks)
        # queryset  delete() 方法进行批量删除，并不调用模型上的任何 delete() 方法,需要通过循环对象进行删除
        for instance in self.queryset.filter(pk__in=pks):
            instance.delete()
        return ApiResponse(detail=f"批量操作成功")
