#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""用户候选数据源（选人控件 / 报表 IM 收件人）。

口径：关键字（用户名/昵称）搜索，或按主键回显（编辑既有数据）；字段收敛为
用户主键/用户名/昵称，结果条数封顶——必须带关键字或 pks，不做通讯录全量枚举。

权限：由调用方视图的 action 决定；action 路径以 ``/user-options`` 结尾时，
权限解析与该视图 list 权限同口径（见 common/core/permission.py，存量角色免重授权）。
"""

from django.db.models import Q

from system.models import UserInfo

MAX_USER_OPTIONS = 20


def search_user_options(keyword: str = "", pks: str = "", limit: int = MAX_USER_OPTIONS) -> list:
    """返回候选用户列表（pk/用户名/昵称）；无关键字且无合法主键时返回空列表。"""
    keyword = (keyword or "").strip()
    queryset = UserInfo.objects.filter(is_active=True)
    if keyword:
        queryset = queryset.filter(Q(username__icontains=keyword) | Q(nickname__icontains=keyword))
    else:
        pk_list = [int(item) for item in (pks or "").split(",") if item.strip().isdigit()][:limit]
        if not pk_list:
            return []
        queryset = queryset.filter(pk__in=pk_list)
    return [
        {"pk": user.pk, "username": user.username, "nickname": user.nickname}
        for user in queryset.order_by("username")[:limit]
    ]
