#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : notice
# author : ly_13
# date : 9/15/2023
from hashlib import md5

from django.db.models import Q
from django_filters import rest_framework as filters
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter

from common.base.magic import cache_response
from common.base.utils import get_choices_dict
from common.core.modelset import BaseModelSet, OnlyListModelSet
from common.core.response import ApiResponse
from system.models import NoticeMessage, NoticeUserRead
from system.utils.serializer import NoticeMessageSerializer, NoticeUserReadMessageSerializer, UserNoticeSerializer, \
    AnnouncementSerializer


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


class UserNoticeMessageFilter(filters.FilterSet):
    message = filters.CharFilter(field_name='message', lookup_expr='icontains')
    title = filters.CharFilter(field_name='title', lookup_expr='icontains')
    pk = filters.NumberFilter(field_name='id')
    unread = filters.BooleanFilter(field_name='unread', method='unread_filter')

    def unread_filter(self, queryset, name, value):
        if value:
            return queryset.filter(get_user_unread_q(self.request.user))
        else:
            return queryset.filter(notice_user=self.request.user, noticeuserread__unread=False)

    class Meta:
        model = NoticeMessage
        fields = ['notice_type', 'level']


class UserNoticeMessage(OnlyListModelSet):
    queryset = NoticeMessage.objects.filter(publish=True).all().distinct()
    serializer_class = UserNoticeSerializer
    filter_backends = [filters.DjangoFilterBackend, OrderingFilter]
    ordering_fields = ['created_time', 'pk']
    filterset_class = UserNoticeMessageFilter

    @cache_response(timeout=600, key_func='get_cache_key')
    def list(self, request, *args, **kwargs):
        unread_count = self.filter_queryset(self.get_queryset()).filter(get_user_unread_q(self.request.user)).count()
        q = get_users_notice_q(request.user)
        q |= Q(notice_type__in=NoticeMessage.user_choices, notice_user=request.user)
        self.queryset = self.filter_queryset(self.get_queryset()).filter(q)
        data = super().list(request, *args, **kwargs).data
        return ApiResponse(**data, unread_count=unread_count,
                           level_choices=get_choices_dict(NoticeMessage.LevelChoices.choices),
                           notice_type_choices=get_choices_dict(NoticeMessage.NoticeChoices.choices))

    def get_cache_key(self, view_instance, view_method, request, args, kwargs):
        func_name = f'{view_instance.__class__.__name__}_{view_method.__name__}'
        return f"{func_name}_{request.user.pk}_{md5(request.META['QUERY_STRING'].encode('utf-8')).hexdigest()}"

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

    @action(methods=['put'], detail=False)
    def read(self, request, *args, **kwargs):
        pks = request.data.get('pks', [])
        return self.read_message(pks, request)

    @action(methods=['put'], detail=False)
    def read_all(self, request, *args, **kwargs):
        pks = self.filter_queryset(self.get_queryset()).filter(get_user_unread_q(self.request.user)).values_list('pk',
                                                                                                                 flat=True).distinct()
        return self.read_message(pks, request)
