#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : notice
# author : ly_13
# date : 9/15/2023
from django.db.models import Q
from django_filters import rest_framework as filters
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter

from common.base.utils import get_choices_dict
from common.core.modelset import BaseModelSet, OnlyListModelSet
from common.core.response import ApiResponse
from system.models import NoticeMessage, NoticeUserRead
from system.utils.serializer import NoticeMessageSerializer, NoticeUserReadMessageSerializer, UserNoticeSerializer


class NoticeMessageFilter(filters.FilterSet):
    message = filters.CharFilter(field_name='message', lookup_expr='icontains')
    title = filters.CharFilter(field_name='title', lookup_expr='icontains')
    pk = filters.NumberFilter(field_name='id')

    class Meta:
        model = NoticeMessage
        fields = ['notice_type', 'level', 'publish']


class NoticeMessageView(BaseModelSet):
    queryset = NoticeMessage.objects.all()
    serializer_class = NoticeMessageSerializer

    filter_backends = [filters.DjangoFilterBackend, OrderingFilter]
    ordering_fields = ['updated_time', 'created_time', 'pk']
    filterset_class = NoticeMessageFilter

    def list(self, request, *args, **kwargs):
        data = super().list(request, *args, **kwargs).data
        return ApiResponse(**data, level_choices=get_choices_dict(NoticeMessage.level_choices),
                           notice_type_choices=get_choices_dict(NoticeMessage.notice_type_choices[1:]))

    @action(methods=['put'], detail=True)
    def publish(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.publish = request.data.get('publish')
        instance.save(update_fields=['publish'])
        return ApiResponse()


class NoticeUserReadMessageFilter(filters.FilterSet):
    message = filters.CharFilter(field_name='notice__message', lookup_expr='icontains')
    title = filters.CharFilter(field_name='notice__title', lookup_expr='icontains')
    username = filters.CharFilter(field_name='owner__username')
    owner_id = filters.NumberFilter(field_name='owner__pk')
    notice_id = filters.NumberFilter(field_name='notice__pk')
    notice_type = filters.NumberFilter(field_name='notice__notice_type')
    level = filters.CharFilter(field_name='notice__level')

    class Meta:
        model = NoticeUserRead
        fields = ['unread', ]


class NoticeUserReadMessageView(BaseModelSet):
    queryset = NoticeUserRead.objects.all()
    serializer_class = NoticeUserReadMessageSerializer

    filter_backends = [filters.DjangoFilterBackend, OrderingFilter]
    ordering_fields = ['updated_time', 'created_time', 'pk']
    filterset_class = NoticeUserReadMessageFilter

    def list(self, request, *args, **kwargs):
        data = super().list(request, *args, **kwargs).data
        return ApiResponse(**data, level_choices=get_choices_dict(NoticeMessage.level_choices),
                           notice_type_choices=get_choices_dict(NoticeMessage.notice_type_choices[1:]))

    @action(methods=['put'], detail=True)
    def state(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.notice.notice_type in [0, 1]:
            instance.unread = True
            instance.save(update_fields=['unread'])
        if instance.notice.notice_type == 2:
            instance.delete()
        return ApiResponse()


class UserNoticeMessageFilter(filters.FilterSet):
    message = filters.CharFilter(field_name='message', lookup_expr='icontains')
    title = filters.CharFilter(field_name='title', lookup_expr='icontains')
    pk = filters.NumberFilter(field_name='id')
    unread = filters.BooleanFilter(field_name='unread', method='unread_filter')

    def unread_filter(self, queryset, name, value):
        if value:
            return queryset.filter(
                (Q(notice_type=2) & ~Q(owner=self.request.user)) | Q(notice_type__in=[0, 1], owner=self.request.user,
                                                                     noticeuserread__unread=True))
        else:
            return queryset.filter(
                (Q(notice_type=2, owner=self.request.user)) | Q(notice_type__in=[0, 1], owner=self.request.user,
                                                                noticeuserread__unread=False))

    class Meta:
        model = NoticeMessage
        fields = ['notice_type', 'level']


class UserNoticeMessage(OnlyListModelSet):
    queryset = NoticeMessage.objects.filter(publish=True).all().distinct()
    serializer_class = UserNoticeSerializer
    filter_backends = [filters.DjangoFilterBackend, OrderingFilter]
    ordering_fields = ['created_time', 'pk']
    filterset_class = UserNoticeMessageFilter

    def list(self, request, *args, **kwargs):
        self.queryset = self.get_queryset().filter(Q(notice_type=2) | Q(notice_type__in=[0, 1], owner=request.user))
        data = super().list(request, *args, **kwargs).data
        return ApiResponse(**data, level_choices=get_choices_dict(NoticeMessage.level_choices),
                           notice_type_choices=get_choices_dict(NoticeMessage.notice_type_choices[1:]))

    @action(methods=['get'], detail=False)
    def unread(self, request, *args, **kwargs):
        notice_queryset = self.get_queryset().filter(notice_type__in=[0, 1], owner=request.user,
                                                     noticeuserread__unread=True)
        announce_queryset = self.get_queryset().filter(notice_type=2).exclude(owner=request.user)
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

    @action(methods=['put'], detail=False)
    def read(self, request, *args, **kwargs):
        pks = request.data.get('pks')
        NoticeUserRead.objects.filter(notice__id__in=pks, owner=request.user, unread=True).update(unread=False)
        for pk in pks:
            NoticeUserRead.objects.update_or_create(owner=request.user, notice_id=pk, defaults={'unread': False})
        return ApiResponse(detail="操作成功")
