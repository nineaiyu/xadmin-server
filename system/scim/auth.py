# -*- coding: utf-8 -*-
"""SCIM 独立鉴权：Bearer Token（与 JWT/PAT 分离）+ 凭证级限流。

- 令牌来自 `SysConfig.SCIM_TOKEN`（默认空 = 未配置，任何请求 401）；
- `SCIM_ENABLED` 关闭时 403（整体休眠，默认关闭，需显式开启）；
- 比较用 `secrets.compare_digest`（防时序侧信道）；
- 限流按单一服务凭证计数（`SCIM_RATE_LIMIT`，空/0 = 不限），避免 IdP 批量同步被
  通用用户限流误伤，同时保证凭证泄漏时爆炸半径可控。
"""

import secrets

from django.utils.translation import gettext_lazy as _
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework.throttling import SimpleRateThrottle


class ScimPrincipal:
    """SCIM 服务凭证主体（request.auth）：仅标记身份来源，不代表任何业务用户。"""

    def __str__(self):
        return "scim-token"


class ScimTokenAuthentication(BaseAuthentication):
    """`Authorization: Bearer <SCIM_TOKEN>`；失败抛 AuthenticationFailed（401）。"""

    keyword = "Bearer"

    def authenticate(self, request):
        from common.core.config import SysConfig

        if not bool(SysConfig.SCIM_ENABLED):
            raise PermissionDenied(_("SCIM directory sync is disabled"))

        header = get_authorization_header(request).split()
        if not header or header[0].lower() != self.keyword.lower().encode():
            # SCIM 无匿名入口：缺凭证即 401（不是 403），并附带 WWW-Authenticate 头
            raise AuthenticationFailed(_("SCIM credentials were not provided"))
        if len(header) != 2:
            raise AuthenticationFailed(_("Invalid SCIM authorization header"))

        try:
            token = header[1].decode()
        except UnicodeError:
            raise AuthenticationFailed(_("Invalid SCIM token")) from None

        expected = str(SysConfig.SCIM_TOKEN or "")
        if not expected or not secrets.compare_digest(token, expected):
            raise AuthenticationFailed(_("Invalid SCIM token"))

        from django.contrib.auth.models import AnonymousUser

        # request.auth 标记 SCIM 主体（权限类据此放行）；request.user 保持匿名，
        # 避免任何业务代码误把它当作真实用户
        return (AnonymousUser(), ScimPrincipal())

    def authenticate_header(self, request):
        return self.keyword


class ScimTokenPermission:
    """仅放行携带 SCIM 凭证的请求（业务用户身份不能访问 SCIM 端点）。"""

    def has_permission(self, request, view):
        return isinstance(getattr(request, "auth", None), ScimPrincipal)


class ScimThrottle(SimpleRateThrottle):
    """SCIM 凭证级限流（`SCIM_RATE_LIMIT`，默认 600/min；空/0 = 不限）。"""

    scope = "scim"

    def get_rate(self):
        from common.core.config import SysConfig

        limit = str(SysConfig.SCIM_RATE_LIMIT or "").strip()
        return None if not limit or limit == "0" else limit

    def allow_request(self, request, view):
        if self.rate is None:
            return True
        return super().allow_request(request, view)

    def get_cache_key(self, request, view):
        if not isinstance(getattr(request, "auth", None), ScimPrincipal):
            return None
        # 单一服务凭证：ident 固定，按凭证空间计数即可
        return self.cache_format % {"scope": self.scope, "ident": "token"}
