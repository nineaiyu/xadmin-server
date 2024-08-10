#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : userinfo
# author : ly_13
# date : 8/10/2024


import logging

from rest_framework.validators import UniqueValidator

from common.core.serializers import BaseModelSerializer
from system import models
from system.models.user import UserInfo

logger = logging.getLogger(__name__)


class UserInfoSerializer(BaseModelSerializer):
    class Meta:
        model = UserInfo
        fields = ['username', 'nickname', 'email', 'last_login', 'gender', 'pk', 'phone', 'avatar', 'roles',
                  'date_joined', 'dept']
        extra_kwargs = {'last_login': {'read_only': True}, 'date_joined': {'read_only': True},
                        'pk': {'read_only': True}, 'avatar': {'read_only': True},
                        'email': {'validators': [UniqueValidator(queryset=models.UserInfo.objects.all())]},
                        'phone': {'validators': [UniqueValidator(queryset=models.UserInfo.objects.all())]},
                        }
        read_only_fields = ['pk'] + list(set([x.name for x in models.UserInfo._meta.fields]) - set(fields))
