"""系统配置写入期类型校验与受控收敛。

类型化配置键（数值/布尔等）此前可写入任意 JSON 值，坏值静默落库、直到读取
使用时才炸（比较/强转 TypeError）。现按注册键建立期望类型表，在写入期校验：
- conf 默认值（唯一事实源）全部通过且不被改写；
- 每类类型的典型非法值被拒（400 + 明确文案）；
- 受控收敛：数值键接受等值字符串并转型入库为正确类型；
- 未注册键行为与现状一致（原样放行）。
"""

import pytest
from django.utils.translation import gettext as _

from common.core.config import BaseConfCache, ConfigCache, MessagePushConfCache
from common.injection import get_server_config
from system.models import SystemConfig
from system.serializers.config import _config_value_types, validate_config_value

pytestmark = pytest.mark.django_db

SYSTEM_URL = "/api/system/config/system"

# 与序列化器建表同口径收集注册键（property 名 = 配置键）
REGISTERED_KEYS = set()
for _cls in (BaseConfCache, MessagePushConfCache, ConfigCache):
    for _klass in _cls.__mro__:
        REGISTERED_KEYS |= {name for name, value in vars(_klass).items() if isinstance(value, property)}


@pytest.fixture
def admin_client(api_client, superuser):
    api_client.force_authenticate(user=superuser)
    return api_client


@pytest.fixture
def cleanup_config_rows():
    """用例产生的配置行用后即清，防 --keepdb 下残留影响后续断言。"""

    def _cleanup(*keys):
        SystemConfig.objects.filter(key__in=keys).delete()

    return _cleanup


def _post_value(admin_client, key, value):
    return admin_client.post(SYSTEM_URL, {"key": key, "value": value, "is_active": True}, format="json")


class TestRegisteredDefaultsPass:
    def test_table_covers_all_registered_keys(self):
        """注册键全部入表（另补 WEB_SITE_CONFIG 站点配置对象）。"""
        types = _config_value_types()
        assert REGISTERED_KEYS - set(types) == set()
        assert "WEB_SITE_CONFIG" in types

    def test_every_conf_default_passes_unchanged(self):
        """conf 默认值是合法值：全量代入校验逻辑，类型与值都不得被改写。"""
        defaults = type(get_server_config()).defaults
        types = _config_value_types()
        assert len(types) >= len(REGISTERED_KEYS)
        for key in REGISTERED_KEYS:
            default = defaults[key]
            assert validate_config_value(key, default) == default, key

    def test_site_config_default_dict_passes(self):
        assert validate_config_value("WEB_SITE_CONFIG", {"SplitPanes": {}}) == {"SplitPanes": {}}


class TestTypedValueRejection:
    """每类类型的典型非法值在写入期被拒，报错指明键与期望类型。"""

    def test_integer_key_rejects_text(self, admin_client, cleanup_config_rows):
        resp = _post_value(admin_client, "FILE_KEEP_DAYS", "abc")
        assert resp.status_code == 400, resp.data
        assert _("Config value for {} must be an integer").format("FILE_KEEP_DAYS") in str(resp.data["detail"])
        assert not SystemConfig.objects.filter(key="FILE_KEEP_DAYS").exists()
        cleanup_config_rows("FILE_KEEP_DAYS")

    def test_integer_key_rejects_boolean(self, admin_client, cleanup_config_rows):
        # bool 是 int 子类，显式拒绝：True 不能当 1 写进数值配置
        resp = _post_value(admin_client, "FILE_KEEP_DAYS", True)
        assert resp.status_code == 400
        assert _("Config value for {} must be an integer").format("FILE_KEEP_DAYS") in str(resp.data["detail"])
        cleanup_config_rows("FILE_KEEP_DAYS")

    def test_float_key_rejects_non_numeric_string(self, admin_client, cleanup_config_rows):
        resp = _post_value(admin_client, "SLOW_REQUEST_THRESHOLD", "fast")
        assert resp.status_code == 400
        assert _("Config value for {} must be a number").format("SLOW_REQUEST_THRESHOLD") in str(resp.data["detail"])
        cleanup_config_rows("SLOW_REQUEST_THRESHOLD")

    def test_boolean_key_rejects_ambiguous_text(self, admin_client, cleanup_config_rows):
        resp = _post_value(admin_client, "SCIM_ENABLED", "maybe")
        assert resp.status_code == 400
        assert _("Config value for {} must be a boolean").format("SCIM_ENABLED") in str(resp.data["detail"])
        cleanup_config_rows("SCIM_ENABLED")

    def test_string_key_rejects_number(self, admin_client, cleanup_config_rows):
        resp = _post_value(admin_client, "CSP_MODE", 123)
        assert resp.status_code == 400
        assert _("Config value for {} must be a string").format("CSP_MODE") in str(resp.data["detail"])
        cleanup_config_rows("CSP_MODE")

    def test_list_key_rejects_plain_string(self, admin_client, cleanup_config_rows):
        resp = _post_value(admin_client, "SENSITIVE_OPERATION_METHODS", "DELETE")
        assert resp.status_code == 400
        assert _("Config value for {} must be a JSON array").format("SENSITIVE_OPERATION_METHODS") in str(
            resp.data["detail"]
        )
        cleanup_config_rows("SENSITIVE_OPERATION_METHODS")

    def test_dict_key_rejects_list(self, admin_client, cleanup_config_rows):
        resp = _post_value(admin_client, "WEB_SITE_CONFIG", ["Grey"])
        assert resp.status_code == 400
        assert _("Config value for {} must be a JSON object").format("WEB_SITE_CONFIG") in str(resp.data["detail"])
        cleanup_config_rows("WEB_SITE_CONFIG")


