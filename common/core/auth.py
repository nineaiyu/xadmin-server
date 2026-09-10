#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : system
# author : ly_13
# date : 6/2/2023
import functools
import hashlib
import ipaddress
import re

from django.http.cookie import parse_cookie
from django.utils.translation import gettext_lazy as _
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, NotAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

from common.cache.storage import BlackAccessTokenCache, SessionTokenRevokedCache, UserTokenRevokedCache
from common.utils import get_logger

logger = get_logger(__name__)


def auth_required(view_func):
    @functools.wraps(view_func)
    def wrapper(view, request, *args, **kwargs):
        if request.user and request.user.is_authenticated:
            return view_func(view, request, *args, **kwargs)
        raise NotAuthenticated(_("Unauthorized authentication"))

    return wrapper


# scope 条目可选的方法前缀：`GET /api/system/user`（方法名 + 空白 + 路径）
SCOPE_METHOD_RE = re.compile(r"^(?P<method>[A-Za-z]{3,7})\s+(?P<path>\S.*)$")
SCOPE_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}


def split_scope_entry(pattern) -> tuple:
    """拆分 scope 条目为 ``(method, path)``；无方法前缀时 method 为 None。

    仅当首段是合法 HTTP 方法名时才按「方法 + 路径」解析，避免把含空格的
    路径/正则条目误判（历史条目一律按纯路径口径）。
    """
    text = str(pattern).strip()
    match = SCOPE_METHOD_RE.match(text)
    if match and match.group("method").upper() in SCOPE_HTTP_METHODS:
        return match.group("method").upper(), match.group("path").strip()
    return None, text


def path_allowed_by_scopes(path: str, scopes, method: str = None) -> bool:
    """PAT scope 判定：空清单 = 不限（ADR-008 既有 token 向后兼容）。

    条目语义（大小写不敏感，与 SENSITIVE_OPERATION_PATHS 同口径，re.search 子串命中）：

    - ``METHOD /path``：仅该 HTTP 方法放行（如 ``GET /api/system/user``）；
    - 纯路径前缀/正则：沿用旧口径，不限方法。

    非法正则跳过并告警，不 500、不放任整清单失效；方法限定条目在请求方法未知时
    不放行（fail-closed）。
    """
    if not scopes:
        return True
    for pattern in scopes:
        if not pattern:
            continue
        entry_method, entry_path = split_scope_entry(pattern)
        if entry_method and entry_method != (method or "").upper():
            continue
        if not entry_path:
            continue
        try:
            if re.search(entry_path, path):
                return True
        except re.error:
            logger.warning("pat scope skipped: invalid path regex %s", entry_path)
            continue
    return False


def ip_allowed_by_allowlist(client_ip: str, allowlist) -> bool:
    """PAT IP 白名单判定：空清单 = 不限；支持单个 IP 与 CIDR 网段。

    fail-closed：无法解析的客户端 IP 视为不匹配；非法条目跳过并告警
    （与 scope 非法正则同口径：不 500、也不放任整清单失效）。
    """
    if not allowlist:
        return True
    try:
        addr = ipaddress.ip_address(str(client_ip))
    except ValueError:
        logger.warning("pat ip not parseable: %s", client_ip)
        return False
    for entry in allowlist:
        text = str(entry or "").strip()
        if not text:
            continue
        try:
            if "/" in text:
                if addr in ipaddress.ip_network(text, strict=False):
                    return True
            elif addr == ipaddress.ip_address(text):
                return True
        except ValueError:
            logger.warning("pat ip allowlist skipped: invalid entry %s", text)
            continue
    return False


# IP 白名单未命中的告警节流窗口（秒）：防伪造请求刷日志
PAT_IP_REJECT_LOG_THROTTLE_SECONDS = 60


def _log_pat_ip_rejection(pat, client_ip):
    """IP 白名单未命中告警（按凭证节流；缓存不可用时退化为每次都记，不静默）。"""
    from django.core.cache import cache

    try:
        first = cache.add(f"pat_ip_rejected_{pat.pk}", 1, PAT_IP_REJECT_LOG_THROTTLE_SECONDS)
    except Exception:  # noqa: BLE001 缓存不可用时仍需留痕
        first = True
    if first:
        logger.warning("pat ip not allowed. token:%s ip:%s", pat.token_prefix, client_ip)


def hash_pat_token(raw_token: str) -> str:
    """PAT 明文凭证的存储哈希（sha256）。

    独立成模块级函数：认证类、权限类与序列化器都要用，避免「类内静态方法 +
    循环依赖只能在文件底部 import」的写法。
    """
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


