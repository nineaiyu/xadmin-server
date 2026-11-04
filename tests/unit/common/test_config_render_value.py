# -*- coding: utf-8 -*-
"""配置值渲染：只解包「整值恰好是数字括号占位」的形态，不能篡改业务字符串。"""

import json

import pytest

from common.core.auth.credentials import SENSITIVE_SETTING_KEYS
from common.core.config.base import render_config_value

pytestmark = pytest.mark.django_db


def test_business_parentheses_preserved():
    assert render_config_value(json.dumps("(默认)"), {}, None) == "(默认)"
    assert render_config_value(json.dumps("(内网)"), {}, None) == "(内网)"
    assert render_config_value(json.dumps("a(b)c"), {}, None) == "a(b)c"
    assert render_config_value(json.dumps("前缀(示例)后缀"), {}, None) == "前缀(示例)后缀"


def test_numeric_parentheses_unwrapped():
    assert render_config_value('"(123)"', {}, None) == 123
    assert render_config_value('"(-5)"', {}, None) == -5
    assert render_config_value('"(1.5)"', {}, None) == 1.5


def test_non_numeric_parentheses_not_unwrapped_as_number():
    assert render_config_value('"(abc)"', {}, None) == "(abc)"


def test_mutual_reference_does_not_recurse():
    """A→B→A 互引配置只渲染一层，不因重建渲染上下文而无限递归。"""
    from common.core.config import SysConfig
    from system.models import SystemConfig

    SystemConfig.objects.create(key="MUTUAL_A", value="{{ MUTUAL_B }}", is_active=True)
    SystemConfig.objects.create(key="MUTUAL_B", value="{{ MUTUAL_A }}", is_active=True)
    value = SysConfig.get_value("MUTUAL_A")
    assert "MUTUAL_A" in str(value)


def test_unknown_config_attribute_logs_warning(caplog):
    from common.core.config import SysConfig

    with caplog.at_level("WARNING"):
        assert getattr(SysConfig, "TYPO_SECURITY_SWITCH", None) == {}
    assert "not registered as SysConfig property" in caplog.text


class TestSensitiveKeyNotDereferencedByTemplate:
    """修复守护：组合模板 {{ KEY }} 不得把敏感加密键解引用成明文。

    出口掩码按「宿主键」判定（非敏感宿主键不掩码），因此唯一的收口点在渲染
    上下文：敏感键要么不入上下文（build_config_render_context 排除），要么经
    ``get_render_context`` 掩码取值——三条路径（系统键引用系统敏感键、个人配置
    模板自引用、字段级敏感键展开）都必须只产出 MASK。
    """

    MASK = "••••••"

    def _seed_system_sensitive_row(self, key: str, value, access: bool = True):
        """经 set_value 写入敏感键：整值键/敏感字段自动加密落库（与生产一致）。"""
        from common.core.config import SysConfig
        from system.models import SystemConfig

        SysConfig.set_value(key, value, is_active=True, access=access)
        row = SystemConfig.objects.get(key=key)
        return row

    def test_system_template_cannot_exfiltrate_whole_value_key(self):
        """非敏感键写 {{ SCIM_TOKEN }}：渲染产物是掩码，不是明文凭据。"""
        from common.core.config import SysConfig
        from system.models import SystemConfig

        self._seed_system_sensitive_row("SCIM_TOKEN", "super-secret-token")
        # 前置：落库确为密文（信息流的明文只在读取侧解密）
        assert "super-secret-token" not in SystemConfig.objects.get(key="SCIM_TOKEN").value
        SystemConfig.objects.create(key="LEAK_PROBE_URL", value="{{ SCIM_TOKEN }}", is_active=True)

        result = str(SysConfig.get_value("LEAK_PROBE_URL"))

        assert "super-secret-token" not in result
        assert self.MASK in result

    def test_personal_config_template_cannot_exfiltrate(self, normal_user):
        """低权面：普通用户把 {{ SCIM_TOKEN }} 写进自己的个人配置，读回仍是掩码。

        个人配置的渲染上下文只含个人行（不含系统敏感键），模板引用经
        getattr(SysConfig) 解密兜底解析——修复前该路径直出明文，修复后掩码。
        """
        from common.core.config import UserConfig
        from system.models import UserPersonalConfig

        self._seed_system_sensitive_row("SCIM_TOKEN", "super-secret-token")
        UserPersonalConfig.objects.create(
            owner=normal_user, key="PUSH_MESSAGE_NOTICE", value="{{ SCIM_TOKEN }}", is_active=True
        )

        result = str(UserConfig(normal_user).get_value("PUSH_MESSAGE_NOTICE"))

        assert "super-secret-token" not in result
        assert self.MASK in result

    def test_field_level_key_masks_declared_fields_only(self):
        """{{ OAUTH_PROVIDERS }}：client_secret 掩码，非敏感字段照常可组合。"""
        from common.core.config import SysConfig
        from system.models import SystemConfig

        self._seed_system_sensitive_row(
            "OAUTH_PROVIDERS", {"client_secret": "cs-plain", "authorize_url": "https://example.com/oauth"}
        )
        SystemConfig.objects.create(key="PROVIDER_VIEW", value="{{ OAUTH_PROVIDERS }}", is_active=True)

        result = str(SysConfig.get_value("PROVIDER_VIEW"))

        assert "cs-plain" not in result
        assert self.MASK in result
        assert "https://example.com/oauth" in result

    def test_render_context_excludes_sensitive_rows(self):
        """build_config_render_context 直接排除敏感行（密文进上下文只有噪音）。"""
        from common.core.config.base import build_config_render_context
        from system.models import SystemConfig

        self._seed_system_sensitive_row("SCIM_TOKEN", "super-secret-token")
        SystemConfig.objects.create(key="PLAIN_KEY", value="plain-value", is_active=True)

        context = build_config_render_context(SystemConfig)

        assert "SCIM_TOKEN" not in context
        assert "PLAIN_KEY" in context

    def test_plain_key_composition_still_renders(self):
        """回归守护：非敏感键之间的组合渲染不受影响（上下文值按既有 JSON 串口径代入）。"""
        from common.core.config import SysConfig
        from system.models import SystemConfig

        SystemConfig.objects.create(key="SITE_HOST", value="example.com", is_active=True)
        SystemConfig.objects.create(key="RESET_URL", value="https://{{ SITE_HOST }}/reset", is_active=True)

        result = str(SysConfig.get_value("RESET_URL"))

        assert "{{ SITE_HOST }}" not in result  # 模板变量被正常代入（未被本修复阻断）
        assert "example.com" in result


