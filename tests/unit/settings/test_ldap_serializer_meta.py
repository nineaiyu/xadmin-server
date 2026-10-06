"""LDAP 设置序列化器字段元数据回归：下拉带 label、留空语义显式化。

回归守护三件事：
- 认证优先级 / 缺失用户策略两个字段必须以 LabeledChoiceField 下发——裸字符串
  choices 会让 API 元数据与前端下拉只显示机器值，retrieve 回显也不携带可读
  label；同时 value 取值集合必须与存量完全一致（落库值与既有请求兼容）；
- LabeledChoiceField 对裸值与 {value,label} 两种提交形态都要照常解析为原 value；
- 留空语义字段（留空回落默认值 / 留空不启用特性）的 label 与 help_text 必须显
  式存在，界面与 API 元数据据此区分两类留空含义，文案指向运行时真实行为。
"""

from common.core.fields import LabeledChoiceField
from settings.serializers.ldap import LdapSettingSerializer

LABELED_CHOICES = {
    "LDAP_AUTH_PRIORITY": {"local_first", "ldap_first"},
    "LDAP_SYNC_MISSING_POLICY": {"deactivate", "soft_delete", "ignore"},
}

# 留空语义字段 → 留空的真实运行含义（与运行时消费方一一对应，防文案漂移）
BLANK_SEMANTICS = {
    # write_only 密文：视图层跳过空值提交，留空 = 不修改（沿用已存密码）
    "LDAP_BIND_PASSWORD": "keep",
    # 保存链路 validate() 收敛默认值，运行时读取侧同默认兜底
    "LDAP_USER_FILTER": "default",
    "LDAP_ATTR_USERNAME": "default",
    "LDAP_ATTR_NICKNAME": "default",
    "LDAP_ATTR_EMAIL": "default",
    "LDAP_ATTR_PHONE": "default",
    # 部门搜索基为空时部门同步直接跳过（即便 LDAP_DEPT_ENABLED 开着）
    "LDAP_DEPT_SEARCH_BASE": "disabled",
    # 组成员属性为空时组匹配不命中，组→角色映射不生效
    "LDAP_ATTR_GROUPS": "disabled",
    # 映射为空时角色挂/撤逻辑整体短路，手工授权不受影响
    "LDAP_GROUP_ROLE_MAP": "disabled",
}


def _fields():
    return LdapSettingSerializer().fields


def test_choice_fields_use_labeled_choices():
    fields = _fields()
    for name, values in LABELED_CHOICES.items():
        field = fields[name]
        assert isinstance(field, LabeledChoiceField), name
        assert set(field.choices) == values, name
        for value in values:
            rendered = field.to_representation(value)
            assert rendered["value"] == value, name
            assert rendered["label"].strip(), name


def test_choice_fields_accept_plain_and_labeled_values():
    """存量请求兼容：裸字符串与 {value,label} 两种提交形态都解析为原 value。"""
    fields = _fields()
    for name, values in LABELED_CHOICES.items():
        field = fields[name]
        for value in values:
            assert field.to_internal_value(value) == value, name
            assert field.to_internal_value({"value": value, "label": "display"}) == value, name


def test_blank_semantic_fields_documented():
    fields = _fields()
    for name, semantic in BLANK_SEMANTICS.items():
        field = fields[name]
        assert str(field.label).strip(), name
        assert str(field.help_text).strip(), name
        assert semantic in ("keep", "default", "disabled"), name


def test_existing_payload_parse_unchanged():
    """既有合法载荷解析结果不变：choice 字段取原 value，留空字段收敛到默认值。"""
    payload = {
        "LDAP_AUTH_ENABLED": True,
        "LDAP_AUTH_PRIORITY": "local_first",
        "LDAP_AUTH_AUTO_CREATE": True,
        "LDAP_SERVER_URI": "ldap://directory.corp.com",
        "LDAP_START_TLS": False,
        "LDAP_BIND_DN": "cn=svc,dc=corp,dc=com",
        "LDAP_BIND_PASSWORD": "S3cret-Bind-Pwd",
        "LDAP_CONNECT_TIMEOUT": 10,
        "LDAP_USER_SEARCH_BASE": "dc=corp,dc=com",
        "LDAP_DEPT_ENABLED": True,
        "LDAP_DEPT_SEARCH_BASE": "ou=depts,dc=corp,dc=com",
        "LDAP_SYNC_ENABLED": True,
        "LDAP_SYNC_AUTO_CREATE": True,
        "LDAP_SYNC_MISSING_POLICY": "deactivate",
        "LDAP_ATTR_GROUPS": "memberOf",
        "LDAP_GROUP_ROLE_MAP": {"cn=ldap-admins,dc=corp,dc=com": "admin"},
    }
    validated = LdapSettingSerializer().run_validation(payload)
    assert validated["LDAP_AUTH_PRIORITY"] == "local_first"
    assert validated["LDAP_SYNC_MISSING_POLICY"] == "deactivate"
    assert validated["LDAP_GROUP_ROLE_MAP"] == {"cn=ldap-admins,dc=corp,dc=com": "admin"}
    # 未提交的 filter / 属性映射字段由 validate() 收敛到默认值
    assert validated["LDAP_USER_FILTER"] == "(objectClass=person)"
    assert validated["LDAP_ATTR_USERNAME"] == "sAMAccountName"
    assert validated["LDAP_ATTR_NICKNAME"] == "cn"
    assert validated["LDAP_ATTR_EMAIL"] == "mail"
    assert validated["LDAP_ATTR_PHONE"] == "telephoneNumber"


def test_blank_values_still_converge_to_defaults():
    """留空提交的 filter / 属性字段保存时收敛为默认值，choice 字段不受空映射键影响。"""
    validated = LdapSettingSerializer().run_validation({"LDAP_USER_FILTER": "", "LDAP_ATTR_USERNAME": ""})
    assert validated["LDAP_USER_FILTER"] == "(objectClass=person)"
    assert validated["LDAP_ATTR_USERNAME"] == "sAMAccountName"
