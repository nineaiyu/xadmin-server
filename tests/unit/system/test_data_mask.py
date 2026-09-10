# -*- coding: utf-8 -*-
"""字段级数据脱敏：apply_mask 纯函数 / 规则缓存与失效 / 序列化器输出掩码 / 管理接口。"""

import pytest
from django.test import RequestFactory, override_settings
from unittest.mock import patch

from server.utils import set_current_request
from system.models import DataMaskRule, UserInfo
from system.serializers.userinfo import UserInfoSerializer
from system.utils.mask import apply_mask, get_mask_rules, invalid_mask_cache
from system.views.admin.mask import PREVIEW_MAX_VALUE_LENGTH, PREVIEW_MAX_VALUES

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


def _make_request_with_params(user, params=None, path="/api/system/user/"):
    """带查询参数的请求（?mask=false 原文通道用例）。"""
    request = RequestFactory().get(path, params or {})
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
        """GET 详情请求 + 该地址的 PUT 权限 → 原文；无更新权限 → 仍掩码。

        同一 path 的 GET / PUT / PATCH 是三条主键不同的菜单，门禁必须按**请求地址**
        判定：按菜单主键比对时 GET 详情请求永远命中不了更新权限菜单（通道恒关）。
        """
        from django.core.cache import cache

        normal_user.phone = PHONE
        normal_user.save()
        _rule()
        detail_url = f"/api/system/user/{normal_user.pk}"
        perm = menu_factory("用户更新", path="api/system/user/(?P<pk>[^/.]+)$", method="PUT")
        role.menu.add(perm)
        # 权限查询按 {user.pk}_{method} 缓存 24h，跨用例复用需先清理
        cache.clear()

        _make_request_with_params(normal_user, {"mask": "false"}, path=detail_url)
        assert _serialize(normal_user)["phone"] == PHONE

        # 无更新权限（角色未授予 PUT 菜单）：原文通道不放行
        role.menu.remove(perm)
        cache.clear()
        _make_request_with_params(normal_user, {"mask": "false"}, path=detail_url)
        assert _serialize(normal_user)["phone"] == "138******78"

        # 未显式请求原文：即使有更新权限也掩码（列表/详情/导出口径不变）
        role.menu.add(perm)
        cache.clear()
        _make_request_with_params(normal_user, path=detail_url)
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

    def test_original_channel_access_audited(self, normal_user, role, menu_factory):
        """原文通道放行时记审计日志：谁 / 哪个模型，且**每请求只记一次**。"""
        from django.core.cache import cache

        normal_user.phone = PHONE
        normal_user.save()
        _rule()
        perm = menu_factory("用户更新", path="api/system/user/(?P<pk>[^/.]+)$", method="PUT")
        role.menu.add(perm)
        cache.clear()

        request = _make_request_with_params(normal_user, {"mask": "false"}, path=f"/api/system/user/{normal_user.pk}")
        other = UserInfo.objects.create_user(username="audit_target", password="Test@123456")
        with patch("system.utils.mask.logger") as mock_logger:
            UserInfoSerializer([normal_user, other], many=True, context={"request": request}).data
        mock_logger.warning.assert_called_once()
        _, audited_user_pk, _path, model_label = mock_logger.warning.call_args[0]
        assert audited_user_pk == normal_user.pk
        assert model_label == "system.userinfo"

        # 未走原文通道（无 ?mask=false）不记审计
        _make_request_with_params(normal_user)
        with patch("system.utils.mask.logger") as mock_logger_plain:
            _serialize(normal_user)
        mock_logger_plain.warning.assert_not_called()

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


