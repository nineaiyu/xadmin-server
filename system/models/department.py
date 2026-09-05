#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : department
# author : ly_13
# date : 8/10/2024

import json

from django.core.cache import cache
from django.db import models
from django.utils.translation import gettext_lazy as _
from rest_framework.utils import encoders

from common.core.models import DbAuditModel, DbUuidModel
from system.models import ModeTypeAbstract


class DeptInfo(DbAuditModel, ModeTypeAbstract, DbUuidModel):
    # PERF-09：部门树缓存有效期（秒）。部门变更通过信号即时失效，TTL 仅兜底。
    DEPT_TREE_CACHE_TTL = 60

    name = models.CharField(verbose_name=_("Department name"), max_length=128)
    code = models.CharField(max_length=128, verbose_name=_("Department code"), unique=True)
    parent = models.ForeignKey('system.DeptInfo', on_delete=models.PROTECT, verbose_name=_("Superior department"),
                               null=True, blank=True, related_query_name="parent_query")
    roles = models.ManyToManyField("system.UserRole", verbose_name=_("Role permission"), blank=True)
    rules = models.ManyToManyField("system.DataPermission", verbose_name=_("Data permission"), blank=True)
    rank = models.IntegerField(verbose_name=_("Rank"), default=99)
    auto_bind = models.BooleanField(verbose_name=_("Auto bind"), default=False,
                                    help_text=_(
                                        "If the value of the registration parameter channel is consistent with the department code, the user is automatically bound to the department"))
    is_active = models.BooleanField(verbose_name=_("Is active"), default=True)

    @classmethod
    def recursion_dept_info(cls, dept_id, dept_all_list=None, dept_list=None, is_parent=False):
        """递归获取部门（含自身）及其全部下级（is_parent=True 时向上级方向）。

        PERF-09：全量部门表 + O(n²) 递归扫描被数据权限过滤的每个请求调用。
        这里按 (dept_id, is_parent) 维度缓存结果，DeptInfo 变更时通过信号失效。
        传入自定义 dept_all_list/dept_list 的调用（仅递归内部使用）不走缓存。
        """
        if dept_all_list is None and dept_list is None and not isinstance(dept_id, (list, tuple)):
            cache_key = f"dept_recursion_{int(bool(is_parent))}_{dept_id}"
            cached = cache.get(cache_key)
            if cached is not None:
                return cached
            result = cls._recursion_dept_info(dept_id, None, None, is_parent)
            cache.set(cache_key, result, cls.DEPT_TREE_CACHE_TTL)
            return result
        return cls._recursion_dept_info(dept_id, dept_all_list, dept_list, is_parent)

    @classmethod
    def _recursion_dept_info(cls, dept_id, dept_all_list, dept_list, is_parent=False):
        parent = 'parent'
        pk = 'pk'
        if is_parent:
            parent, pk = pk, parent
        if not dept_all_list:
            dept_all_list = DeptInfo.objects.values("pk", "parent")
        if dept_list is None:
            dept_list = [dept_id]
        for dept in dept_all_list:
            if dept.get(parent) == dept_id:
                if dept.get(pk):
                    dept_list.append(dept.get(pk))
                    cls._recursion_dept_info(dept.get(pk), dept_all_list, dept_list, is_parent)
        return json.loads(json.dumps(list(set(dept_list)), cls=encoders.JSONEncoder))

    @classmethod
    def invalid_dept_tree_cache(cls):
        """PERF-09：部门树缓存失效（DeptInfo 增删改时调用）。"""
        cache.delete_pattern("dept_recursion_*")

    class Meta:
        verbose_name = _("Department")
        verbose_name_plural = verbose_name
        ordering = ("-rank", "-created_time",)

    def __str__(self):
        return f"{self.name}({self.pk})"
