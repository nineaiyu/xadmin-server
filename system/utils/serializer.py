#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : serializer
# author : ly_13
# date : 6/6/2023
import os.path
from typing import OrderedDict

from django.conf import settings
from rest_framework import serializers

from common.core.permission import get_user_menu_queryset
from common.core.serializers import BaseModelSerializer
from system import models


class UserSerializer(BaseModelSerializer):
    class Meta:
        model = models.UserInfo
        fields = ['username', 'nickname', 'email', 'last_login', 'gender', 'date_joined', 'pk', 'roles', 'rules',
                  'dept', 'is_active', 'mobile', 'avatar', 'roles_info', 'description', 'dept_info', 'gender_display',
                  'rules_info', 'mode_type', 'mode_display']
        extra_kwargs = {'last_login': {'read_only': True}, 'date_joined': {'read_only': True},
                        'rules': {'read_only': True}, 'pk': {'read_only': True}, 'avatar': {'read_only': True},
                        'roles': {'read_only': True}}
        # extra_kwargs = {'password': {'write_only': True}}
        read_only_fields = ['pk'] + list(set([x.name for x in models.UserInfo._meta.fields]) - set(fields))

    roles_info = serializers.SerializerMethodField(read_only=True)
    rules_info = serializers.SerializerMethodField(read_only=True)
    dept_info = serializers.SerializerMethodField(read_only=True)
    gender_display = serializers.CharField(read_only=True, source='get_gender_display')
    mode_display = serializers.CharField(read_only=True, source='get_mode_type_display')
    def get_roles_info(self, obj):
        result = []
        if isinstance(obj, OrderedDict):
            queryset = obj.get('roles')
        else:
            queryset = obj.roles.all()
        if queryset:
            for obj in queryset:
                result.append({'pk': obj.pk, 'name': obj.name})
        return result

    def get_rules_info(self, obj):
        result = []
        if isinstance(obj, OrderedDict):
            queryset = obj.get('rules')
        else:
            queryset = obj.rules.all()
        if queryset:
            for obj in queryset:
                result.append({'pk': obj.pk, 'name': obj.name})
        return result

    def get_dept_info(self, obj):
        if isinstance(obj, OrderedDict):
            dept = obj.get('dept')
        else:
            dept = obj.dept
        if dept:
            return dept.name
        return '/'


class DeptSerializer(UserSerializer):
    class Meta:
        model = models.DeptInfo
        fields = ['pk', 'name', 'code', 'parent', 'rank', 'is_active', 'roles', 'roles_info', 'user_count', 'rules',
                  'mode_type', 'mode_display', 'rules_info', 'auto_bind']
        extra_kwargs = {'pk': {'read_only': True}, 'roles': {'read_only': True}, 'rules': {'read_only': True}}

    user_count = serializers.SerializerMethodField(read_only=True)
    mode_display = serializers.CharField(read_only=True, source='get_mode_type_display')

    def get_user_count(self, obj):
        return obj.userinfo_set.count()


class UserInfoSerializer(UserSerializer):
    class Meta:
        model = models.UserInfo
        fields = ['username', 'nickname', 'email', 'last_login', 'gender', 'pk', 'mobile', 'avatar', 'roles_info',
                  'date_joined', 'gender_display', 'dept_info']
        extra_kwargs = {'last_login': {'read_only': True}, 'date_joined': {'read_only': True},
                        'pk': {'read_only': True}, 'avatar': {'read_only': True}}
        read_only_fields = ['pk'] + list(set([x.name for x in models.UserInfo._meta.fields]) - set(fields))


class RouteMetaSerializer(BaseModelSerializer):
    class Meta:
        model = models.MenuMeta
        fields = ['title', 'icon', 'showParent', 'showLink', 'extraIcon', 'keepAlive', 'frameSrc', 'frameLoading',
                  'transition', 'hiddenTag', 'dynamicLevel', 'auths']

    showParent = serializers.BooleanField(source='is_show_parent', read_only=True)
    showLink = serializers.BooleanField(source='is_show_menu', read_only=True)
    extraIcon = serializers.CharField(source='r_svg_name', read_only=True)
    keepAlive = serializers.BooleanField(source='is_keepalive', read_only=True)
    frameSrc = serializers.CharField(source='frame_url', read_only=True)
    frameLoading = serializers.BooleanField(source='frame_loading', read_only=True)

    transition = serializers.SerializerMethodField()

    def get_transition(self, obj):
        return {
            'enterTransition': obj.transition_enter,
            'leaveTransition': obj.transition_leave,
        }

    hiddenTag = serializers.BooleanField(source='is_hidden_tag', read_only=True)
    dynamicLevel = serializers.IntegerField(source='dynamic_level', read_only=True)

    auths = serializers.SerializerMethodField()

    def get_auths(self, obj):
        user = self.context.get('user')
        if user.is_superuser:
            menu_obj = models.Menu.objects.filter(is_active=True)
        else:
            menu_obj = get_user_menu_queryset(user)
        if menu_obj:
            return menu_obj.filter(menu_type=2, parent=obj.menu).values_list('name', flat=True).distinct()
        else:
            return []


