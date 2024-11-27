#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : message
# author : ly_13
# date : 9/15/2024

from django_filters import rest_framework as filters
from drf_spectacular.plumbing import build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiRequest
from rest_framework.decorators import action

from common.core.filter import BaseFilterSet, PkMultipleFilter
from common.core.modelset import BaseModelSet, ListDeleteModelSet
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from notifications.models import MessageContent, MessageUserRead
from notifications.serializers.message import NoticeMessageSerializer, NoticeUserReadMessageSerializer, \
    AnnouncementSerializer


class NoticeMessageFilter(BaseFilterSet):
    message = filters.CharFilter(field_name='message', lookup_expr='icontains')
    title = filters.CharFilter(field_name='title', lookup_expr='icontains')

    class Meta:
        model = MessageContent
        fields = ['pk', 'title', 'message', 'notice_type', 'level', 'publish']


class NoticeMessageViewSet(BaseModelSet):
    """消息通知"""
    queryset = MessageContent.objects.all()
    serializer_class = NoticeMessageSerializer

    ordering_fields = ['updated_time', 'created_time']
    filterset_class = NoticeMessageFilter

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={'publish': build_basic_type(OpenApiTypes.BOOL)},
                required=['publish']
            )
        ),
        responses=get_default_response_schema()
    )
    @action(methods=['patch'], detail=True)
    def publish(self, request, *args, **kwargs):
        """修改{cls}状态"""
        instance: MessageContent = self.get_object()
        instance.publish = request.data.get('publish')
        instance.modifier = request.user
        instance.save(update_fields=['publish', 'modifier'])
        return ApiResponse()

    @action(methods=['post'], detail=False)
    def announcement(self, request, *args, **kwargs):
        """添加{cls}公告"""
        self.serializer_class = AnnouncementSerializer
        return super().create(request, *args, **kwargs)


class NoticeUserReadMessageFilter(BaseFilterSet):
    message = filters.CharFilter(field_name='notice__message', lookup_expr='icontains', label='Message')
    title = filters.CharFilter(field_name='notice__title', lookup_expr='icontains')
    username = filters.CharFilter(field_name='owner__username')
    notice_id = filters.NumberFilter(field_name='notice__pk')
    notice_type = filters.ChoiceFilter(field_name='notice__notice_type', choices=MessageContent.NoticeChoices.choices)
    level = filters.MultipleChoiceFilter(field_name='notice__level', choices=MessageContent.LevelChoices)
    owner_id = PkMultipleFilter(input_type='api-search-user')

    class Meta:
        model = MessageUserRead
        fields = ['notice_id', 'title', 'username', 'owner_id', 'notice_type', 'unread', 'level', 'message']


class NoticeUserReadMessageViewSet(ListDeleteModelSet):
    """已读消息公告"""
    queryset = MessageUserRead.objects.all()
    serializer_class = NoticeUserReadMessageSerializer
    choices_models = [MessageContent]
    ordering_fields = ['updated_time', 'created_time']
    filterset_class = NoticeUserReadMessageFilter

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={'unread': build_basic_type(OpenApiTypes.BOOL)},
                required=['unread']
            )
        ),
        responses=get_default_response_schema()
    )
    @action(methods=['patch'], detail=True)
    def state(self, request, *args, **kwargs):
        """修改{cls}状态"""
        instance = self.get_object()
        if instance.notice.notice_type in MessageContent.get_user_choices():
            instance.unread = request.data.get('unread', True)
            instance.modifier = request.user
            instance.save(update_fields=['unread', 'modifier'])
        if instance.notice.notice_type in MessageContent.get_notice_choices():
            instance.delete()
        return ApiResponse()
