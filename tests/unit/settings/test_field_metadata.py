# -*- coding: utf-8 -*-
"""设置页字段类型元数据：DictField 必须下发为可编辑 JSON。

回归守护：DRF 对 ``DictField`` 的默认元数据是 ``"nested object"``，前端渲染器
注册表没有该键，会回退成纯文本输入框并把对象值字符串化成 ``[object Object]``
（LDAP 组到角色映射页实际表现）。DictField 语义是自由键值 JSON，统一按 ``json``
下发，复用 JSON 编辑器（含读写回环）。
"""

import pytest

from common.drf.metadata import SimpleMetadataWithFilters
from settings.serializers.ldap import LdapSettingSerializer

pytestmark = pytest.mark.django_db


def test_ldap_group_role_map_renders_as_json():
    metadata = SimpleMetadataWithFilters()
    field = LdapSettingSerializer().fields["LDAP_GROUP_ROLE_MAP"]
    assert field.__class__.__name__ == "DictField"
    assert metadata.get_field_type(field) == "json"
    assert metadata.get_field_info(field)["type"] == "json"


def test_plain_dict_field_renders_as_json():
    """任意 DictField（含 HStoreField 子类）口径一致，不依赖具体业务字段名。"""
    from rest_framework import serializers

    metadata = SimpleMetadataWithFilters()
    field = serializers.DictField(child=serializers.CharField())
    assert metadata.get_field_type(field) == "json"