class TestSensitiveRegistryFullCoverage:
    """注册表全量守护：``SENSITIVE_SETTING_KEYS`` 每个键都不得被模板解引用成明文。

    既有用例（TestSensitiveKeyNotDereferencedByTemplate）钉死的是修复当时的具体键；
    本类按注册表**全量参数化**——新增敏感键（如又一个 *_TOKEN / *_SECRET_KEY）无需
    改测试即自动纳入「非敏感键写 {{ KEY }} 只能渲染出掩码」的守护，堵住
    「新增配置模板路径再次泄露」的回潮面（审查 SEC-M2 的根因收口）。
    """

    MASK = "••••••"

    @pytest.mark.parametrize("key", sorted(SENSITIVE_SETTING_KEYS))
    def test_registry_key_never_dereferenced_to_plaintext(self, key):
        from common.core.config import SysConfig
        from system.models import SystemConfig

        fields = SENSITIVE_SETTING_KEYS[key]
        secret_token = "registry-guard-secret"
        if not fields:
            plaintext_probe = secret_token  # 整值加密键：值本体即凭据
        else:
            plaintext_probe = {field: secret_token for field in fields}
        self._seed(key, plaintext_probe)
        SystemConfig.objects.create(key=f"REGISTRY_PROBE_{key}", value=f"{{{{ {key} }}}}", is_active=True)

        rendered = str(SysConfig.get_value(f"REGISTRY_PROBE_{key}"))

        assert secret_token not in rendered, f"敏感键 {key} 经组合模板泄露明文"
        assert self.MASK in rendered, f"敏感键 {key} 的模板引用应渲染为掩码"

    def test_field_level_registry_keys_declare_nested_fields(self):
        """字段级键必须声明非空字段集且字段名是合法标识符（声明错误早失败）。"""
        for key, fields in SENSITIVE_SETTING_KEYS.items():
            assert isinstance(fields, tuple), f"{key} 的敏感字段声明必须是元组"
            for field in fields:
                assert field.isidentifier(), f"{key} 声明了非法字段名 {field!r}"

    @staticmethod
    def _seed(key: str, value):
        """经 set_value 写入敏感键（加密落库与生产一致）。"""
        from common.core.config import SysConfig

        SysConfig.set_value(key, value, is_active=True)
