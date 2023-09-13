#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : notification
# author : ly_13
# date : 9/8/2023
from django_filters import rest_framework as filters
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter

from common.base.utils import get_choices_dict
from common.core.filter import OwnerUserFilter
from common.core.modelset import BaseModelSet
from common.core.response import ApiResponse
from system.models import Notification
from system.utils.serializer import NotifySerializer, SimpleNotifySerializer


class NotifyMessageFilter(filters.FilterSet):
    message = filters.CharFilter(field_name='message', lookup_expr='icontains')
    title = filters.CharFilter(field_name='title', lookup_expr='icontains')
    owner_id = filters.NumberFilter(field_name='owner__id')

    class Meta:
        model = Notification
        fields = ['unread', 'owner_id', 'notify_type', 'level', 'publish']


class NotifyMessage(BaseModelSet):
    queryset = Notification.objects.all()
    serializer_class = NotifySerializer

    filter_backends = [filters.DjangoFilterBackend, OrderingFilter]
    ordering_fields = ['updated_time', 'created_time', 'pk']
    filterset_class = NotifyMessageFilter

    def list(self, request, *args, **kwargs):
        data = super().list(request, *args, **kwargs).data
        return ApiResponse(**data, choices_dict=get_choices_dict(Notification.level_choices))

    @action(methods=['put'], detail=True)
    def publish(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.publish = request.data.get('publish')
        instance.save(update_fields=['publish'])
        return ApiResponse()


class UserNotice(BaseModelSet):
    queryset = Notification.objects.filter(publish=True).all()
    serializer_class = SimpleNotifySerializer
    filter_backends = [filters.DjangoFilterBackend, OrderingFilter, OwnerUserFilter]
    ordering_fields = ['created_time', 'pk']

    @action(methods=['get'], detail=False)
    def unread(self, request, *args, **kwargs):
        self.queryset = self.get_queryset().filter(unread=True)
        return super().list(request, *args, **kwargs)

    @action(methods=['put'], detail=False)
    def read(self, request, *args, **kwargs):
        pks = request.data.get('pks')
        request.user.notifications.filter(pk__in=pks, unread=True).update(unread=False)
        return ApiResponse(detail="操作成功")
