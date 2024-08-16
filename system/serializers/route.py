#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : route
# author : ly_13
# date : 8/16/2024
import logging

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.core.fields import BasePrimaryKeyRelatedField
from common.core.serializers import BaseModelSerializer
from system.models import Menu, MenuMeta

logger = logging.getLogger(__name__)


class RouteMetaSerializer(BaseModelSerializer):
    class Meta:
        model = MenuMeta
        fields = ['title', 'icon', 'showParent', 'showLink', 'extraIcon', 'keepAlive', 'frameSrc', 'frameLoading',
                  'transition', 'hiddenTag', 'dynamicLevel', 'fixedTag']

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


class RouteSerializer(BaseModelSerializer):
    class Meta:
        model = Menu
        fields = ['pk', 'name', 'rank', 'path', 'component', 'meta', 'parent']
        extra_kwargs = {'rank': {'read_only': True}}

    parent = BasePrimaryKeyRelatedField(queryset=Menu.objects, allow_null=True, required=False,
                                        label=_("Parent menu"), attrs=['pk', 'name'])

    meta = RouteMetaSerializer(all_fields=True, label=_("Menu meta"))  # 用于前端菜单渲染
