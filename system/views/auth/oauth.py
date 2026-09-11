#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""第三方登录（OAuth2 / OIDC 通用 provider）：authorize / callback / 绑定管理。

**URL 白名单说明**：整段 `^/api/system/auth/oauth/` 已在 `PERMISSION_WHITE_URL` 中，
因为登录前置的 authorize/callback 必须匿名可达；绑定管理是个人凭证（同 MFA/PAT 口径），
也不该依赖菜单权限。因此这里显式要求 DRF 的 `IsAuthenticated`，
不能用项目的自定义 `IsAuthenticated`（后者按菜单权限校验，白名单已被绕过）。
"""

from django.contrib.auth import authenticate
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated

from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger
from system.models.log import UserLoginLog
from system.models.oauth import UserOAuthBinding
from system.serializers.oauth import OAuthBindingSerializer
from system.utils.auth import get_token_lifetime
from system.utils.session import bind_session_claim
from system.utils.oauth import (
    OAuthError,
    build_authorize_url,
    consume_state,
    exchange_code,
    fetch_userinfo,
    get_provider,
    get_providers,
    issue_state,
    make_unique_username,
    mask_providers,
    resolve_subject,
)
from system.views.auth.login import _register_session_safe, complete_login

logger = get_logger(__name__)

# 业务码：前端按码给出可读文案（不回显 IdP 原始报文）
OAUTH_ERROR_CODE = 1006


def _redirect_uri(request, provider: str) -> str:
    """回调地址：固定落地页 + provider 标识（前端据此回传回调），
    避免被伪造的 redirect_uri 带走 code（换取 token 时用同一份地址校验）。
    """
    return f"{request.scheme}://{request.get_host()}/#/oauth/callback?provider={provider}"


class OAuthProvidersAPIView(GenericAPIView):
    """登录页可见的第三方登录入口（未启用 / 未配置的不返回）。"""

    # 登录前置：必须匿名可达（项目默认权限类对匿名用户直接 401）
    permission_classes = [AllowAny]

    @extend_schema(responses=get_default_response_schema({"data": {"providers": [{"key": "str", "name": "str"}]}}))
    def get(self, request, *args, **kwargs):
        providers = get_providers(enabled_only=True)
        return ApiResponse(
            data={"providers": [{"key": item["key"], "name": item["name"]} for item in mask_providers(providers)]}
        )


class OAuthAuthorizeAPIView(GenericAPIView):
    """返回 IdP 授权跳转地址（含一次性 state）。"""

    # 登录前置：必须匿名可达（项目默认权限类对匿名用户直接 401）
    permission_classes = [AllowAny]

    @extend_schema(responses=get_default_response_schema({"data": {"url": "str", "state": "str"}}))
    def get(self, request, provider, *args, **kwargs):
        config = get_provider(provider, enabled_only=True)
        if not config:
            return ApiResponse(code=OAUTH_ERROR_CODE, detail=_("Third-party login is not enabled"))
        state = issue_state(provider)
        return ApiResponse(
            data={
                "url": build_authorize_url(config, _redirect_uri(request, provider), state),
                "state": state,
            }
        )


class OAuthCallbackAPIView(GenericAPIView):
    """IdP 回调：校验 state → 换 token → 取 userinfo → 绑定判定 → `complete_login`。

    登录成功后**必须**走 `complete_login`（登录后置链路唯一入口），
    否则等于绕过登录 MFA / 会话登记 / 登录日志 / 锁定计数清理。
    """

    # 登录前置：必须匿名可达（项目默认权限类对匿名用户直接 401）
    permission_classes = [AllowAny]

    @extend_schema(responses=get_default_response_schema())
    def get(self, request, provider, *args, **kwargs):
        code = request.query_params.get("code")
        state = request.query_params.get("state")
        bound_provider = consume_state(state or "")
        if not code or bound_provider != provider:
            return ApiResponse(code=OAUTH_ERROR_CODE, detail=_("The login link has expired, please try again"))

        config = get_provider(provider, enabled_only=True)
        if not config:
            return ApiResponse(code=OAUTH_ERROR_CODE, detail=_("Third-party login is not enabled"))

        try:
            token_payload = exchange_code(config, code, _redirect_uri(request, provider))
            userinfo = fetch_userinfo(config, token_payload.get("access_token"))
        except OAuthError as exc:
            return ApiResponse(code=OAUTH_ERROR_CODE, detail=exc.detail)

        subject = resolve_subject(config, userinfo)
        binding = UserOAuthBinding.objects.filter(provider=provider, subject=subject).first()

        if not binding:
            if not config.get("auto_create"):
                return ApiResponse(
                    code=OAUTH_ERROR_CODE,
                    detail=_("This account is not bound, please login and bind it first"),
                )
            binding = _create_user_and_binding(provider, config, subject, userinfo)

        user = binding.user
        if not user.is_active:
            return ApiResponse(code=OAUTH_ERROR_CODE, detail=_("The account has been disabled"))

        mfa_response = complete_login(request, user, login_type=UserLoginLog.LoginTypeChoices.OAUTH)
        if mfa_response:
            return mfa_response

        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])
        result = _issue_token(request, user)
        result.update(get_token_lifetime(user))
        return ApiResponse(data=result)


class OAuthBindingsAPIView(GenericAPIView):
    """本人第三方绑定列表（归属个人，不提供他人查询）。"""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses=get_default_response_schema())
    def get(self, request, *args, **kwargs):
        rows = UserOAuthBinding.objects.filter(user=request.user).select_related("user")
        names = {item["key"]: item["name"] for item in get_providers()}
        data = [
            {
                **OAuthBindingSerializer(row).data,
                "provider_name": names.get(row.provider, row.provider),
            }
            for row in rows
        ]
        return ApiResponse(data=data)


class OAuthUnbindAPIView(GenericAPIView):
    """解绑第三方账号：口令二次确认 + 禁止解掉最后一种登录方式（防自锁）。"""

    permission_classes = [IsAuthenticated]

    class UnbindSerializer(serializers.Serializer):
        password = serializers.CharField(required=True, write_only=True, label=_("Password"))

    @extend_schema(request=UnbindSerializer, responses=get_default_response_schema())
    def delete(self, request, pk, *args, **kwargs):
        binding = UserOAuthBinding.objects.filter(pk=pk, user=request.user).first()
        if not binding:
            return ApiResponse(code=1001, detail=_("The binding does not exist"))
        serializer = self.UnbindSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not authenticate(username=request.user.username, password=serializer.validated_data["password"]):
            return ApiResponse(code=1001, detail=_("Incorrect password"))
        if not UserOAuthBinding.user_has_other_login_method(request.user, exclude_pk=binding.pk):
            return ApiResponse(
                code=OAUTH_ERROR_CODE,
                detail=_("This is the only login method, bind another one before unbinding"),
            )
        binding.delete()
        return ApiResponse(detail=str(_("Unbound successfully")))


def _create_user_and_binding(provider, config, subject, userinfo):
    """auto_create：按 `provider_subject` 规则建号并绑定（密码置为不可用，防本地口令爆破）。"""
    from system.models import UserInfo

    username = make_unique_username(provider, subject)
    user = UserInfo(username=username, nickname=str(userinfo.get("nickname") or username))
    user.email = str(userinfo.get("email") or "")
    user.set_unusable_password()
    user.save()
    return UserOAuthBinding.objects.create(
        user=user,
        provider=provider,
        subject=subject,
        profile={
            "nickname": userinfo.get("nickname") or "",
            "email": userinfo.get("email") or "",
            "picture": userinfo.get("picture") or "",
        },
    )


def _issue_token(request, user):
    """签发会话 token（与本地登录同口径：登记 UserSession 并写入 sid claim）。"""
    from rest_framework_simplejwt.tokens import RefreshToken

    refresh = RefreshToken.for_user(user)
    try:
        session = _register_session_safe(request, user, UserLoginLog.LoginTypeChoices.OAUTH)
        if session:
            refresh_str, access_str = bind_session_claim(refresh, session.pk)
            return {"refresh": refresh_str, "access": access_str}
    except Exception:  # noqa: BLE001 会话登记失败不应阻断登录，退回无 sid 行为
        logger.warning("bind oauth session failed", exc_info=True)
    return {"refresh": str(refresh), "access": str(refresh.access_token)}
