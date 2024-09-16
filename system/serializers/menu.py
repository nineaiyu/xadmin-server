#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : menu
# author : ly_13
# date : 8/10/2024
import logging

from django.db import transaction
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.core.fields import BasePrimaryKeyRelatedField, LabeledChoiceField
from common.core.serializers import BaseModelSerializer
from system.models import Menu, MenuMeta, ModelLabelField

logger = logging.getLogger(__name__)


class MenuMetaSerializer(BaseModelSerializer):
    class Meta:
        model = MenuMeta
        exclude = ['creator', 'modifier', 'id']
        read_only_fields = ['creator', 'modifier', 'dept_belong', 'id']

    pk = serializers.UUIDField(source='id', read_only=True)


class MenuSerializer(BaseModelSerializer):
    meta = MenuMetaSerializer(label=_("Menu meta"))

    class Meta:
        model = Menu
        fields = ['pk', 'name', 'rank', 'path', 'component', 'meta', 'parent', 'menu_type', 'is_active',
                  'model', 'method']
        # read_only_fields = ['pk'] # 用于文件导入导出时，不丢失上级节点
        extra_kwargs = {'rank': {'read_only': True}}

    parent = BasePrimaryKeyRelatedField(queryset=Menu.objects, allow_null=True, required=False,
                                        label=_("Parent menu"), attrs=['pk', 'name'])

    model = BasePrimaryKeyRelatedField(queryset=ModelLabelField.objects, allow_null=True, required=False,
                                       label=_("Model"), attrs=['pk', 'name', 'label'], many=True)
    menu_type = LabeledChoiceField(choices=Menu.MenuChoices.choices,
                                   default=Menu.MenuChoices.DIRECTORY, label=_("Menu type"))

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
        model = Menu
        fields = ['pk', 'title', 'parent', 'menu_type']
        read_only_fields = ['pk']
        extra_kwargs = {'rank': {'read_only': True}}

    title = serializers.CharField(source='meta.title', read_only=True, label=_("Menu title"))
