#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""登录态 token 签发服务：给人登录用的 JWT 的唯一签发实现。

与 :mod:`identity.services.token_issue`（PAT / 应用凭证 / OAuth 访问凭证）并列：
后者管机器凭证，本模块管登录态凭证。历史上「登记会话 → 签发 → 写 sid claim →
附生命周期 → 附改密标记」这五步在 7 个入口各写一遍且已漂移（账密登录漏
``last_login``、注册漏 ``must_change_password``、降级 try/except 写法各不相同），
本模块把五步收敛为一处，并明确降级口径：

- 会话登记失败、claim 注入失败均告警后退回无 sid 令牌，绝不阻断登录
  （会话管理属附加能力，不是登录的前置条件）；
- ``last_login`` 由本模块显式写入，不再依赖 simplejwt 的 ``UPDATE_LAST_LOGIN``
  隐式补写（该开关保留为 refresh 轮换路径的兜底，见 ``server/settings/libs.py``）；
- 用户模拟（impersonation）的 ``imp`` claim 经 ``extra_claims`` 传入；
  退出模拟是「重签非登录」语义，不经过本函数（复用发起人既有会话语义）。

任何新增登录方式（SAML / 扫码 / 企微…）都必须调用本函数，不得自行拼接
``RefreshToken.for_user`` / ``bind_session_claim`` / ``get_token_lifetime``。
"""

from typing import Any

from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from audit.services import UserLoginLog
from common.utils import get_logger
from identity.utils.auth import get_token_lifetime
from identity.utils.session import bind_session_claim, register_user_session

logger = get_logger(__name__)


def register_login_session(request: Any, user: Any, login_type: Any) -> Any:
    """登记登录会话（失败仅告警，不阻断登录）。返回 UserSession 或 None。"""
    try:
        return register_user_session(request, user, login_type)
    except Exception:  # noqa: BLE001 会话管理属附加能力
        logger.warning("register user session failed", exc_info=True)
        return None


def issue_session_token(
    request: Any,
    user: Any,
    login_type: Any = UserLoginLog.LoginTypeChoices.USERNAME,
    *,
    extra_claims: dict[str, Any] | None = None,
    touch_last_login: bool = True,
) -> dict[str, Any]:
    """登录态 token 签发的唯一实现：会话登记 → 签发 → sid claim → 生命周期 → 改密标记。

    任何签发「给人登录用」的 JWT 的路径都必须调本函数，不得自行拼接
    ``RefreshToken.for_user`` / ``bind_session_claim`` / ``get_token_lifetime``。

    降级口径统一：会话登记失败、claim 注入失败均退回无 sid 令牌并告警，
    绝不阻断登录（会话管理属附加能力）。

    :param extra_claims: 额外 claim（如用户模拟的 impersonator claim）
    :param touch_last_login: 是否刷新 last_login（模拟等已有明确语义时传 False）
    :return: dict，键为 refresh/access/access_token_lifetime/refresh_token_lifetime/
             must_change_password
    """
    session = register_login_session(request, user, login_type)
    refresh = RefreshToken.for_user(user)
    for key, value in (extra_claims or {}).items():
        refresh[key] = value
    result: dict[str, Any] = {"refresh": str(refresh), "access": str(refresh.access_token)}
    if session:
        try:
            refresh_str, access_str = bind_session_claim(refresh, session.pk)
            result = {"refresh": refresh_str, "access": access_str}
        except Exception:  # noqa: BLE001 claim 注入失败退回无 sid 行为
            logger.warning("bind session claim failed", exc_info=True)
    result.update(get_token_lifetime(user))
    # 强制改密标记：前端登录后引导改密（改密成功自动清除）
    result["must_change_password"] = bool(getattr(user, "must_change_password", False))
    if touch_last_login:
        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])
    return result
