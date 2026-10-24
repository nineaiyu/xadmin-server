#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""身份域对外契约门面（``identity.contracts``）。

ai 等业务域此前直读 ``identity.models``（用户/角色的自由文本解析、按主键取展示值、
审计行用户名映射），经此收敛成显式契约面：调用方只见稳定入口，身份域内部演进
（如更换自定义用户模型）不再需要逐 app 排查引用。

契约分组：

- 用户解析：``resolve_user``（用户名/昵称/主键宽容三段式）、``get_user``（主键）；
- 角色解析：``resolve_role``（uuid/名称）、``get_role``（主键）；
- 批量映射：``username_map``（主键 → 用户名，审计/观测面板行定位用）。
"""

from typing import Any

__all__ = ["get_role", "get_user", "resolve_role", "resolve_user", "username_map"]


def resolve_user(text: Any) -> Any | None:
    """自由文本 → 用户对象（用户名 → 昵称 → 主键三段式），解析不到返回 None。

    消费方（如 AI 动作参数）自行决定错误文案；非法主键形态静默跳过。
    """
    from django.core.exceptions import ValidationError as DjangoValidationError

    from identity.models import UserInfo

    value = str(text or "").strip()
    if not value:
        return None
    user = UserInfo.objects.filter(username=value).first() or UserInfo.objects.filter(nickname=value).first()
    if user is None:
        try:
            user = UserInfo.objects.filter(pk=value).first()
        except (DjangoValidationError, ValueError, TypeError):
            user = None
    return user


def get_user(pk: Any) -> Any | None:
    """主键 → 用户对象；非法形态 / 不存在返回 None（展示场景不抛错）。"""
    from django.core.exceptions import ValidationError as DjangoValidationError

    from identity.models import UserInfo

    try:
        return UserInfo.objects.filter(pk=pk).first()
    except (DjangoValidationError, ValueError, TypeError):
        return None


def resolve_role(text: Any) -> Any | None:
    """自由文本 → 角色对象（uuid 形态优先，回落角色名），解析不到返回 None。"""
    import re

    from identity.models import UserRole

    value = str(text or "").strip()
    if not value:
        return None
    role = None
    if re.fullmatch(r"[0-9a-fA-F-]{32,36}", value):
        role = UserRole.objects.filter(pk=value).first()
    if role is None:
        role = UserRole.objects.filter(name=value).first()
    return role


def get_role(pk: Any) -> Any | None:
    """主键 → 角色对象；非法形态 / 不存在返回 None。"""
    from django.core.exceptions import ValidationError as DjangoValidationError

    from identity.models import UserRole

    try:
        return UserRole.objects.filter(pk=pk).first()
    except (DjangoValidationError, ValueError, TypeError):
        return None


def username_map(pks: Any) -> dict[str, str]:
    """用户主键列表 → {pk: username} 映射（观测面板把审计行 object_pk 定位到人）。"""
    from identity.models import UserInfo

    rows = UserInfo.objects.filter(pk__in=[pk for pk in pks if pk]).values_list("pk", "username")
    return {str(pk): str(username) for pk, username in rows}
