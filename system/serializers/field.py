#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : field
# author : ly_13
# date : 8/10/2024
import logging

from django.utils.translation import gettext_lazy as _

from common.core.fields import BasePrimaryKeyRelatedField, LabeledChoiceField
from common.core.serializers import BaseModelSerializer
from system.models import ModelLabelField

logger = logging.getLogger(__name__)


class ModelLabelFieldSerializer(BaseModelSerializer):
    class Meta:
        model = ModelLabelField
        fields = ['pk', 'name', 'label', 'parent', 'field_type', 'created_time', 'updated_time']
        read_only_fields = [x.name for x in ModelLabelField._meta.fields]

    parent = BasePrimaryKeyRelatedField(read_only=True, attrs=['pk', 'name', 'label'], format="{label}({pk})")
    field_type = LabeledChoiceField(choices=ModelLabelField.FieldChoices.choices,
                                    default=ModelLabelField.FieldChoices.DATA, label=_("Field type"))
