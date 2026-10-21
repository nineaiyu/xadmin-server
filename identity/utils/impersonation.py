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
from typing import Any

from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from common.cache.storage import BlackAccessTokenCache, SessionTokenRevokedCache
from common.utils import get_logger

logger = get_logger(__name__)

#: 模拟态 claim：值为模拟发起人（真实操作者）的 UserInfo pk（str）
IMPERSONATOR_CLAIM = "imp"

#: 模拟相关的操作日志 module（开始 / 退出共用，便于按模块检索）
IMPERSONATE_LOG_MODULE = "User:impersonate"


def get_impersonator_pk(request: Any) -> Any:
    """当前请求处于模拟态时返回发起人 pk，否则 None（session/PAT 认证无 payload）。"""
    payload = getattr(getattr(request, "auth", None), "payload", None) or {}
    return payload.get(IMPERSONATOR_CLAIM)


def is_impersonating(request: Any) -> bool:
    """当前请求是否处于模拟态（token 带发起人 claim）。"""
    return bool(get_impersonator_pk(request))


def start_impersonation(request: Any, target: Any, impersonator: Any) -> dict[str, Any]:
    """以 target 身份签发模拟 token：登记模拟会话 + 登录日志留痕。

    返回登录载荷（refresh / access / 生存期 / 目标用户摘要），由调用方下发；
    ``touch_last_login=False``：模拟不是 target 的真实登录，不刷新其 last_login
    （``UPDATE_LAST_LOGIN`` 也只作用于账密登录链路）。
    """
    from audit.services import UserLoginLog
    from identity.services.token_issue_login import issue_session_token
    from identity.utils.auth import save_login_log

    data = issue_session_token(
        request,
        target,
        UserLoginLog.LoginTypeChoices.IMPERSONATE,
        extra_claims={IMPERSONATOR_CLAIM: str(impersonator.pk)},
        touch_last_login=False,
    )
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


def stop_impersonation(request: Any) -> Any:
    """退出模拟：失效当前模拟会话 + 为发起人重签 token。

    重签不经过 :func:`identity.services.token_issue_login.issue_session_token`：
    这是「恢复发起人原有登录态」而非一次新登录，有意不登记新会话（复用发起人
    既有会话语义）、不写 last_login、不带 must_change_password（响应键与登录
    载荷一致的部分仅为前端兼容）。

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
        # 黑名单键派生用 MD5 仅作定长缓存键（非安全用途，usedforsecurity=False）
        BlackAccessTokenCache(
            request.user.pk, hashlib.md5(auth.token, usedforsecurity=False).hexdigest()
        ).set_storage_cache(1, timeout)
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


def blacklist_impersonated_refresh(request: Any) -> None:
    """拉黑请求体携带的模拟态 refresh token（缺失/已失效静默跳过，不阻断退出）。"""
    from rest_framework_simplejwt.tokens import RefreshToken

    refresh_raw = (request.data or {}).get("refresh")
    if not refresh_raw:
        return
    try:
        RefreshToken(refresh_raw).blacklist()
    except Exception:  # noqa: BLE001 refresh 缺失/已失效/已黑名单不阻断退出
        logger.info("blacklist impersonated refresh token skipped", exc_info=True)


def _record_impersonation_log(request: Any, target: Any, action: str, impersonator: Any) -> None:
    """模拟开始 / 退出的显式审计留痕（操作日志，module=User:impersonate）。"""
    from audit.services import OperationLog

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
