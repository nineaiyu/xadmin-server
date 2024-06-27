#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : modelset
# author : ly_13
# date : 6/2/2023
import logging
from typing import Callable

from django.conf import settings
from django.db import transaction
from django.forms.widgets import SelectMultiple, DateTimeInput
from django.http import QueryDict
from django_filters.utils import get_model_field
from django_filters.widgets import DateRangeWidget
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import mixins
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser
from rest_framework.viewsets import GenericViewSet, ModelViewSet, ReadOnlyModelViewSet

from common.base.utils import get_choices_dict
from common.core.config import SysConfig
from common.core.response import ApiResponse
from common.core.serializers import BasePrimaryKeyRelatedField
from common.drf.renders.csv import CSVFileRenderer
from common.drf.renders.excel import ExcelFileRenderer

logger = logging.getLogger(__name__)


class UploadFileAction(object):
    FILE_UPLOAD_TYPE = ['png', 'jpeg', 'jpg', 'gif']
    FILE_UPLOAD_FIELD = 'avatar'
    FILE_UPLOAD_SIZE = settings.FILE_UPLOAD_SIZE

    def get_upload_size(self):
        return SysConfig.PICTURE_UPLOAD_SIZE

    def get_object(self):
        raise NotImplementedError('get_object must be overridden')

    @swagger_auto_schema(ignore_body_params=True, manual_parameters=[
        openapi.Parameter(
            'file', in_=openapi.IN_FORM, type=openapi.TYPE_FILE,
            description='File to upload', required=True
        ),
    ], operation_description='上传头像', methods=['post'])
    @action(methods=['post'], detail=True, parser_classes=(MultiPartParser,))
    def upload(self, request, *args, **kwargs):
        self.FILE_UPLOAD_SIZE = self.get_upload_size()
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
        instance.modifier = request.user
        instance.save(update_fields=[self.FILE_UPLOAD_FIELD, 'modifier'])
        if delete_file_name:
            file_instance.name = delete_file_name
            file_instance.delete(save=False)
        return ApiResponse()


class RankAction(object):
    filter_queryset: Callable
    get_queryset: Callable

    @swagger_auto_schema(request_body=openapi.Schema(
        type=openapi.TYPE_OBJECT,
        required=['pks'],
        properties={'pks': openapi.Schema(description='主键列表', type=openapi.TYPE_ARRAY,
                                          items=openapi.Schema(type=openapi.TYPE_STRING))}
    ), operation_description='根据主键顺序，进行从小到大进行排序')
    @action(methods=['post'], detail=False, url_path='rank')
    def action_rank(self, request, *args, **kwargs):
        pks = request.data.get('pks', [])
        rank = 1
        for pk in pks:
            self.filter_queryset(self.get_queryset()).filter(pk=pk).update(rank=rank)
            rank += 1
        return ApiResponse(detail='顺序保存成功')


class OnlyExportDataAction(object):
    @action(methods=['get'], detail=False, url_path='export-data')
    def export_data(self, request, *args, **kwargs):
        self.format_kwarg = request.query_params.get('type', 'xlsx')
        request.no_cache = True  # 防止自定义缓存数据
        self.renderer_classes = [ExcelFileRenderer, CSVFileRenderer]
        request.accepted_renderer = None
        data = self.list(request, *args, **kwargs)
        return data


class ImportExportDataAction(OnlyExportDataAction):
    filter_queryset: Callable
    get_queryset: Callable
    get_serializer: Callable
    perform_create: Callable
    perform_update: Callable

    @action(methods=['post'], detail=False, url_path='import-data')
    @transaction.atomic
    def import_data(self, request, *args, **kwargs):
        act = request.query_params.get('action')
        if act and request.data:
            if act == 'create':
                for data in request.data:
                    serializer = self.get_serializer(data=data)
                    serializer.is_valid(raise_exception=True)
                    self.perform_create(serializer)
            elif act == 'update':
                queryset = self.get_queryset()
                for data in request.data:
                    instance = queryset.get(pk=data.get('pk'))
                    if getattr(instance, 'is_superuser', None):
                        continue
                    serializer = self.get_serializer(instance, data=data, partial=True)
                    serializer.is_valid(raise_exception=True)
                    self.perform_update(serializer)
            return ApiResponse(detail="操作成功")
        return ApiResponse(detail='数据异常', code=1001)


