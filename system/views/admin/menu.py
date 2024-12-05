#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : menu
# author : ly_13
# date : 6/6/2023
from django.db.models.signals import post_save
from django_filters import rest_framework as filters
from drf_spectacular.plumbing import build_object_type, build_basic_type, build_array_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiRequest
from rest_framework.decorators import action

from common.base.magic import temporary_disable_signal
from common.core.filter import BaseFilterSet
from common.core.modelset import BaseModelSet, RankAction, ImportExportDataAction, ChoicesAction, CacheListResponseMixin
from common.core.pagination import DynamicPageNumber
from common.core.response import ApiResponse
from common.core.utils import get_all_url_dict
from common.swagger.utils import get_default_response_schema
from system.models import Menu, ModelLabelField
from system.serializers.menu import MenuSerializer
from system.signal_handler import clean_cache_handler
from system.utils.menu import get_view_permissions


class MenuFilter(BaseFilterSet):
    name = filters.CharFilter(field_name='name', lookup_expr='icontains')
    component = filters.CharFilter(field_name='component', lookup_expr='icontains')
    title = filters.CharFilter(field_name='meta__title', lookup_expr='icontains')
    path = filters.CharFilter(field_name='path', lookup_expr='icontains')

    class Meta:
        model = Menu
        fields = ['name']


class MenuViewSet(BaseModelSet, RankAction, ImportExportDataAction, ChoicesAction, CacheListResponseMixin):
    """菜单"""
    queryset = Menu.objects.order_by('rank').all()
    serializer_class = MenuSerializer
    pagination_class = DynamicPageNumber(1000)
    ordering_fields = ['updated_time', 'name', 'created_time', 'rank']
    filterset_class = MenuFilter

    # @cache_response(timeout=600, key_func='get_cache_key')
    # def list(self, request, *args, **kwargs):
    #     """获取{cls}的列表"""
    #     data = super().list(request, *args, **kwargs).data
    #     return ApiResponse(**data)

    @extend_schema(
        responses=get_default_response_schema(
            {
                'data': build_array_type(
                    build_object_type(
                        properties={
                            'name': build_basic_type(OpenApiTypes.STR),
                            'url': build_basic_type(OpenApiTypes.STR)
                        }
                    )
                )
            }
        )
    )
    @action(methods=['get'], detail=False, url_path='api-url')
    def api_url(self, request, *args, **kwargs):
        """获取后端API列表"""
        return ApiResponse(data=get_all_url_dict(''))

    @temporary_disable_signal(post_save, receiver=clean_cache_handler, sender=Menu)
    def _save_permissions(self, instance, permissions, skip_existing):
        # 该代码禁用了信号，菜单数据不刷新
        rank = 10000
        for permission in permissions:
            rank += 1
            models = ModelLabelField.objects.filter(field_type=ModelLabelField.FieldChoices.ROLE,
                                                    name__in=permission.get('models')).all()
            data = {
                'rank': rank,
                'is_active': True,
                'menu_type': Menu.MenuChoices.PERMISSION,
                'name': permission.get('code'),
                'parent': instance,
                'path': permission.get('url'),
                'method': permission.get('method'),
                'model': models,
                'meta': {
                    'title': permission.get('description')[:250]
                }
            }
            permission_menu = self.get_queryset().filter(menu_type=data['menu_type'], name=data['name']).first()
            if permission_menu:
                if skip_existing:
                    continue
                data['meta']['title'] = 'U-' + data['meta']['title']
                serializer = self.get_serializer(permission_menu, data=data, partial=True, ignore_field_permission=True)
                serializer.is_valid(raise_exception=True)
                self.perform_update(serializer)
            else:
                data['meta']['title'] = 'C-' + data['meta']['title']
                serializer = self.get_serializer(data=data, ignore_field_permission=True)
                serializer.is_valid(raise_exception=True)
                self.perform_create(serializer)

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    'views': build_array_type(build_basic_type(OpenApiTypes.STR)),
                    'component': build_basic_type(OpenApiTypes.STR),
                },
                required=['views'],
            )
        ),
        responses=get_default_response_schema()
    )
    @action(methods=['post'], detail=True, url_path='permissions')
    def permissions(self, request, *args, **kwargs):
        """自动添加API权限"""
        views = request.data.get('views')
        component = request.data.get('component')
        skip_existing = request.data.get('skip_existing')
        if isinstance(views, list) and len(views) > 0:
            instance = self.get_object()

            for view in views:
                code_suffix = view.split(".")[-1].replace('ViewSet', ' ').replace('APIView', ' ')
                if len(views) == 1:
                    if component:
                        code_suffix = component
                self._save_permissions(instance, get_view_permissions(view, code_suffix), skip_existing)

            # 保存数据，触发刷新缓存信号
            instance.save(update_fields=['is_active'])
            return ApiResponse()
        return ApiResponse(code=1001)
