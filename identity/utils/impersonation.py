#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""用户模拟（impersonation）：持有权限点的用户以目标用户身份使用后台。

- 模拟态由 JWT 自定义 claim ``imp``（模拟发起人 pk）承载：token 由服务端签发，
  无法伪造；发起人原会话不受影响，退出时由服务端为发起人重签 token，
  浏览器不需要保存发起人原 token 副本。
- 模拟会话登记 UserSession（login_type=IMPERSONATE）：在线用户可见，
  单会话强退 / 用户级强退对模拟态同样生效；登录日志按 IMPERSONATE 类型留痕。
- 模拟期间 request.user 即被模拟用户：API/菜单、数据行、字段列三层权限与
  操作审计天然以目标身份生效，模拟发起人只出现在本模块的审计记录里。
"""

import hashlib
import json
import time

from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from common.cache.storage import BlackAccessTokenCache, SessionTokenRevokedCache
from common.utils import get_logger

logger = get_logger(__name__)

#: 模拟态 claim：值为模拟发起人（真实操作者）的 UserInfo pk（str）
IMPERSONATOR_CLAIM = "imp"

#: 模拟相关的操作日志 module（开始 / 退出共用，便于按模块检索）
IMPERSONATE_LOG_MODULE = "User:impersonate"


def get_impersonator_pk(request):
    """当前请求处于模拟态时返回发起人 pk，否则 None（session/PAT 认证无 payload）。"""
    payload = getattr(getattr(request, "auth", None), "payload", None) or {}
    return payload.get(IMPERSONATOR_CLAIM)


def is_impersonating(request) -> bool:
    """当前请求是否处于模拟态（token 带发起人 claim）。"""
    return bool(get_impersonator_pk(request))


def start_impersonation(request, target, impersonator) -> dict:
    """以 target 身份签发模拟 token：登记模拟会话 + 登录日志留痕。

    返回登录载荷（refresh / access / 生存期 / 目标用户摘要），由调用方下发；
    ``UPDATE_LAST_LOGIN`` 只作用于账密登录链路，模拟不污染 target 的 last_login。
    """
    from identity.utils.auth import get_token_lifetime, save_login_log
    from identity.utils.session import bind_session_claim, register_user_session
    from system.services import UserLoginLog

    session = None
    try:
        session = register_user_session(request, target, UserLoginLog.LoginTypeChoices.IMPERSONATE)
    except Exception:  # noqa: BLE001 会话登记失败不影响签发（与登录链路同口径）
        logger.warning("register impersonation session failed", exc_info=True)

    refresh = RefreshToken.for_user(target)
    refresh[IMPERSONATOR_CLAIM] = str(impersonator.pk)
    if session:
        try:
            refresh_str, access_str = bind_session_claim(refresh, session.pk)
        except Exception:  # noqa: BLE001 claim 注入失败退回无 sid 行为
            logger.warning("bind impersonation session claim failed", exc_info=True)
            refresh_str, access_str = str(refresh), str(refresh.access_token)
    else:
        refresh_str, access_str = str(refresh), str(refresh.access_token)

    data = {"refresh": refresh_str, "access": access_str}
    data.update(get_token_lifetime(target))
    data["user"] = {
        "pk": str(target.pk),
        "username": target.username,
        "nickname": target.nickname,
    }

    # 登录日志归属被模拟用户（这是被模拟账号的"登录"记录）；请求级操作日志
    # 仍归模拟发起人，restore 保证中间件落库时身份不被覆盖
    original_user = request.user
    request.user = target
    try:
        save_login_log(request, login_type=UserLoginLog.LoginTypeChoices.IMPERSONATE)
    finally:
        request.user = original_user

    _record_impersonation_log(request, target, "start", impersonator)
    return data


def stop_impersonation(request):
    """退出模拟：失效当前模拟会话 + 为发起人重签 token。

    :return: (impersonator, data)；发起人不存在或已停用时返回 (None, None)，
             调用方需给出「请重新登录」类可读文案（此时只能重新登录恢复身份）。
    """
    from identity.models import UserInfo, UserSession
    from identity.utils.auth import get_token_lifetime

    impersonator = UserInfo.objects.filter(pk=get_impersonator_pk(request), is_active=True).first()
    if impersonator is None:
        return None, None

    # 失效模拟态凭证（与登出同口径）：access 拉黑 + 会话级失效 + 会话置离线；
    # refresh token 由调用方传回时再拉黑（与 logout 相同的防御深度）
    auth = request.auth
    if auth is not None:
        exp = int(auth.payload.get("exp") or 0)
        timeout = max(exp - int(time.time()), 0)
        BlackAccessTokenCache(request.user.pk, hashlib.md5(auth.token).hexdigest()).set_storage_cache(1, timeout)
        sid = auth.payload.get("sid")
        if sid:
            SessionTokenRevokedCache(sid).set_storage_cache(1)
            UserSession.objects.filter(pk=sid, status=UserSession.Status.ONLINE).update(
                status=UserSession.Status.OFFLINE, last_active=timezone.now()
            )

    refresh = RefreshToken.for_user(impersonator)
    data = {"refresh": str(refresh), "access": str(refresh.access_token)}
    data.update(get_token_lifetime(impersonator))

    _record_impersonation_log(request, request.user, "stop", impersonator)
    return impersonator, data


def blacklist_impersonated_refresh(request) -> None:
    """拉黑请求体携带的模拟态 refresh token（缺失/已失效静默跳过，不阻断退出）。"""
    from rest_framework_simplejwt.tokens import RefreshToken

    refresh_raw = (request.data or {}).get("refresh")
    if not refresh_raw:
        return
    try:
        RefreshToken(refresh_raw).blacklist()
    except Exception:  # noqa: BLE001 refresh 缺失/已失效/已黑名单不阻断退出
        logger.info("blacklist impersonated refresh token skipped", exc_info=True)


def _record_impersonation_log(request, target, action: str, impersonator) -> None:
    """模拟开始 / 退出的显式审计留痕（操作日志，module=User:impersonate）。"""
    from system.services import OperationLog

    try:
        OperationLog.objects.create(
            module=IMPERSONATE_LOG_MODULE,
            object_pk=str(target.pk),
            path=request.path,
            changes=json.dumps(
                {
                    "action": action,
                    "target": target.username,
                    "impersonator": impersonator.username,
                },
                ensure_ascii=False,
            )[:4096],
        )
    except Exception:  # noqa: BLE001 审计失败不影响主流程
        logger.warning("record impersonation operation log failed", exc_info=True)
