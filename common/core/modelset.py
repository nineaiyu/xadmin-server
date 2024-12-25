#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : modelset
# author : ly_13
# date : 6/2/2023
import itertools
import json
import uuid
from hashlib import md5
from typing import Callable

import math
from django.conf import settings
from django.db import transaction
from django.forms.widgets import SelectMultiple, DateTimeInput
from django.utils.translation import gettext_lazy as _
from django_filters.utils import get_model_field
from django_filters.widgets import DateRangeWidget
from drf_spectacular.plumbing import build_object_type, build_basic_type, build_array_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiResponse, OpenApiRequest, OpenApiParameter
from rest_framework import mixins
from rest_framework.decorators import action
from rest_framework.fields import CharField
from rest_framework.parsers import MultiPartParser
from rest_framework.utils import encoders
from rest_framework.viewsets import GenericViewSet

from common.base.magic import cache_response
from common.base.utils import get_choices_dict
from common.core.config import SysConfig
from common.core.response import ApiResponse
from common.core.serializers import BasePrimaryKeyRelatedField
from common.core.utils import has_self_fields, topological_sort
from common.drf.renders.csv import CSVFileRenderer
from common.drf.renders.excel import ExcelFileRenderer
from common.swagger.utils import get_default_response_schema
from common.tasks import background_task_view_set_job
from common.utils import get_logger

logger = get_logger(__name__)

def run_view_by_celery_task(view, request, kwargs, data, batch_length=100):
    task = kwargs.get("task", request.query_params.get('task', 'true').lower() in ['true', '1', 'yes'])  # 默认为任务异步导入
    if task:
        view_str = f"{view.__class__.__module__}.{view.__class__.__name__}"
        meta = request.META
        task_id = uuid.uuid4()
        if isinstance(data, dict):
            data = [data]
        meta["task_count"] = math.ceil(len(data) / batch_length)
        meta["action"] = view.action
        for index, batch in enumerate(itertools.batched(data, batch_length)):
            meta["task_id"] = f"{task_id}_{index}"
            meta["task_index"] = index
            res = background_task_view_set_job.apply_async(args=(view_str, meta, json.dumps(batch), view.action_map),
                                                           task_id=meta["task_id"])
            logger.info(f"add {view_str} task success. {res}")
        return ApiResponse(detail=_("Task add success"))


class CacheDetailResponseMixin(object):
    def get_cache_key(self, view_instance, view_method, request, args, kwargs):
        func_name = f'{view_instance.__class__.__name__}_{view_method.__name__}'
        return f"{func_name}_{request.user.pk}"

    @classmethod
    def invalid_cache(cls, pk, methods=None):
        if methods is None:
            methods = ['retrieve', 'get']
        for method in methods:
            cache_response.invalid_cache(f'{cls.__name__}_{method}_{pk}')


class CacheListResponseMixin(object):
    def get_cache_key(self, view_instance, view_method, request, args, kwargs):
        func_name = f'{view_instance.__class__.__name__}_{view_method.__name__}'
        return f"{func_name}_{request.user.pk}_{md5(json.dumps(request.query_params, sort_keys=True).encode('utf-8')).hexdigest()}"

    @classmethod
    def invalid_cache(cls, pk, methods=None):
        if methods is None:
            methods = ['list']
        for method in methods:
            cache_response.invalid_cache(f'{cls.__name__}_{method}_{pk}')


class UploadFileAction(object):
    FILE_UPLOAD_TYPE = ['png', 'jpeg', 'jpg', 'gif']
    FILE_UPLOAD_FIELD = 'avatar'
    FILE_UPLOAD_SIZE = settings.FILE_UPLOAD_SIZE

    def get_upload_size(self):
        return SysConfig.PICTURE_UPLOAD_SIZE

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(properties={'file': build_basic_type(OpenApiTypes.BINARY)})
        ),
        responses=get_default_response_schema()
    )
    @action(methods=['post'], detail=True, parser_classes=(MultiPartParser,))
    def upload(self, request, *args, **kwargs):
        """上传头像"""
        self.FILE_UPLOAD_SIZE = self.get_upload_size()
        files = request.FILES.getlist('file', [])
        instance = self.get_object()
        file_obj = files[0]
        try:
            file_type = file_obj.name.split(".")[-1]
            if file_type not in self.FILE_UPLOAD_TYPE:
                raise
            if file_obj.size > self.FILE_UPLOAD_SIZE:
                return ApiResponse(code=1003, detail=_("Image size cannot exceed {}").format(self.FILE_UPLOAD_SIZE))
        except Exception as e:
            return ApiResponse(code=1002,
                               detail=_("Wrong image type, the type should be {}").format(
                                   ','.join(self.FILE_UPLOAD_TYPE)))
        setattr(instance, self.FILE_UPLOAD_FIELD, file_obj)
        instance.modifier = request.user
        instance.save(update_fields=[self.FILE_UPLOAD_FIELD, 'modifier'])
        return ApiResponse()


