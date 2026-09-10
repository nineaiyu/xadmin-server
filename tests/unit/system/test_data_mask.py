# -*- coding: utf-8 -*-
"""字段级数据脱敏：apply_mask 纯函数 / 规则缓存与失效 / 序列化器输出掩码 / 管理接口。"""

import pytest
from django.test import RequestFactory

from server.utils import set_current_request
from system.models import DataMaskRule, UserInfo
from system.serializers.userinfo import UserInfoSerializer
from system.utils.mask import apply_mask, get_mask_rules, invalid_mask_cache

pytestmark = pytest.mark.django_db

PHONE = "13812345678"


def _make_request(user, fields=None):
    request = RequestFactory().get("/api/system/user/")
    request.user = user
    request.fields = fields or {"system.userinfo": ["pk", "username", "nickname", "phone", "email"]}
    set_current_request(request)
    return request


def _serialize(user, instance=None, request=None, **kwargs):
    return UserInfoSerializer(instance or user, context={"request": request}, **kwargs).data


def _make_request_with_params(user, params=None):
    """带查询参数的请求（?mask=false 原文通道用例）。"""
    request = RequestFactory().get("/api/system/user/", params or {})
    request.user = user
    request.fields = {"system.userinfo": ["pk", "username", "nickname", "phone", "email"]}
    set_current_request(request)
    return request


def _rule(model="system.userinfo", field="phone", mask_type="phone", sort=0, **extra):
    rule = DataMaskRule.objects.create(model=model, field=field, mask_type=mask_type, sort=sort, **extra)
    return rule


class TestApplyMask:
    """纯函数六种掩码 + 边界。"""

    def test_phone_segment(self):
        rule = {"mask_type": "phone", "keep_head": 3, "keep_tail": 4}
        assert apply_mask(PHONE, rule) == "138****5678"

    def test_custom_keep_head_tail(self):
        rule = {"mask_type": "name", "keep_head": 1, "keep_tail": 1}
        assert apply_mask("张三丰", rule) == "张*丰"
        assert apply_mask("欧阳锋", rule) == "欧*锋"

    def test_custom_mask_char(self):
        rule = {"mask_type": "phone", "keep_head": 3, "keep_tail": 2, "mask_char": "%"}
        assert apply_mask(PHONE, rule) == "138%%%%%%78"

    def test_email_masks_local_only(self):
        rule = {"mask_type": "email", "keep_head": 1, "keep_tail": 0}
        assert apply_mask("ab@example.com", rule) == "a*@example.com"

    def test_custom_regex_pattern(self):
        rule = {"mask_type": "custom", "keep_head": 1, "keep_tail": 1, "pattern": r"\d{4}"}
        assert apply_mask("1234", rule) == "1**4"

    def test_custom_illegal_regex_returns_original(self):
        rule = {"mask_type": "custom", "keep_head": 1, "keep_tail": 1, "pattern": "("}
        assert apply_mask("1381234", rule) == "1381234"

    def test_short_value_returns_original(self):
        rule = {"mask_type": "phone", "keep_head": 3, "keep_tail": 4}
        assert apply_mask("138", rule) == "138"

    def test_empty_and_none_and_non_str(self):
        rule = {"mask_type": "phone", "keep_head": 3, "keep_tail": 4}
        assert apply_mask("", rule) == ""
        assert apply_mask(None, rule) is None
        assert apply_mask(12345, rule) == 12345


