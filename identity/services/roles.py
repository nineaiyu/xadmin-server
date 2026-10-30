#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""角色成员解析：直挂角色 ∪ 部门挂角色。

审批人/流程可发起角色/权限反查等「按角色 code 找用户」的消费点统一走本模块，
避免只查 ``UserInfo.roles`` 而漏掉部门绑定的角色。
"""

from typing import Any

from common.core.roles import role_members_q, user_role_codes

__all__ = ["get_users_by_role_codes", "role_members_q", "user_role_codes"]


def get_users_by_role_codes(codes: Any) -> Any:
    """启用用户中，直挂或经所属启用部门绑定这些角色 code 的用户（去重）。"""
    from identity.models import UserInfo

    normalized = [str(code) for code in (codes or []) if str(code).strip()]
    if not normalized:
        return UserInfo.objects.none()
    return UserInfo.objects.filter(is_active=True).filter(role_members_q(normalized)).distinct()
