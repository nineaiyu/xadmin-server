# -*- coding: utf-8 -*-
"""企业 IM 扫码登录 flavor 适配器：钉钉 / 企业微信 / 飞书。

三家协议都不是标准 OAuth2（授权参数名、换码方式、用户信息端点与响应结构、
错误语义各有差异），差异全部收口在本模块；对外仍满足 `system/utils/oauth.py`
的协议契约：

- ``exchange_flavor_code(provider, code, redirect_uri, http_client)`` →
  token_payload dict（企业微信同时携带 corp access_token 与 userid）；
- ``fetch_flavor_userinfo(provider, token_payload, http_client)`` → **归一化**
  userinfo：原始报文 ⊕ ``nickname``/``email``/``picture`` 标准键
  （subject 按配置的 ``subject_field`` 读取）；
- ``build_flavor_authorize_url(provider, redirect_uri, state)`` → 授权地址
  （钉钉参数形状与标准一致，返回 None 落回通用构造）。

安全纪律与通用流一致：IdP 原始报文只进日志，用户侧错误统一 ``OAuthError``
可读文案；http 客户端可注入，单测完全离线。
"""

import hashlib
from urllib.parse import urlencode

from django.core.cache import cache
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger
from system.utils.oauth import OAuthError

logger = get_logger(__name__)

__all__ = [
    "FLAVOR_PRESETS",
    "FLAVOR_REQUIRED_KEYS",
    "build_flavor_authorize_url",
    "exchange_flavor_code",
    "fetch_flavor_userinfo",
]

# 各 flavor 官方端点与默认字段预设：读取时合并（显式配置 > 预设 > 通用默认），
# 管理员只需 key/name/flavor/client_id/client_secret（企业微信另需 agent_id）。
# 配置里仍可显式覆盖 URL（私有化网关场景），https 校验只针对显式配置的 URL。
FLAVOR_PRESETS = {
    "dingtalk": {
        "authorize_url": "https://login.dingtalk.com/oauth2/auth",
        "token_url": "https://api.dingtalk.com/v1.0/oauth2/userAccessToken",
        "userinfo_url": "https://api.dingtalk.com/v1.0/contact/users/me",
        "subject_field": "unionId",
        "scope": "openid",
    },
    "wecom": {
        "authorize_url": "https://login.work.weixin.qq.com/wwlogin/sso/login",
        "token_url": "https://qyapi.weixin.qq.com/cgi-bin/auth/getuserinfo",
        "userinfo_url": "https://qyapi.weixin.qq.com/cgi-bin/user/get",
        "gettoken_url": "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
        "subject_field": "userid",
    },
    "feishu": {
        "authorize_url": "https://accounts.feishu.cn/open-apis/authen/v1/authorize",
        "token_url": "https://open.feishu.cn/open-apis/authen/v2/oauth/token",
        "userinfo_url": "https://open.feishu.cn/open-apis/authen/v1/user_info",
        "subject_field": "union_id",
        "scope": "",
    },
    "oidc": {
        # 标准 OIDC：端点来自 issuer discovery（或显式 authorize_url/token_url），
        # 身份取自 id_token claims（签名经 JWKS 验签），无 userinfo 端点也可用
        "scope": "openid profile email",
        "subject_field": "sub",
    },
}

# 各 flavor 的写入侧必填键（oauth2 沿用 oauth.REQUIRED_KEYS，URL 必填）
FLAVOR_REQUIRED_KEYS = {
    "dingtalk": ("key", "name", "client_id", "client_secret"),
    "wecom": ("key", "name", "client_id", "client_secret", "agent_id"),
    "feishu": ("key", "name", "client_id", "client_secret"),
    # OIDC：端点由 issuer discovery 解析或显式填写，故 URL 不在必填面
    "oidc": ("key", "name", "client_id", "client_secret"),
}

_ERR_CONTACT = _("Failed to contact the identity provider")
_ERR_REJECTED = _("The identity provider rejected the login request")
_ERR_INCOMPLETE = _("The identity provider returned incomplete user information")

# 企业微信 corp token 缓存提前失效秒数（expires_in 通常 7200）
WECOM_TOKEN_TTL_SLACK = 120


# ---------------------------------------------------------------- http 助手


def _default_client():
    import requests

    return requests


def _post_json(url, body, timeout=10, http_client=None):
    client = http_client or _default_client()
    return client.post(url, json=body, timeout=timeout)


def _get_params(url, params, headers=None, timeout=10, http_client=None):
    client = http_client or _default_client()
    return client.get(url, params=params, headers=headers or {}, timeout=timeout)


