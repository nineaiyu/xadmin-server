#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""PAT 凭证签发服务：个人令牌 / 开放平台应用凭证 / OAuth 访问凭证同源。

三类凭证此前各自实现「明文前缀 + 随机串 → sha256 哈希 + 截断前缀 + create」
（个人令牌在序列化器、应用凭证与 OAuth 访问凭证在视图模块），签发口径统一
收口到本模块：

- 明文只在签发响应返回一次（哈希落库不可回读）；
- 应用维度凭证（client-credentials 换发 / OAuth access）过期时间 =
  min(应用 TTL 截止, 应用有效期)；均未设置为永不过期；
- 轮换 = 失效旧凭证再签发（明文不可回读，无「复用」语义）。
"""

import secrets
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from common.core.auth import hash_pat_token
from system.models.token import PersonalAccessToken

#: 个人访问令牌前缀（认证链的历史口径，``Authorization: Pat <token>``）
PAT_TOKEN_PREFIX = "pat"
#: 开放平台应用凭证前缀（client-credentials 换发）
APP_TOKEN_PREFIX = "apst"
#: OAuth「代表用户」访问凭证前缀
OAUTH_ACCESS_PREFIX = "aoat"


def new_token_secret(prefix: str = PAT_TOKEN_PREFIX) -> tuple[str, str, str]:
    """生成 (明文 token, 落库哈希, 展示前缀) 三元组。

    明文 = 前缀 + 32 字节 URL 安全随机串（历史口径）；哈希 = sha256
    （common.core.auth.hash_pat_token，认证链同口径）；展示前缀 = 明文前
    12 字符（列表页脱敏展示）。
    """
    raw_token = f"{prefix}_{secrets.token_urlsafe(32)}"
    return raw_token, hash_pat_token(raw_token), raw_token[:12]


def application_token_expires_at(application):
    """应用维度凭证过期时间：TTL 与应用有效期取更早者；均未设置为 None（永不过期）。"""
    expires_at = None
    if application.token_ttl_seconds:
        expires_at = timezone.now() + timedelta(seconds=application.token_ttl_seconds)
    if application.expired_at:
        expires_at = min(expires_at, application.expired_at) if expires_at else application.expired_at
    return expires_at


def issue_access_token(
    *,
    creator,
    name: str,
    prefix: str = PAT_TOKEN_PREFIX,
    scopes=None,
    ip_allowlist=None,
    expired_at=None,
    api_application=None,
) -> tuple[PersonalAccessToken, str]:
    """签发一条 PAT 凭证，返回 (实例, 明文)。明文仅本次返回，不落库。"""
    raw_token, token_hash, token_prefix = new_token_secret(prefix)
    token = PersonalAccessToken.objects.create(
        name=name,
        token_hash=token_hash,
        token_prefix=token_prefix,
        scopes=list(scopes or []),
        ip_allowlist=list(ip_allowlist or []),
        expired_at=expired_at,
        api_application=api_application,
        creator=creator,
    )
    return token, raw_token


def revoke_application_tokens(application) -> int:
    """失效应用全部有效凭证（应用停用 / 密钥重置 / 凭证轮换共用），返回失效条数。"""
    return PersonalAccessToken.objects.filter(api_application=application, is_active=True).update(is_active=False)


def issue_application_token(application) -> tuple[PersonalAccessToken, str]:
    """为应用轮换一条凭证：失效旧凭证 → 新建（client-credentials 换发口径）。

    凭证 scope/IP 白名单/过期时间取应用当前配置；creator = 应用 owner，
    认证时以 owner 身份走既有 PAT 认证链。
    """
    with transaction.atomic():
        revoke_application_tokens(application)
        return issue_access_token(
            creator=application.creator,
            name=f"app:{application.client_id}",
            prefix=APP_TOKEN_PREFIX,
            scopes=application.scopes or [],
            ip_allowlist=application.ip_allowlist or [],
            expired_at=application_token_expires_at(application),
            api_application=application,
        )