class ChoicesAction(object):
    choices_models: []

    @swagger_auto_schema(operation_description='获取字段选择', ignore_params=True, responses={
        200: openapi.Response('字段选择结果', openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'code': openapi.Schema(type=openapi.TYPE_NUMBER, default=1000),
                'detail': openapi.Schema(type=openapi.TYPE_STRING, default='success'),
                'choices_dict': openapi.Schema(type=openapi.TYPE_OBJECT, properties={
                    'key': openapi.Schema(type=openapi.TYPE_ARRAY,
                                          items=openapi.Schema(type=openapi.TYPE_OBJECT, properties={
                                              'value': openapi.Schema(type=openapi.TYPE_STRING),
                                              'label': openapi.Schema(type=openapi.TYPE_STRING),
                                          })),
                }),
            }
        ))
    })
    @action(methods=['get'], detail=False, url_path='choices')
    def choices_dict(self, request, *args, **kwargs):
        result = {}
        models = getattr(self, 'choices_models', None)
        if not models:
            models = [self.queryset.model]
        for model in models:
            for field in model._meta.fields:
                choices = field.choices
                if choices:
                    result[field.name] = get_choices_dict(choices)
        return ApiResponse(choices_dict=result)


class SearchFieldsAction(object):
    filterset_class: Callable

    @swagger_auto_schema(operation_description='获取可查询字段', ignore_params=True, responses={
        200: openapi.Response('可查询字段结果', openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'code': openapi.Schema(type=openapi.TYPE_NUMBER, default=1000),
                'detail': openapi.Schema(type=openapi.TYPE_STRING, default='success'),
                'data': openapi.Schema(type=openapi.TYPE_OBJECT, properties={
                    'results': openapi.Schema(type=openapi.TYPE_ARRAY,
                                              items=openapi.Schema(type=openapi.TYPE_OBJECT, properties={
                                                  'key': openapi.Schema(type=openapi.TYPE_STRING),
                                                  'input_type': openapi.Schema(type=openapi.TYPE_STRING),
                                                  'choices': openapi.Schema(type=openapi.TYPE_ARRAY,
                                                                            items=openapi.Schema(
                                                                                type=openapi.TYPE_OBJECT, properties={
                                                                                    'value': openapi.Schema(
                                                                                        type=openapi.TYPE_STRING),
                                                                                    'label': openapi.Schema(
                                                                                        type=openapi.TYPE_STRING),
                                                                                })),
                                              })),
                })

            }
        ))
    })
    @action(methods=['get'], detail=False, url_path='search-fields')
    def search_fields(self, request, *args, **kwargs):
        results = []
        try:
            filterset_class = self.filterset_class.get_filters()
            filter_fields = self.filterset_class.get_fields().keys()
            for field_name, value in filterset_class.items():
                if field_name not in filter_fields: continue
                widget = value.field.widget
                if isinstance(widget, SelectMultiple):
                    value.field.widget.input_type = 'select-multiple'
                if isinstance(widget, DateRangeWidget):
                    value.field.widget.input_type = 'datetimerange'
                if isinstance(widget, DateTimeInput):
                    value.field.widget.input_type = 'datetime'
                # if hasattr(value.field, 'queryset'):  # 将一些具有关联的字段的数据置空
                #     value.field.widget.input_type = 'text'
                #     value.field.widget.choices = []
                if hasattr(value, 'input_type'): value.field.widget.input_type = value.input_type
                choices = list(getattr(value.field.widget, 'choices', []))
                if choices and len(choices) > 0 and choices[0][0] == "":
                    choices.pop(0)
                field = get_model_field(self.filterset_class._meta.model, value.field_name)
                results.append({
                    'key': field_name,
                    'label': value.label if value.label else (
                        getattr(field, 'verbose_name', field.name) if field else field_name),
                    'help_text': value.field.help_text if value.field.help_text else getattr(field, 'help_text', None),
                    'input_type': value.field.widget.input_type,
                    'choices': get_choices_dict(choices)
                })
            order_choices = []
            ordering_fields = list(getattr(self, 'ordering_fields', []))
            for choice in ordering_fields:
                order_choices.extend([(f"-{choice}", f"{choice} descending"), (choice, f"{choice} ascending")])
            if order_choices:
                results.append({
                    'label': 'ordering',
                    'key': "ordering",
                    'input_type': 'select-ordering',
                    'choices': get_choices_dict(order_choices),
                    'default': order_choices[0][0]
                })
        except Exception as e:
            logger.error(f"get search-field failed {e}")
        return ApiResponse(data=results)

    @action(methods=['get'], detail=False, url_path='search-columns')
    def search_columns(self, request, *args, **kwargs):
        results = []

        def get_input_type(value, info):
            if hasattr(value, 'child_relation') and isinstance(value.child_relation, BasePrimaryKeyRelatedField):
                info['multiple'] = True
                tp = value.child_relation.input_type if value.child_relation.input_type else info['type']
            else:
                tp = info['type']
            if tp and tp.endswith('related_field'):
                info['choices'] = [{'value': k, 'label': v} for k, v in value.choices.items()]
            return tp

        metadata_class = self.metadata_class()
        fields = self.get_serializer().fields
        for key, value in fields.items():
            info = metadata_class.get_field_info(value)
            info['key'] = key
            if value.style.get('base_template', '') == 'textarea.html':
                info['input_type'] = 'textarea'
            else:
                info['input_type'] = get_input_type(value, info)
            del info['type']
            results.append(info)
        return ApiResponse(data=results)

