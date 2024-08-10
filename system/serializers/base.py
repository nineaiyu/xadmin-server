#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : base
# author : ly_13
# date : 8/10/2024

import logging

from django.utils.translation import gettext_lazy as _

from common.core.fields import BasePrimaryKeyRelatedField, LabeledChoiceField
from common.core.serializers import BaseModelSerializer
from system.models import UserRole, DataPermission, ModeTypeAbstract

logger = logging.getLogger(__name__)


class BaseRoleRuleInfo(BaseModelSerializer):
    roles = BasePrimaryKeyRelatedField(queryset=UserRole.objects, allow_null=True, required=False, format="{name}",
                                       attrs=['pk', 'name', 'code'], label=_("Role permission"), many=True)
    rules = BasePrimaryKeyRelatedField(queryset=DataPermission.objects, allow_null=True, required=False, many=True,
                                       format="{name}", attrs=['pk', 'name', 'get_mode_type_display'],
                                       label=_("Data permission"))
    mode_type = LabeledChoiceField(choices=ModeTypeAbstract.ModeChoices.choices, label=_("Mode type"),
                                   default=ModeTypeAbstract.ModeChoices.OR.value)
