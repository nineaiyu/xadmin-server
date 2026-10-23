#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""AI 动作规格基座：协议、权限双门与模块裁剪判定。

本模块是「机制」与「声明」的分界线：**只依赖 common 层与标准库**，不 import
任何注册表或聚合模块。各应用在自己的 ``ai_declarations.py`` 里 import 本模块
构造声明——发现器会 import 各应用声明，若声明反过来 import 注册表就会形成
循环导入，故 ActionSpec 必须住在这里。

协议（三条红线，与执行链路一致）：
- 白名单：动作必须显式声明，未登记一律拒绝；
- 权限双门：AI 端点权限 × 底层业务权限点（``user_can_visit``），缺一不可；
- 模块裁剪：声明标注所属功能模块，停用模块的动作不进目录、不可执行。
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from common.utils import get_logger

logger = get_logger(__name__)

#: 动态表单目录最多列出的表单数（防 prompt 过大）
MAX_CATALOG_FORMS = 20
#: 事由/文本参数上限（与 Leave.reason 字段一致）
MAX_REASON_LENGTH = 500


def user_can_visit(user: Any, method: str, path: str) -> bool:
    """按菜单权限点口径判定用户能否访问「方法 + 路径」（与 IsAuthenticated 同一匹配函数）。

    业务动作执行前的第二道门：仅有 AI 执行端点权限、而没有底层业务权限的用户不得执行。
    白名单端点（登录即可访问、无菜单权限点，如 /api/system/dashboard/*）与运行时
    访问控制同口径视为有权限——否则这类动作对普通用户永久不可见/不可执行。
    """
    if not getattr(user, "is_authenticated", False) or not getattr(user, "pk", None):
        return False
    if getattr(user, "is_superuser", False):
        return True
    from common.core.permission import get_menu_pk, get_user_permission, match_permission_white_url

    if match_permission_white_url(method, path):
        return True
    try:
        permission_data = get_user_permission(user, method.upper())
    except Exception:  # noqa: BLE001 权限查询失败按无权限处理（fail-closed）
        logger.warning("check ai action permission failed. user:%s", getattr(user, "pk", ""), exc_info=True)
        return False
    return bool(permission_data and get_menu_pk(permission_data, path))


def module_allows(spec: Any) -> bool:
    """动作声明的功能模块是否全部启用（未声明 = 不受模块裁剪）。

    内部 dispatch 绕过 ASGI 的模块网关，若不在目录/执行两处统一判定，停用模块
    的动作会对超管仍可见（``user_can_visit`` 对超管直接放行）。
    """
    modules = tuple(getattr(spec, "modules", ()) or ())
    if not modules:
        return True
    from common.core.modules import is_module_enabled

    return all(is_module_enabled(module_id) for module_id in modules)


def never_requires_approval(user: Any, params: Any) -> bool:
    """默认审批谓词：动作不触发 412 审批协议。"""
    return False


def always_available(user: Any) -> bool:
    """默认可谓词：动作对任何已鉴权用户都参与目录（实际可见性仍由权限双门决定）。"""
    return True


@dataclass(frozen=True)
class ActionSpec:
    """受限动作规格（白名单条目）。"""

    key: str
    label: Any
    description: Any
    params: dict[str, Any]
    #: ((method, path), ...)：执行所需底层业务权限点（路径与菜单权限点 path 同口径）
    required_visits: tuple[Any, ...]
    validate: Callable[..., Any]
    execute: Callable[..., Any]
    requires_approval: Callable[..., bool]
    available: Callable[..., bool]
    #: 目录附加信息（如动态表单可选表单清单），签名 ``(user) -> dict``
    catalog: Callable[..., dict[str, Any]] | None = None
    #: 所属功能模块（停用即从目录移除并拒绝执行；空 = 不受模块裁剪）
    modules: tuple[str, ...] = ()

    def has_permission(self, user: Any) -> bool:
        """业务权限 + 可用性 + 模块启用三合一（执行前与草稿生成前共用）。"""
        return (
            module_allows(self)
            and all(user_can_visit(user, method, path) for method, path in self.required_visits)
            and bool(self.available(user))
        )
