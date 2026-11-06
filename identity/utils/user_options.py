#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""用户候选数据源（选人控件 / 报表 IM 收件人）。

口径：关键字（用户名/昵称）搜索，或按主键回显（编辑既有数据）；字段收敛为
用户主键/用户名/昵称，结果条数封顶——必须带关键字或 pks，不做通讯录全量枚举。

权限：由调用方视图的 action 决定；action 路径以 ``/user-options`` 结尾时，
权限解析与该视图 list 权限同口径（见 packages/xadmin-common/common/core/permission.py，存量角色免重授权）。

数据权限两种调用口径（显式约定，调用方按场景选择）：

1. **组织管理场景（传 ``user``）**：部门/岗位成员管理等身份域页面——管理员在
   管理组织成员，与列表页同口径套数据权限，防跨部门探测/枚举；
2. **跨域功能选人场景（不传 ``user``，显式豁免）**：报表 IM 收件人、表单数据
   过滤、消息选人等——功能作者选择**目标用户**（选收件人本就要能搜到全公司的
   人）；若套数据权限，未配置数据权限授权的普通用户会 fail-closed 得到空候选
   （``get_filter_queryset`` 无授权即 ``none()``），功能被破坏。此口径的固有
   约束：认证 + 该功能 list 权限点 + 20 条上限 + 必须带 keyword/pks，探测面
   收敛为「持功能权限者可按关键字搜索用户名/昵称」。

   当前登记的豁免调用点：``dataset/views/report.py``（IM 收件人）、
   ``dataset/views/form_data.py``（表单数据过滤）、
   ``dataset/views/dform_submission.py``（提交数据过滤）、
   ``message/views.py``（消息联系人）。
"""

from typing import Any

from django.db.models import Q

from identity.models import UserInfo

MAX_USER_OPTIONS = 20


def search_user_options(keyword: str = "", pks: str = "", limit: int = MAX_USER_OPTIONS, user: Any = None) -> list[Any]:
    """返回候选用户列表（pk/用户名/昵称）；无关键字且无合法主键时返回空列表。

    ``user`` 非空时套用数据权限（组织管理口径）：只按 is_active 枚举会让
    仅持 dept 列表权限的部门管理员跨部门探测/枚举用户。跨域功能选人
    场景不传 user（显式豁免，见模块 docstring 的口径说明）。
    """
    keyword = (keyword or "").strip()
    queryset = UserInfo.objects.filter(is_active=True)
    if user is not None:
        from common.core.filter import get_filter_queryset

        queryset = get_filter_queryset(queryset, user)
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
