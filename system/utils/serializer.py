#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : serializer
# author : ly_13
# date : 6/6/2023
import json
import logging
import os.path

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.db.models import Q
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from common.base.utils import AESCipherV2
from common.core.config import SysConfig, UserConfig
from common.core.filter import get_filter_queryset
from common.core.permission import get_user_menu_queryset
from common.core.serializers import BaseModelSerializer, BasePrimaryKeyRelatedField, LabeledChoiceField
from common.fields.utils import get_file_absolute_uri
from system import models

logger = logging.getLogger(__name__)


class ModelLabelFieldSerializer(BaseModelSerializer):
    class Meta:
        model = models.ModelLabelField
        fields = ['pk', 'name', 'label', 'parent', 'field_type', 'created_time', 'updated_time']
        read_only_fields = [x.name for x in models.ModelLabelField._meta.fields]

    parent = BasePrimaryKeyRelatedField(read_only=True, attrs=['pk', 'name'])
    field_type = LabeledChoiceField(choices=models.ModelLabelField.FieldChoices.choices,
                                    default=models.ModelLabelField.FieldChoices.DATA, label="字段类型")


class FieldPermissionSerializer(BaseModelSerializer):
    class Meta:
        model = models.FieldPermission
        fields = ['pk', 'role', 'menu', 'field']
        read_only_fields = ['pk']


