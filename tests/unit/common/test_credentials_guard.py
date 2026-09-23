# -*- coding: utf-8 -*-
"""凭据治理守护测试：敏感键注册表 / 加密往返 / 明文巡检 / 种子清白。

守护口径：
1. ``ENCRYPTED_SETTING_KEYS`` 的每个键必须仍是 write_only（Setting 加密声明的
   唯一作用点，漏了就静默明文落库）；
2. 新增的 write_only 敏感键必须登记注册表（防「新增明文」）；
3. ``loadjson/systemconfig.json`` 中声明的敏感键不得为明文；
4. SystemConfig 值内字段加密往返 + ``plaintext_sensitive_keys`` 巡检 + 轮换修复。
"""

import ast
import json
import pathlib

import pytest

from common.core.credentials import (
    ENCRYPTED_SETTING_KEYS,
    PLAINTEXT_EXEMPT_KEYS,
    SENSITIVE_KEY_PATTERN,
    decrypt_setting_value,
    encrypt_setting_value,
    encryption_status,
    plaintext_sensitive_keys,
)

pytestmark = pytest.mark.django_db

SERVER_ROOT = pathlib.Path(__file__).resolve().parents[3]
SERIALIZER_DIR = SERVER_ROOT / "settings" / "serializers"


def _write_only_keys() -> dict:
    """AST 扫描 settings/serializers/*.py：字段名 → 声明 write_only=True 的键。"""
    keys = {}
    for path in SERIALIZER_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
                continue
            has_write_only = any(
                kw.arg == "write_only" and getattr(kw.value, "value", None) is True for kw in node.value.keywords
            )
            if not has_write_only:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    keys.setdefault(target.id, path.name)
    return keys


class TestRegistryGuard:
    def test_encrypted_setting_keys_are_write_only(self):
        declared = _write_only_keys()
        missing = [key for key in ENCRYPTED_SETTING_KEYS if key not in declared]
        assert missing == [], f"以下键声明为加密但不是 write_only（不会加密落库）: {missing}"

    def test_sensitive_write_only_keys_are_registered(self):
        """新增 write_only 敏感键必须登记注册表（先落「防新增明文」机制）。"""
        declared = {key for key in _write_only_keys() if SENSITIVE_KEY_PATTERN.search(key)}
        unregistered = sorted(declared - set(ENCRYPTED_SETTING_KEYS))
        assert unregistered == [], f"write_only 敏感键未登记 ENCRYPTED_SETTING_KEYS: {unregistered}"

    def test_seed_has_no_plaintext_sensitive_values(self):
        seed_path = SERVER_ROOT / "loadjson" / "systemconfig.json"
        seeds = json.loads(seed_path.read_text(encoding="utf-8"))
        offenders = []
        for row in seeds:
            key, value = row["fields"]["key"], row["fields"]["value"]
            if encryption_status(key, value) == "plaintext" and key not in PLAINTEXT_EXEMPT_KEYS:
                offenders.append(key)
        assert offenders == [], f"systemconfig.json 种子中敏感键为明文: {offenders}"


class TestValueEncryption:
    def test_oauth_providers_roundtrip(self):
        from system.models import SystemConfig

        providers = [
            {
                "key": "sso",
                "name": "SSO",
                "client_id": "cid",
                "client_secret": "plain-secret",
                "authorize_url": "https://a.example.com/auth",
            }
        ]
        encrypted = encrypt_setting_value("OAUTH_PROVIDERS", providers)
        assert encrypted[0]["client_secret"].startswith("v3:")
        assert encrypted[0]["client_id"] == "cid"  # 非敏感字段不动
        assert decrypt_setting_value("OAUTH_PROVIDERS", encrypted) == providers
        assert encryption_status("OAUTH_PROVIDERS", encrypted) == "encrypted"
        assert encryption_status("OAUTH_PROVIDERS", providers) == "plaintext"
        # 幂等：已加密不重复加密
        assert encrypt_setting_value("OAUTH_PROVIDERS", encrypted) == encrypted
        assert SystemConfig.objects.count() == 0  # 纯函数，不落库

    def test_non_sensitive_key_untouched(self):
        value = {"any": "plain"}
        assert encrypt_setting_value("WEB_SITE_CONFIG", value) is value
        assert decrypt_setting_value("WEB_SITE_CONFIG", value) is value
        assert encryption_status("WEB_SITE_CONFIG", value) == "not_sensitive"

    def test_empty_value_status(self):
        assert encryption_status("SCIM_TOKEN", "") == "empty"
        assert encryption_status("SCIM_TOKEN", None) == "empty"
        assert encryption_status("SCIM_TOKEN", []) == "empty"

    def test_plaintext_detected_and_rotate_fixes(self, superuser):
        from system.models import SystemConfig
        from system.utils.credential import rotate_system_config

        SystemConfig.objects.update_or_create(key="SCIM_TOKEN", defaults={"value": "plain-token"})
        assert plaintext_sensitive_keys() == ["SCIM_TOKEN"]

        result = rotate_system_config("SCIM_TOKEN", user=superuser)
        assert result == {"ok": True, "action": "encrypt", "detail": ""}
        assert plaintext_sensitive_keys() == []
        row = SystemConfig.objects.get(key="SCIM_TOKEN")
        assert row.value.startswith("v3:")
        assert decrypt_setting_value("SCIM_TOKEN", row.value) == "plain-token"

        # 二次轮换：密文重加密（salt/nonce 变化）
        first_cipher = row.value
        result = rotate_system_config("SCIM_TOKEN", user=superuser)
        assert result["action"] == "rotate"
        row.refresh_from_db()
        assert row.value != first_cipher
        assert decrypt_setting_value("SCIM_TOKEN", row.value) == "plain-token"

    def test_rotate_unknown_key_rejected(self, superuser):
        from system.utils.credential import rotate_system_config

        result = rotate_system_config("WEB_SITE_CONFIG", user=superuser)
        assert result["ok"] is False

    def test_rotate_writes_audit(self, superuser):
        from system.models import OperationLog, SystemConfig
        from system.utils.credential import rotate_system_config

        SystemConfig.objects.update_or_create(key="OPS_ALERT_TOKEN", defaults={"value": "plain"})
        rotate_system_config("OPS_ALERT_TOKEN", user=superuser)
        row = OperationLog.objects.filter(module="system:credential").first()
        assert row is not None
        assert "OPS_ALERT_TOKEN" in row.changes


class TestConfigCacheIntegration:
    def test_cache_read_decrypts_and_write_encrypts(self):
        """经 ConfigCacheBase 读写：落库密文、消费方拿明文（读写两侧统一收口）。"""
        from common.core.config import SysConfig
        from system.models import SystemConfig

        providers = [{"key": "sso", "name": "SSO", "client_id": "cid", "client_secret": "raw-secret"}]
        SysConfig.set_value("OAUTH_PROVIDERS", providers, is_active=True, description="测试")
        row = SystemConfig.objects.get(key="OAUTH_PROVIDERS")
        assert row.value[0]["client_secret"].startswith("v3:")
        assert SysConfig.get_value("OAUTH_PROVIDERS")[0]["client_secret"] == "raw-secret"
