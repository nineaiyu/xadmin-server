#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : userinfo
# author : ly_13
# date : 8/10/2024


from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from common.base.utils import AESCipherV2
from common.core.serializers import BaseModelSerializer
from common.utils import get_logger
from settings.utils.password import check_password_rules
from system import models
from system.models import UserInfo

logger = get_logger(__name__)


class UserInfoSerializer(BaseModelSerializer):
    class Meta:
        model = UserInfo
        write_fields = ['username', 'nickname', 'gender']
        fields = write_fields + ['email', 'last_login', 'pk', 'phone', 'avatar', 'roles', 'date_joined', 'dept']
        read_only_fields = list(set([x.name for x in models.UserInfo._meta.fields]) - set(write_fields))

    dept = serializers.CharField(source='dept.name', read_only=True)
    roles = serializers.SerializerMethodField()

    @extend_schema_field(serializers.ListField)
    def get_roles(self, obj):
        return list(obj.roles.values_list('name', flat=True))


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(
        min_length=5, max_length=128, required=True, write_only=True, label=_("Old password")
    )
    sure_password = serializers.CharField(
        min_length=5, max_length=128, required=True, write_only=True, label=_("Confirm password")
    )

    def update(self, instance, validated_data):
        sure_password = AESCipherV2(instance.username).decrypt(validated_data.get('sure_password'))
        old_password = AESCipherV2(instance.username).decrypt(validated_data.get('old_password'))
        if not instance.check_password(old_password):
            raise serializers.ValidationError(_("Old password verification failed"))
        if not check_password_rules(sure_password, instance.is_superuser):
            raise serializers.ValidationError(_('Password does not match security rules'))

        instance.set_password(sure_password)
        instance.modifier = self.context.get('request').user
        instance.save(update_fields=['password', 'modifier'])
        return instance
