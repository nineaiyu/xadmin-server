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
from common.core.modelset import BaseModelSet
from common.core.response import ApiResponse
from system.models import Announcement
from system.utils.serializer import AnnouncementSerializer, SimpleAnnouncementSerializer


class AnnouncementMessageFilter(filters.FilterSet):
    message = filters.CharFilter(field_name='message', lookup_expr='icontains')
    title = filters.CharFilter(field_name='title', lookup_expr='icontains')

    class Meta:
        model = Announcement
        fields = ['level', 'publish']


class AnnouncementMessage(BaseModelSet):
    queryset = Announcement.objects.all()
    serializer_class = AnnouncementSerializer

    filter_backends = [filters.DjangoFilterBackend, OrderingFilter]
    ordering_fields = ['updated_time', 'created_time', 'pk']
    filterset_class = AnnouncementMessageFilter

    def list(self, request, *args, **kwargs):
        data = super().list(request, *args, **kwargs).data
        return ApiResponse(**data, choices_dict=get_choices_dict(Announcement.level_choices))

    @action(methods=['put'], detail=True)
    def publish(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.publish = request.data.get('publish')
        instance.save(update_fields=['publish'])
        return ApiResponse()


class UserAnnouncement(BaseModelSet):
    queryset = Announcement.objects.filter(publish=True).all()
    serializer_class = SimpleAnnouncementSerializer
    filter_backends = [filters.DjangoFilterBackend, OrderingFilter]
    ordering_fields = ['created_time', 'pk']

    @action(methods=['get'], detail=False)
    def unread(self, request, *args, **kwargs):
        self.queryset = self.get_queryset().exclude(owner=request.user)
        return super().list(request, *args, **kwargs)

    @action(methods=['put'], detail=False)
    def read(self, request, *args, **kwargs):
        pks = request.data.get('pks')
        for announce in self.queryset.exclude(owner=request.user).filter(pk__in=pks).all():
            announce.owner.add(request.user)
        return ApiResponse(detail="操作成功")
