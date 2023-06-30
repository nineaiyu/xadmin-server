#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : modelset
# author : ly_13
# date : 6/2/2023
import json

from rest_framework.decorators import action
from rest_framework.viewsets import ModelViewSet

from common.core.response import ApiResponse


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
        self.queryset.filter(pk__in=pks).delete()
        return ApiResponse(detail=f"批量操作成功")
