# -*- coding: utf-8 -*-
"""system/serializers/role.py FieldPermissionSerializer / RoleSerializer 单元测试。

验证字段权限的序列化/反序列化、RoleSerializer.save_fields 建立关系、update
替换旧关系，以及 get_field 输出的 {menu: [field,...]} 结构。
"""
import pytest

from system.models import FieldPermission, Menu, ModelLabelField, UserRole
from system.serializers.role import FieldPermissionSerializer, RoleSerializer

pytestmark = pytest.mark.django_db


@pytest.fixture
def field_tree(db):
    """返回 (parent, [children])，模拟某个模型的字段标签树。"""

    def _build(model="demo.book"):
        parent = ModelLabelField.objects.create(
            name=model, label="Book", field_type=ModelLabelField.FieldChoices.ROLE
        )
        children = [
            ModelLabelField.objects.create(
                name=n, label=n, parent=parent, field_type=ModelLabelField.FieldChoices.ROLE
            )
            for n in ["pk", "name", "isbn"]
        ]
        return parent, children

    return _build


class TestFieldPermissionSerializer:
    def test_serialize_field_permission(self, role, menu_factory, field_tree):
        menu = menu_factory("menu", menu_type=Menu.MenuChoices.MENU)
        _, children = field_tree()
        fp = FieldPermission.objects.create(role=role, menu=menu)
        fp.field.add(*children)

        data = FieldPermissionSerializer(fp, ignore_field_permission=True).data
        assert set(data.keys()) == {"pk", "role", "menu", "field"}
        assert data["role"]["pk"] == role.pk
        assert data["menu"]["pk"] == menu.pk
        assert {item["pk"] for item in data["field"]} == {c.pk for c in children}

    def test_deserialize_creates_field_permission(self, role, menu_factory, field_tree):
        menu = menu_factory("menu", menu_type=Menu.MenuChoices.MENU)
        _, children = field_tree()
        serializer = FieldPermissionSerializer(
            data={"role": role.pk, "menu": menu.pk, "field": [c.pk for c in children]}
        )
        assert serializer.is_valid(), serializer.errors
        fp = serializer.save()
        assert FieldPermission.objects.filter(role=role, menu=menu).exists()
        assert set(fp.field.values_list("pk", flat=True)) == {c.pk for c in children}


class TestRoleSerializer:
    def test_save_fields_builds_field_permission(self, role, menu_factory, field_tree):
        menu = menu_factory("menu", menu_type=Menu.MenuChoices.MENU)
        _, children = field_tree()
        RoleSerializer().save_fields({str(menu.pk): [children[0].pk]}, role)
        fp = FieldPermission.objects.get(role=role, menu=menu)
        assert set(fp.field.values_list("pk", flat=True)) == {children[0].pk}

    def test_get_field_returns_menu_field_structure(self, role, menu_factory, field_tree):
        """get_field 输出 {menu: [field,...]} 结构，键为菜单序列化表示的字符串。"""
        menu = menu_factory("menu", menu_type=Menu.MenuChoices.MENU)
        _, children = field_tree()
        fp = FieldPermission.objects.create(role=role, menu=menu)
        fp.field.add(*children)

        data = RoleSerializer().get_field(role)
        assert isinstance(data, dict)
        assert len(data) == 1
        key = next(iter(data.keys()))
        assert str(menu.pk) in key
        value = data[key]
        assert isinstance(value, list)
        assert {item["pk"] for item in value} == {c.pk for c in children}

    def test_create_with_fields_creates_field_permissions(self, menu_factory, field_tree):
        menu = menu_factory("menu", menu_type=Menu.MenuChoices.MENU)
        _, children = field_tree()
        serializer = RoleSerializer(
            data={"name": "编辑", "code": "editor", "fields": {str(menu.pk): [c.pk for c in children]}}
        )
        assert serializer.is_valid(), serializer.errors
        role = serializer.save()

        assert UserRole.objects.filter(pk=role.pk).exists()
        fp = FieldPermission.objects.get(role=role, menu=menu)
        assert set(fp.field.values_list("pk", flat=True)) == {c.pk for c in children}

    def test_update_replaces_old_field_permissions(self, role, menu_factory, field_tree):
        menu1 = menu_factory("menu1", menu_type=Menu.MenuChoices.MENU)
        menu2 = menu_factory("menu2", menu_type=Menu.MenuChoices.MENU)
        _, children = field_tree()
        init = FieldPermissionSerializer(data={"role": role.pk, "menu": menu1.pk, "field": [children[0].pk]})
        assert init.is_valid(), init.errors
        init.save()
        assert FieldPermission.objects.filter(role=role, menu=menu1).exists()

        serializer = RoleSerializer(instance=role, data={"fields": {str(menu2.pk): [children[1].pk]}}, partial=True)
        assert serializer.is_valid(), serializer.errors
        serializer.save()

        # 旧关系被清空，仅保留新的 menu2 关系
        assert not FieldPermission.objects.filter(role=role, menu=menu1).exists()
        fp = FieldPermission.objects.get(role=role, menu=menu2)
        assert {children[1].pk} == set(fp.field.values_list("pk", flat=True))