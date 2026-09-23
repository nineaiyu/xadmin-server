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
    "consume_bind_state",
    "consume_nonce",
    "consume_state",
    "exchange_code",
    "fetch_userinfo",
    "get_provider",
    "get_providers",
    "issue_bind_state",
    "issue_nonce",
    "issue_state",
    "mask_providers",
    "make_unique_username",
    "resolve_subject",
    "validate_providers",
]

# state 有效期（秒）：登录跳转通常在 1 分钟内完成，5 分钟足够且限制重放窗口
OAUTH_STATE_TTL = 300
STATE_CACHE_KEY = "oauth_state_{state}"
# 「绑定意图」state 走独立键空间：与登录 state 互不可用（登录不得触发绑定，反之亦然）。
# 载荷携带发起人 pk，回调据此校验归属后才建立绑定
BIND_STATE_CACHE_KEY = "oauth_bind_state_{state}"
# OIDC 的 nonce（防 id_token 重放）与 state 绑定存续：授权请求下发、回调时校验后即失效
NONCE_CACHE_KEY = "oauth_nonce_{state}"

# 配置项的必填/可选键
REQUIRED_KEYS = ("key", "name", "client_id", "authorize_url", "token_url", "userinfo_url")
OPTIONAL_DEFAULTS = {
    "scope": "openid profile email",
    "subject_field": "sub",
    "enabled": False,
    "auto_create": False,
    # ---- 标准 OIDC（flavor=oidc，F-10）----
    "issuer": "",  # discovery 基址（同时作为 id_token 的 iss 校验值）
    "discovery_url": "",  # 显式覆盖 discovery 地址（缺省 {issuer}/.well-known/openid-configuration）
    "jwks_uri": "",  # 显式覆盖 JWKS 地址（缺省取 discovery 的 jwks_uri）
    "groups_field": "groups",  # 组 claim 名（组 → 角色映射的取值键）
    "group_role_map": {},  # 组名 → 角色 code（口径同 LDAP_GROUP_ROLE_MAP：只增删映射内角色）
    "nickname_claim": "name",  # claims → 本地资料字段映射
    "email_claim": "email",
    "phone_claim": "phone_number",
}


def _config_providers():
    from common.core.config import SysConfig

    return SysConfig.OAUTH_PROVIDERS or []


