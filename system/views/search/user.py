#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : user
# author : ly_13
# date : 7/22/2024

from django_filters import rest_framework as filters

from common.core.filter import BaseFilterSet
from common.core.modelset import OnlyListModelSet
from system.models import UserInfo
from system.serializers.user import UserSerializer


class SearchUserFilter(BaseFilterSet):
    username = filters.CharFilter(field_name='username', lookup_expr='icontains')
    nickname = filters.CharFilter(field_name='nickname', lookup_expr='icontains')
    phone = filters.CharFilter(field_name='phone', lookup_expr='icontains')

    class Meta:
        model = UserInfo
        fields = ['username', 'nickname', 'phone', 'email', 'is_active', 'gender', 'dept']


class SearchUserSerializer(UserSerializer):
    class Meta:
        model = UserInfo
        fields = ['pk', 'avatar', 'username', 'nickname', 'phone', 'email', 'gender', 'is_active', 'password', 'dept',
                  'description', 'last_login', 'date_joined']

        read_only_fields = [x.name for x in UserInfo._meta.fields]

        table_fields = ['pk', 'avatar', 'username', 'nickname', 'gender', 'is_active', 'dept', 'phone',
                        'last_login', 'date_joined']


class SearchUserViewSet(OnlyListModelSet):
    """用户搜索"""
    queryset = UserInfo.objects.all()
    serializer_class = SearchUserSerializer

    ordering_fields = ['date_joined', 'last_login', 'created_time']
    filterset_class = SearchUserFilter
