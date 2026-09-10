#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : permission
# author : ly_13
# date : 8/10/2024

from django.db.models import Q

from common.core.data_scope import validate_rules
from common.core.serializers import BaseModelSerializer
from common.utils import get_logger
from system.models import DataPermission, Menu

logger = get_logger(__name__)


def get_menu_queryset():
    queryset = Menu.objects
    pks = queryset.filter(menu_type=Menu.MenuChoices.PERMISSION).values_list("parent", flat=True)
    return queryset.filter(Q(menu_type=Menu.MenuChoices.PERMISSION) | Q(id__in=pks)).order_by("rank")


class DataPermissionSerializer(BaseModelSerializer):
    class Meta:
        model = DataPermission
        fields = ["pk", "name", "is_active", "mode_type", "menu", "description", "rules", "created_time"]
        table_fields = ["pk", "name", "mode_type", "is_active", "description", "created_time"]
        extra_kwargs = {
            "menu": {
                # 菜单关联行数超过 SEARCH_CHOICES_MAX_COUNT，choices 会被截断，
                # 标记 api-search-* 走远程搜索口径，由前端按页自定义渲染（数据权限页用全量菜单树级联）
                "attrs": ["pk", "name", "parent_id", "meta__title"],
                "many": True,
                "required": False,
                "queryset": get_menu_queryset(),
                "input_type": "api-search-menu",
            },
        }

    def validate(self, attrs):
        rules = attrs.get("rules", [] if not self.instance else self.instance.rules)
        # 写入侧结构校验：字段名手滑/非法匹配符等坏规则在保存时被拒，
        # 而不是让绑定用户的列表接口在读取时 500（编译器读侧另有 fail-closed 兜底）
        validate_rules(rules)
        if len(rules) < 2:
            attrs["mode_type"] = DataPermission.ModeChoices.OR
        return attrs