class MenuMetaSerializer(BaseModelSerializer):
    class Meta:
        model = models.MenuMeta
        fields = '__all__'


class MenuSerializer(BaseModelSerializer):
    meta = MenuMetaSerializer()

    class Meta:
        model = models.Menu
        fields = ['pk', 'name', 'rank', 'path', 'component', 'meta', 'parent', 'menu_type', 'is_active']
        read_only_fields = ['pk']
        extra_kwargs = {'rank': {'read_only': True}}

    def update(self, instance, validated_data):
        serializer = MenuMetaSerializer(instance.meta, data=validated_data.pop('meta'), partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return super().update(instance, validated_data)

    def create(self, validated_data):
        serializer = MenuMetaSerializer(data=validated_data.pop('meta'))
        serializer.is_valid(raise_exception=True)
        validated_data['meta'] = serializer.save()
        return super().create(validated_data)


class RoleSerializer(BaseModelSerializer):
    class Meta:
        model = models.UserRole
        fields = ['pk', 'name', 'is_active', 'code', 'menu', 'description', 'created_time']
        read_only_fields = ['pk']


class RouteSerializer(MenuSerializer):
    meta = RouteMetaSerializer()


class OperationLogSerializer(BaseModelSerializer):
    class Meta:
        model = models.OperationLog
        fields = ["pk", "creator", "module", "path", "body", "method", "ipaddress", "browser", "system",
                  "response_code",
                  "response_result", "status_code", "created_time"]
        read_only_fields = ["pk"] + list(set([x.name for x in models.OperationLog._meta.fields]))

    creator = serializers.SerializerMethodField()
    module = serializers.SerializerMethodField()

    def get_creator(self, obj):
        if obj.creator:
            return {'pk': obj.creator.pk, 'username': obj.creator.username}
        return {}

    def get_module(self, obj):
        module_name = obj.module
        map_module_name = settings.API_MODEL_MAP.get(obj.path, None)
        if not module_name and map_module_name:
            return map_module_name
        return module_name


class UploadFileSerializer(BaseModelSerializer):
    class Meta:
        model = models.UploadFile
        fields = ['pk', 'filepath', 'filename', 'filesize']
        read_only_fields = [x.name for x in models.UploadFile._meta.fields]


class NoticeMessageSerializer(BaseModelSerializer):

    class Meta:
        model = models.NoticeMessage
        fields = ['pk', 'level', 'title', 'message', "created_time", "notice_user", "user_count", "read_user_count",
                  'notice_type', 'extra_json', "files", "notice_users", "publish", 'notice_type_display', 'notice_dept',
                  'notice_role']

        read_only_fields = ['pk', 'notice_user']

    notice_type_display = serializers.CharField(source="get_notice_type_display", read_only=True)
    notice_users = serializers.JSONField(write_only=True)
    files = serializers.JSONField(write_only=True)

    user_count = serializers.SerializerMethodField(read_only=True)
    read_user_count = serializers.SerializerMethodField(read_only=True)

    def get_read_user_count(self, obj):
        if obj.notice_type in models.NoticeMessage.user_choices:
            return models.NoticeUserRead.objects.filter(notice=obj, unread=False,
                                                        owner_id__in=obj.notice_user.all()).count()

        elif obj.notice_type in models.NoticeMessage.notice_choices:
            return obj.notice_user.count()

        return 0

    def get_user_count(self, obj):
        if obj.notice_type == models.NoticeMessage.NoticeChoices.DEPT:
            return models.UserInfo.objects.filter(dept__in=obj.notice_dept.all()).count()
        if obj.notice_type == models.NoticeMessage.NoticeChoices.ROLE:
            return models.UserInfo.objects.filter(roles__in=obj.notice_role.all()).count()
        return obj.notice_user.count()

    def validate(self, attrs):
        notice_type = attrs.get('notice_type')
        notice_users = attrs.get('notice_users')

        if notice_type == models.NoticeMessage.NoticeChoices.ROLE:
            attrs.pop('notice_dept', None)
            attrs.pop('notice_users', None)
            if not attrs.get('notice_role'):
                raise Exception('消息通知缺少角色')

        if notice_type == models.NoticeMessage.NoticeChoices.DEPT:
            attrs.pop('notice_users', None)
            attrs.pop('notice_role', None)
            if not attrs.get('notice_dept'):
                raise Exception('消息通知缺少部门')

        if notice_type == models.NoticeMessage.NoticeChoices.USER:
            attrs.pop('notice_role', None)
            attrs.pop('notice_dept', None)
            if not notice_users:
                raise Exception('消息通知缺少用户')

        files = attrs.get('files')
        if files is not None:
            del attrs['files']
            attrs['file'] = models.UploadFile.objects.filter(
                filepath__in=[file.replace(os.path.join('/', settings.MEDIA_URL), '') for file in files],
                creator=self.context.get('request').user).all()
        return attrs

    def create(self, validated_data):
        notice_users = []
        if validated_data.get('notice_users') is not None:
            notice_users = validated_data.pop('notice_users', None)
        instance = super().create(validated_data)
        instance.file.filter(is_tmp=True).update(is_tmp=False)
        if notice_users and validated_data['notice_type'] in models.NoticeMessage.user_choices:
            instance.notice_user.set(models.UserInfo.objects.filter(pk__in=notice_users))
        return instance

    def update(self, instance, validated_data):
        notice_type = validated_data.pop('notice_type')  # 不能修改消息类型
        o_files = instance.file.all().values_list('pk', flat=True)
        n_files = []
        if validated_data.get('file'):
            n_files = validated_data.get('file').values_list('pk', flat=True)

        instance = super().update(instance, validated_data)
        if instance:
            if notice_type in models.NoticeMessage.user_choices:
                form_users = set(validated_data.get('notice_users'))
                instance.notice_user.set(models.UserInfo.objects.filter(pk__in=form_users))
            instance.file.filter(is_tmp=True).update(is_tmp=False)
            del_files = set(o_files) - set(n_files)
            if del_files:
                for file in models.UploadFile.objects.filter(pk__in=del_files,
                                                             creator=self.context.get('request').user):
                    file.delete()  # 这样操作，才可以同时删除底层的文件，如果直接 queryset 进行delete操作，则不删除底层文件
        return instance


class UserNoticeSerializer(BaseModelSerializer):
    class Meta:
        model = models.NoticeMessage
        fields = ['pk', 'level', 'title', 'message', "created_time", 'notice_type_display', 'unread']
        read_only_fields = ['pk', 'notice_user']

    notice_type_display = serializers.CharField(source="get_notice_type_display", read_only=True)
    unread = serializers.SerializerMethodField()

    def get_unread(self, obj):
        queryset = models.NoticeUserRead.objects.filter(notice=obj, owner=self.context.get('request').user)
        if obj.notice_type in models.NoticeMessage.user_choices:
            return bool(queryset.filter(unread=True).count())
        elif obj.notice_type in models.NoticeMessage.notice_choices:
            return not bool(queryset.count())
        return True


class NoticeUserReadMessageSerializer(BaseModelSerializer):
    class Meta:
        model = models.NoticeUserRead
        fields = ['pk', 'owner_info', 'notice_info', "updated_time", "unread"]
        read_only_fields = [x.name for x in models.NoticeUserRead._meta.fields]
        # depth = 1

    owner_info = serializers.SerializerMethodField()
    notice_info = serializers.SerializerMethodField()

    def get_owner_info(self, obj):
        return {'pk': obj.owner.pk, 'username': obj.owner.username}

    def get_notice_info(self, obj):
        return NoticeMessageSerializer(obj.notice).data


class DataPermissionSerializer(BaseModelSerializer):
    class Meta:
        model = models.DataPermission
        fields = ['pk', 'name', 'rules', "description", "is_active", "created_time", "mode_type", "mode_display"]
        read_only_fields = ['pk']

    mode_display = serializers.CharField(read_only=True, source='get_mode_type_display')

    def validate(self, attrs):
        rules = attrs.get('rules', [])
        if len(rules) < 2:
            attrs['mode_type'] = 0
        return attrs


class SystemConfigSerializer(BaseModelSerializer):
    class Meta:
        model = models.SystemConfig
        fields = ['pk', 'value', 'key', 'is_active', 'created_time', 'description']
        read_only_fields = ['pk']
