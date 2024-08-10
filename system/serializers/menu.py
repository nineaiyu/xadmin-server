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
from common.core.permission import get_user_menu_queryset
from common.core.serializers import BaseModelSerializer
from system.models import Menu, ModelLabelField, MenuMeta

logger = logging.getLogger(__name__)


class RouteMetaSerializer(BaseModelSerializer):
    class Meta:
        model = MenuMeta
        fields = ['title', 'icon', 'showParent', 'showLink', 'extraIcon', 'keepAlive', 'frameSrc', 'frameLoading',
                  'transition', 'hiddenTag', 'dynamicLevel', 'fixedTag', 'auths']

    showParent = serializers.BooleanField(source='is_show_parent', read_only=True, label=_("Show parent menu"))
    showLink = serializers.BooleanField(source='is_show_menu', read_only=True, label=_("Show menu"))
    extraIcon = serializers.CharField(source='r_svg_name', read_only=True, label=_("Right icon"))
    keepAlive = serializers.BooleanField(source='is_keepalive', read_only=True, label=_("Keepalive"))
    frameSrc = serializers.CharField(source='frame_url', read_only=True, label=_("Iframe URL"))
    frameLoading = serializers.BooleanField(source='frame_loading', read_only=True, label=_("Iframe loading"))

    transition = serializers.SerializerMethodField()

    def get_transition(self, obj):
        return {
            'enterTransition': obj.transition_enter,
            'leaveTransition': obj.transition_leave,
        }

    hiddenTag = serializers.BooleanField(source='is_hidden_tag', read_only=True, label=_("Hidden tag"))
    fixedTag = serializers.BooleanField(source='fixed_tag', read_only=True, label=_("Fixed tag"))
    dynamicLevel = serializers.IntegerField(source='dynamic_level', read_only=True, label=_("Dynamic level"))

    auths = serializers.SerializerMethodField()

    def get_auths(self, obj):
        user = self.context.get('user')
        if user.is_superuser:
            menu_obj = Menu.objects.filter(is_active=True)
        else:
            menu_obj = get_user_menu_queryset(user)
        if menu_obj.exists():
            return menu_obj.filter(menu_type=Menu.MenuChoices.PERMISSION, parent=obj.menu).values_list('name',
                                                                                                       flat=True).distinct()
        else:
            return []


class MenuMetaSerializer(BaseModelSerializer):
    class Meta:
        model = MenuMeta
        exclude = ['creator', 'modifier', 'id']
        read_only_fields = ['creator', 'modifier', 'dept_belong', 'id']

    pk = serializers.IntegerField(source='id', read_only=True)


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
                                       label=_("Model"), attrs=['pk', 'name'], many=True)

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


class RouteSerializer(MenuSerializer):
    meta = RouteMetaSerializer(all_fields=True, label=_("Menu meta"))  # 用于前端菜单渲染
