#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : notice
# author : ly_13
# date : 3/4/2024
from hashlib import md5

from django.db.models import Q
from django_filters import rest_framework as filters
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter

from common.base.magic import cache_response
from common.core.filter import BaseFilterSet
from common.core.modelset import OnlyListModelSet
from common.core.response import ApiResponse
from system.models import NoticeMessage, NoticeUserRead
from system.utils.serializer import UserNoticeSerializer


def get_users_notice_q(user_obj):
    q = Q()
    q |= Q(notice_type=NoticeMessage.NoticeChoices.NOTICE)
    q |= Q(notice_type=NoticeMessage.NoticeChoices.DEPT, notice_dept=user_obj.dept)
    q |= Q(notice_type=NoticeMessage.NoticeChoices.ROLE, notice_role__in=user_obj.roles.all())
    return q


def get_user_unread_q1(user_obj):
    return get_users_notice_q(user_obj) & ~Q(notice_user=user_obj)


def get_user_unread_q2(user_obj):
    return Q(notice_type__in=NoticeMessage.user_choices, notice_user=user_obj, noticeuserread__unread=True)


def get_user_unread_q(user_obj):
    return get_user_unread_q1(user_obj) | get_user_unread_q2(user_obj)


class UserNoticeMessageFilter(BaseFilterSet):
    message = filters.CharFilter(field_name='message', lookup_expr='icontains')
    title = filters.CharFilter(field_name='title', lookup_expr='icontains')
    unread = filters.BooleanFilter(field_name='unread', method='unread_filter')

    def unread_filter(self, queryset, name, value):
        if value:
            return queryset.filter(get_user_unread_q(self.request.user))
        else:
            return queryset.filter(notice_user=self.request.user, noticeuserread__unread=False)

    class Meta:
        model = NoticeMessage
        fields = ['title', 'message', 'pk', 'notice_type', 'unread', 'level']


class UserNoticeMessage(OnlyListModelSet):
    """用户个人通知公告管理"""
    queryset = NoticeMessage.objects.filter(publish=True).all().distinct()
    serializer_class = UserNoticeSerializer
    filter_backends = [filters.DjangoFilterBackend, OrderingFilter]
    ordering_fields = ['created_time']
    filterset_class = UserNoticeMessageFilter

    @cache_response(timeout=600, key_func='get_cache_key')
    def list(self, request, *args, **kwargs):
        unread_count = self.filter_queryset(self.get_queryset()).filter(get_user_unread_q(self.request.user)).count()
        q = get_users_notice_q(request.user)
        q |= Q(notice_type__in=NoticeMessage.user_choices, notice_user=request.user)
        self.queryset = self.filter_queryset(self.get_queryset()).filter(q)
        data = super().list(request, *args, **kwargs).data
        return ApiResponse(**data, unread_count=unread_count)

    def get_cache_key(self, view_instance, view_method, request, args, kwargs):
        func_name = f'{view_instance.__class__.__name__}_{view_method.__name__}'
        return f"{func_name}_{request.user.pk}_{md5(request.META['QUERY_STRING'].encode('utf-8')).hexdigest()}"

    @swagger_auto_schema(ignore_params=True)
    @cache_response(timeout=600, key_func='get_cache_key')
    @action(methods=['get'], detail=False)
    def unread(self, request, *args, **kwargs):
        notice_queryset = self.filter_queryset(self.get_queryset()).filter(get_user_unread_q2(request.user))
        announce_queryset = self.filter_queryset(self.get_queryset()).filter(get_user_unread_q1(request.user))
        results = [
            {
                "key": "1",
                "name": "消息通知",
                "list": self.serializer_class(notice_queryset[:10], many=True, context={'request': request}).data
            },
            {
                "key": "2",
                "name": "系统公告",
                "list": self.serializer_class(announce_queryset[:10], many=True, context={'request': request}).data
            }
        ]

        return ApiResponse(data={'results': results, 'total': notice_queryset.count() + announce_queryset.count()})

    def read_message(self, pks, request):
        if pks:
            NoticeUserRead.objects.filter(notice__id__in=pks, owner=request.user, unread=True).update(unread=False)
            for pk in pks:
                NoticeUserRead.objects.update_or_create(owner=request.user, notice_id=pk, defaults={'unread': False})
        return ApiResponse(detail="操作成功")

    @swagger_auto_schema(request_body=openapi.Schema(
        type=openapi.TYPE_OBJECT,
        required=['pks'],
        properties={'pks': openapi.Schema(description='主键列表', type=openapi.TYPE_ARRAY,
                                          items=openapi.Schema(type=openapi.TYPE_STRING))}
    ), operation_description='批量已读消息')
    @action(methods=['put'], detail=False)
    def read(self, request, *args, **kwargs):
        pks = request.data.get('pks', [])
        return self.read_message(pks, request)

    @swagger_auto_schema(ignore_params=True)
    @action(methods=['put'], detail=False, url_path='read-all')
    def read_all(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset()).filter(get_user_unread_q(self.request.user))
        return self.read_message(queryset.values_list('pk', flat=True).distinct(), request)