class RankAction(object):
    filter_queryset: Callable
    get_queryset: Callable

    @extend_schema(
        request=OpenApiRequest(build_array_type(build_basic_type(OpenApiTypes.STR))),
        responses=get_default_response_schema()
    )
    @action(methods=['post'], detail=False, url_path='rank')
    def rank(self, request, *args, **kwargs):
        """{cls}排序"""
        rank = 1
        for pk in request.data:
            self.filter_queryset(self.get_queryset()).filter(pk=pk).update(rank=rank)
            rank += 1
        return ApiResponse(detail=_("Sorting saved successfully"))


class ChoicesAction(object):
    choices_models: []

    @extend_schema(
        responses=get_default_response_schema(
            {
                'choices_dict': build_object_type(
                    properties={
                        'key': build_array_type(
                            build_object_type(
                                properties={
                                    'value': build_basic_type(OpenApiTypes.STR),
                                    'label': build_basic_type(OpenApiTypes.STR),
                                }
                            )
                        )
                    }
                )
            }
        )
    )
    @action(methods=['get'], detail=False, url_path='choices')
    def choices_dict(self, request, *args, **kwargs):
        """获取{cls}的字段选择"""
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

    @extend_schema(
        responses=get_default_response_schema(
            {
                'data': build_array_type(
                    build_object_type(
                        properties={
                            'key': build_basic_type(OpenApiTypes.STR),
                            'label': build_basic_type(OpenApiTypes.STR),
                            'help_text': build_basic_type(OpenApiTypes.STR),
                            'default': build_basic_type(OpenApiTypes.ANY),
                            'input_type': build_basic_type(OpenApiTypes.STR),
                            'choices': build_array_type(
                                build_object_type(
                                    properties={
                                        'pk': build_basic_type(OpenApiTypes.STR),
                                        'value': build_basic_type(OpenApiTypes.STR),
                                        'label': build_basic_type(OpenApiTypes.STR),
                                    }
                                )
                            )
                        }
                    )
                )
            }
        )
    )
    @action(methods=['get'], detail=False, url_path='search-fields')
    def search_fields(self, request, *args, **kwargs):
        """获取{cls}的查询字段"""
        results = []
        try:
            filterset_class = self.filterset_class.get_filters()
            filter_fields = self.filterset_class.get_fields().keys()
            for field_name, value in filterset_class.items():
                if field_name not in filter_fields: continue
                widget = value.field.widget
                if isinstance(widget, SelectMultiple):
                    widget.input_type = 'select-multiple'
                if isinstance(widget, DateRangeWidget):
                    widget.input_type = 'datetimerange'
                if isinstance(widget, DateTimeInput):
                    widget.input_type = 'datetime'
                # if hasattr(value.field, 'queryset'):  # 将一些具有关联的字段的数据置空
                #     widget.input_type = 'text'
                #     widget.choices = []
                if hasattr(value, 'input_type'): widget.input_type = value.input_type
                choices = list(getattr(widget, 'choices', []))
                if choices and len(choices) > 0 and choices[0][0] == "":
                    choices.pop(0)
                field = get_model_field(self.filterset_class._meta.model, value.field_name)
                results.append({
                    'key': field_name,
                    'label': value.label if value.label else (
                        getattr(field, 'verbose_name', field.name) if field else field_name),
                    'help_text': value.field.help_text if value.field.help_text else getattr(field, 'help_text', None),
                    'input_type': widget.input_type,
                    'choices': get_choices_dict(choices),
                    'default': [] if 'multiple' in widget.input_type else ""
                })
            order_choices = []
            ordering_fields = list(getattr(self, 'ordering_fields', []))
            for choice in ordering_fields:
                is_des = False
                if choice.startswith('-'):
                    choice = choice[1:]
                    is_des = True
                label = choice
                field = get_model_field(self.filterset_class._meta.model, choice)
                if field:
                    label = getattr(field, 'verbose_name', choice)
                des = (f"-{choice}", f"{label} descending")
                ase = (choice, f"{label} ascending")
                if is_des:
                    des, ase = ase, des
                order_choices.extend([des, ase])
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


