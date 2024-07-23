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
from system.utils.serializer import UserSerializer


class SearchUserFilter(BaseFilterSet):
    username = filters.CharFilter(field_name='username', lookup_expr='icontains')
    nickname = filters.CharFilter(field_name='nickname', lookup_expr='icontains')
    mobile = filters.CharFilter(field_name='mobile', lookup_expr='icontains')

    class Meta:
        model = UserInfo
        fields = ['username', 'nickname', 'mobile', 'email', 'is_active', 'gender', 'dept']


class SearchUserSerializer(UserSerializer):
    class Meta:
        model = UserInfo
        fields = ['pk', 'avatar', 'username', 'nickname', 'mobile', 'email', 'gender', 'is_active', 'password', 'dept',
                  'description', 'last_login', 'date_joined']

        read_only_fields = [x.name for x in UserInfo._meta.fields]

        table_fields = ['pk', 'avatar', 'username', 'nickname', 'gender', 'is_active', 'dept', 'mobile',
                        'last_login', 'date_joined']


class SearchUserView(OnlyListModelSet):
    queryset = UserInfo.objects.all()
    serializer_class = SearchUserSerializer

    ordering_fields = ['date_joined', 'last_login', 'created_time']
    filterset_class = SearchUserFilter
