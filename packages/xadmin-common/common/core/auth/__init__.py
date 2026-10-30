#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""认证与凭据族：JWT/PAT 认证、PAT scope 判定、敏感键识别与设置加密。

自 core 顶层平铺的 auth / auth_scopes / sensitive / credentials 四件归组。
``__init__`` 走 PEP 562 惰性导出（不急切 import 子模块）：日志引导期等
极早期消费方（``server.logging`` 引 ``auth.sensitive``）在 apps 就绪前导入
本包，急切导入 ``authentication``（内部访问 settings）会在 Django 启动期
炸 ``INSTALLED_APPS`` 未配置——惰性面保持任意时点可导入。
"""

from typing import TYPE_CHECKING, Any

# 名字 → 子模块（与 identity.services 的惰性导出同模式）
_LAZY_EXPORTS = {
    "auth_required": "authentication",
    "PAT_IP_REJECT_LOG_THROTTLE_SECONDS": "authentication",
    "hash_pat_token": "authentication",
    "check_api_application_quota": "authentication",
    "check_api_application_rate_limit": "authentication",
    "notify_api_quota_warning": "authentication",
    "ServerAccessToken": "authentication",
    "GetUserFromAccessToken": "authentication",
    "CookieJWTAuthentication": "authentication",
    "PersonalAccessTokenAuthentication": "authentication",
    "SCOPE_METHOD_RE": "auth_scopes",
    "SCOPE_HTTP_METHODS": "auth_scopes",
    "split_scope_entry": "auth_scopes",
    "normalize_scope_entry": "auth_scopes",
    "path_allowed_by_scopes": "auth_scopes",
    "ip_allowed_by_allowlist": "auth_scopes",
    "SENSITIVE_FIELDS": "sensitive",
    "SENSITIVE_SETTING_KEYS": "credentials",
    "ENCRYPTED_SETTING_KEYS": "credentials",
    "SENSITIVE_KEY_PATTERN": "credentials",
    "MASK": "credentials",
    "CIPHER_PREFIX": "credentials",
    "is_sensitive_key": "credentials",
    "is_sensitive_setting_row": "credentials",
    "filter_out_sensitive_rows": "credentials",
    "mask_sensitive_config_value": "credentials",
    "is_cipher_str": "credentials",
    "encrypt_setting_value": "credentials",
    "decrypt_setting_value": "credentials",
    "encryption_status": "credentials",
    "sensitive_setting_names": "credentials",
    "plaintext_setting_names": "credentials",
    "plaintext_sensitive_keys": "credentials",
}

if TYPE_CHECKING:
    # 静态类型面（运行期仍走 PEP 562 惰性加载）：`from common.core.auth import X`
    # 在类型检查下拿到真实签名，而不是 __getattr__ 的 Any
    from .auth_scopes import (
        SCOPE_HTTP_METHODS as SCOPE_HTTP_METHODS,
    )
    from .auth_scopes import (
        SCOPE_METHOD_RE as SCOPE_METHOD_RE,
    )
    from .auth_scopes import (
        ip_allowed_by_allowlist as ip_allowed_by_allowlist,
    )
    from .auth_scopes import (
        normalize_scope_entry as normalize_scope_entry,
    )
    from .auth_scopes import (
        path_allowed_by_scopes as path_allowed_by_scopes,
    )
    from .auth_scopes import (
        split_scope_entry as split_scope_entry,
    )
    from .authentication import (
        PAT_IP_REJECT_LOG_THROTTLE_SECONDS as PAT_IP_REJECT_LOG_THROTTLE_SECONDS,
    )
    from .authentication import (
        CookieJWTAuthentication as CookieJWTAuthentication,
    )
    from .authentication import (
        GetUserFromAccessToken as GetUserFromAccessToken,
    )
    from .authentication import (
        PersonalAccessTokenAuthentication as PersonalAccessTokenAuthentication,
    )
    from .authentication import (
        ServerAccessToken as ServerAccessToken,
    )
    from .authentication import (
        auth_required as auth_required,
    )
    from .authentication import (
        check_api_application_quota as check_api_application_quota,
    )
    from .authentication import (
        check_api_application_rate_limit as check_api_application_rate_limit,
    )
    from .authentication import (
        hash_pat_token as hash_pat_token,
    )
    from .authentication import (
        notify_api_quota_warning as notify_api_quota_warning,
    )
    from .credentials import (
        CIPHER_PREFIX as CIPHER_PREFIX,
    )
    from .credentials import (
        ENCRYPTED_SETTING_KEYS as ENCRYPTED_SETTING_KEYS,
    )
    from .credentials import (
        MASK as MASK,
    )
    from .credentials import (
        SENSITIVE_KEY_PATTERN as SENSITIVE_KEY_PATTERN,
    )
    from .credentials import (
        SENSITIVE_SETTING_KEYS as SENSITIVE_SETTING_KEYS,
    )
    from .credentials import (
        decrypt_setting_value as decrypt_setting_value,
    )
    from .credentials import (
        encrypt_setting_value as encrypt_setting_value,
    )
    from .credentials import (
        encryption_status as encryption_status,
    )
    from .credentials import (
        filter_out_sensitive_rows as filter_out_sensitive_rows,
    )
    from .credentials import (
        is_cipher_str as is_cipher_str,
    )
    from .credentials import (
        is_sensitive_key as is_sensitive_key,
    )
    from .credentials import (
        is_sensitive_setting_row as is_sensitive_setting_row,
    )
    from .credentials import (
        mask_sensitive_config_value as mask_sensitive_config_value,
    )
    from .credentials import (
        plaintext_sensitive_keys as plaintext_sensitive_keys,
    )
    from .credentials import (
        plaintext_setting_names as plaintext_setting_names,
    )
    from .credentials import (
        sensitive_setting_names as sensitive_setting_names,
    )
    from .sensitive import SENSITIVE_FIELDS as SENSITIVE_FIELDS

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> Any:
    from importlib import import_module

    module = _LAZY_EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(f".{module}", __package__), name)


def __dir__() -> list[str]:
    return [*__all__]
