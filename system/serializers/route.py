#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : route
# author : ly_13
# date : 8/16/2024
from typing import Any
from urllib.parse import parse_qsl

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers
from rest_framework.serializers import ModelSerializer

from common.core.serializers import BaseModelSerializer
from common.utils import get_logger
from system.models import Menu, MenuMeta

logger = get_logger(__name__)


class RouteMetaSerializer(ModelSerializer):
    class Meta:
        model = MenuMeta
        fields = [
            "title",
            "icon",
            "showParent",
            "showLink",
            "extraIcon",
            "keepAlive",
            "frameSrc",
            "frameLoading",
            "transition",
            "hiddenTag",
            "dynamicLevel",
            "fixedTag",
            "watermark",
            "activePath",
            "badge",
            "badgeType",
            "query",
            "groupName",
            "groupRank",
        ]

    showParent = serializers.BooleanField(source="is_show_parent", read_only=True, label=_("Show parent menu"))
    showLink = serializers.BooleanField(source="is_show_menu", read_only=True, label=_("Show menu"))
    extraIcon = serializers.CharField(source="r_svg_name", read_only=True, label=_("Right icon"))
    keepAlive = serializers.BooleanField(source="is_keepalive", read_only=True, label=_("Keepalive"))
    frameSrc = serializers.CharField(source="frame_url", read_only=True, label=_("Iframe URL"))
    frameLoading = serializers.BooleanField(source="frame_loading", read_only=True, label=_("Iframe loading"))

    transition = serializers.SerializerMethodField()

    def get_transition(self, obj: Any) -> Any:
        return {
            "enterTransition": obj.transition_enter,
            "leaveTransition": obj.transition_leave,
        }

    hiddenTag = serializers.BooleanField(source="is_hidden_tag", read_only=True, label=_("Hidden tag"))
    fixedTag = serializers.BooleanField(source="fixed_tag", read_only=True, label=_("Fixed tag"))
    dynamicLevel = serializers.IntegerField(source="dynamic_level", read_only=True, label=_("Dynamic level"))
    # 菜单级水印开关：置顶「页面强制挂载」，与基本设置的路径前缀范围是或关系
    watermark = serializers.BooleanField(read_only=True, label=_("Page watermark"))

    # 菜单外页面（详情页等）高亮指定菜单
    activePath = serializers.CharField(source="active_path", read_only=True, label=_("Active path"))
    badge = serializers.CharField(read_only=True, label=_("Menu badge"))
    badgeType = serializers.CharField(source="badge_type", read_only=True, label=_("Badge type"))

    query = serializers.SerializerMethodField()

    def get_query(self, obj: Any) -> Any:
        """查询参数以 ``k=v&k2=v2`` 存储，下发时解析为对象；空值下发 null（前端不附加 query）"""
        if not obj.query:
            return None
        return dict(parse_qsl(obj.query, keep_blank_values=True))

    groupName = serializers.CharField(source="group_name", read_only=True, label=_("Menu group name"))
    groupRank = serializers.IntegerField(source="group_rank", read_only=True, label=_("Menu group rank"))


class RouteSerializer(BaseModelSerializer):
    class Meta:
        model = Menu
        fields = ["pk", "name", "rank", "path", "component", "meta", "parent"]
        extra_kwargs = {
            "rank": {"read_only": True},
            "parent": {"attrs": ["pk", "name"], "allow_null": True, "required": False},
        }

    meta = RouteMetaSerializer(label=_("Menu meta"))  # 用于前端菜单渲染