class ServerAccessToken(AccessToken):
    """
    自定义的token方法是为了登出的时候，将 access token 禁用
    """

    def verify(self):
        user_id = self.payload.get("user_id")
        # token 在认证链路为 bytes，兼容 str 入参（测试/工具直调）
        raw_token = self.token if isinstance(self.token, bytes) else str(self.token).encode()
        if BlackAccessTokenCache(user_id, hashlib.md5(raw_token).hexdigest()).get_storage_cache():
            raise TokenError(_("Token is invalid or expired"))
        # 强制下线（踢全部会话）：服务端拿不到用户的 token 清单，用「失效时间戳 +
        # iat 比较」拒绝被踢时刻之前签发的所有 access token
        revoked_at = UserTokenRevokedCache(user_id).get_storage_cache()
        if revoked_at and float(self.payload.get("iat", 0)) <= float(revoked_at):
            raise TokenError(_("Token is invalid or expired"))
        # 单会话下线（在线用户页行维度）：按登录时写入的 sid claim 精确拒绝，
        # 不影响该用户其他在用登录；旧 token 无 sid 自然跳过
        sid = self.payload.get("sid")
        if sid and SessionTokenRevokedCache(sid).get_storage_cache():
            raise TokenError(_("Token is invalid or expired"))
        super().verify()


class GetUserFromAccessToken(AccessToken):
    token_type = "refresh"


class CookieJWTAuthentication(JWTAuthentication):
    """
    支持cookie认证，是为了可以访问 django-proxy 的页面，比如 flower
    """

    def get_header(self, request):
        header = super().get_header(request)
        if not header:
            cookies = request.META.get("HTTP_COOKIE")
            if cookies:
                cookie_dict = parse_cookie(cookies)
                if cookie_dict and cookie_dict.get("X-Token"):
                    header = f"Bearer {cookie_dict.get('X-Token')}".encode("utf-8")
        return header

    def authenticate(self, request):
        result = super().authenticate(request)
        if result:
            # 会话活跃刷新（登录即登记的 UserSession）：节流门控在 touch 内部，
            # 失败静默——会话管理属附加能力，不能影响认证主链路
            try:
                sid = result[1].payload.get("sid")
                if sid:
                    from django.apps import apps

                    apps.get_model("system", "UserSession").touch(sid)
            except Exception:  # noqa: BLE001 会话刷新失败不影响认证
                pass
        return result


class PersonalAccessTokenAuthentication(BaseAuthentication):
    """个人访问令牌（PAT）认证：`Authorization: Pat <token>`。

    - 与 JWT 认证链并列（DEFAULT_AUTHENTICATION_CLASSES 末位）：无 PAT 头时静默
      返回 None，完全不影响既有认证方式；带了 PAT 头但无效则显式 401（fail-closed）。
    - PAT 完全继承所属用户（creator）既有权限：三层权限/数据权限/审计全链路天然生效，
      不能越过用户权限。
    - 不建 UserSession、不受 UserTokenRevokedCache 影响（按 iat 只针对 JWT）；
      吊销 = 置 is_active=False，每次认证查库即时生效。
    - last_used_time 经 Redis 60s 节流更新，防止高频机器请求刷库。
    """

    keyword = "pat"
    LAST_USED_THROTTLE_SECONDS = 60

    @staticmethod
    def hash_token(raw_token: str) -> str:
        # 保留类方法入口（既有调用点/测试引用），实现统一走 hash_pat_token
        return hash_pat_token(raw_token)

    def authenticate(self, request):
        header = request.META.get("HTTP_AUTHORIZATION", "")
        if not header:
            return None
        parts = header.split()
        if len(parts) != 2 or parts[0].lower() != self.keyword:
            return None

        from django.apps import apps
        from django.core.cache import cache
        from django.utils import timezone

        # 惰性 import：common.utils.request 顶层反向依赖本模块的 token 类
        from common.utils.request import get_request_ip

        token_model = apps.get_model("system", "PersonalAccessToken")
        pat = (
            token_model.objects.filter(token_hash=self.hash_token(parts[1]), is_active=True)
            .select_related("creator")
            .first()
        )
        if pat is None:
            raise AuthenticationFailed(_("Token is invalid or expired"))
        now = timezone.now()
        if pat.expired_at and pat.expired_at <= now:
            raise AuthenticationFailed(_("Token is invalid or expired"))
        user = pat.creator
        if user is None or not user.is_active:
            raise AuthenticationFailed(_("User account is disabled"))

        client_ip = get_request_ip(request)
        if not ip_allowed_by_allowlist(client_ip, pat.ip_allowlist):
            # 拒绝前埋点：认证失败会清空 request.auth，中间件据此把本次被拒请求
            # 归集到该凭证（审计可回溯「哪个凭证从哪被拒」）
            try:
                request._pat_rejected_token_pk = pat.pk
            except AttributeError:
                pass
            _log_pat_ip_rejection(pat, client_ip)
            raise AuthenticationFailed(_("Token is not allowed from this IP address"))

        # scope 清单挂 request（消费方 = 认证后的统一权限层 PatScopePermission）：
        # 认证类内不做拒绝——双 header（JWT 优先）时本类不会被调用，拒绝逻辑必须下沉
        request.pat_scopes = pat.scopes or []

        # last_used_time 节流更新：cache.add 原子占位，60s 内多次请求只回写一次
        throttle_key = f"pat_last_used_{pat.pk}"
        if cache.add(throttle_key, 1, self.LAST_USED_THROTTLE_SECONDS):
            token_model.objects.filter(pk=pat.pk).update(last_used_time=now)
        return (user, pat)
