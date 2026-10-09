#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : message
# author : ly_13
# date : 9/15/2024
from typing import Any

from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action

from common.core.filter import BaseFilterSet, PkMultipleFilter
from common.core.modelset import BaseModelSet, ListDeleteModelSet, RecycleBinAction
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from notifications.models import MessageContent, MessageUserRead
from notifications.serializers.message import (
    AnnouncementSerializer,
    NoticeMessageSerializer,
    NoticePublishSerializer,
    NoticeUserReadMessageSerializer,
    NoticeUserReadStateSerializer,
)


class NoticeMessageFilter(BaseFilterSet):
    message = filters.CharFilter(field_name="message", lookup_expr="icontains")
    title = filters.CharFilter(field_name="title", lookup_expr="icontains")

    class Meta:
        model = MessageContent
        fields = ["pk", "title", "message", "notice_type", "level", "publish"]


class NoticeMessageViewSet(RecycleBinAction, BaseModelSet):
    """消息通知"""

    queryset = MessageContent.objects.all()
    serializer_class = NoticeMessageSerializer

    ordering_fields = ["updated_time", "created_time"]
    filterset_class = NoticeMessageFilter

    @extend_schema(
        request=NoticePublishSerializer,
        responses=get_default_response_schema(),
    )
    @action(methods=["patch"], detail=True)  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def publish(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """修改{cls}状态"""
        serializer = NoticePublishSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance: MessageContent = self.get_object()
        instance.publish = serializer.validated_data["publish"]
        instance.modifier = request.user
        instance.save(update_fields=["publish", "modifier"])
        return ApiResponse()

    @action(methods=["post"], detail=False)  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def announcement(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """添加{cls}公告"""
        self.serializer_class = AnnouncementSerializer
        return super().create(request, *args, **kwargs)


class NoticeUserReadMessageFilter(BaseFilterSet):
    message = filters.CharFilter(field_name="notice__message", lookup_expr="icontains", label="Message")
    title = filters.CharFilter(field_name="notice__title", lookup_expr="icontains")
    username = filters.CharFilter(field_name="owner__username")
    notice_id = filters.NumberFilter(field_name="notice__pk")
    notice_type = filters.ChoiceFilter(field_name="notice__notice_type", choices=MessageContent.NoticeChoices.choices)
    level = filters.MultipleChoiceFilter(field_name="notice__level", choices=MessageContent.LevelChoices)
    owner_id = PkMultipleFilter(input_type="api-search-user")

    class Meta:
        model = MessageUserRead
        fields = ["notice_id", "title", "username", "owner_id", "notice_type", "unread", "level", "message"]


class NoticeUserReadMessageViewSet(ListDeleteModelSet):
    """已读消息公告"""

    queryset = MessageUserRead.objects.all()
    serializer_class = NoticeUserReadMessageSerializer
    choices_models = [MessageContent]
    ordering_fields = ["updated_time", "created_time"]
    filterset_class = NoticeUserReadMessageFilter

    @extend_schema(
        request=NoticeUserReadStateSerializer,
        responses=get_default_response_schema(),
    )
    @action(methods=["patch"], detail=True)  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def state(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """修改{cls}状态"""
        serializer = NoticeUserReadStateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = self.get_object()
        # 两类消息的 state 语义相反：用户类消息的已读状态可回写（unread 开关，可再置未读）；
        # 公告类不持久化阅读状态——state 表示「从我的列表移除」，故直接删行。勿合并两分支。
        if instance.notice.notice_type in MessageContent.get_user_choices():
            instance.unread = serializer.validated_data["unread"]
            instance.modifier = request.user
            instance.save(update_fields=["unread", "modifier"])
        if instance.notice.notice_type in MessageContent.get_notice_choices():
            instance.delete()
        return ApiResponse()
