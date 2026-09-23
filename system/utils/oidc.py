#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""标准 OIDC：discovery 元数据、id_token 验签（JWKS）、claims 映射与组角色同步。

安全口径（与既有 OAuth 链路一致）：

- 端点与 JWKS 全部来自 **issuer discovery（HTTPS）** 或管理员显式配置；
- id_token 必须经 JWKS 公钥验签，并校验 iss / aud / exp / nonce；算法白名单
  （禁 none / HS*，防算法混淆），签名密钥按 kid 选取，密钥轮换时强制刷新一次 JWKS；
- claims → 本地资料字段映射显式可配（默认 name / email / phone_number），
  建号用户名仍走 ``provider_subject`` 规则（不做 IdP 用户名对齐，防命名劫持）；
- 组 → 角色映射仅增删**映射内出现的角色**（口径同 LDAP 组映射，映射为空即不启用）。
"""

import hashlib
import json

from django.core.cache import cache
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger
from system.utils.oauth import OAuthError, _get

logger = get_logger(__name__)

#: discovery / JWKS 缓存时长（秒）：公钥轮换由「验签失败强制刷新一次」兜底
OIDC_META_TTL = 600
#: id_token 验签算法白名单（禁 none / HS*：防算法混淆攻击）
ALLOWED_ID_TOKEN_ALGORITHMS = ("RS256", "RS384", "RS512", "PS256", "ES256", "ES384", "ES512")


def is_oidc_provider(provider: dict) -> bool:
    return (provider or {}).get("flavor") == "oidc"


def _cache_suffix(provider, *parts) -> str:
    raw = "|".join(str(part or "") for part in (provider.get("key"), *parts))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def discovery_metadata(provider, http_client=None) -> dict:
    """拉取并缓存 OIDC discovery 元数据（来源：显式 discovery_url 或 issuer 推导）。

    IdP 不可达 / 元数据不完整统一抛 `OAuthError`（可读文案，不回显原始报文）。
    """
    issuer = str(provider.get("issuer") or "").rstrip("/")
    url = provider.get("discovery_url") or (f"{issuer}/.well-known/openid-configuration" if issuer else "")
    if not url:
        return {}
    cache_key = f"oidc_discovery_{_cache_suffix(provider, url)}"
    cached = cache.get(cache_key)
    if isinstance(cached, dict) and cached.get("jwks_uri"):
        return cached
    try:
        response = _get(url, {}, http_client=http_client)
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 网络 / 解析异常对调用方语义相同
        logger.warning(f"oidc discovery failed. provider:{provider.get('key')} error:{exc}")
        raise OAuthError(_("Failed to contact the identity provider")) from exc
    if not isinstance(payload, dict) or not (payload.get("jwks_uri") or payload.get("token_endpoint")):
        logger.warning(f"oidc discovery incomplete. provider:{provider.get('key')}")
        raise OAuthError(_("The identity provider returned incomplete configuration"))
    cache.set(cache_key, payload, OIDC_META_TTL)
    return payload


def resolve_endpoints(provider, http_client=None) -> dict:
    """解析 OIDC 端点（显式配置优先，缺省取 discovery）。"""
    meta = discovery_metadata(provider, http_client)
    return {
        "authorize_url": provider.get("authorize_url") or meta.get("authorization_endpoint") or "",
        "token_url": provider.get("token_url") or meta.get("token_endpoint") or "",
        "jwks_uri": provider.get("jwks_uri") or meta.get("jwks_uri") or "",
        "issuer": str(provider.get("issuer") or meta.get("issuer") or "").rstrip("/"),
    }


def prepare_oidc_provider(provider: dict, http_client=None) -> dict:
    """把 discovery 结果写回 provider（授权 / 换码复用通用链路，调用方零分叉）。"""
    for key, value in resolve_endpoints(provider, http_client).items():
        if value and not provider.get(key):
            provider[key] = value
    return provider


def fetch_jwks(provider, jwks_uri: str, http_client=None, force_refresh: bool = False) -> dict:
    cache_key = f"oidc_jwks_{_cache_suffix(provider, jwks_uri)}"
    if not force_refresh:
        cached = cache.get(cache_key)
        if isinstance(cached, dict) and cached.get("keys"):
            return cached
    try:
        response = _get(jwks_uri, {}, http_client=http_client)
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"oidc jwks fetch failed. provider:{provider.get('key')} error:{exc}")
        raise OAuthError(_("Failed to contact the identity provider")) from exc
    if not isinstance(payload, dict) or not payload.get("keys"):
        logger.warning(f"oidc jwks incomplete. provider:{provider.get('key')}")
        raise OAuthError(_("The identity provider returned incomplete configuration"))
    cache.set(cache_key, payload, OIDC_META_TTL)
    return payload


def _select_jwk(jwks: dict, kid) -> dict | None:
    keys = [item for item in (jwks.get("keys") or []) if isinstance(item, dict)]
    if kid:
        for item in keys:
            if str(item.get("kid") or "") == str(kid):
                return item
        return None
    # 无 kid（少见）：仅在唯一密钥时使用，避免误用错误密钥
    return keys[0] if len(keys) == 1 else None


def _public_key(jwk: dict, algorithm: str):
    import jwt

    payload = json.dumps(jwk)
    if algorithm.startswith(("RS", "PS")):
        return jwt.algorithms.RSAAlgorithm.from_jwk(payload)
    if algorithm.startswith("ES"):
        return jwt.algorithms.ECAlgorithm.from_jwk(payload)
    raise OAuthError(_("The identity provider returned an invalid id_token"))


def verify_id_token(provider: dict, id_token: str, nonce: str | None = None, http_client=None) -> dict:
    """验签并校验 id_token，返回 claims（任一环节失败统一抛 `OAuthError`）。"""
    import jwt

    endpoints = resolve_endpoints(provider, http_client)
    jwks_uri = endpoints.get("jwks_uri") or ""
    if not jwks_uri:
        raise OAuthError(_("The identity provider returned incomplete configuration"))
    try:
        header = jwt.get_unverified_header(id_token)
    except Exception as exc:  # noqa: BLE001 非法 token 结构
        logger.warning(f"oidc id_token malformed. provider:{provider.get('key')} error:{exc}")
        raise OAuthError(_("The identity provider returned an invalid id_token")) from exc
    algorithm = str(header.get("alg") or "")
    if algorithm not in ALLOWED_ID_TOKEN_ALGORITHMS:
        logger.warning(f"oidc id_token algorithm rejected. provider:{provider.get('key')} alg:{algorithm}")
        raise OAuthError(_("The identity provider returned an invalid id_token"))

    claims = None
    last_error = None
    # 密钥轮换：首次失败强制刷新 JWKS 再试一次
    for force_refresh in (False, True):
        jwks = fetch_jwks(provider, jwks_uri, http_client, force_refresh=force_refresh)
        jwk = _select_jwk(jwks, header.get("kid"))
        if not jwk:
            last_error = f"kid not found: {header.get('kid')}"
            continue
        try:
            claims = jwt.decode(
                id_token,
                key=_public_key(jwk, algorithm),
                algorithms=[algorithm],
                audience=provider.get("client_id"),
                issuer=endpoints.get("issuer") or None,
                options={
                    "require": ["exp"],
                    "verify_aud": bool(provider.get("client_id")),
                    "verify_iss": bool(endpoints.get("issuer")),
                },
            )
            break
        except Exception as exc:  # noqa: BLE001 验签 / 时效 / 受众 / 签发者任一失败
            last_error = exc
    if claims is None:
        logger.warning(f"oidc id_token verify failed. provider:{provider.get('key')} error:{last_error}")
        raise OAuthError(_("The identity provider returned an invalid id_token"))
    if nonce is not None and str(claims.get("nonce") or "") != str(nonce):
        logger.warning(f"oidc id_token nonce mismatch. provider:{provider.get('key')}")
        raise OAuthError(_("The identity provider returned an invalid id_token"))
    return claims


def _claim_groups(provider: dict, claims: dict) -> list[str]:
    value = claims.get(provider.get("groups_field") or "groups")
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def claims_to_userinfo(provider: dict, claims: dict) -> dict:
    """claims → 通用 userinfo 形状（与既有 flavor 适配层同键：nickname/email/picture）。

    只映射显式声明的资料字段；``groups`` 供组 → 角色同步，不落库。
    """
    return {
        "sub": str(claims.get(provider.get("subject_field") or "sub") or ""),
        "nickname": str(
            claims.get(provider.get("nickname_claim") or "name")
            or claims.get("preferred_username")
            or claims.get("nickname")
            or ""
        ),
        "email": str(claims.get(provider.get("email_claim") or "email") or ""),
        "phone": str(claims.get(provider.get("phone_claim") or "phone_number") or ""),
        "picture": str(claims.get("picture") or ""),
        "groups": _claim_groups(provider, claims),
    }


def fetch_oidc_identity(provider: dict, token_payload: dict, nonce: str | None = None, http_client=None):
    """OIDC 身份获取：``(subject, userinfo)``（换码结果里的 id_token 验签后取 claims）。"""
    id_token = token_payload.get("id_token")
    if not id_token:
        logger.warning(f"oidc id_token missing. provider:{provider.get('key')}")
        raise OAuthError(_("The identity provider did not return an id_token"))
    claims = verify_id_token(provider, id_token, nonce=nonce, http_client=http_client)
    userinfo = claims_to_userinfo(provider, claims)
    if not userinfo.get("sub"):
        raise OAuthError(_("The identity provider returned incomplete user information"))
    return userinfo["sub"], userinfo


def _normalized_group_map(mapping: dict) -> dict:
    """组映射归一（组名 / code 小写去空白，口径同 LDAP 的 get_group_role_map）。"""
    result = {}
    for group, codes in (mapping or {}).items():
        key = str(group or "").strip().lower()
        if not key:
            continue
        if isinstance(codes, str):
            codes = [codes]
        result[key] = [str(code).strip() for code in codes if str(code).strip()]
    return result


def _group_matches(key: str, value: str) -> bool:
    """组值匹配：完整值相等，或 DN 形态的 CN 段前缀匹配（口径同 LDAP）。"""
    normalized = str(value or "").strip().lower()
    if normalized == key:
        return True
    return normalized.startswith(f"cn={key},")


def sync_group_roles(user, provider: dict, userinfo: dict) -> list[str]:
    """按 groups claim 同步本地角色；返回命中的角色 code 清单。

    - 只增删**映射内出现的角色**（映射外的授予不被触碰，口径同 LDAP 组映射）；
    - 映射为空 / 未配置 → 不启用（返回空清单，零副作用）。
    """
    from system.models import UserRole

    mapping = _normalized_group_map(provider.get("group_role_map") or {})
    if not mapping:
        return []
    wanted = set()
    for group in userinfo.get("groups") or []:
        for key, codes in mapping.items():
            if _group_matches(key, group):
                wanted.update(codes)
    mapped_codes = {code for codes in mapping.values() for code in codes}
    current = set(user.roles.filter(code__in=mapped_codes).values_list("code", flat=True))
    to_add = wanted - current
    to_remove = current - wanted
    if to_add:
        roles = list(UserRole.objects.filter(code__in=to_add, is_active=True))
        if roles:
            user.roles.add(*roles)
    if to_remove:
        user.roles.remove(*UserRole.objects.filter(code__in=to_remove))
    if to_add or to_remove:
        logger.info(f"oidc group roles synced. user:{user.pk} add:{sorted(to_add)} remove:{sorted(to_remove)}")
    return sorted(wanted)
