# -*- coding: utf-8 -*-
"""数据权限规则元数据下发与生效范围归一的单元测试。

覆盖：
- rule_meta 的类型元数据（组 / 值控件 / 默认匹配符）与类型清单一一对应；
- 页面/目录菜单 → 接口权限点的展开（生效范围存储口径）；
- 序列化器 validate_menu 的展开与空展开拒绝；
- 列表统计字段（规则数 / 接口数 / 分配用户数 / 分配部门数）；
- 字段形态元数据（get_field_meta）与 choices / lookups 接口下发。
"""

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from system.models import DataPermission, Menu, ModelLabelField
from system.serializers.permission import DataPermissionSerializer, expand_menu_scope
from system.utils.modelfield import get_field_meta
from system.utils.rule_meta import RULE_TYPE_GROUP_TEXTS, RULE_TYPE_META, RULE_TYPE_TEXTS
from system.views.admin.modelfield import ModelLabelFieldViewSet

pytestmark = pytest.mark.django_db


def make_rule(field="creator", type_="value.user.id", value="", match="exact", table="*"):
    return {"table": table, "field": field, "type": type_, "value": value, "match": match}


class TestRuleTypeMeta:
    def test_meta_covers_every_choice(self):
        assert set(RULE_TYPE_META) == {choice for choice, _ in ModelLabelField.KeyChoices.choices}

    def test_texts_groups_and_defaults_aligned(self):
        assert set(RULE_TYPE_META) == set(RULE_TYPE_TEXTS)
        for value, meta in RULE_TYPE_META.items():
            assert meta["group"] in RULE_TYPE_GROUP_TEXTS, value
            assert isinstance(meta["value_required"], bool), value
            assert meta["default_match"], value
            # 运行期注入值的类型配置端无需填写 value，两者必须一致
            assert meta["value_required"] is (meta["input"] != "none"), value


class TestExpandMenuScope:
    def test_permission_menu_kept(self, menu_factory):
        perm = menu_factory("list:SystemUser", path="api/system/user$", method="GET")
        assert expand_menu_scope([perm]) == [perm]

    def test_page_expands_to_permission_descendants(self, menu_factory):
        page = menu_factory("用户管理", menu_type=Menu.MenuChoices.MENU)
        perm = menu_factory("list:SystemUser", path="api/system/user$", method="GET", parent=page)
        nested_dir = menu_factory("接口分组", menu_type=Menu.MenuChoices.DIRECTORY, parent=page)
        nested_perm = menu_factory("create:SystemUser", path="api/system/user$", method="POST", parent=nested_dir)

        result = expand_menu_scope([page])
        assert {menu.pk for menu in result} == {perm.pk, nested_perm.pk}

    def test_scope_deduplicated(self, menu_factory):
        page = menu_factory("用户管理", menu_type=Menu.MenuChoices.MENU)
        perm = menu_factory("list:SystemUser", path="api/system/user$", method="GET", parent=page)

        result = expand_menu_scope([page, perm])
        assert [menu.pk for menu in result] == [perm.pk]

    def test_page_without_permission_expands_empty(self, menu_factory):
        page = menu_factory("空页面", menu_type=Menu.MenuChoices.MENU)
        assert expand_menu_scope([page]) == []


class TestSerializerScope:
    def test_validate_menu_expands_page(self, menu_factory):
        page = menu_factory("用户管理", menu_type=Menu.MenuChoices.MENU)
        perm = menu_factory("list:SystemUser", path="api/system/user$", method="GET", parent=page)

        serializer = DataPermissionSerializer()
        result = serializer.validate_menu([page])
        assert [menu.pk for menu in result] == [perm.pk]

    def test_validate_menu_rejects_empty_expansion(self, menu_factory):
        page = menu_factory("空页面", menu_type=Menu.MenuChoices.MENU)
        serializer = DataPermissionSerializer()
        with pytest.raises(Exception) as exc:
            serializer.validate_menu([page])
        assert "permission" in str(exc.value).lower()

    def test_validate_menu_keeps_empty_scope(self):
        serializer = DataPermissionSerializer()
        assert serializer.validate_menu([]) == []

    def test_scope_count_fields(self, normal_user, dept, menu_factory):
        perm = menu_factory("list:SystemUser", path="api/system/user$", method="GET")
        dp = DataPermission.objects.create(name="范围统计", rules=[make_rule()])
        dp.menu.add(perm)
        dp.userinfo_set.add(normal_user)
        dp.deptinfo_set.add(dept)

        data = DataPermissionSerializer(dp).data
        assert data["rule_count"] == 1
        assert data["menu_count"] == 1
        assert data["user_count"] == 1
        assert data["dept_count"] == 1

    def test_scope_count_uses_annotation(self, normal_user, menu_factory):
        """列表注解存在时零查询（不触发关系 count）。"""
        dp = DataPermission.objects.create(name="注解优先", rules=[make_rule()])
        dp.scope_menu_count = 7
        dp.scope_user_count = 3
        dp.scope_dept_count = 2

        data = DataPermissionSerializer(dp).data
        assert (data["menu_count"], data["user_count"], data["dept_count"]) == (7, 3, 2)


class TestFieldMeta:
    def test_relation_field_meta(self):
        from system.models import UserInfo

        field = UserInfo._meta.get_field("dept")
        meta = get_field_meta(field)
        assert meta["internal_type"] == "ForeignKey"
        assert meta["related_model"] == "system.deptinfo"
        assert meta["multiple"] is False
        assert meta["null"] is True

    def test_m2m_field_meta(self):
        from system.models import UserInfo

        meta = get_field_meta(UserInfo._meta.get_field("rules"))
        assert meta["multiple"] is True
        assert meta["related_model"] == "system.datapermission"


class TestFieldApiMetadata:
    @staticmethod
    def _get(action, path, user, **params):
        factory = APIRequestFactory()
        request = factory.get(path, params)
        force_authenticate(request, user=user)
        view = ModelLabelFieldViewSet.as_view({"get": action})
        return view(request)

    def test_choices_exposes_control_meta(self, superuser):
        response = self._get("choices_dict", "/api/system/field/choices", superuser)
        payload = response.data["choices_dict"]
        choices = {item["value"]: item for item in payload["choices"]}
        assert payload["groups"] == RULE_TYPE_GROUP_TEXTS
        # 匹配符文案与预览解码同源（前端规则摘要直接消费）
        assert payload["matches"]["in"] == "属于"
        assert choices["value.user.id"]["input"] == "none"
        assert choices["value.user.id"]["value_required"] is False
        assert choices["value.table.user.ids"]["input"] == "user"
        assert choices["value.date"]["default_match"] == "gte"
        # 全部类型都下发控件元数据，且不再有被禁用的类型
        assert all("input" in item for item in choices.values())
        assert not any(item.get("disabled") for item in choices.values())

    def test_lookups_exposes_field_meta(self, superuser):
        parent = ModelLabelField.objects.create(name="system.userinfo", label="用户信息")
        ModelLabelField.objects.create(name="dept", label="部门", parent=parent)

        response = self._get(
            "lookups",
            "/api/system/field/lookups",
            superuser,
            table="system.userinfo",
            field="dept",
        )
        assert response.data["code"] == 1000
        assert response.data["field_meta"]["internal_type"] == "ForeignKey"
        assert response.data["field_meta"]["related_model"] == "system.deptinfo"

    def test_lookups_unknown_field_returns_business_error(self, superuser):
        response = self._get(
            "lookups",
            "/api/system/field/lookups",
            superuser,
            table="system.notexists",
            field="whatever",
        )
        assert response.data["code"] == 1001