class SearchColumnsAction(object):
    filterset_class: Callable

    @extend_schema(
        responses=get_default_response_schema(
            {
                'data': build_array_type(
                    build_object_type(
                        properties={
                            'key': build_basic_type(OpenApiTypes.STR),
                            'label': build_basic_type(OpenApiTypes.STR),
                            'help_text': build_basic_type(OpenApiTypes.STR),
                            'default': build_basic_type(OpenApiTypes.ANY),
                            'input_type': build_basic_type(OpenApiTypes.STR),
                            'required': build_basic_type(OpenApiTypes.BOOL),
                            'read_only': build_basic_type(OpenApiTypes.BOOL),
                            'write_only': build_basic_type(OpenApiTypes.BOOL),
                            'multiple': build_basic_type(OpenApiTypes.BOOL),
                            'max_length': build_basic_type(OpenApiTypes.NUMBER),
                            'table_show': build_basic_type(OpenApiTypes.NUMBER),
                            'choices': build_array_type(
                                build_object_type(
                                    properties={
                                        'pk': build_basic_type(OpenApiTypes.STR),
                                        'value': build_basic_type(OpenApiTypes.STR),
                                        'label': build_basic_type(OpenApiTypes.STR),
                                    }
                                )
                            )
                        }
                    )
                )
            }
        )
    )
    @action(methods=['get'], detail=False, url_path='search-columns')
    def search_columns(self, request, *args, **kwargs):
        """获取{cls}的展示字段"""
        results = []

        def get_input_type(value, info):
            if hasattr(value, 'child_relation') and isinstance(value.child_relation, BasePrimaryKeyRelatedField):
                info['multiple'] = True
                setattr(value.child_relation, 'is_column', True)
                tp = value.child_relation.input_type if value.child_relation.input_type else info['type']
            else:
                tp = info['type']
            if tp and tp.endswith('related_field'):
                setattr(value, 'is_column', True)
                info['choices'] = json.loads(json.dumps(value.choices, cls=encoders.JSONEncoder))
                # info['choices'] = [{'value': k, 'label': v} for k, v in value.choices.items()]
            return tp

        metadata_class = self.metadata_class()
        serializer = self.get_serializer()
        fields = getattr(serializer, 'fields', [])
        meta = getattr(serializer, 'Meta', {})
        table_fields = getattr(meta, 'table_fields', [])
        for key, value in fields.items():
            info = metadata_class.get_field_info(value)
            if hasattr(meta, 'model'):
                field = get_model_field(meta.model, value.source)
            else:
                field = None
            info['key'] = key
            if info.get("help_text", None) is None and hasattr(field, 'help_text'):
                info['help_text'] = field.help_text

            if value.field_name.replace('_', ' ').capitalize() == info['label'] and hasattr(field, 'verbose_name'):
                info['label'] = field.verbose_name

            if isinstance(value, CharField) and value.style.get('base_template', '') == 'textarea.html':
                info['input_type'] = 'textarea'
            else:
                info['input_type'] = get_input_type(value, info)
            del info['type']
            if not table_fields:
                info['table_show'] = 1
            if key in table_fields:
                info['table_show'] = (table_fields.index(key)) + 1
            results.append(info)
        return ApiResponse(data=results)


class BaseViewSet(object):
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


class BatchDestroyAction(object):
    filter_queryset: Callable
    get_queryset: Callable
    perform_destroy: Callable

    @extend_schema(
        request=OpenApiRequest(build_array_type(build_basic_type(OpenApiTypes.STR))),
        responses=get_default_response_schema()
    )
    @action(methods=['post'], detail=False, url_path='batch-destroy')
    def batch_destroy(self, request, *args, **kwargs):
        """批量删除{cls}"""

        # response = run_view_by_celery_task(self, request, kwargs, request.data, batch_length=30)
        # if response:
        #     return response

        # queryset  delete() 方法进行批量删除，并不调用模型上的任何 delete() 方法,需要通过循环对象进行删除
        count = 0
        for instance in self.filter_queryset(self.get_queryset()).filter(pk__in=request.data):
            try:
                deleted, _rows_count = self.perform_destroy(instance)
                if deleted:
                    count += 1
            except Exception as e:
                logger.error(f"failed to destroy instance {instance} with error {e}")
        return ApiResponse(detail=_("Operation successful. Batch deleted {} data").format(count))


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
        kwargs['partial'] = True
        return self.update(request, *args, **kwargs)