class TestMaskRulesCache:
    def test_load_active_rules_sorted_by_sort(self):
        _rule(model="system.userinfo", field="email", mask_type="email", sort=2)
        _rule(model="system.userinfo", field="phone", mask_type="phone", sort=1)
        _rule(model="system.userinfo", field="phone", mask_type="phone", sort=0, is_active=False)
        rules = get_mask_rules("system.userinfo")
        assert [r["field"] for r in rules] == ["phone", "email"]
        assert rules[0].get("roles") == []

    def test_post_save_signal_invalidates_cache(self):
        rule = _rule()
        assert get_mask_rules("system.userinfo")  # 预热缓存
        rule.is_active = False
        rule.save()
        assert get_mask_rules("system.userinfo") == []

    def test_roles_m2m_changed_invalidates_cache(self, role):
        rule = _rule()
        get_mask_rules("system.userinfo")  # 预热
        rule.roles.add(role)
        rules = get_mask_rules("system.userinfo")
        assert rules[0]["roles"] == [role.pk]

    def test_invalid_mask_cache_by_model(self):
        _rule(model="system.message", field="content", mask_type="name")
        assert get_mask_rules("system.message")  # 预热缓存
        invalid_mask_cache("system.message")
        from django.core.cache import cache

        assert cache.get("data_mask_system.message") is None  # 键已被显式失效
        # 规则删除后（pre_delete 信号失效）重新加载为空
        DataMaskRule.objects.filter(model="system.message").delete()
        assert get_mask_rules("system.message") == []


class TestSerializerMasking:
    def test_normal_user_phone_masked(self, normal_user):
        normal_user.phone = PHONE
        normal_user.save()
        _rule()  # 模型默认 keep_head=3 / keep_tail=2
        _make_request(normal_user)
        data = _serialize(normal_user)
        assert data["phone"] == "138******78"

    def test_superuser_exempt(self, superuser):
        superuser.phone = PHONE
        superuser.save()
        _rule()
        _make_request(superuser)
        data = _serialize(superuser)
        assert data["phone"] == PHONE

    def test_ignore_field_permission_exempt(self, normal_user):
        """编辑原文通道：ignore_field_permission 请求输出原文（前端编辑表单原文来源）。"""
        normal_user.phone = PHONE
        normal_user.save()
        _rule()
        _make_request(normal_user)
        data = UserInfoSerializer(normal_user, ignore_field_permission=True).data
        assert data["phone"] == PHONE
        # 普通通道仍掩码（对比保障）
        assert _serialize(normal_user)["phone"] == "138******78"

    def test_rule_restricted_by_roles(self, normal_user, role):
        normal_user.phone = PHONE
        normal_user.email = "nobody@example.com"
        normal_user.save()
        # phone 掩码仅对 role 生效；email 掩码（keep_head=1）无角色限制、对所有非超管生效
        _rule(field="phone", mask_type="phone", sort=0).roles.add(role)
        _rule(field="email", mask_type="email", keep_head=1, sort=1)
        user_no_role = UserInfo.objects.create_user(username="lisi", password="Test@123456")
        user_no_role.phone = PHONE
        user_no_role.email = "nobody@example.com"
        user_no_role.save()
        _make_request(user_no_role)
        data = _serialize(user_no_role)
        assert data["phone"] == PHONE  # 无该角色 → 不掩码
        assert data["email"] == "n*****@example.com"  # 无角色限制规则生效
        _make_request(normal_user)
        data2 = _serialize(normal_user)
        assert data2["phone"] == "138******78"  # 命中角色 → 掩码
        assert data2["email"] == "n*****@example.com"

    def test_multi_rule_smallest_sort_wins(self, normal_user, role):
        normal_user.phone = PHONE
        normal_user.save()
        _rule(mask_type="phone", keep_head=3, keep_tail=4, sort=0).roles.add(role)
        _rule(mask_type="name", keep_head=1, keep_tail=1, sort=1)
        _make_request(normal_user)
        data = _serialize(normal_user)
        # 命中 sort=0（phone 掩码）→ 保留 3+4；不被 sort=1 的 name 规则覆盖
        assert data["phone"] == "138****5678"

    def test_write_path_keeps_original(self, normal_user):
        """只脱敏输出不脱敏输入：序列化后掩码不影响 DB 原值。"""
        normal_user.phone = PHONE
        normal_user.save()
        _rule()
        _make_request(normal_user)
        data = _serialize(normal_user)
        assert data["phone"] == "138******78"
        normal_user.refresh_from_db()
        assert normal_user.phone == PHONE


