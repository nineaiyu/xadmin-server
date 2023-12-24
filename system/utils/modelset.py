#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : modelset
# author : ly_13
# date : 12/24/2023
from rest_framework.decorators import action

from common.core.response import ApiResponse
from system.models import UserRole, DataPermission


class ChangeRolePermissionAction(object):
    def get_object(self):
        raise NotImplementedError('get_object must be overridden')

    @action(methods=['post'], detail=True)
    def empower(self, request, *args, **kwargs):
        instance = self.get_object()
        roles = request.data.get('roles')
        rules = request.data.get('rules')
        mode_type = request.data.get('mode_type')
        if roles is not None or rules is not None:
            if roles is not None:
                roles_queryset = UserRole.objects.filter(pk__in=roles).all()
                instance.roles.set(roles_queryset)
            if rules is not None:
                instance.mode_type = mode_type
                instance.modifier = request.user
                instance.save(update_fields=['mode_type', 'modifier'])
                rules_queryset = DataPermission.objects.filter(pk__in=rules).all()
                instance.rules.set(rules_queryset)
            return ApiResponse(detail="操作成功")
        return ApiResponse(code=1004, detail="数据异常")
