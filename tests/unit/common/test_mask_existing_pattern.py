# -*- coding: utf-8 -*-
"""存量脏脱敏 pattern 的输出侧行为：fail-closed，不再原值放行。

背景：``apply_mask`` 对非法 / 空 custom pattern 的直接调用保持原值返回（纯函数
兼容口径，另由 test_data_mask.py 钉住）；但**输出链路的规则统一经
``get_mask_rules`` 加载**——存量脏 pattern 在加载层降级为分段掩码，序列化器 /
数据集 / 操作日志 changes 等输出面因此不再把敏感字段原文放行。
"""

import pytest
from django.test import RequestFactory

from audit.models import DataMaskRule
from audit.utils.mask import get_mask_rules, mask_operation_changes
from common.core.mask import apply_output_mask
from identity.models import UserInfo

pytestmark = pytest.mark.django_db

PHONE = "13812345678"


def _make_request(user, params=None):
    request = RequestFactory().get("/api/identity/user/", params or {})
    request.user = user
    return request


def _rule(pattern, mask_type="custom", field="phone", keep_head=3, keep_tail=2, **extra):
    """直写库建规则：绕过序列化器，模拟保存期校验上线前的存量脏配置。"""
    return DataMaskRule.objects.create(
        model="identity.userinfo",
        field=field,
        mask_type=mask_type,
        pattern=pattern,
        keep_head=keep_head,
        keep_tail=keep_tail,
        **extra,
    )


class TestExistingPatternFailClosed:
    def test_invalid_pattern_degraded_to_segment_mask(self):
        """存量非法正则：加载层降级为分段掩码（规则形态）。"""
        _rule("(")
        rules = get_mask_rules("identity.userinfo")
        assert rules[0]["mask_type"] == "name"
        assert rules[0]["field"] == "phone"

    def test_empty_pattern_degraded_to_segment_mask(self):
        """空 pattern 的 custom 规则（save 期放行过的存量形态）同样降级。"""
        _rule("")
        assert get_mask_rules("identity.userinfo")[0]["mask_type"] == "name"

    def test_catastrophic_backtracking_pattern_degraded(self):
        """灾难性回溯形态（嵌套量词）：降级，不在输出链路执行用户正则。"""
        _rule(r"(a+)+$")
        assert get_mask_rules("identity.userinfo")[0]["mask_type"] == "name"

    def test_valid_custom_pattern_unchanged(self, normal_user):
        """合法 pattern 不降级：custom 掩码按正则生效（对比「不误伤」面）。"""
        _rule(r"1[3-9]\d{9}")
        rules = get_mask_rules("identity.userinfo")
        assert rules[0]["mask_type"] == "custom"

        data = {"phone": PHONE}
        apply_output_mask(data, _make_request(normal_user), normal_user, UserInfo)
        assert data["phone"] == "138******78"

    def test_output_mask_masks_value_for_dirty_pattern(self, normal_user):
        """端到端：脏 pattern 规则下，非超管查看者拿到的是掩码而非原文。"""
        _rule("(")
        user = normal_user
        data = {"phone": PHONE}
        apply_output_mask(data, _make_request(user), user, UserInfo)
        assert data["phone"] == "138******78"  # 掩码输出，不再是 13812345678

    def test_operation_log_changes_masked_for_dirty_pattern(self, normal_user):
        """操作日志 changes JSON 输出：同样经规则加载层，脏 pattern 不原值放行。"""
        _rule("(")
        user = normal_user
        value = '{"phone": {"old": null, "new": "%s"}}'.replace("%s", PHONE)
        masked = mask_operation_changes(value, "identity.userinfo", _make_request(user), user)
        assert PHONE not in masked
        assert "138******78" in masked