class TestControlledConvergence:
    """受控收敛：等值字符串/整数值浮点入库前转型为正确类型。"""

    def test_numeric_string_converged_to_int(self, admin_client, cleanup_config_rows):
        resp = _post_value(admin_client, "FILE_KEEP_DAYS", "90")
        assert resp.status_code in (200, 201), resp.data
        row = SystemConfig.objects.get(key="FILE_KEEP_DAYS")
        assert row.value == 90 and type(row.value) is int
        cleanup_config_rows("FILE_KEEP_DAYS")

    def test_integral_float_converged_to_int(self, admin_client, cleanup_config_rows):
        resp = _post_value(admin_client, "FILE_KEEP_DAYS", 90.0)
        assert resp.status_code in (200, 201), resp.data
        row = SystemConfig.objects.get(key="FILE_KEEP_DAYS")
        assert row.value == 90 and type(row.value) is int
        cleanup_config_rows("FILE_KEEP_DAYS")

    def test_int_converged_to_float(self, admin_client, cleanup_config_rows):
        resp = _post_value(admin_client, "SLOW_REQUEST_THRESHOLD", 2)
        assert resp.status_code in (200, 201), resp.data
        row = SystemConfig.objects.get(key="SLOW_REQUEST_THRESHOLD")
        assert row.value == 2.0 and type(row.value) is float
        cleanup_config_rows("SLOW_REQUEST_THRESHOLD")

    def test_bool_string_converged_to_real_false(self, admin_client, cleanup_config_rows):
        """字符串 "false" 收敛为真布尔：消除读取侧 bool("false") 恒真的坏值。"""
        resp = _post_value(admin_client, "SCIM_ENABLED", "false")
        assert resp.status_code in (200, 201), resp.data
        row = SystemConfig.objects.get(key="SCIM_ENABLED")
        assert row.value is False
        cleanup_config_rows("SCIM_ENABLED")


class TestUnknownAndPartialUpdate:
    def test_unknown_key_passes_through(self, admin_client, cleanup_config_rows):
        """未注册键行为与现状一致：任意 JSON 值原样放行。"""
        value = {"whatever": ["goes"]}
        resp = _post_value(admin_client, "NOT_A_REGISTERED_KEY", value)
        assert resp.status_code in (200, 201), resp.data
        assert SystemConfig.objects.get(key="NOT_A_REGISTERED_KEY").value == value
        cleanup_config_rows("NOT_A_REGISTERED_KEY")

    def test_partial_update_without_key_validated_by_row_key(self, admin_client, cleanup_config_rows):
        """PATCH 只带 value：按实例行键补校验，非法值同样被拒。"""
        SystemConfig.objects.create(key="FILE_KEEP_DAYS", value=90, is_active=True)
        row = SystemConfig.objects.get(key="FILE_KEEP_DAYS")
        resp = admin_client.patch(f"{SYSTEM_URL}/{row.pk}", {"value": "abc"}, format="json")
        assert resp.status_code == 400
        assert _("Config value for {} must be an integer").format("FILE_KEEP_DAYS") in str(resp.data["detail"])
        cleanup_config_rows("FILE_KEEP_DAYS")

    def test_partial_update_numeric_string_converged(self, admin_client, cleanup_config_rows):
        SystemConfig.objects.create(key="FILE_KEEP_DAYS", value=90, is_active=True)
        row = SystemConfig.objects.get(key="FILE_KEEP_DAYS")
        resp = admin_client.patch(f"{SYSTEM_URL}/{row.pk}", {"value": "120"}, format="json")
        assert resp.status_code == 200, resp.data
        row.refresh_from_db()
        assert row.value == 120 and type(row.value) is int
        cleanup_config_rows("FILE_KEEP_DAYS")


class TestRegisteredKeysEndpoint:
    """注册键清单端点：配置页新增/编辑键时的键枚举提示数据源（结构元数据）。"""

    REGISTERED_KEYS_URL = "/api/system/config/system/registered-keys"

    def test_keys_sorted_with_expected_types(self, admin_client):
        resp = admin_client.get(self.REGISTERED_KEYS_URL)
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000
        keys = resp.data["data"]["keys"]
        names = [item["key"] for item in keys]
        assert names == sorted(names)
        by_key = {item["key"]: item["type"] for item in keys}
        assert by_key["WEB_SITE_CONFIG"] == "object"
        assert by_key["SLOW_REQUEST_THRESHOLD"] == "number"
        # 结构元数据只含键名与类型名，不含任何配置值
        assert all(set(item) == {"key", "type"} for item in keys)
        assert set(by_key.values()) <= {"boolean", "integer", "number", "string", "array", "object"}

    def test_permission_follows_parent_list(self, api_client, normal_user, role, menu_factory):
        """shared_list 口径：有 config/system list 权限可读，无权限拒绝。"""
        api_client.force_authenticate(user=normal_user)
        assert api_client.get(self.REGISTERED_KEYS_URL).status_code == 403
        role.menu.add(menu_factory(name="list:SystemConfig", path="api/system/config/system$", method="GET"))
        api_client.force_authenticate(user=normal_user)
        resp = api_client.get(self.REGISTERED_KEYS_URL)
        assert resp.status_code == 200, resp.data
        assert resp.data["code"] == 1000
