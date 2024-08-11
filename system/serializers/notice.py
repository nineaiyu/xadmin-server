#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : notice
# author : ly_13
# date : 8/10/2024
import logging
import os.path

from django.conf import settings
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from common.core.fields import BasePrimaryKeyRelatedField, LabeledChoiceField
from common.core.filter import get_filter_queryset
from common.core.serializers import BaseModelSerializer
from system.models import NoticeMessage, UploadFile, NoticeUserRead, DeptInfo, UserRole, UserInfo

logger = logging.getLogger(__name__)


class NoticeMessageSerializer(BaseModelSerializer):
    class Meta:
        model = NoticeMessage
        fields = ['pk', 'title', 'level', "publish", 'notice_type', "notice_user", 'notice_dept', 'notice_role',
                  'message', "created_time", "user_count", "read_user_count", 'extra_json', "files"]

        table_fields = ['pk', 'title', 'notice_type', "read_user_count", "publish", "created_time"]
        extra_kwargs = {'extra_json': {'read_only': True}}

    notice_user = BasePrimaryKeyRelatedField(many=True, queryset=UserInfo.objects, label=_("The notified user"),
                                             attrs=['pk', 'username'], input_type='api-search-user',
                                             format='{username}')
    notice_dept = BasePrimaryKeyRelatedField(many=True, queryset=DeptInfo.objects, input_type='api-search-dept',
                                             label=_("The notified department"), attrs=['pk', 'name'], format='{name}')
    notice_role = BasePrimaryKeyRelatedField(many=True, queryset=UserRole.objects, label=_("The notified role"),
                                             attrs=['pk', 'name'], input_type='api-search-role', format='{name}')

    files = serializers.JSONField(write_only=True, label=_("Uploaded attachments"))
    user_count = serializers.SerializerMethodField(read_only=True, label=_("User count"))
    read_user_count = serializers.SerializerMethodField(read_only=True, label=_("Read user count"))

    notice_type = LabeledChoiceField(choices=NoticeMessage.NoticeChoices.choices,
                                     default=NoticeMessage.NoticeChoices.USER, label=_("Notice type"))
    level = LabeledChoiceField(choices=NoticeMessage.LevelChoices.choices,
                               default=NoticeMessage.LevelChoices.DEFAULT, label=_("Notice level"))

    def get_read_user_count(self, obj):
        if obj.notice_type in NoticeMessage.user_choices:
            return NoticeUserRead.objects.filter(notice=obj, unread=False,
                                                 owner_id__in=obj.notice_user.all()).count()

        elif obj.notice_type in NoticeMessage.notice_choices:
            return obj.notice_user.count()

        return 0

    def get_user_count(self, obj):
        if obj.notice_type == NoticeMessage.NoticeChoices.DEPT:
            return UserInfo.objects.filter(dept__in=obj.notice_dept.all()).count()
        if obj.notice_type == NoticeMessage.NoticeChoices.ROLE:
            return UserInfo.objects.filter(roles__in=obj.notice_role.all()).count()
        return obj.notice_user.count()

    def validate_notice_type(self, val):
        if NoticeMessage.NoticeChoices.NOTICE == val and self.request.method == 'POST':
            raise ValidationError(_("Parameter error. System announcement cannot be created"))
        return val

    def validate(self, attrs):
        notice_type = attrs.get('notice_type')

        if notice_type == NoticeMessage.NoticeChoices.ROLE:
            attrs.pop('notice_dept', None)
            attrs.pop('notice_user', None)
            if not attrs.get('notice_role'):
                raise ValidationError(_("The notice role cannot be null"))

        if notice_type == NoticeMessage.NoticeChoices.DEPT:
            attrs.pop('notice_user', None)
            attrs.pop('notice_role', None)
            if not attrs.get('notice_dept'):
                raise ValidationError(_("The notice department cannot be null"))

        if notice_type == NoticeMessage.NoticeChoices.USER:
            attrs.pop('notice_role', None)
            attrs.pop('notice_dept', None)
            if not attrs.get('notice_user'):
                raise ValidationError(_("The notice user cannot be null"))

        files = attrs.get('files')
        if files is not None:
            del attrs['files']
            queryset = UploadFile.objects.filter(
                filepath__in=[file.replace(os.path.join('/', settings.MEDIA_URL), '') for file in files])
            attrs['file'] = get_filter_queryset(queryset, self.request.user).all()
        return attrs

    def create(self, validated_data):
        with transaction.atomic():
            instance = super().create(validated_data)
            instance.file.filter(is_tmp=True).update(is_tmp=False)
            return instance

    def update(self, instance, validated_data):
        validated_data.pop('notice_type', None)  # 不能修改消息类型
        o_files = list(instance.file.all().values_list('pk', flat=True))  # 加上list，否则删除文件不会清理底层资源
        n_files = []
        if validated_data.get('file', None) is not None:
            n_files = validated_data.get('file').values_list('pk', flat=True)
        else:
            o_files = []
        instance = super().update(instance, validated_data)
        if instance:
            instance.file.filter(is_tmp=True).update(is_tmp=False)
            del_files = set(o_files) - set(n_files)
            if del_files:
                for file in UploadFile.objects.filter(pk__in=del_files):
                    file.delete()  # 这样操作，才可以同时删除底层的文件，如果直接 queryset 进行delete操作，则不删除底层文件
        return instance


class AnnouncementSerializer(NoticeMessageSerializer):

    def validate_notice_type(self, val):
        if NoticeMessage.NoticeChoices.NOTICE == val:
            return val
        raise ValidationError(_("Parameter error"))


class NoticeUserReadMessageSerializer(BaseModelSerializer):
    class Meta:
        model = NoticeUserRead
        fields = ['pk', 'notice_info', 'notice_type', 'owner_info', "unread", "updated_time"]
        read_only_fields = [x.name for x in NoticeUserRead._meta.fields]
        # depth = 1

    notice_type = serializers.CharField(source='notice.get_notice_type_display', read_only=True, label=_("Notice type"))
    owner_info = BasePrimaryKeyRelatedField(attrs=['pk', 'username'], read_only=True, source='owner', label=_("User"))

    notice_info = NoticeMessageSerializer(fields=['pk', 'level', 'title', 'notice_type', 'message', 'publish'],
                                          read_only=True, source='notice', label=_("Notice message"))


class UserNoticeSerializer(BaseModelSerializer):
    ignore_field_permission = True

    class Meta:
        model = NoticeMessage
        fields = ['pk', 'level', 'title', 'message', "created_time", 'unread', 'notice_type']
        table_fields = ['pk', 'title', 'unread', 'notice_type', "created_time"]
        read_only_fields = ['pk', 'notice_user', 'notice_type']

    notice_type = LabeledChoiceField(choices=NoticeMessage.NoticeChoices.choices,
                                     default=NoticeMessage.NoticeChoices.USER, label=_("Notice type"))
    unread = serializers.SerializerMethodField(label=_("Unread"))

    def get_unread(self, obj):
        queryset = NoticeUserRead.objects.filter(notice=obj, owner=self.context.get('request').user)
        if obj.notice_type in NoticeMessage.user_choices:
            return bool(queryset.filter(unread=True).count())
        elif obj.notice_type in NoticeMessage.notice_choices:
            return not bool(queryset.count())
        return True
