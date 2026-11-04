#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : logout
# author : ly_13
# date : 8/8/2024
import hashlib
import time
from typing import Any

from django.contrib.auth import logout
from django.utils import timezone
from drf_spectacular.plumbing import build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema
from rest_framework.generics import GenericAPIView
from rest_framework_simplejwt.tokens import RefreshToken

from common.cache.storage import BlackAccessTokenCache, SessionTokenRevokedCache
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from identity.services.auth_cookie import clear_auth_cookies, refresh_token_from_cookie
from mfa.cache import UserConfirmStateCache


class LogoutAPIView(GenericAPIView):
    """用户登出"""

    @extend_schema(
        request=OpenApiRequest(build_object_type(properties={"refresh": build_basic_type(OpenApiTypes.STR)})),
        responses=get_default_response_schema(),
    )
    def post(self, request: Any) -> Any:
        """用户登出"""
        # 登出同时清除敏感操作二次确认状态，避免下个会话在有效期内绕过二次验证。
        # 只需清一次：放在 auth 判断之前（session 等非 JWT 认证方式下 request.auth 为空）；
        # 勿在文末 logout() 之后再清一次——那时 request.user 已可能是 AnonymousUser。
        if getattr(request, "user", None) and request.user.is_authenticated:
            UserConfirmStateCache(request.user).clear()
        auth = request.auth
        # PAT 的 request.auth 是凭证模型（无 payload）；docs session 的 auth 为 None。
        # 两种情况都必须继续执行 logout(request)，只跳过 JWT 黑名单分支。
        payload = getattr(auth, "payload", None) or {}
        exp = payload.get("exp") if isinstance(payload, dict) else None
        if payload and exp is not None:
            user_id = payload.get("user_id")
            timeout = int(exp - time.time())
            # 黑名单键派生用 MD5 仅作定长缓存键（非安全用途，usedforsecurity=False）
            BlackAccessTokenCache(
                user_id, hashlib.md5(auth.token, usedforsecurity=False).hexdigest()
            ).set_storage_cache(1, timeout)
            # 会话级撤销：仅置 OFFLINE 不阻止 refresh 续期，必须写 sid 失效标记
            sid = payload.get("sid")
            if sid:
                try:
                    SessionTokenRevokedCache(sid).set_storage_cache(1)
                    from identity.models import UserSession

                    UserSession.objects.filter(pk=sid, status=UserSession.Status.ONLINE).update(
                        status=UserSession.Status.OFFLINE, last_active=timezone.now()
                    )
                except Exception:  # noqa: BLE001 会话管理属附加能力
                    pass
        # refresh 拉黑双读：body 优先（Header 模式），缺失回落 X-Refresh-Token Cookie
        # （HttpOnly Cookie 模式下 refresh 只在 Cookie 里，登出必须同样拉黑，
        # 否则 Cookie 会话登出后仍可续期）。已失效/已黑名单不阻断本地登出。
        refresh_raw: Any = request.data.get("refresh") or refresh_token_from_cookie(request)
        if refresh_raw:
            try:
                RefreshToken(refresh_raw).blacklist()
            except Exception:  # noqa: BLE001 refresh 缺失/已失效/已黑名单不阻断登出（本地登出仍完成）
                pass
        logout(request)
        response = ApiResponse()
        # 无条件清理服务端下发的认证 Cookie（含模式标记）：开关切换窗口内新旧两种
        # 会话形态都在登出时清干净，不留「登出后 Cookie 仍可用」的窗口
        clear_auth_cookies(response)
        return response