def get_providers(enabled_only: bool = False) -> list[dict]:
    """读取 provider 配置；`enabled_only=True` 时只返回已启用且配置完整的。

    flavor 预设合并优先级：显式配置 > flavor 官方端点预设 > 通用默认值
    （IM flavor 管理员只需填应用三元组）。
    """
    from system.utils.oauth_flavors import FLAVOR_PRESETS

    providers = []
    for item in _config_providers():
        if not isinstance(item, dict):
            continue
        flavor = item.get("flavor") or "oauth2"
        provider = {**OPTIONAL_DEFAULTS, **FLAVOR_PRESETS.get(flavor, {}), **item, "flavor": flavor}
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
    IM flavor 的 URL 有官方预设可不填，https 只校验显式配置的 URL。
    """
    from system.utils.oauth_flavors import FLAVOR_PRESETS, FLAVOR_REQUIRED_KEYS

    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ValidationError(_("Invalid OAuth providers configuration"))

    seen = set()
    providers = []
    for item in value:
        if not isinstance(item, dict):
            raise ValidationError(_("Invalid OAuth providers configuration"))
        flavor = str(item.get("flavor") or "oauth2")
        if flavor != "oauth2" and flavor not in FLAVOR_PRESETS:
            raise ValidationError(_("Unknown OAuth provider flavor: {}").format(flavor))
        missing = [key for key in FLAVOR_REQUIRED_KEYS.get(flavor, REQUIRED_KEYS) if not item.get(key)]
        if missing:
            raise ValidationError(_("OAuth provider is missing required fields: {}").format(", ".join(missing)))
        key = str(item["key"])
        if key in seen:
            raise ValidationError(_("Duplicate OAuth provider key: {}").format(key))
        seen.add(key)

        for url_key in ("authorize_url", "token_url", "userinfo_url", "issuer", "discovery_url", "jwks_uri"):
            url = str(item.get(url_key) or "")
            if url and not url.startswith("https://"):
                raise ValidationError(_("OAuth provider url must use https: {}").format(url_key))
        if item.get("enabled") and not item.get("client_secret"):
            raise ValidationError(_("Enabled OAuth provider requires client_secret"))
        if flavor == "oidc":
            # 端点来源：issuer / discovery_url（自动发现）或显式 authorize_url + token_url
            if not (
                item.get("issuer") or item.get("discovery_url") or (item.get("authorize_url") and item.get("token_url"))
            ):
                raise ValidationError(
                    _("OIDC provider requires issuer (discovery) or explicit authorize_url and token_url")
                )
            if not isinstance(item.get("group_role_map") or {}, dict):
                raise ValidationError(_("OIDC provider group_role_map must be an object"))
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


def issue_bind_state(provider_key: str, user_pk) -> str:
    """生成「绑定意图」的一次性 state（载荷含发起人 pk，回调据此绑定到本人）。

    与登录 state 用不同键空间：即使 state 泄露，也不能把登录流程变成绑定流程。
    """
    state = secrets.token_urlsafe(32)
    cache.set(
        BIND_STATE_CACHE_KEY.format(state=state),
        {"provider": provider_key, "user_pk": str(user_pk)},
        OAUTH_STATE_TTL,
    )
    return state


def consume_bind_state(state: str) -> dict | None:
    """消费绑定 state：返回载荷 ``{provider, user_pk}``；已使用/过期返回 None（一次性）。"""
    if not state:
        return None
    key = BIND_STATE_CACHE_KEY.format(state=state)
    payload = cache.get(key)
    cache.delete(key)
    return payload if isinstance(payload, dict) else None


def issue_nonce(state: str) -> str:
    """生成 OIDC nonce（与 state 绑定，回调校验后即失效；防 id_token 重放）。"""
    nonce = secrets.token_urlsafe(24)
    cache.set(NONCE_CACHE_KEY.format(state=state), nonce, OAUTH_STATE_TTL)
    return nonce


def consume_nonce(state: str) -> str | None:
    """消费 nonce：一次性；未下发（非 OIDC）/已使用/过期返回 None。"""
    if not state:
        return None
    key = NONCE_CACHE_KEY.format(state=state)
    nonce = cache.get(key)
    if nonce:
        cache.delete(key)
    return nonce


def build_authorize_url(provider: dict, redirect_uri: str, state: str, nonce: str | None = None) -> str:
    from system.utils.oauth_flavors import build_flavor_authorize_url

    # IM flavor 参数形状不同（企微 appid/agentid、飞书 app_id）；返回 None 表示
    # 与标准形状一致（含钉钉），落回通用构造
    flavor_url = build_flavor_authorize_url(provider, redirect_uri, state)
    if flavor_url:
        return flavor_url
    params = {
        "response_type": "code",
        "client_id": provider.get("client_id"),
        "redirect_uri": redirect_uri,
        "scope": provider.get("scope") or "",
        "state": state,
        "nonce": nonce,
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
    """授权码换 token；失败统一抛 `OAuthError`（不回显 IdP 原始报文）。

    IM flavor（钉钉/企微/飞书）由适配器处理；返回 None 落回通用表单换码。
    """
    from system.utils.oauth_flavors import exchange_flavor_code

    adapted = exchange_flavor_code(provider, code, redirect_uri, http_client)
    if adapted is not None:
        return adapted
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


def fetch_userinfo(provider: dict, token_payload: dict, http_client=None) -> dict:
    """取用户信息；失败或缺少 subject 时抛 `OAuthError`。

    :param token_payload: `exchange_code` 的返回（oauth2 用 access_token；
        企微的 userid 也在其中——两步式协议下身份已在换码步确定）
    """
    from system.utils.oauth_flavors import fetch_flavor_userinfo

    adapted = fetch_flavor_userinfo(provider, token_payload, http_client)
    if adapted is not None:
        return adapted
    try:
        response = _get(
            provider["userinfo_url"],
            {"Authorization": f"Bearer {token_payload.get('access_token')}"},
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
