#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : menu
# author : ly_13
# date : 7/22/2024

from django.utils.translation import gettext_lazy as _
from django_filters import rest_framework as filters
from rest_framework import serializers

from common.core.filter import BaseFilterSet
from common.core.modelset import OnlyListModelSet
from common.core.pagination import DynamicPageNumber
from system.models import Menu
from system.serializers.menu import MenuSerializer


class SearchMenuFilter(BaseFilterSet):
    component = filters.CharFilter(field_name='component', lookup_expr='icontains')
    title = filters.CharFilter(field_name='meta__title', lookup_expr='icontains')
    path = filters.CharFilter(field_name='path', lookup_expr='icontains')

    class Meta:
        model = Menu
        fields = ['title', 'path', 'component']


class SearchMenuSerializer(MenuSerializer):
    class Meta:
        model = Menu
        fields = ['title', 'pk', 'rank', 'path', 'component', 'parent', 'menu_type', 'is_active', 'method']
        table_fields = ['title', 'menu_type', 'path', 'component', 'is_active', 'method']
        read_only_fields = [x.name for x in Menu._meta.fields]

    title = serializers.CharField(source='meta.title', read_only=True, label=_("Menu title"))


class SearchMenuViewSet(OnlyListModelSet):
    """菜单搜索"""
    queryset = Menu.objects.order_by('rank').all()
    serializer_class = SearchMenuSerializer
    pagination_class = DynamicPageNumber(1000)
    ordering_fields = ['-rank', 'updated_time', 'created_time']
    filterset_class = SearchMenuFilter
