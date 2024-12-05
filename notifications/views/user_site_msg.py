#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : user_site_msg
# author : ly_13
# date : 9/15/2024

from django.db.models import Q
from django_filters import rest_framework as filters
from drf_spectacular.plumbing import build_object_type, build_basic_type, build_array_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, inline_serializer, \
    OpenApiRequest
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter

from common.core.filter import BaseFilterSet
from common.core.modelset import OnlyListModelSet, CacheListResponseMixin
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from notifications.models import MessageContent, MessageUserRead
from notifications.serializers.message import UserNoticeSerializer


def get_users_notice_q(user_obj):
    q = Q()
    q |= Q(notice_type=MessageContent.NoticeChoices.NOTICE)
    q |= Q(notice_type=MessageContent.NoticeChoices.DEPT, notice_dept=user_obj.dept)
    q |= Q(notice_type=MessageContent.NoticeChoices.ROLE, notice_role__in=user_obj.roles.all())
    return q


def get_user_unread_q1(user_obj):
    return get_users_notice_q(user_obj) & ~Q(notice_user=user_obj)


def get_user_unread_q2(user_obj):
    return Q(notice_type__in=MessageContent.get_user_choices(), notice_user=user_obj, messageuserread__unread=True)


def get_user_unread_q(user_obj):
    return get_user_unread_q1(user_obj) | get_user_unread_q2(user_obj)


class UserSiteMessageViewSetFilter(BaseFilterSet):
    message = filters.CharFilter(field_name='message', lookup_expr='icontains')
    title = filters.CharFilter(field_name='title', lookup_expr='icontains')
    unread = filters.BooleanFilter(field_name='unread', method='unread_filter')

    def unread_filter(self, queryset, name, value):
        if value:
            return queryset.filter(get_user_unread_q(self.request.user))
        else:
            return queryset.filter(notice_user=self.request.user, messageuserread__unread=False)

    class Meta:
        model = MessageContent
        fields = ['title', 'message', 'pk', 'notice_type', 'unread', 'level']


class UserSiteMessageViewSet(OnlyListModelSet, CacheListResponseMixin):
    """用户消息中心"""
    queryset = MessageContent.objects.filter(publish=True).all().distinct()
    serializer_class = UserNoticeSerializer
    filter_backends = [filters.DjangoFilterBackend, OrderingFilter]
    ordering_fields = ['created_time']
    filterset_class = UserSiteMessageViewSetFilter

    # @cache_response(timeout=600, key_func='get_cache_key')
    def list(self, request, *args, **kwargs):
        unread_count = self.filter_queryset(self.get_queryset()).filter(get_user_unread_q(self.request.user)).count()
        q = get_users_notice_q(request.user)
        q |= Q(notice_type__in=MessageContent.get_user_choices(), notice_user=request.user)
        self.queryset = self.filter_queryset(self.get_queryset()).filter(q)
        data = super().list(request, *args, **kwargs).data
        return ApiResponse(**data, unread_count=unread_count)

    @extend_schema(
        parameters=[],
        responses={
            200: inline_serializer(name='unread', fields={
                'code': serializers.IntegerField(),
                'detail': serializers.CharField(),
                'data': inline_serializer(name='data', fields={
                    'results': inline_serializer(name='results', fields={
                        'key': serializers.CharField(),
                        'name': serializers.CharField(),
                        'list': UserNoticeSerializer(many=True),
                        'total': serializers.IntegerField()
                    }),
                    'total': serializers.IntegerField(),
                })
            })
        }
    )
    # @cache_response(timeout=600, key_func='get_cache_key')
    @action(methods=['get'], detail=False)
    def unread(self, request, *args, **kwargs):
        """用户未读消息"""
        notice_queryset = self.filter_queryset(self.get_queryset()).filter(get_user_unread_q2(request.user))
        announce_queryset = self.filter_queryset(self.get_queryset()).filter(get_user_unread_q1(request.user))
        results = [
            {
                "key": "1",
                "name": "layout.notice",
                "list": self.serializer_class(notice_queryset[:10], many=True, context={'request': request}).data,
                "total": notice_queryset.count()
            },
            {
                "key": "2",
                "name": "layout.announcement",
                "list": self.serializer_class(announce_queryset[:10], many=True, context={'request': request}).data,
                "total": announce_queryset.count()
            }
        ]

        return ApiResponse(data={'results': results, 'total': sum([item.get('total', 0) for item in results])})

    def read_message(self, pks, request):
        if pks:
            MessageUserRead.objects.filter(notice__id__in=pks, owner=request.user, unread=True).update(unread=False)
            for pk in pks:
                MessageUserRead.objects.update_or_create(owner=request.user, notice_id=pk, defaults={'unread': False})
        return ApiResponse()

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={'pks': build_array_type(build_basic_type(OpenApiTypes.STR))},
                required=['pks'],
                description="主键列表"
            )
        ),
        responses=get_default_response_schema()
    )
    @action(methods=['patch'], detail=False, url_path='batch-read')
    def batch_read(self, request, *args, **kwargs):
        """批量已读消息"""
        pks = request.data.get('pks', [])
        return self.read_message(pks, request)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=['patch'], detail=False, url_path='all-read')
    def all_read(self, request, *args, **kwargs):
        """全部已读消息"""
        queryset = self.filter_queryset(self.get_queryset()).filter(get_user_unread_q(self.request.user))
        return self.read_message(queryset.values_list('pk', flat=True).distinct(), request)
