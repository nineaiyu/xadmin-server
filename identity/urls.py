#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""identity 域路由：经 system/urls.py 同前缀挂载（ADR-057 D1.2 口径）。

本模块**不设 app_name**——注册项并入 system 命名空间，``reverse("system:user")``
等既有视图名、权限点与 menu.json 全部零变化。
"""

from django.urls import path, re_path
from rest_framework.routers import SimpleRouter

from common.core.routers import NoDetailRouter
from identity.views.admin.account_risk import AccountRiskViewSet
from identity.views.admin.dept import DeptViewSet
from identity.views.admin.login_policy import LoginAccessPolicyViewSet
from identity.views.admin.online import UserOnlineViewSet
from identity.views.admin.passkey import PasskeyViewSet
from identity.views.admin.post import PostViewSet
from identity.views.admin.role import RoleViewSet
from identity.views.admin.user import UserViewSet
from identity.views.auth.impersonation import ImpersonateExitAPIView
from identity.views.auth.invite import InviteAcceptAPIView, InviteValidateAPIView
from identity.views.auth.login import BasicLoginAPIView, VerifyCodeLoginAPIView
from identity.views.auth.logout import LogoutAPIView
from identity.views.auth.mfa import (
    LoginMFAPasskeyChallengeAPIView,
    LoginMFASendCodeAPIView,
    LoginMFAVerifyAPIView,
)
from identity.views.auth.oauth import (
    OAuthAuthorizeAPIView,
    OAuthBindAuthorizeAPIView,
    OAuthBindingsAPIView,
    OAuthCallbackAPIView,
    OAuthProvidersAPIView,
    OAuthUnbindAPIView,
)
from identity.views.auth.register import RegisterViewAPIView
from identity.views.auth.reset import ResetPasswordAPIView
from identity.views.auth.rule import PasswordRulesAPIView
from identity.views.auth.token import CaptchaAPIView, RefreshTokenAPIView, TempTokenAPIView
from identity.views.auth.verify_code import SendVerifyCodeAPIView
from identity.views.open.open import ApiApplicationTokenAPIView, ApiApplicationViewSet
from identity.views.open.open_oauth import (
    OpenOAuthApproveAPIView,
    OpenOAuthAuthorizeAPIView,
    OpenOAuthRevokeAPIView,
    OpenOAuthTokenAPIView,
)
from identity.views.search.dept import SearchDeptViewSet
from identity.views.search.post import SearchPostViewSet
from identity.views.search.role import SearchRoleViewSet
from identity.views.search.user import SearchUserViewSet
from identity.views.user.directory import DirectoryViewSet
from identity.views.user.token import PersonalAccessTokenViewSet
from identity.views.user.userinfo import UserInfoViewSet

router = SimpleRouter(False)
no_detail_router = NoDetailRouter(False)

no_auth_url = [
    re_path("^login/basic$", BasicLoginAPIView.as_view(), name="login-by-basic"),
    re_path("^login/code$", VerifyCodeLoginAPIView.as_view(), name="login-by-code"),
    re_path("^login/mfa/send-code$", LoginMFASendCodeAPIView.as_view(), name="login-mfa-send-code"),
    re_path("^login/mfa/verify$", LoginMFAVerifyAPIView.as_view(), name="login-mfa-verify"),
    re_path(
        "^login/mfa/passkey/challenge$",
        LoginMFAPasskeyChallengeAPIView.as_view(),
        name="login-mfa-passkey-challenge",
    ),
    re_path("^register$", RegisterViewAPIView.as_view(), name="register"),
    re_path("^auth/captcha$", CaptchaAPIView.as_view(), name="captcha"),
    re_path("^auth/token$", TempTokenAPIView.as_view(), name="temp_token"),
    re_path("^auth/verify$", SendVerifyCodeAPIView.as_view(), name="send-verify-code"),
    re_path("^auth/reset$", ResetPasswordAPIView.as_view(), name="reset-password"),
    # 邀请激活：令牌即凭据，激活页未登录，必须匿名可达
    re_path("^auth/invite/validate$", InviteValidateAPIView.as_view(), name="invite-validate"),
    re_path("^auth/invite/accept$", InviteAcceptAPIView.as_view(), name="invite-accept"),
    # 第三方登录：authorize/callback 必须匿名可达，故挂在 no_auth_url
    re_path("^auth/oauth/providers$", OAuthProvidersAPIView.as_view(), name="oauth-providers"),
    re_path(
        "^auth/oauth/(?P<provider>[^/]+)/authorize$",
        OAuthAuthorizeAPIView.as_view(),
        name="oauth-authorize",
    ),
    re_path(
        "^auth/oauth/(?P<provider>[^/]+)/callback$",
        OAuthCallbackAPIView.as_view(),
        name="oauth-callback",
    ),
    # 绑定意图的授权地址（同样是白名单路径，视图内要求 DRF IsAuthenticated）
    re_path(
        "^auth/oauth/(?P<provider>[^/]+)/bind-authorize$",
        OAuthBindAuthorizeAPIView.as_view(),
        name="oauth-bind-authorize",
    ),
    re_path("^auth/oauth/bindings$", OAuthBindingsAPIView.as_view(), name="oauth-bindings"),
    re_path(
        "^auth/oauth/bindings/(?P<pk>[^/]+)$",
        OAuthUnbindAPIView.as_view(),
        name="oauth-unbind",
    ),
]

auth_url = [
    re_path("^logout$", LogoutAPIView.as_view(), name="logout"),
    re_path("^impersonate/exit$", ImpersonateExitAPIView.as_view(), name="impersonate-exit"),
    re_path("^refresh$", RefreshTokenAPIView.as_view(), name="refresh"),
    re_path("^rules/password$", PasswordRulesAPIView.as_view(), name="password-rules"),
]

router_url = []

# 个人用户信息
no_detail_router.register("userinfo", UserInfoViewSet, basename="userinfo")
router.register("personal-access-tokens", PersonalAccessTokenViewSet, basename="personal_access_token")

# 系统组织与认证相关路由
router.register("user", UserViewSet, basename="user")
router.register("dept", DeptViewSet, basename="dept")
router.register("posts", PostViewSet, basename="post")
router.register("role", RoleViewSet, basename="role")
router.register("online", UserOnlineViewSet, basename="online_socket")
# 安全域：账号风险巡检 / 登录访问策略 / Passkey 凭据
router.register("account-risks", AccountRiskViewSet, basename="account_risk")
router.register("login-policies", LoginAccessPolicyViewSet, basename="login_policy")
router.register("passkeys", PasskeyViewSet, basename="passkey")

# 通讯录（人员名录，只读）
router.register("directory", DirectoryViewSet, basename="SystemDirectory")

# 仅数据搜索
router.register("search/user", SearchUserViewSet, basename="SearchUser")
router.register("search/role", SearchRoleViewSet, basename="SearchRole")
router.register("search/dept", SearchDeptViewSet, basename="SearchDept")
router.register("search/post", SearchPostViewSet, basename="SearchPost")

# 开放平台应用：client-credentials 应用管理与回调测试
router.register("api-applications", ApiApplicationViewSet, basename="api_application")

urlpatterns = no_auth_url + auth_url + router_url + router.urls + no_detail_router.urls
# 开放平台换发端点：匿名可达（白名单），凭 client_secret 换 PAT 凭证
urlpatterns += [path("open/token", ApiApplicationTokenAPIView.as_view())]
# 开放平台 OAuth 授权码：authorize/approve 需登录态，token/revoke 匿名可达
urlpatterns += [
    path("open/oauth/authorize", OpenOAuthAuthorizeAPIView.as_view()),
    path("open/oauth/approve", OpenOAuthApproveAPIView.as_view()),
    path("open/oauth/token", OpenOAuthTokenAPIView.as_view()),
    path("open/oauth/revoke", OpenOAuthRevokeAPIView.as_view()),
]
