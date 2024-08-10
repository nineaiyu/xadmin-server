#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : permission
# author : ly_13
# date : 8/10/2024

import logging

from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import ValidationError

from common.core.fields import BasePrimaryKeyRelatedField, LabeledChoiceField
from common.core.serializers import BaseModelSerializer
from system.models import DataPermission, Menu, ModeTypeAbstract

logger = logging.getLogger(__name__)


def get_menu_queryset():
    queryset = Menu.objects
    pks = queryset.filter(menu_type=Menu.MenuChoices.PERMISSION).values_list(
        'parent', flat=True)
    return queryset.filter(Q(menu_type=Menu.MenuChoices.PERMISSION) | Q(id__in=pks)).order_by('rank')


class DataPermissionSerializer(BaseModelSerializer):
    class Meta:
        model = DataPermission
        fields = ['pk', 'name', "is_active", "mode_type", "menu", "description", 'rules', "created_time"]
        table_fields = ['pk', 'name', "mode_type", "is_active", "description", "created_time"]
        # extra_kwargs = {'rules': {'required': True}}

    menu = BasePrimaryKeyRelatedField(queryset=get_menu_queryset(), many=True, label=_("Menu"), required=False,
                                      attrs=['pk', 'name', 'parent_id', 'meta__title'])
    mode_type = LabeledChoiceField(choices=ModeTypeAbstract.ModeChoices.choices,
                                   default=ModeTypeAbstract.ModeChoices.OR, label=_("Mode type"))

    def validate(self, attrs):
        rules = attrs.get('rules', [] if not self.instance else self.instance.rules)
        if not rules:
            raise ValidationError(_("The rule cannot be null"))
        if len(rules) < 2:
            attrs['mode_type'] = DataPermission.ModeChoices.OR
        return attrs