class OnlyExportDataAction(ListAction):
    @extend_schema(
        parameters=[
            OpenApiParameter(name='type', required=True, enum=['xlsx', 'csv']),
        ],
        responses={
            200: OpenApiResponse(build_basic_type(OpenApiTypes.BINARY))
        }
    )
    @action(methods=['get'], detail=False, url_path='export-data')
    def export_data(self, request, *args, **kwargs):
        """导出{cls}数据"""
        self.format_kwarg = request.query_params.get('type', 'xlsx')
        request.no_cache = True  # 防止自定义缓存数据
        self.renderer_classes = [ExcelFileRenderer, CSVFileRenderer]
        request.accepted_renderer = None
        data = self.list(request, *args, **kwargs)
        return data


class ImportExportDataAction(CreateAction, UpdateAction, OnlyExportDataAction):
    filter_queryset: Callable
    get_queryset: Callable
    get_serializer: Callable

    @extend_schema(
        parameters=[
            OpenApiParameter(name='action', required=True, enum=['create', 'update']),
        ],
        request=OpenApiRequest(
            build_basic_type(OpenApiTypes.BINARY),
        ),
        responses={
            200: OpenApiResponse(build_basic_type(OpenApiTypes.BINARY))
        }
    )
    @action(methods=['post'], detail=False, url_path='import-data')
    @transaction.atomic
    def import_data(self, request, *args, **kwargs):
        """导入{cls}数据"""

        task = kwargs.get("task", request.query_params.get('task', 'true').lower() in ['true', '1', 'yes'])  # 默认为任务异步导入
        if task:
            # 如果包含自关联数据，则对数据进行排序，将依赖数据先导入，并且取消批量导入任务
            datas = request.data
            if isinstance(datas, dict):
                datas = [datas]
            self_field = has_self_fields(self.queryset.model)
            if self_field:
                batch_length = 99999999
                datas = topological_sort(datas, parent=self_field)
            else:
                batch_length = 100
            response = run_view_by_celery_task(self, request, kwargs, datas, batch_length)
            if response:
                return response

        act = request.query_params.get('action')
        ignore_error = request.query_params.get('ignore_error', 'false') == 'true'
        if act and request.data:
            count = 0
            if act == 'create':
                for data in request.data:
                    serializer = self.get_serializer(data=data)
                    serializer.is_valid(raise_exception=not ignore_error)
                    if serializer.errors and ignore_error:
                        continue
                    self.perform_create(serializer)
                    count += 1
            elif act == 'update':
                queryset = self.filter_queryset(self.get_queryset())
                for data in request.data:
                    instance = queryset.filter(pk=data.get('pk')).first()
                    if not instance:
                        continue
                    serializer = self.get_serializer(instance, data=data, partial=True)
                    serializer.is_valid(raise_exception=not ignore_error)
                    if serializer.errors and ignore_error:
                        continue
                    self.perform_update(serializer)
                    count += 1
            return ApiResponse(detail=_("Operation successful. Import {} data").format(count))
        return ApiResponse(detail=_("Operation failed. Abnormal data"), code=1001)


class DetailUpdateModelSet(BaseViewSet, UpdateAction, DetailAction, GenericViewSet):
    pass


class OnlyListModelSet(BaseViewSet, ListAction, SearchFieldsAction, SearchColumnsAction, GenericViewSet):
    pass


# 全部 ViewSet 包含增删改查 
class BaseModelSet(BaseViewSet, CreateAction, DestroyAction, UpdateAction, ListAction, DetailAction, SearchFieldsAction,
                   SearchColumnsAction, BatchDestroyAction, GenericViewSet):
    pass


# 只允许读和删除，不允许创建和修改
class ListDeleteModelSet(BaseViewSet, DestroyAction, ListAction, DetailAction, SearchFieldsAction, SearchColumnsAction,
                         BatchDestroyAction, GenericViewSet):
    pass


class NoDetailModelSet(BaseViewSet, UpdateAction, DetailAction, SearchColumnsAction, GenericViewSet):
    pass
