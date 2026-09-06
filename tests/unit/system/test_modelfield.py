# -*- coding: utf-8 -*-
"""system/utils/modelfield.py：字段 lookup 说明与模型字段同步。"""
import pytest

from system.utils.modelfield import get_field_lookup_info


def test_lookup_info_covers_known_lookups():
    fields = ["exact", "icontains", "in", "gt", "isnull"]
    result = get_field_lookup_info(fields)
    assert [r["value"] for r in result] == fields
    for item in result:
        assert item["label"]  # 已知 lookup 必须有翻译说明


def test_lookup_info_unknown_lookup_falls_back_to_name():
    result = get_field_lookup_info(["custom_lookup"])
    assert result == [{"value": "custom_lookup", "label": "custom_lookup"}]


def test_lookup_info_empty_fields():
    assert get_field_lookup_info([]) == []


@pytest.mark.django_db
class TestSyncModelField:
    def test_sync_model_field_creates_label_fields(self):
        from system.models import ModelLabelField
        from system.utils.modelfield import sync_model_field

        sync_model_field()
        # 数据权限维度：所有表/所有字段节点存在
        assert ModelLabelField.objects.filter(
            field_type=ModelLabelField.FieldChoices.DATA, parent=None
        ).exists()
        # 角色字段权限维度：按序列化器生成
        assert ModelLabelField.objects.filter(
            field_type=ModelLabelField.FieldChoices.ROLE
        ).exists()

        # 幂等：重复执行不报错且数量收敛（旧记录被清理）
        count = ModelLabelField.objects.count()
        sync_model_field()
        assert ModelLabelField.objects.count() <= count

    def test_get_app_model_fields_includes_system_models(self):
        from system.models import ModelLabelField
        from system.utils.modelfield import get_app_model_fields

        get_app_model_fields()
        names = set(
            ModelLabelField.objects.filter(
                field_type=ModelLabelField.FieldChoices.DATA
            ).values_list("name", flat=True)
        )
        assert "*" in names
        assert any(name.startswith("system.") for name in names)
