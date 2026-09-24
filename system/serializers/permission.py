#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : permission
# author : ly_13
# date : 8/10/2024

from collections import defaultdict

from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from common.core.data_scope import validate_rules
from common.core.serializers import BaseModelSerializer
from common.utils import get_logger
from system.models import DataPermission, Menu

logger = get_logger(__name__)


def get_menu_queryset():
    queryset = Menu.objects
    pks = queryset.filter(menu_type=Menu.MenuChoices.PERMISSION).values_list("parent", flat=True)
    return queryset.filter(Q(menu_type=Menu.MenuChoices.PERMISSION) | Q(id__in=pks)).order_by("rank")


def expand_menu_scope(menus):
    """生效范围归一：页面/目录菜单展开为其下全部权限点菜单（去重）。

    运行时菜单上下文恒为「请求命中的权限点菜单」（IsAuthenticated 写入 request.user.menu），
    只绑定页面菜单的授权不会命中任何请求。此处把页面级勾选展开为权限点集合，
    「整个页面生效」与「单个接口生效」因此共用同一份存储与判定口径。
    """
    permission_menus = {}
    pending = []
    for menu in menus:
        if menu.menu_type == Menu.MenuChoices.PERMISSION:
            permission_menus[str(menu.pk)] = menu
        else:
            pending.append(menu)
    if not pending:
        return list(permission_menus.values())

    children = defaultdict(list)
    # 一次取全量菜单树（仅三列）建立父子索引，避免按菜单逐个递归查询
    for row in Menu.objects.all().only("pk", "parent_id", "menu_type"):
        children[row.parent_id].append(row)
    while pending:
        current = pending.pop()
        for child in children.get(current.pk, []):
            if child.menu_type == Menu.MenuChoices.PERMISSION:
                permission_menus[str(child.pk)] = child
            else:
                pending.append(child)
    return list(permission_menus.values())


class DataPermissionSerializer(BaseModelSerializer):
    # 生效范围与分配对象统计（列表页展示「规则数 / 接口数 / 分配给谁」，
    # 列表 action 由视图集 annotate 零查询带出，其余场景回退 count()）
    rule_count = serializers.SerializerMethodField(help_text=_("Number of rules"))
    menu_count = serializers.SerializerMethodField(help_text=_("Number of bound menus"))
    user_count = serializers.SerializerMethodField(help_text=_("Number of bound users"))
    dept_count = serializers.SerializerMethodField(help_text=_("Number of bound departments"))

    class Meta:
        model = DataPermission
        fields = [
            "pk",
            "name",
            "is_active",
            "mode_type",
            "menu",
            "description",
            "rules",
            "rule_count",
            "menu_count",
            "user_count",
            "dept_count",
            "created_time",
        ]
        table_fields = [
            "pk",
            "name",
            "mode_type",
            "rule_count",
            "menu_count",
            "user_count",
            "dept_count",
            "is_active",
            "description",
            "created_time",
        ]
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

    @staticmethod
    def _annotated(obj, attr, fallback):
        """列表注解优先（零查询），无注解时回退关系计数。"""
        value = getattr(obj, attr, None)
        return fallback() if value is None else value

    def get_rule_count(self, obj):
        return len(obj.rules or [])

    def get_menu_count(self, obj):
        return self._annotated(obj, "scope_menu_count", obj.menu.count)

    def get_user_count(self, obj):
        return self._annotated(obj, "scope_user_count", obj.userinfo_set.count)

    def get_dept_count(self, obj):
        return self._annotated(obj, "scope_dept_count", obj.deptinfo_set.count)

    def validate_menu(self, value):
        """生效范围归一：页面级勾选展开为权限点集合（空集合拒绝，避免范围被意外放大）。"""
        menus = list(value)
        if not menus:
            return menus
        expanded = expand_menu_scope(menus)
        if not expanded:
            raise ValidationError(_("The selected menus contain no API permission, please select a page with APIs"))
        return expanded

    def validate(self, attrs):
        rules = attrs.get("rules", [] if not self.instance else self.instance.rules)
        # 写入侧结构校验：字段名手滑/非法匹配符等坏规则在保存时被拒，
        # 而不是让绑定用户的列表接口在读取时 500（编译器读侧另有 fail-closed 兜底）
        validate_rules(rules)
        if len(rules) < 2:
            attrs["mode_type"] = DataPermission.ModeChoices.OR
        return attrs