class RoleSerializer(BaseModelSerializer):
    class Meta:
        model = models.UserRole
        fields = ['pk', 'name', 'code', 'is_active', 'description', 'menu', 'updated_time', 'field', 'fields']
        table_fields = ['pk', 'name', 'code', 'is_active', 'description', 'updated_time']
        read_only_fields = ['pk']

    menu = BasePrimaryKeyRelatedField(queryset=models.Menu.objects, many=True, label="菜单", attrs=['pk', 'name'],
                                      input_type="input")

    # field和fields 设置两个相同的label，可以进行文件导入导出
    field = serializers.SerializerMethodField(read_only=True, label="Fields")
    fields = serializers.DictField(write_only=True, label="Fields")

    def get_field(self, obj):
        results = FieldPermissionSerializer(models.FieldPermission.objects.filter(role=obj), many=True,
                                            request=self.request, all_fields=True).data
        data = {}
        for res in results:
            data[str(res.get('menu'))] = res.get('field', [])
        return data

    def save_fields(self, fields, instance):
        for k, v in fields.items():
            serializer = FieldPermissionSerializer(data={'role': instance.pk, 'menu': k, 'field': v},
                                                   request=self.request, all_fields=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()

    def update(self, instance, validated_data):
        fields = validated_data.pop('fields', None)
        with transaction.atomic():
            instance = super().update(instance, validated_data)
            if fields:
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
        fields = ['pk', 'name', 'is_active', 'code', 'menu', 'description', 'updated_time', 'field', 'fields']
        read_only_fields = [x.name for x in models.UserRole._meta.fields]

    field = serializers.ListField(default=[], read_only=True)
    menu = serializers.SerializerMethodField(read_only=True)

    def get_menu(self, instance):
        return []


def get_menu_queryset():
    queryset = models.Menu.objects
    pks = queryset.filter(menu_type=models.Menu.MenuChoices.PERMISSION).values_list(
        'parent', flat=True)
    return queryset.filter(Q(menu_type=models.Menu.MenuChoices.PERMISSION) | Q(id__in=pks)).order_by('rank')

class DataPermissionSerializer(BaseModelSerializer):
    class Meta:
        model = models.DataPermission
        fields = ['pk', 'name', "is_active", "mode_type", "menu", "description", 'rules', "created_time"]
        table_fields = ['pk', 'name', "mode_type", "is_active", "description", "created_time"]
        # extra_kwargs = {'rules': {'required': True}}

    menu = BasePrimaryKeyRelatedField(queryset=get_menu_queryset(), many=True, label="菜单", required=False,
                                      attrs=['pk', 'name', 'parent_id', 'meta__title'])
    mode_type = LabeledChoiceField(choices=models.ModeTypeAbstract.ModeChoices.choices,
                                   default=models.ModeTypeAbstract.ModeChoices.OR, label="权限模式")

    def validate(self, attrs):
        rules = attrs.get('rules', [] if not self.instance else self.instance.rules)
        if not rules:
            raise ValidationError('规则不能为空')
        if len(rules) < 2:
            attrs['mode_type'] = models.DataPermission.ModeChoices.OR
        return attrs


class BaseRoleRuleInfo(BaseModelSerializer):
    roles = BasePrimaryKeyRelatedField(queryset=models.UserRole.objects, allow_null=True, required=False,
                                       attrs=['pk', 'name', 'code'], label='角色', many=True, format="{name}")
    rules = BasePrimaryKeyRelatedField(queryset=models.DataPermission.objects, allow_null=True, required=False,
                                       attrs=['pk', 'name', 'get_mode_type_display'], label='数据权限', many=True,
                                       format="{name}")
    mode_type = LabeledChoiceField(choices=models.ModeTypeAbstract.ModeChoices.choices,
                                   default=models.ModeTypeAbstract.ModeChoices.OR.value, label="权限模式")


class DeptSerializer(BaseRoleRuleInfo):
    class Meta:
        model = models.DeptInfo
        fields = ['pk', 'name', 'code', 'parent', 'rank', 'is_active', 'roles', 'user_count', 'rules',
                  'mode_type', 'auto_bind', 'description', 'created_time']

        table_fields = ['name', 'pk', 'code', 'user_count', 'rank', 'mode_type', 'auto_bind', 'is_active', 'roles',
                        'rules', 'created_time']

        extra_kwargs = {'roles': {'read_only': True}, 'rules': {'read_only': True}}

    user_count = serializers.SerializerMethodField(read_only=True, label="用户数量")
    parent = BasePrimaryKeyRelatedField(queryset=models.DeptInfo.objects, allow_null=True, required=False,
                                        label="上级部门", attrs=['pk', 'name', 'parent_id'])

    def validate(self, attrs):
        # 权限需要其他接口设置，下面三个参数忽略
        attrs.pop('rules', None)
        attrs.pop('roles', None)
        attrs.pop('mode_type', None)
        # 上级部门必须存在，否则会出现数据权限问题
        parent = attrs.get('parent', self.instance.parent if self.instance else None)
        if not parent:
            attrs['parent'] = self.request.user.dept
        return attrs

    def update(self, instance, validated_data):
        parent = validated_data.get('parent')
        if parent and parent.pk in models.DeptInfo.recursion_dept_info(dept_id=instance.pk):
            raise ValidationError('Parent not in children')
        return super().update(instance, validated_data)

    def get_user_count(self, obj):
        return obj.userinfo_set.count()


class UserSerializer(BaseRoleRuleInfo):
    class Meta:
        model = models.UserInfo
        fields = ['pk', 'avatar', 'username', 'nickname', 'mobile', 'email', 'gender', 'is_active', 'password', 'dept',
                  'description', 'last_login', 'date_joined', 'roles', 'rules', 'mode_type']

        extra_kwargs = {'last_login': {'read_only': True}, 'date_joined': {'read_only': True},
                        'rules': {'read_only': True}, 'pk': {'read_only': True}, 'avatar': {'read_only': True},
                        'roles': {'read_only': True}, 'dept': {'required': True}, 'password': {'write_only': True}}
        read_only_fields = ['pk'] + list(set([x.name for x in models.UserInfo._meta.fields]) - set(fields))

        table_fields = ['pk', 'avatar', 'username', 'nickname', 'gender', 'is_active', 'dept', 'mobile',
                        'last_login', 'date_joined', 'roles', 'rules']

    dept = BasePrimaryKeyRelatedField(queryset=models.DeptInfo.objects, allow_null=True, required=False,
                                      attrs=['pk', 'name', 'parent_id'], label='部门', format="{name}")
    gender = LabeledChoiceField(choices=models.UserInfo.GenderChoices.choices,
                                default=models.UserInfo.GenderChoices.UNKNOWN, label='性别')

    def validate(self, attrs):
        password = attrs.get('password')
        if password:
            if self.request.method == 'POST':
                try:
                    attrs['password'] = make_password(AESCipherV2(attrs.get('username')).decrypt(password))
                except Exception as e:
                    attrs['password'] = make_password(attrs.get('password'))
                    logger.warning(f"create user and set password failed:{e}. so set default password")
            else:
                raise ValidationError("参数有误")
        return attrs


class UserInfoSerializer(UserSerializer):
    class Meta:
        model = models.UserInfo
        fields = ['username', 'nickname', 'email', 'last_login', 'gender', 'pk', 'mobile', 'avatar', 'roles',
                  'date_joined', 'dept']
        extra_kwargs = {'last_login': {'read_only': True}, 'date_joined': {'read_only': True},
                        'pk': {'read_only': True}, 'avatar': {'read_only': True}}
        read_only_fields = ['pk'] + list(set([x.name for x in models.UserInfo._meta.fields]) - set(fields))


class RouteMetaSerializer(BaseModelSerializer):
    class Meta:
        model = models.MenuMeta
        fields = ['title', 'icon', 'showParent', 'showLink', 'extraIcon', 'keepAlive', 'frameSrc', 'frameLoading',
                  'transition', 'hiddenTag', 'dynamicLevel', 'fixedTag', 'auths']

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
    fixedTag = serializers.BooleanField(source='fixed_tag', read_only=True)
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
        exclude = ['creator', 'modifier', 'id']
        read_only_fields = ['creator', 'modifier', 'dept_belong', 'id']

    pk = serializers.IntegerField(source='id', read_only=True)


class MenuSerializer(BaseModelSerializer):
    meta = MenuMetaSerializer(label='菜单元属性')

    class Meta:
        model = models.Menu
        fields = ['pk', 'name', 'rank', 'path', 'component', 'meta', 'parent', 'menu_type', 'is_active',
                  'model', 'method']
        # read_only_fields = ['pk'] # 用于文件导入导出时，不丢失上级节点
        extra_kwargs = {'rank': {'read_only': True}}

    parent = BasePrimaryKeyRelatedField(queryset=models.Menu.objects, allow_null=True, required=False, label="上级菜单",
                                        attrs=['pk', 'name'])
    model = BasePrimaryKeyRelatedField(queryset=models.ModelLabelField.objects, allow_null=True, required=False,
                                       label="绑定模型", attrs=['pk', 'name'], many=True)

    menu_type = LabeledChoiceField(choices=models.Menu.MenuChoices.choices,
                                   default=models.Menu.MenuChoices.DIRECTORY, label='菜单类型')

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
        fields = ['pk', 'title', 'parent', 'menu_type']
        read_only_fields = ['pk']
        extra_kwargs = {'rank': {'read_only': True}}

    title = serializers.CharField(source='meta.title', read_only=True, label="菜单名称")


class RouteSerializer(MenuSerializer):
    meta = RouteMetaSerializer(all_fields=True, label="菜单元属性")  # 用于前端菜单渲染


class OperationLogSerializer(BaseModelSerializer):
    class Meta:
        model = models.OperationLog
        fields = ["pk", "module", "creator", "ipaddress", "path", "method", "browser", "system",
                  "response_code", "status_code", "body", "response_result", "created_time"]

        table_fields = ["pk", "module", "creator", "ipaddress", "path", "method", "browser", "system",
                        "status_code", "created_time"]
        read_only_fields = ["pk"] + list(set([x.name for x in models.OperationLog._meta.fields]))

    creator = UserInfoSerializer(fields=['pk', 'username'], read_only=True, label="操作用户")
    module = serializers.SerializerMethodField(label="访问模块")

    def get_module(self, obj):
        module_name = obj.module
        map_module_name = settings.API_MODEL_MAP.get(obj.path, None)
        if not module_name and map_module_name:
            return map_module_name
        return module_name


class UploadFileSerializer(BaseModelSerializer):
    class Meta:
        model = models.UploadFile
        fields = ['pk', 'filename', 'filesize', 'mime_type', 'md5sum', 'file_url', 'access_url', 'is_tmp', 'is_upload']
        read_only_fields = ["pk", "is_upload"]
        table_fields = ['pk', 'filename', 'filesize', 'mime_type', 'access_url', 'is_tmp', 'is_upload', 'md5sum']

    access_url = serializers.SerializerMethodField(label="访问URL")

    def get_access_url(self, obj):
        return obj.file_url if obj.file_url else get_file_absolute_uri(obj.filepath, self.context.get('request', None))

    def create(self, validated_data):
        if not validated_data.get('file_url'):
            raise ValidationError('外部地址必须存在')
        return super().create(validated_data)

    def update(self, instance, validated_data):
        if not validated_data.get('file_url') and not instance.is_upload:
            raise ValidationError('外部地址必须存在')
        return super().update(instance, validated_data)


class NoticeMessageSerializer(BaseModelSerializer):
    class Meta:
        model = models.NoticeMessage
        fields = ['pk', 'title', 'level', "publish", 'notice_type', "notice_user", 'notice_dept', 'notice_role',
                  'message', "created_time", "user_count", "read_user_count", 'extra_json', "files"]

        table_fields = ['pk', 'title', 'notice_type', "read_user_count", "publish", "created_time"]
        extra_kwargs = {'extra_json': {'read_only': True}}

    notice_user = BasePrimaryKeyRelatedField(many=True, queryset=models.UserInfo.objects, label='被通知的用户',
                                             attrs=['pk', 'username'], input_type='api-search-user',
                                             format='{username}')
    notice_dept = BasePrimaryKeyRelatedField(many=True, queryset=models.DeptInfo.objects, label='被通知的部门',
                                             attrs=['pk', 'name'], input_type='api-search-dept', format='{name}')
    notice_role = BasePrimaryKeyRelatedField(many=True, queryset=models.UserRole.objects, label='被通知的角色',
                                             attrs=['pk', 'name'], input_type='api-search-role', format='{name}')

    files = serializers.JSONField(write_only=True, label="上传文件")
    user_count = serializers.SerializerMethodField(read_only=True, label="用户数量")
    read_user_count = serializers.SerializerMethodField(read_only=True, label="消息已读用户数量")

    notice_type = LabeledChoiceField(choices=models.NoticeMessage.NoticeChoices.choices,
                                     default=models.NoticeMessage.NoticeChoices.USER, label="消息类型")
    level = LabeledChoiceField(choices=models.NoticeMessage.LevelChoices.choices,
                               default=models.NoticeMessage.LevelChoices.DEFAULT, label="消息级别")

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
        if models.NoticeMessage.NoticeChoices.NOTICE == val and self.request.method == 'POST':
            raise ValidationError('参数有误，不支持创建系统公告')
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
        fields = ['pk', 'level', 'title', 'message', "created_time", 'unread', 'notice_type']
        table_fields = ['pk', 'title', 'unread', 'notice_type', "created_time"]
        read_only_fields = ['pk', 'notice_user', 'notice_type']

    notice_type = LabeledChoiceField(choices=models.NoticeMessage.NoticeChoices.choices,
                                     default=models.NoticeMessage.NoticeChoices.USER, label='消息类型')
    unread = serializers.SerializerMethodField(label="消息是否未读")

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
        fields = ['pk', 'notice_info', 'notice_type', 'owner_info', "unread", "updated_time"]
        read_only_fields = [x.name for x in models.NoticeUserRead._meta.fields]
        # depth = 1

    notice_type = serializers.CharField(source='notice.get_notice_type_display', read_only=True, label="消息类型")
    owner_info = UserInfoSerializer(fields=['pk', 'username'], read_only=True, source='owner', label="已读用户")
    notice_info = NoticeMessageSerializer(fields=['pk', 'level', 'title', 'notice_type', 'message', 'publish'],
                                          read_only=True, source='notice', label="消息公告")


class SystemConfigSerializer(BaseModelSerializer):
    class Meta:
        model = models.SystemConfig
        fields = ['pk', 'key', 'value', 'cache_value', 'is_active', 'inherit', 'access', 'description', 'created_time']
        read_only_fields = ['pk']
        fields_unexport = ['cache_value']  # 导入导出文件时，忽略该字段

    cache_value = serializers.SerializerMethodField(read_only=True, label="配置缓存数据")

    def get_cache_value(self, obj):
        val = SysConfig.get_value(obj.key)
        if isinstance(val, dict):
            return json.dumps(val)
        return json.dumps(val)


class UserPersonalConfigExportImportSerializer(SystemConfigSerializer):
    class Meta:
        model = models.UserPersonalConfig
        fields = ['pk', 'value', 'key', 'is_active', 'created_time', 'description', 'cache_value', 'owner', 'access']
        read_only_fields = ['pk']

    owner = BasePrimaryKeyRelatedField(attrs=['pk', 'username'], label="用户", queryset=models.UserInfo.objects,
                                       required=True)


class UserPersonalConfigSerializer(SystemConfigSerializer):
    class Meta:
        model = models.UserPersonalConfig
        fields = ['pk', 'config_user', 'owner', 'key', 'value', 'cache_value', 'is_active', 'access', 'description',
                  'created_time']

        read_only_fields = ['pk', 'owner']

    owner = BasePrimaryKeyRelatedField(attrs=['pk', 'username'], label="用户", read_only=True, format='{username}')
    config_user = BasePrimaryKeyRelatedField(write_only=True, many=True, queryset=models.UserInfo.objects,
                                             label="多个用户", input_type='api-search-user')

    def create(self, validated_data):
        config_user = validated_data.pop('config_user', [])
        owner = validated_data.pop('owner', None)
        instance = None
        if not config_user and not owner:
            raise ValidationError('用户ID不能为空')
        if owner:
            config_user.append(owner)
        for owner in config_user:
            validated_data['owner'] = owner
            instance = super().create(validated_data)
        return instance

    def update(self, instance, validated_data):
        validated_data.pop('config_user', None)
        return super().update(instance, validated_data)

    def get_cache_value(self, obj):
        val = UserConfig(obj.owner).get_value(obj.key)
        if isinstance(val, dict):
            val = json.dumps(val)
        return val


class UserLoginLogSerializer(BaseModelSerializer):
    class Meta:
        model = models.UserLoginLog
        fields = ['pk', 'creator', 'ipaddress', 'login_type', 'browser', 'system', 'agent', 'status', 'created_time']
        table_fields = ['pk', 'creator', 'ipaddress', 'login_type', 'browser', 'system', 'status', 'created_time']
        read_only_fields = ['pk', 'creator']

    creator = BasePrimaryKeyRelatedField(attrs=['pk', 'username'], read_only=True, label="操作用户")
    login_type = LabeledChoiceField(choices=models.UserLoginLog.LoginTypeChoices.choices,
                                    default=models.UserLoginLog.LoginTypeChoices.USERNAME)