def _get_bearer(url, token, timeout=10, http_client=None):
    return _get_params(url, {}, headers={"Authorization": f"Bearer {token}"}, timeout=timeout, http_client=http_client)


def _json(response) -> dict:
    try:
        payload = response.json() if hasattr(response, "json") else {}
    except Exception:  # noqa: BLE001 非法 JSON 与网络异常同语义
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _reject(action: str, provider: dict, detail, exc=None):
    logger.warning("oauth flavor %s rejected. provider:%s detail:%s", action, provider.get("key"), detail)
    raise OAuthError(_ERR_REJECTED) from exc


def _contact(action: str, provider: dict, exc):
    logger.warning("oauth flavor %s failed. provider:%s error:%s", action, provider.get("key"), exc)
    raise OAuthError(_ERR_CONTACT) from exc


def _normalize(userinfo: dict, **standard_keys) -> dict:
    """归一化：补 nickname/email/picture 标准键（不覆盖 IdP 已有的同名键）。"""
    for key, value in standard_keys.items():
        userinfo.setdefault(key, value or "")
    return userinfo


def _require_subject(userinfo: dict, provider: dict) -> dict:
    from system.utils.oauth import resolve_subject

    if not resolve_subject(provider, userinfo):
        raise OAuthError(_ERR_INCOMPLETE)
    return userinfo


# ---------------------------------------------------------------- 钉钉


def exchange_code_dingtalk(provider, code, redirect_uri, http_client=None):
    """POST JSON 换 userAccessToken（clientId/clientSecret/grantType 驼峰键）。"""
    try:
        response = _post_json(
            provider["token_url"],
            {
                "clientId": provider.get("client_id"),
                "clientSecret": provider.get("client_secret"),
                "code": code,
                "grantType": "authorization_code",
            },
            http_client=http_client,
        )
        payload = _json(response)
    except OAuthError:
        raise
    except Exception as exc:  # noqa: BLE001
        _contact("dingtalk exchange", provider, exc)
    if not payload.get("access_token"):
        _reject("dingtalk exchange", provider, payload.get("message") or payload)
    return payload


def fetch_userinfo_dingtalk(provider, token_payload, http_client=None):
    """GET contact/users/me：nick/unionId/openId/email/avatarUrl。"""
    try:
        response = _get_bearer(provider["userinfo_url"], token_payload.get("access_token"), http_client=http_client)
        payload = _json(response)
    except OAuthError:
        raise
    except Exception as exc:  # noqa: BLE001
        _contact("dingtalk userinfo", provider, exc)
    return _require_subject(
        _normalize(payload, nickname=payload.get("nick"), picture=payload.get("avatarUrl"), email=payload.get("email")),
        provider,
    )


# ---------------------------------------------------------------- 企业微信


def _wecom_check(payload: dict, action: str, provider: dict) -> dict:
    errcode = payload.get("errcode")
    if errcode not in (0, None):
        _reject(action, provider, f"errcode={errcode} errmsg={payload.get('errmsg')}")
    return payload


def _wecom_corp_token(provider, http_client=None) -> str:
    """corp access_token：gettoken 有频控且 7200s 有效，进 django cache；
    缓存 key 含 corpId+secret 摘要，改密自动换 key 不复用旧 token。"""
    secret = provider.get("client_secret") or ""
    digest = hashlib.sha256(f"{provider.get('client_id')}:{secret}".encode()).hexdigest()[:32]
    cache_key = f"oauth_wecom_token_{digest}"
    token = cache.get(cache_key)
    if token:
        return str(token)
    try:
        response = _get_params(
            provider["gettoken_url"],
            {"corpid": provider.get("client_id"), "corpsecret": secret},
            http_client=http_client,
        )
        payload = _wecom_check(_json(response), "wecom gettoken", provider)
    except OAuthError:
        raise
    except Exception as exc:  # noqa: BLE001
        _contact("wecom gettoken", provider, exc)
    token = str(payload.get("access_token") or "")
    if not token:
        _reject("wecom gettoken", provider, payload)
    ttl = int(payload.get("expires_in") or 7200) - WECOM_TOKEN_TTL_SLACK
    cache.set(cache_key, token, max(ttl, 60))
    return token


def exchange_code_wecom(provider, code, redirect_uri, http_client=None):
    """corp token（缓存）+ auth/getuserinfo 一步取身份：返回含 userid 的 payload。"""
    token = _wecom_corp_token(provider, http_client=http_client)
    try:
        response = _get_params(provider["token_url"], {"access_token": token, "code": code}, http_client=http_client)
        payload = _wecom_check(_json(response), "wecom exchange", provider)
    except OAuthError:
        raise
    except Exception as exc:  # noqa: BLE001
        _contact("wecom exchange", provider, exc)
    if not payload.get("userid"):
        # code 无效 / 用户不在应用可见范围
        _reject("wecom exchange", provider, payload)
    return {**payload, "access_token": token}