class TestMaskOriginalChannel:
    """原文通道（?mask=false + 更新权限）与掩码回写守护。"""

    def test_original_channel_gated_by_update_permission(self, normal_user, role, menu_factory):
        """有该菜单更新权限 → 原文；无更新权限 → 仍掩码。"""
        from django.core.cache import cache

        normal_user.phone = PHONE
        normal_user.save()
        _rule()
        perm = menu_factory("用户更新", path="api/system/user/", method="PUT")
        role.menu.add(perm)
        normal_user.menu = perm.pk
        # 权限查询按 {user.pk}_{method} 缓存 24h，跨用例复用需先清理
        cache.clear()

        _make_request_with_params(normal_user, {"mask": "false"})
        assert _serialize(normal_user)["phone"] == PHONE

        # 无更新权限（menu 未匹配）：原文通道不放行
        normal_user.menu = None
        _make_request_with_params(normal_user, {"mask": "false"})
        assert _serialize(normal_user)["phone"] == "138******78"

        # 未显式请求原文：即使有更新权限也掩码（列表/详情/导出口径不变）
        normal_user.menu = perm.pk
        _make_request_with_params(normal_user)
        assert _serialize(normal_user)["phone"] == "138******78"

    def test_masked_writeback_dropped(self, normal_user):
        """掩码回写守护：编辑表单原样提交掩码值时不覆盖库内原文。

        用可写字段 nickname（UserInfoSerializer 的 phone 为 read_only，写路径不适用）。
        """
        normal_user.nickname = "张三丰"
        normal_user.save()
        _rule(field="nickname", mask_type="name", keep_head=1, keep_tail=1)
        _make_request(normal_user)
        assert _serialize(normal_user)["nickname"] == "张*丰"

        serializer = UserInfoSerializer(normal_user, data={"nickname": "张*丰"}, partial=True)
        assert serializer.is_valid(), serializer.errors
        serializer.save()
        normal_user.refresh_from_db()
        assert normal_user.nickname == "张三丰"  # 掩码值被丢弃

    def test_real_change_still_written(self, normal_user):
        """真实改动（提交值 != 掩码结果）照常写入。"""
        normal_user.nickname = "张三丰"
        normal_user.save()
        _rule(field="nickname", mask_type="name", keep_head=1, keep_tail=1)
        _make_request(normal_user)

        serializer = UserInfoSerializer(normal_user, data={"nickname": "李四光"}, partial=True)
        assert serializer.is_valid(), serializer.errors
        serializer.save()
        normal_user.refresh_from_db()
        assert normal_user.nickname == "李四光"


class TestMaskRuleAPI:
    def test_preview_action(self, auth_client):
        resp = auth_client.post(
            "/api/system/mask-rules/preview",
            {"value": PHONE, "rule": {"mask_type": "phone", "keep_head": 3, "keep_tail": 4}},
            format="json",
        )
        assert resp.data["code"] == 1000
        assert resp.data["data"]["result"] == "138****5678"

    def test_crud_smoke(self, auth_client):
        resp = auth_client.post(
            "/api/system/mask-rules",
            {"model": "system.userinfo", "field": "phone", "mask_type": "phone", "keep_head": 3, "keep_tail": 4},
            format="json",
        )
        assert resp.data["code"] == 1000
        pk = resp.data["data"]["pk"]
        listing = auth_client.get("/api/system/mask-rules")
        assert listing.data["code"] == 1000
        assert any(row["pk"] == pk for row in listing.data["data"]["results"])
        deleted = auth_client.delete(f"/api/system/mask-rules/{pk}")
        assert deleted.data["code"] == 1000
        assert not DataMaskRule.objects.filter(pk=pk).exists()
