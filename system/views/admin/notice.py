#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : notice
# author : ly_13
# date : 9/15/2023

from django_filters import rest_framework as filters
from rest_framework.decorators import action

from common.base.utils import get_choices_dict
from common.core.modelset import BaseModelSet
from common.core.response import ApiResponse
from system.models import NoticeMessage, NoticeUserRead
from system.utils.serializer import NoticeMessageSerializer, NoticeUserReadMessageSerializer, AnnouncementSerializer


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

    ordering_fields = ['updated_time', 'created_time', 'pk']
    filterset_class = NoticeMessageFilter

    def list(self, request, *args, **kwargs):
        data = super().list(request, *args, **kwargs).data
        return ApiResponse(**data, level_choices=get_choices_dict(NoticeMessage.LevelChoices.choices),
                           notice_type_choices=get_choices_dict(NoticeMessage.NoticeChoices.choices))

    @action(methods=['put'], detail=True)
    def publish(self, request, *args, **kwargs):
        instance: NoticeMessage = self.get_object()
        instance.publish = request.data.get('publish')
        instance.modifier = request.user
        instance.save(update_fields=['publish', 'modifier'])
        return ApiResponse()

    @action(methods=['post'], detail=False)
    def announcement(self, request, *args, **kwargs):
        self.serializer_class = AnnouncementSerializer
        return super().create(request, *args, **kwargs)


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

    ordering_fields = ['updated_time', 'created_time', 'pk']
    filterset_class = NoticeUserReadMessageFilter

    def list(self, request, *args, **kwargs):
        data = super().list(request, *args, **kwargs).data
        return ApiResponse(**data, level_choices=get_choices_dict(NoticeMessage.LevelChoices.choices),
                           notice_type_choices=get_choices_dict(NoticeMessage.NoticeChoices.choices))

    @action(methods=['put'], detail=True)
    def state(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.notice.notice_type in NoticeMessage.user_choices:
            instance.unread = request.data.get('unread', True)
            instance.modifier = request.user
            instance.save(update_fields=['unread', 'modifier'])
        if instance.notice.notice_type in NoticeMessage.notice_choices:
            instance.delete()
        return ApiResponse()
