#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : department
# author : ly_13
# date : 8/10/2024

import hashlib
import json
from typing import Any

from django.core.cache import cache
from django.db import models
from django.utils.translation import gettext_lazy as _
from rest_framework.utils import encoders

from common.core.models import DbAuditModel, DbUuidModel


class DeptInfo(DbAuditModel, DbUuidModel):
    # 部门树缓存有效期（秒）。部门变更通过信号即时失效，TTL 仅兜底。
    DEPT_TREE_CACHE_TTL = 60

    name = models.CharField(verbose_name=_("Department name"), max_length=128)
    code = models.CharField(max_length=128, verbose_name=_("Department code"), unique=True)
    parent = models.ForeignKey(
        "identity.DeptInfo",
        on_delete=models.PROTECT,
        verbose_name=_("Superior department"),
        null=True,
        blank=True,
        related_query_name="parent_query",
    )
    leader = models.ForeignKey(
        "identity.UserInfo",
        on_delete=models.SET_NULL,
        verbose_name=_("Leader"),
        null=True,
        blank=True,
        related_name="leader_depts",
        help_text=_("Department leader, who can be granted data permissions of the led departments"),
    )
    managers = models.ManyToManyField(
        "identity.UserInfo",
        through="identity.DeptManagerAssignment",
        through_fields=("dept", "user"),
        blank=True,
        related_name="managed_depts",
        verbose_name=_("Department managers"),
        help_text=_("Users appointed to manage this department and its descendants"),
    )
    roles = models.ManyToManyField("identity.UserRole", verbose_name=_("Role permission"), blank=True)
    rank = models.IntegerField(verbose_name=_("Rank"), default=99)
    auto_bind = models.BooleanField(
        verbose_name=_("Auto bind"),
        default=False,
        help_text=_(
            "If the value of the registration parameter channel is consistent with the department code, the user is automatically bound to the department"
        ),
    )
    is_active = models.BooleanField(verbose_name=_("Is active"), default=True)

    @classmethod
    def recursion_dept_info(
        cls, dept_id: Any, dept_all_list: Any = None, dept_list: Any = None, is_parent: bool = False
    ) -> Any:
        """递归获取部门（含自身）及其全部下级（is_parent=True 时向上级方向）。

        全量部门表 + O(n²) 递归扫描被数据权限过滤的每个请求调用。
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
    def _recursion_dept_info(
        cls, dept_id: Any, dept_all_list: Any, dept_list: Any = None, is_parent: Any = False
    ) -> Any:
        parent = "parent"
        pk = "pk"
        if is_parent:
            parent, pk = pk, parent
        if not dept_all_list:
            dept_all_list = DeptInfo.objects.values("pk", "parent")
        if dept_list is None:
            dept_list = [dept_id]
        for dept in dept_all_list:
            # str 归一比较：规则 value 经 JSON 反序列化得到的是字符串主键，
            # values() 取出的是 UUID 对象（旧实现直接 == 比较，str 入参永远匹配不上，
            # 「部门及下级」规则从未展开过子树）
            dept_parent = dept.get(parent)
            if dept_parent is not None and str(dept_parent) == str(dept_id):
                if dept.get(pk):
                    dept_list.append(dept.get(pk))
                    cls._recursion_dept_info(dept.get(pk), dept_all_list, dept_list, is_parent)
        return json.loads(json.dumps(list(set(dept_list)), cls=encoders.JSONEncoder))

    @classmethod
    def dept_tree_pks(cls, dept_ids: Any, is_parent: Any = False) -> Any:
        """批量展开：一次取全表 + 内存建索引（结果 = 逐个 ``recursion_dept_info`` 的并集）。

        数据权限编译期按「部门集合」展开子树时，逐 pk 调用会各自全表扫描（缓存未命中
        时 N 个部门 = N 次全表查询）；这里一次查询 + parent→children 索引 + 迭代展开，
        结果与逐个调用并集**逐项一致**（含输入部门自身，已归一为字符串主键，可直接
        用于 ``dept__in=``）。结果按「输入集合 + 方向」缓存，缓存键前缀与单条路径同源
        （``dept_recursion_*``），因此共用同一失效信号。
        """
        wanted = sorted({str(item) for item in dept_ids or [] if item is not None})
        if not wanted:
            return []
        digest = hashlib.md5(",".join(wanted).encode()).hexdigest()[:12]
        cache_key = f"dept_recursion_batch_{int(bool(is_parent))}_{digest}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        children: dict[str, Any] = {}
        parents: dict[str, Any] = {}
        for row in cls.objects.values("pk", "parent"):
            pk, parent = str(row["pk"]), row["parent"]
            parents[pk] = str(parent) if parent is not None else None
            children.setdefault(str(parent) if parent is not None else "", []).append(pk)

        result = set()
        stack = list(wanted)
        while stack:
            current = stack.pop()
            if current in result:
                continue
            result.add(current)
            if is_parent:
                upstream = parents.get(current)
                if upstream:
                    stack.append(upstream)
            else:
                stack.extend(children.get(current, []))

        payload = json.loads(json.dumps(sorted(result), cls=encoders.JSONEncoder))
        cache.set(cache_key, payload, cls.DEPT_TREE_CACHE_TTL)
        return payload

    @classmethod
    def invalid_dept_tree_cache(cls) -> None:
        """部门树缓存失效（DeptInfo 增删改时调用）。"""
        cache.delete_pattern("dept_recursion_*")

    rules = models.ManyToManyField("system.DataPermission", verbose_name=_("Data permission"), blank=True)

    class Meta:
        verbose_name = _("Department")
        verbose_name_plural = verbose_name
        ordering = (
            "-rank",
            "-created_time",
        )

    def __str__(self) -> str:
        return f"{self.name}({self.pk})"


class DeptManagerAssignment(DbUuidModel):
    """部门管理员任命记录（``DeptInfo.managers`` 的 through 模型）。

    记录「谁任命的、什么时候」供审计；任命与解任统一走部门 ViewSet 的
    ``assign-managers`` 端点（同时维护预置角色成员与用户级数据权限规则），
    不直接经序列化器写入。
    """

    dept = models.ForeignKey(
        "identity.DeptInfo",
        on_delete=models.CASCADE,
        related_name="manager_assignments",
        verbose_name=_("Department"),
    )
    user = models.ForeignKey(
        "identity.UserInfo",
        on_delete=models.CASCADE,
        related_name="manager_assignments",
        verbose_name=_("Manager"),
    )
    created_by = models.ForeignKey(
        "identity.UserInfo",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("Assigned by"),
    )
    created_time = models.DateTimeField(auto_now_add=True, verbose_name=_("Created time"))

    class Meta:
        unique_together = ("dept", "user")
        ordering = ("-created_time",)
        verbose_name = _("Department manager assignment")
        verbose_name_plural = verbose_name

    def __str__(self) -> str:
        return f"{self.dept_id}:{self.user_id}"
