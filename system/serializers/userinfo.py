#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : userinfo
# author : ly_13
# date : 8/10/2024


import logging

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.core.fields import LabeledChoiceField
from common.core.serializers import BaseModelSerializer
from system import models
from system.models import UserInfo

logger = logging.getLogger(__name__)


class UserInfoSerializer(BaseModelSerializer):
    class Meta:
        model = UserInfo
        write_fields = ['username', 'nickname', 'gender']
        fields = write_fields + ['email', 'last_login', 'pk', 'phone', 'avatar', 'roles', 'date_joined', 'dept']
        read_only_fields = list(set([x.name for x in models.UserInfo._meta.fields]) - set(write_fields))

    gender = LabeledChoiceField(choices=UserInfo.GenderChoices.choices,
                                default=UserInfo.GenderChoices.UNKNOWN, label=_("Gender"))
    dept = serializers.CharField(source='dept.name', read_only=True)
    roles = serializers.SerializerMethodField()

    def get_roles(self, obj):
        return obj.roles.values_list('name', flat=True)
