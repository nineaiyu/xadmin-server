#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : serializer
# author : ly_13
# date : 6/6/2023
import json
import os.path

from django.conf import settings
from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from common.core.config import SysConfig, UserConfig
from common.core.filter import get_filter_queryset
from common.core.permission import get_user_menu_queryset
from common.core.serializers import BaseModelSerializer, BasePrimaryKeyRelatedField
from system import models


class ModelLabelFieldSerializer(BaseModelSerializer):
    class Meta:
        model = models.ModelLabelField
        fields = ['pk', 'name', 'label', 'parent', 'created_time', 'updated_time', 'field_type_display']
        read_only_fields = ['pk', 'name', 'label', 'parent', 'created_time', 'updated_time']

    field_type_display = serializers.CharField(source='get_field_type_display', read_only=True)


class FieldPermissionSerializer(BaseModelSerializer):
    class Meta:
        model = models.FieldPermission
        fields = ['pk', 'role', 'menu', 'field']
        read_only_fields = ['pk']


class RoleSerializer(BaseModelSerializer):
    class Meta:
        model = models.UserRole
        fields = ['pk', 'name', 'is_active', 'code', 'menu', 'description', 'created_time', 'field', 'fields']
        read_only_fields = ['pk']

    field = serializers.SerializerMethodField(read_only=True)
    fields = serializers.DictField(write_only=True)

    def get_field(self, obj):
        results = FieldPermissionSerializer(models.FieldPermission.objects.filter(role=obj), many=True,
                                            request=self.request, init=True).data
        data = {}
        for res in results:
            data[str(res.get('menu'))] = res.get('field', [])
        return data

    def save_fields(self, fields, instance):
        for k, v in fields.items():
            serializer = FieldPermissionSerializer(data={'role': instance.pk, 'menu': k, 'field': v},
                                                   request=self.request, init=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()

    def update(self, instance, validated_data):
        fields = validated_data.pop('fields')
        with transaction.atomic():
            instance = super().update(instance, validated_data)
            models.FieldPermission.objects.filter(role=instance).delete()
            self.save_fields(fields, instance)
        return instance

    def create(self, validated_data):
        fields = validated_data.pop('fields')
        with transaction.atomic():
            instance = super().create(validated_data)
            self.save_fields(fields, instance)
        return instance


class ListRoleSerializer(RoleSerializer):
    class Meta:
        model = models.UserRole
        fields = ['pk', 'name', 'is_active', 'code', 'menu', 'description', 'created_time', 'field', 'fields']
        read_only_fields = ['pk']

    field = serializers.ListField(default=[], read_only=True)
    menu = serializers.SerializerMethodField(read_only=True)

    def get_menu(self, instance):
        return []


class DataPermissionSerializer(BaseModelSerializer):
    class Meta:
        model = models.DataPermission
        fields = ['pk', 'name', 'rules', "description", "is_active", "created_time", "mode_type", "mode_display",
                  "menu"]
        read_only_fields = ['pk']

    mode_display = serializers.CharField(read_only=True, source='get_mode_type_display')

    def validate(self, attrs):
        rules = attrs.get('rules', [])
        if len(rules) < 2:
            attrs['mode_type'] = models.DataPermission.ModeChoices.OR
        return attrs


class BaseRoleRuleInfo(BaseModelSerializer):
    roles_info = RoleSerializer(fields=['pk', 'name'], many=True, read_only=True, source='roles')
    rules_info = DataPermissionSerializer(fields=['pk', 'name'], many=True, read_only=True, source='rules')
    mode_display = serializers.CharField(read_only=True, source='get_mode_type_display')


class DeptSerializer(BaseRoleRuleInfo):
    class Meta:
        model = models.DeptInfo
        fields = ['pk', 'name', 'code', 'parent', 'rank', 'is_active', 'roles', 'roles_info', 'user_count', 'rules',
                  'mode_type', 'mode_display', 'rules_info', 'auto_bind', 'description']
        extra_kwargs = {'pk': {'read_only': True}, 'roles': {'read_only': True}, 'rules': {'read_only': True}}

    user_count = serializers.SerializerMethodField(read_only=True)
    parent = BasePrimaryKeyRelatedField(queryset=models.DeptInfo.objects, allow_null=True)

    def validate(self, attrs):
        parent = attrs.get('parent')
        if not parent:
            attrs['parent'] = self.request.user.dept
        return attrs

    def update(self, instance, validated_data):
        parent = validated_data.get('parent')
        if parent.pk in models.DeptInfo.recursion_dept_info(dept_id=instance.pk):
            raise ValidationError('Parent not in children')
        return super().update(instance, validated_data)

    def get_user_count(self, obj):
        return obj.userinfo_set.count()


class UserSerializer(BaseRoleRuleInfo):
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

    dept_info = DeptSerializer(fields=['name'], read_only=True, source='dept')
    gender_display = serializers.CharField(read_only=True, source='get_gender_display')


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
            return menu_obj.filter(menu_type=models.Menu.MenuChoices.PERMISSION, parent=obj.menu).values_list('name',
                                                                                                              flat=True).distinct()
        else:
            return []


class MenuMetaSerializer(BaseModelSerializer):
    class Meta:
        model = models.MenuMeta
        exclude = ['creator', 'modifier']
        read_only_fields = ['creator', 'modifier', 'dept_belong', 'id']


class MenuSerializer(BaseModelSerializer):
    meta = MenuMetaSerializer()

    class Meta:
        model = models.Menu
        fields = ['pk', 'name', 'rank', 'path', 'component', 'meta', 'parent', 'menu_type', 'is_active',
                  'menu_type_display', 'model', 'field']
        read_only_fields = ['pk']
        extra_kwargs = {'rank': {'read_only': True}}

    menu_type_display = serializers.CharField(source='get_menu_type_display', read_only=True)
    field = serializers.ListField(default=[], read_only=True)

    def update(self, instance, validated_data):
        with transaction.atomic():
            serializer = MenuMetaSerializer(instance.meta, data=validated_data.pop('meta'), partial=True,
                                            context=self.context, request=self.request)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            return super().update(instance, validated_data)

    def create(self, validated_data):
        with transaction.atomic():
            serializer = MenuMetaSerializer(data=validated_data.pop('meta'), context=self.context, request=self.request)
            serializer.is_valid(raise_exception=True)
            validated_data['meta'] = serializer.save()
            return super().create(validated_data)


class MenuPermissionSerializer(MenuSerializer):
    class Meta:
        model = models.Menu
        fields = ['pk', 'name', 'rank', 'path', 'component', 'title', 'parent', 'menu_type']
        read_only_fields = ['pk']
        extra_kwargs = {'rank': {'read_only': True}}

    title = serializers.CharField(source='meta.title', read_only=True)


class RouteSerializer(MenuSerializer):
    meta = RouteMetaSerializer(init=True)  # 用于前端菜单渲染


class OperationLogSerializer(BaseModelSerializer):
    class Meta:
        model = models.OperationLog
        fields = ["pk", "creator", "module", "path", "body", "method", "ipaddress", "browser", "system",
                  "response_code",
                  "response_result", "status_code", "created_time"]
        read_only_fields = ["pk"] + list(set([x.name for x in models.OperationLog._meta.fields]))

    creator = UserInfoSerializer(fields=['pk', 'username'], read_only=True)
    module = serializers.SerializerMethodField()

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
        fields = ['pk', 'level', 'title', 'message', "created_time", "user_count", "read_user_count", 'extra_json',
                  'notice_type', "files", "publish", 'notice_type_display', "notice_user", 'notice_dept', 'notice_role']
        # extra_kwargs = {'notice_user': {'read_only': False}}

    notice_user = BasePrimaryKeyRelatedField(many=True, queryset=models.UserInfo.objects)
    notice_type_display = serializers.CharField(source="get_notice_type_display", read_only=True)
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

    def validate_notice_type(self, val):
        if models.NoticeMessage.NoticeChoices.NOTICE == val:
            raise ValidationError('参数有误')
        return val

    def validate(self, attrs):
        notice_type = attrs.get('notice_type')

        if notice_type == models.NoticeMessage.NoticeChoices.ROLE:
            attrs.pop('notice_dept', None)
            attrs.pop('notice_user', None)
            if not attrs.get('notice_role'):
                raise ValidationError('消息通知缺少角色')

        if notice_type == models.NoticeMessage.NoticeChoices.DEPT:
            attrs.pop('notice_user', None)
            attrs.pop('notice_role', None)
            if not attrs.get('notice_dept'):
                raise ValidationError('消息通知缺少部门')

        if notice_type == models.NoticeMessage.NoticeChoices.USER:
            attrs.pop('notice_role', None)
            attrs.pop('notice_dept', None)
            if not attrs.get('notice_user'):
                raise ValidationError('消息通知缺少用户')

        files = attrs.get('files')
        if files is not None:
            del attrs['files']
            queryset = models.UploadFile.objects.filter(
                filepath__in=[file.replace(os.path.join('/', settings.MEDIA_URL), '') for file in files])
            attrs['file'] = get_filter_queryset(queryset, self.request.user).all()
        return attrs

    def create(self, validated_data):
        with transaction.atomic():
            instance = super().create(validated_data)
            instance.file.filter(is_tmp=True).update(is_tmp=False)
            return instance

    def update(self, instance, validated_data):
        validated_data.pop('notice_type')  # 不能修改消息类型
        o_files = instance.file.all().values_list('pk', flat=True)
        n_files = []
        if validated_data.get('file'):
            n_files = validated_data.get('file').values_list('pk', flat=True)

        instance = super().update(instance, validated_data)
        if instance:
            instance.file.filter(is_tmp=True).update(is_tmp=False)
            del_files = set(o_files) - set(n_files)
            if del_files:
                for file in models.UploadFile.objects.filter(pk__in=del_files):
                    file.delete()  # 这样操作，才可以同时删除底层的文件，如果直接 queryset 进行delete操作，则不删除底层文件
        return instance


class AnnouncementSerializer(NoticeMessageSerializer):

    def validate_notice_type(self, val):
        if models.NoticeMessage.NoticeChoices.NOTICE == val:
            return val
        raise ValidationError('参数有误')


class UserNoticeSerializer(BaseModelSerializer):
    ignore_field_permission = True

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

    owner_info = UserInfoSerializer(fields=['pk', 'username'], read_only=True, source='owner')
    notice_info = NoticeMessageSerializer(fields=['pk', 'level', 'title', 'notice_type_display', 'message', 'publish'],
                                          read_only=True, source='notice')


class SystemConfigSerializer(BaseModelSerializer):
    class Meta:
        model = models.SystemConfig
        fields = ['pk', 'value', 'key', 'is_active', 'created_time', 'description', 'cache_value']
        read_only_fields = ['pk']

    cache_value = serializers.SerializerMethodField(read_only=True)

    def get_cache_value(self, obj):
        val = SysConfig.get_value(obj.key)
        if isinstance(val, dict):
            val = json.dumps(val)
        return val


class UserPersonalConfigSerializer(SystemConfigSerializer):
    class Meta:
        model = models.UserPersonalConfig
        fields = ['pk', 'value', 'key', 'is_active', 'created_time', 'description', 'cache_value', 'owner',
                  'owner_info', 'config_user']
        read_only_fields = ['pk', 'owner']

    owner_info = UserInfoSerializer(fields=['pk', 'username'], read_only=True, source='owner')
    config_user = BasePrimaryKeyRelatedField(write_only=True, many=True, queryset=models.UserInfo.objects)

    def create(self, validated_data):
        config_user = validated_data.pop('config_user')
        instance = None
        for owner in config_user:
            validated_data['owner'] = owner
            instance = super().create(validated_data)
        return instance

    def update(self, instance, validated_data):
        validated_data.pop('config_user')
        return super().update(instance, validated_data)

    def get_cache_value(self, obj):
        val = UserConfig(obj.owner).get_value(obj.key)
        if isinstance(val, dict):
            val = json.dumps(val)
        return val


class UserLoginLogSerializer(BaseModelSerializer):
    class Meta:
        model = models.UserLoginLog
        fields = ['pk', 'ipaddress', 'browser', 'system', 'agent', 'login_type', 'creator', 'created_time',
                  'login_display']
        read_only_fields = ['pk', 'creator']

    creator = UserInfoSerializer(fields=['pk', 'username'], read_only=True)
    login_display = serializers.CharField(read_only=True, source='get_login_type_display')