class TestMaskOriginalChannelAPI:
    """原文通道的真实 HTTP 链路（权限解析 → 序列化器豁免）。

    单测里直接构造 request 会绕开 IsAuthenticated 的菜单解析，掩盖
    「GET 请求 + 该地址更新权限」这一真实组合，故补一条端到端请求用例。
    """

    @override_settings(PERMISSION_FIELD_ENABLED=False)
    def test_detail_request_returns_original_with_update_permission(self, normal_user, role, menu_factory):
        """字段权限单独关闭（与本期关注点正交）：聚焦原文通道门禁本身。"""
        from django.core.cache import cache
        from rest_framework.test import APIClient

        from system.models import DataPermission

        normal_user.phone = PHONE
        normal_user.save()
        _rule()
        # 数据权限默认拒绝：列表/详情需要显式「全部数据」规则才可见
        dp = DataPermission.objects.create(
            name="E2E-全部用户数据",
            rules=[
                {
                    "table": "system.userinfo",
                    "field": "id",
                    "type": "value.all",
                    "match": "",
                    "value": "",
                    "exclude": False,
                }
            ],
            mode_type=DataPermission.ModeChoices.OR,
            is_active=True,
        )
        dp.menu.clear()
        normal_user.rules.add(dp)
        role.menu.add(
            menu_factory("用户列表", path="api/system/user$", method="GET"),
            menu_factory("用户详情", path="api/system/user/(?P<pk>[^/.]+)$", method="GET"),
            menu_factory("用户更新", path="api/system/user/(?P<pk>[^/.]+)$", method="PATCH"),
        )
        cache.clear()

        client = APIClient()
        client.force_authenticate(user=normal_user)

        resp = client.get(f"/api/system/user/{normal_user.pk}?mask=false")
        assert resp.status_code == 200
        assert resp.data["data"]["phone"] == PHONE

        # 未显式请求原文：即使有更新权限也掩码（列表/详情/导出口径不变）
        resp_masked = client.get(f"/api/system/user/{normal_user.pk}")
        assert resp_masked.data["data"]["phone"] == "138******78"


class TestMaskRuleAPI:
    def test_preview_action(self, auth_client):
        resp = auth_client.post(
            "/api/system/mask-rules/preview",
            {"value": PHONE, "rule": {"mask_type": "phone", "keep_head": 3, "keep_tail": 4}},
            format="json",
        )
        assert resp.data["code"] == 1000
        assert resp.data["data"]["result"] == "138****5678"

    def test_preview_batch_values(self, auth_client):
        """批量样例：逐条 results，result 保留首条（单值调用向后兼容）。"""
        resp = auth_client.post(
            "/api/system/mask-rules/preview",
            {
                "values": [PHONE, "13900000000"],
                "rule": {"mask_type": "phone", "keep_head": 3, "keep_tail": 4},
            },
            format="json",
        )
        assert resp.data["code"] == 1000
        data = resp.data["data"]
        assert [item["input"] for item in data["results"]] == [PHONE, "13900000000"]
        assert data["results"][0]["output"] == "138****5678"
        assert data["result"] == data["results"][0]["output"]
        assert data["truncated"] is False

    def test_preview_value_newline_split(self, auth_client):
        """value 内含换行时按行拆分（尾随换行不产生空样例）。"""
        resp = auth_client.post(
            "/api/system/mask-rules/preview",
            {
                "value": f"{PHONE}\n13900000000\n",
                "rule": {"mask_type": "phone", "keep_head": 3, "keep_tail": 4},
            },
            format="json",
        )
        assert resp.data["code"] == 1000
        assert [item["input"] for item in resp.data["data"]["results"]] == [PHONE, "13900000000"]

    def test_preview_truncates_over_limit(self, auth_client):
        """样例条数超上限截断；单条超长截断后仍可脱敏。"""
        resp = auth_client.post(
            "/api/system/mask-rules/preview",
            {
                "values": [PHONE] * (PREVIEW_MAX_VALUES + 5),
                "rule": {"mask_type": "phone", "keep_head": 3, "keep_tail": 4},
            },
            format="json",
        )
        data = resp.data["data"]
        assert len(data["results"]) == PREVIEW_MAX_VALUES
        assert data["truncated"] is True

        long_resp = auth_client.post(
            "/api/system/mask-rules/preview",
            {"values": ["1" * (PREVIEW_MAX_VALUE_LENGTH + 10)], "rule": {"mask_type": "phone"}},
            format="json",
        )
        long_data = long_resp.data["data"]
        assert long_data["truncated"] is True
        assert len(long_data["results"][0]["input"]) == PREVIEW_MAX_VALUE_LENGTH

    def test_preview_invalid_values_type(self, auth_client):
        """values 非数组：返回可读校验失败，而不是 500。"""
        resp = auth_client.post(
            "/api/system/mask-rules/preview",
            {"values": "not-a-list", "rule": {"mask_type": "phone"}},
            format="json",
        )
        assert resp.status_code == 400

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
