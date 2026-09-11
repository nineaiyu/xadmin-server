# -*- coding: utf-8 -*-
"""OAuth2 / OIDC 通用 provider：配置读取与校验、state 一次性校验、token/userinfo 交换。

**安全口径**：

- provider 配置进 SystemConfig（`OAUTH_PROVIDERS`，默认 `[]` = 整体休眠），
  列表接口回传时 `client_secret` 一律掩码，密钥不出服务端；
- state 一次性（Redis `cache.add` 占位 + 5 分钟 TTL），回调消费即失效；
- 与 IdP 的交互全部走**可注入的 http 客户端**，单测可完全离线覆盖（含异常分支）；
- IdP 原始报文不回显给前端，所有失败统一映射为可读业务文案。
"""

import secrets
import time
from urllib.parse import urlencode

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger

logger = get_logger(__name__)


class OAuthError(Exception):
    """第三方登录流程中的可读业务错误。

    只带面向用户的文案：IdP 的原始报文（含 client_id / token 片段）一律不回显。
    """

    def __init__(self, detail):
        self.detail = detail
        super().__init__(str(detail))


__all__ = [
    "OAUTH_STATE_TTL",
    "OAuthError",
    "build_authorize_url",
    "consume_state",
    "exchange_code",
    "fetch_userinfo",
    "get_provider",
    "get_providers",
    "issue_state",
    "mask_providers",
    "make_unique_username",
    "resolve_subject",
    "validate_providers",
]

# state 有效期（秒）：登录跳转通常在 1 分钟内完成，5 分钟足够且限制重放窗口
OAUTH_STATE_TTL = 300
STATE_CACHE_KEY = "oauth_state_{state}"

# 配置项的必填/可选键
REQUIRED_KEYS = ("key", "name", "client_id", "authorize_url", "token_url", "userinfo_url")
OPTIONAL_DEFAULTS = {
    "scope": "openid profile email",
    "subject_field": "sub",
    "enabled": False,
    "auto_create": False,
}


def _config_providers():
    from common.core.config import SysConfig

    return SysConfig.OAUTH_PROVIDERS or []


def get_providers(enabled_only: bool = False) -> list[dict]:
    """读取 provider 配置；`enabled_only=True` 时只返回已启用且配置完整的。"""
    providers = []
    for item in _config_providers():
        if not isinstance(item, dict):
            continue
        provider = {**OPTIONAL_DEFAULTS, **item}
        if enabled_only and not (provider.get("enabled") and provider.get("client_id")):
            continue
        providers.append(provider)
    return providers


def get_provider(key: str, enabled_only: bool = True) -> dict | None:
    for provider in get_providers(enabled_only=enabled_only):
        if provider.get("key") == key:
            return provider
    return None


def validate_providers(value) -> list[dict]:
    """写入侧校验：结构、必填键、key 唯一、URL 必须 https、启用时 secret 非空。

    配置错误必须在**保存时**挡住，否则会让每个用户都撞到一个看不懂的回调错误。
    """
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ValidationError(_("Invalid OAuth providers configuration"))

    seen = set()
    providers = []
    for item in value:
        if not isinstance(item, dict):
            raise ValidationError(_("Invalid OAuth providers configuration"))
        missing = [key for key in REQUIRED_KEYS if not item.get(key)]
        if missing:
            raise ValidationError(_("OAuth provider is missing required fields: {}").format(", ".join(missing)))
        key = str(item["key"])
        if key in seen:
            raise ValidationError(_("Duplicate OAuth provider key: {}").format(key))
        seen.add(key)

        for url_key in ("authorize_url", "token_url", "userinfo_url"):
            url = str(item.get(url_key) or "")
            if not url.startswith("https://"):
                raise ValidationError(_("OAuth provider url must use https: {}").format(url_key))
        if item.get("enabled") and not item.get("client_secret"):
            raise ValidationError(_("Enabled OAuth provider requires client_secret"))
        providers.append({**OPTIONAL_DEFAULTS, **item})
    return providers