class BaseModelAction(object):
    filterset_class: Callable
    filter_queryset: Callable
    get_queryset: Callable
    get_object: Callable
    action: Callable
    extra_filter_class = []

    def perform_destroy(self, instance):
        return instance.delete()

    def filter_queryset(self, queryset):
        for backend in set(set(self.filter_backends) | set(self.extra_filter_class or [])):
            queryset = backend().filter_queryset(self.request, queryset, self)
        return queryset

    def get_queryset(self):
        if getattr(self, 'values_queryset', None):
            return self.values_queryset
        return super().get_queryset()

    def paginate_queryset(self, queryset):
        # 文件导出的时候，忽略 paginate_queryset
        if self.request.query_params.get('type') in ['csv', 'xlsx'] and self.request.path_info.endswith('export-data'):
            return None
        return super().paginate_queryset(queryset)

    def get_serializer_class(self):
        action_serializer_name = f"{self.action}_serializer_class"
        action_serializer_class = getattr(self, action_serializer_name, None)
        if action_serializer_class:
            return action_serializer_class
        return super().get_serializer_class()


class BatchDeleteAction(object):
    filter_queryset: Callable
    get_queryset: Callable
    perform_destroy: Callable

    @swagger_auto_schema(request_body=openapi.Schema(
        type=openapi.TYPE_OBJECT,
        required=['pks'],
        properties={'pks': openapi.Schema(description='主键列表', type=openapi.TYPE_ARRAY,
                                          items=openapi.Schema(type=openapi.TYPE_STRING))}
    ), operation_description='批量删除')
    @action(methods=['post'], detail=False, url_path='batch-delete')
    def batch_delete(self, request, *args, **kwargs):
        if isinstance(request.data, QueryDict):
            pks = request.data.getlist('pks', None)
        else:
            pks = request.data.get('pks', None)
        if not pks:
            return ApiResponse(code=1003, detail="数据异常，批量操作主键列表不存在")
        # queryset  delete() 方法进行批量删除，并不调用模型上的任何 delete() 方法,需要通过循环对象进行删除
        count = 0
        for instance in self.filter_queryset(self.get_queryset()).filter(pk__in=pks):
            try:
                deleted, _rows_count = self.perform_destroy(instance)
                if deleted:
                    count += 1
            except Exception:
                pass
        return ApiResponse(detail=f"操作成功，批量删除{count}条数据")


class BaseAction(BaseModelAction, ChoicesAction, SearchFieldsAction, BatchDeleteAction):

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


class OwnerModelSet(BaseModelAction, ChoicesAction, mixins.RetrieveModelMixin, mixins.UpdateModelMixin, GenericViewSet):
    def update(self, request, *args, **kwargs):
        data = super().update(request, *args, **kwargs).data
        return ApiResponse(data=data)

    def retrieve(self, request, *args, **kwargs):
        data = super().retrieve(request, *args, **kwargs).data
        return ApiResponse(data=data)


class OnlyListModelSet(BaseModelAction, ChoicesAction, SearchFieldsAction, mixins.ListModelMixin, GenericViewSet):
    def list(self, request, *args, **kwargs):
        data = super().list(request, *args, **kwargs).data
        return ApiResponse(data=data)


class BaseModelSet(BaseAction, ModelViewSet):
    pass


# 只允许读和删除，不允许创建和修改
class ListDeleteModelSet(BaseModelAction, ChoicesAction, SearchFieldsAction, BatchDeleteAction,
                         mixins.DestroyModelMixin, ReadOnlyModelViewSet):
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
