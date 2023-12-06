#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : modelset
# author : ly_13
# date : 6/2/2023
import json

from django.conf import settings
from django.db.models import FileField
from rest_framework import mixins
from rest_framework.decorators import action
from rest_framework.viewsets import GenericViewSet, ModelViewSet

from common.core.response import ApiResponse


class UploadFileAction(object):
    FILE_UPLOAD_TYPE = ['png', 'jpeg', 'jpg', 'gif']
    FILE_UPLOAD_FIELD = 'avatar'
    FILE_UPLOAD_SIZE = settings.FILE_UPLOAD_SIZE

    def get_object(self):
        raise NotImplementedError('get_object must be overridden')

    @action(methods=['post'], detail=True)
    def upload(self, request, *args, **kwargs):
        files = request.FILES.getlist('file', [])
        instance = self.get_object()
        file_obj = files[0]
        try:
            file_type = file_obj.name.split(".")[-1]
            if file_type not in self.FILE_UPLOAD_TYPE:
                raise
            if file_obj.size > self.FILE_UPLOAD_SIZE:
                return ApiResponse(code=1003, detail=f"图片大小不能超过 {self.FILE_UPLOAD_SIZE}")
        except Exception as e:
            return ApiResponse(code=1002, detail=f"错误的图片类型, 类型应该为 {','.join(self.FILE_UPLOAD_TYPE)}")
        delete_file_name = None
        file_instance = getattr(instance, self.FILE_UPLOAD_FIELD)
        if file_instance:
            delete_file_name = file_instance.name
        setattr(instance, self.FILE_UPLOAD_FIELD, file_obj)
        instance.save(update_fields=[self.FILE_UPLOAD_FIELD])
        if delete_file_name:
            FileField(name=delete_file_name).storage.delete(delete_file_name)
        return ApiResponse()


class RankAction(object):

    def get_queryset(self):
        raise NotImplementedError('get_queryset must be overridden')

    @action(methods=['post'], detail=False)
    def action_rank(self, request, *args, **kwargs):
        pks = request.data.get('pks', [])
        rank = 1
        for pk in pks:
            self.get_queryset().filter(pk=pk).update(rank=rank)
            rank += 1
        return ApiResponse(detail='顺序保存成功')


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

    @action(methods=['delete'], detail=False, url_path='many-delete')
    def many_delete(self, request, *args, **kwargs):
        pks = request.query_params.get('pks', None)
        if not pks:
            return ApiResponse(code=1003, detail="数据异常，批量操作id不存在")
        pks = json.loads(pks)
        # queryset  delete() 方法进行批量删除，并不调用模型上的任何 delete() 方法,需要通过循环对象进行删除
        for instance in self.queryset.filter(pk__in=pks):
            instance.delete()
        return ApiResponse(detail=f"批量操作成功")
