# -*- coding: utf-8 -*-
"""SCIM 治理面护栏：内置角色与超管账号不接受外部目录同步改写。

单一全局 ``SCIM_TOKEN`` 泄露或 IdP 侧误操作都不得摧毁治理配置（内置角色 code
被代码与治理配置按值引用）与超管账号（停用即锁死管理面）；写路径统一 fail-closed，
与后台管理面的内置角色删除保护、code 不可改口径一致。
"""

from django.utils.translation import gettext_lazy as _

from identity.scim.errors import ScimApiError


def ensure_user_writable(user) -> None:
    """超管账号护栏：SCIM 不得改写 / 停用超管。"""
    if getattr(user, "is_superuser", False):
        raise ScimApiError(403, str(_("Superuser accounts cannot be modified via SCIM")), scim_type="mutability")


def ensure_group_writable(role) -> None:
    """内置角色护栏：连改名与成员改写也拒绝（成员与授权由管理面维护）。"""
    from identity.builtin import BUILTIN_ROLE_CODES

    if getattr(role, "builtin", False) or role.code in BUILTIN_ROLE_CODES:
        raise ScimApiError(403, str(_("Builtin roles cannot be modified via SCIM")), scim_type="mutability")
