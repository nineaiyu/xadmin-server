#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : notice
# author : ly_13
# date : 9/15/2023

from django_filters import rest_framework as filters
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework.decorators import action

from common.core.filter import BaseFilterSet
from common.core.modelset import BaseModelSet, ListDeleteModelSet
from common.core.response import ApiResponse
from system.models import NoticeMessage, NoticeUserRead
from system.utils.serializer import NoticeMessageSerializer, NoticeUserReadMessageSerializer, AnnouncementSerializer


class NoticeMessageFilter(BaseFilterSet):
    message = filters.CharFilter(field_name='message', lookup_expr='icontains')
    title = filters.CharFilter(field_name='title', lookup_expr='icontains')

    class Meta:
        model = NoticeMessage
        fields = ['pk', 'title', 'message', 'notice_type', 'level', 'publish']


class NoticeMessageView(BaseModelSet):
    """消息通知管理"""
    queryset = NoticeMessage.objects.all()
    serializer_class = NoticeMessageSerializer

    ordering_fields = ['updated_time', 'created_time']
    filterset_class = NoticeMessageFilter

    @swagger_auto_schema(request_body=openapi.Schema(type=openapi.TYPE_OBJECT, properties={
        'publish': openapi.Schema(type=openapi.TYPE_BOOLEAN)}))
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


class NoticeUserReadMessageFilter(BaseFilterSet):
    message = filters.CharFilter(field_name='notice__message', lookup_expr='icontains', label='Message')
    title = filters.CharFilter(field_name='notice__title', lookup_expr='icontains')
    username = filters.CharFilter(field_name='owner__username')
    owner_id = filters.NumberFilter(field_name='owner__pk')
    notice_id = filters.NumberFilter(field_name='notice__pk')
    notice_type = filters.ChoiceFilter(field_name='notice__notice_type', choices=NoticeMessage.NoticeChoices.choices)
    level = filters.MultipleChoiceFilter(field_name='notice__level', choices=NoticeMessage.LevelChoices)

    class Meta:
        model = NoticeUserRead
        fields = ['title', 'username', 'owner_id', 'notice_id', 'notice_type', 'unread', 'level', 'message']


class NoticeUserReadMessageView(ListDeleteModelSet):
    """用户消息公告已读管理"""
    queryset = NoticeUserRead.objects.all()
    serializer_class = NoticeUserReadMessageSerializer
    choices_models = [NoticeMessage]
    ordering_fields = ['updated_time', 'created_time']
    filterset_class = NoticeUserReadMessageFilter

    @swagger_auto_schema(request_body=openapi.Schema(type=openapi.TYPE_OBJECT, properties={
        'unread': openapi.Schema(type=openapi.TYPE_BOOLEAN)}))
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
