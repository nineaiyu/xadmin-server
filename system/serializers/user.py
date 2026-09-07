#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : user
# author : ly_13
# date : 8/10/2024

from django.contrib.auth.hashers import make_password
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.validators import UniqueValidator

from common.base.utils import AESCipherV2
from common.core.serializers import BaseModelSerializer
from common.fields.utils import input_wrapper
from common.utils import get_logger
from message.services import get_online_users_layers
from settings.services import check_password_rules
from settings.services import LoginBlockUtil
from system.models import UserInfo

logger = get_logger(__name__)


class UserSerializer(BaseModelSerializer):
    class Meta:
        model = UserInfo
        fields = [
            'pk', 'avatar', 'username', 'nickname', 'phone', 'email', 'gender', 'block', 'online_count', 'is_active',
            'password', 'dept', 'description', 'last_login', 'date_joined', 'roles', 'rules', 'mode_type', 'deleted_at'
        ]
        read_only_fields = ['pk', 'deleted_at'] + list(
            set([x.name for x in UserInfo._meta.fields]) - set(fields)
        )
        table_fields = [
            'pk', 'avatar', 'username', 'nickname', 'gender', 'block', 'online_count', 'is_active', 'dept', 'phone',
            'last_login', 'date_joined', 'roles', 'rules'
        ]
        extra_kwargs = {
            'pk': {'read_only': True}, 'last_login': {'read_only': True}, 'date_joined': {'read_only': True},
            'avatar': {'read_only': True}, 'password': {'write_only': True},
            'roles': {'required': False, 'attrs': ['pk', 'name', 'code'], 'format': "{name}", 'many': True},
            'rules': {'required': False, 'attrs': ['pk', 'name', 'get_mode_type_display'], 'format': "{name}",
                      'many': True},
            'dept': {'required': False, 'attrs': ['pk', 'name', 'parent_id'], 'format': "{name}"},
            'email': {'validators': [UniqueValidator(queryset=UserInfo.objects.all())]},
            'phone': {'validators': [UniqueValidator(queryset=UserInfo.objects.all())]}
        }

    block = input_wrapper(serializers.SerializerMethodField)(read_only=True, input_type='boolean',
                                                             label=_("Login blocked"))
    online_count = input_wrapper(serializers.SerializerMethodField)(read_only=True, input_type='number',
                                                                    label=_("Online count"))

    # username 在 DB 层保持全局唯一（auth.E003 约束 USERNAME_FIELD 必须 unique），
    # 模型字段 unique=True 使 DRF 自动生成的 UniqueValidator 只查活跃数据（默认管理器），
    # 会放过回收站中的同名用户造成 IntegrityError——这里显式按 all_objects 拦截，
    # 回收站用户名视为占用并返回可读 400
    def validate_username(self, value):
        queryset = UserInfo.all_objects.filter(username=value)
        if self.instance is not None:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise ValidationError(_("This field already exists"))
        return value

    @extend_schema_field(serializers.BooleanField)
    def get_block(self, obj):
        # 以整页用户名为单位批量查询锁定状态，结果缓存在 context 中（ListSerializer 与子字段共享）
        if 'user_login_block' not in self.context:
            usernames = [instance.username for instance in self.get_page_instances(obj)]
            self.context['user_login_block'] = LoginBlockUtil.get_users_block(usernames)
        return self.context['user_login_block'].get(obj.username, False)

    @extend_schema_field(serializers.IntegerField)
    def get_online_count(self, obj):
        if 'user_online_layers' not in self.context:
            pks = [instance.pk for instance in self.get_page_instances(obj)]
            self.context['user_online_layers'] = get_online_users_layers(pks)
        return len(self.context['user_online_layers'].get(obj.pk, []))

    def validate(self, attrs):
        password = attrs.get('password')
        if password:
            if self.request.method == 'POST':
                # 注意：密码规则必须校验解密后的明文。前端提交的是
                # AESCipherV2(username) 加密串，若拿提交原文校验，收紧
                # 大小写/数字规则后密文无法稳定满足（hex/base64 形态随机），
                # 会导致合法密码被拒。加密失败时提交值即为明文（导入等场景）
                try:
                    plain_password = AESCipherV2(attrs.get('username')).decrypt(password)
                except Exception as e:
                    plain_password = password
                    logger.warning(f"create user and set password failed:{e}. so set default password")
                if not check_password_rules(plain_password):
                    raise ValidationError(_('Password does not match security rules'))
                attrs['password'] = make_password(plain_password)
            else:
                raise ValidationError(_("Abnormal password field"))
        return attrs


class ResetPasswordSerializer(serializers.Serializer):
    password = serializers.CharField(
        min_length=5, max_length=128, required=True, write_only=True, label=_("Password")
    )

    def update(self, instance, validated_data):
        password = AESCipherV2(instance.username).decrypt(validated_data.get('password'))
        if not check_password_rules(password, instance.is_superuser):
            raise serializers.ValidationError(_('Password does not match security rules'))

        instance.set_password(password)
        instance.modifier = self.context.get('request').user
        instance.save(update_fields=['password', 'modifier'])
        return instance