def mask_providers(providers: list[dict]) -> list[dict]:
    """回传前掩码密钥：只保留是否配置（布尔），不泄露任何密钥字符。"""
    masked = []
    for provider in providers:
        item = dict(provider)
        item["client_secret_configured"] = bool(provider.get("client_secret"))
        item.pop("client_secret", None)
        masked.append(item)
    return masked


def issue_state(provider_key: str) -> str:
    """生成一次性 state（与 provider 绑定，防跨 provider 重放）。"""
    state = secrets.token_urlsafe(32)
    cache.set(STATE_CACHE_KEY.format(state=state), provider_key, OAUTH_STATE_TTL)
    return state


def consume_state(state: str) -> str | None:
    """消费 state：返回其绑定的 provider key；已使用/过期返回 None（一次性）。"""
    if not state:
        return None
    key = STATE_CACHE_KEY.format(state=state)
    provider_key = cache.get(key)
    cache.delete(key)
    return provider_key


def build_authorize_url(provider: dict, redirect_uri: str, state: str) -> str:
    params = {
        "response_type": "code",
        "client_id": provider.get("client_id"),
        "redirect_uri": redirect_uri,
        "scope": provider.get("scope") or "",
        "state": state,
    }
    separator = "&" if "?" in provider["authorize_url"] else "?"
    return f"{provider['authorize_url']}{separator}{urlencode({k: v for k, v in params.items() if v})}"


def _post(url, data, timeout=10, http_client=None):
    client = http_client or _default_client()
    return client.post(url, data=data, timeout=timeout)


def _get(url, headers, timeout=10, http_client=None):
    client = http_client or _default_client()
    return client.get(url, headers=headers, timeout=timeout)


def _default_client():
    import requests

    return requests


def exchange_code(provider: dict, code: str, redirect_uri: str, http_client=None) -> dict:
    """授权码换 token；失败统一抛 `OAuthError`（不回显 IdP 原始报文）。"""
    try:
        response = _post(
            provider["token_url"],
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": provider.get("client_id"),
                "client_secret": provider.get("client_secret"),
            },
            http_client=http_client,
        )
        payload = response.json() if hasattr(response, "json") else {}
    except Exception as exc:  # noqa: BLE001 网络/解析异常对调用方语义相同
        logger.warning(f"oauth token exchange failed. provider:{provider.get('key')} error:{exc}")
        raise OAuthError(_("Failed to contact the identity provider")) from exc

    if not payload.get("access_token"):
        logger.warning(f"oauth token exchange rejected. provider:{provider.get('key')}")
        raise OAuthError(_("The identity provider rejected the login request"))
    return payload


def fetch_userinfo(provider: dict, access_token: str, http_client=None) -> dict:
    """取用户信息；失败或缺少 subject 时抛 `OAuthError`。"""
    try:
        response = _get(
            provider["userinfo_url"],
            {"Authorization": f"Bearer {access_token}"},
            http_client=http_client,
        )
        payload = response.json() if hasattr(response, "json") else {}
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"oauth userinfo failed. provider:{provider.get('key')} error:{exc}")
        raise OAuthError(_("Failed to contact the identity provider")) from exc

    if not isinstance(payload, dict) or not resolve_subject(provider, payload):
        raise OAuthError(_("The identity provider returned incomplete user information"))
    return payload


def resolve_subject(provider: dict, userinfo: dict) -> str:
    """按 `subject_field`（默认 sub）取 IdP 唯一标识。"""
    field = provider.get("subject_field") or "sub"
    return str(userinfo.get(field) or "")


def make_unique_username(provider_key: str, subject: str) -> str:
    """按 `provider_subject` 规则生成唯一用户名（不做 IdP 用户名对齐，防命名劫持）。"""
    from system.models import UserInfo

    base = f"{provider_key}_{subject}"[:30]
    username = base
    index = 1
    while UserInfo.objects.filter(username=username).exists():
        index += 1
        username = f"{base[: 30 - len(str(index))]}_{index}"
    return username


def now_timestamp() -> int:
    return int(time.time())