def fetch_userinfo_wecom(provider, token_payload, http_client=None):
    """GET user/get：userid → name/email/avatar（通讯录可见范围内的成员资料）。"""
    token = token_payload.get("access_token")
    userid = token_payload.get("userid")
    if not token or not userid:
        _reject("wecom userinfo", provider, token_payload)
    try:
        response = _get_params(
            provider["userinfo_url"], {"access_token": token, "userid": userid}, http_client=http_client
        )
        payload = _wecom_check(_json(response), "wecom userinfo", provider)
    except OAuthError:
        raise
    except Exception as exc:  # noqa: BLE001
        _contact("wecom userinfo", provider, exc)
    return _require_subject(
        _normalize(payload, nickname=payload.get("name"), picture=payload.get("avatar"), email=payload.get("email")),
        provider,
    )


# ---------------------------------------------------------------- 飞书


def exchange_code_feishu(provider, code, redirect_uri, http_client=None):
    """v2 token 端点：JSON 体（client_id=App ID）；兼容 token 顶层或 data 包裹。"""
    try:
        response = _post_json(
            provider["token_url"],
            {
                "grant_type": "authorization_code",
                "client_id": provider.get("client_id"),
                "client_secret": provider.get("client_secret"),
                "code": code,
                "redirect_uri": redirect_uri,
            },
            http_client=http_client,
        )
        payload = _json(response)
    except OAuthError:
        raise
    except Exception as exc:  # noqa: BLE001
        _contact("feishu exchange", provider, exc)
    if payload.get("code") not in (0, None):
        _reject("feishu exchange", provider, payload.get("msg") or payload)
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    token = payload.get("access_token") or data.get("access_token")
    if not token:
        _reject("feishu exchange", provider, payload)
    return {**data, **payload, "access_token": token}


def fetch_userinfo_feishu(provider, token_payload, http_client=None):
    """GET authen/v1/user_info：响应 data 包裹（union_id/open_id/name/email）。"""
    try:
        response = _get_bearer(provider["userinfo_url"], token_payload.get("access_token"), http_client=http_client)
        payload = _json(response)
    except OAuthError:
        raise
    except Exception as exc:  # noqa: BLE001
        _contact("feishu userinfo", provider, exc)
    if payload.get("code") not in (0, None):
        _reject("feishu userinfo", provider, payload.get("msg") or payload)
    userinfo = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    if not userinfo:
        _reject("feishu userinfo", provider, payload)
    return _require_subject(
        _normalize(
            dict(userinfo),
            nickname=userinfo.get("name"),
            picture=userinfo.get("avatar_url"),
            email=userinfo.get("email"),
        ),
        provider,
    )


# ---------------------------------------------------------------- 分发入口


def build_flavor_authorize_url(provider, redirect_uri, state):
    """flavor 授权地址；返回 None 表示参数形状与标准一致，落回通用构造。"""
    flavor = provider.get("flavor") or "oauth2"
    if flavor == "wecom":
        params = {
            "login_type": "CorpApp",
            "new_login_type": "1",
            "appid": provider.get("client_id"),
            "agentid": provider.get("agent_id"),
            "redirect_uri": redirect_uri,
            "state": state,
        }
        separator = "&" if "?" in provider["authorize_url"] else "?"
        return f"{provider['authorize_url']}{separator}{urlencode({k: v for k, v in params.items() if v})}"
    if flavor == "feishu":
        params = {
            "app_id": provider.get("client_id"),
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
        }
        separator = "&" if "?" in provider["authorize_url"] else "?"
        return f"{provider['authorize_url']}{separator}{urlencode({k: v for k, v in params.items() if v})}"
    # dingtalk：client_id/response_type/scope/state/redirect_uri 与标准形状一致
    return None


def exchange_flavor_code(provider, code, redirect_uri, http_client=None):
    flavor = provider.get("flavor") or "oauth2"
    handler = {
        "dingtalk": exchange_code_dingtalk,
        "wecom": exchange_code_wecom,
        "feishu": exchange_code_feishu,
    }.get(flavor)
    return handler(provider, code, redirect_uri, http_client) if handler else None


def fetch_flavor_userinfo(provider, token_payload, http_client=None):
    flavor = provider.get("flavor") or "oauth2"
    handler = {
        "dingtalk": fetch_userinfo_dingtalk,
        "wecom": fetch_userinfo_wecom,
        "feishu": fetch_userinfo_feishu,
    }.get(flavor)
    return handler(provider, token_payload, http_client) if handler else None
