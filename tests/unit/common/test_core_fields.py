# -*- coding: utf-8 -*-
"""packages/xadmin-common/common/core/fields.py：字段边界（空值序列化与写入契约）。

写入契约（与前端 RePlusPage 表单一致）：labeled 系列提交 ``{value, label}`` 对象
或标量；「未选择 / 清空」形态（``{}`` / ``{"value": null}`` / 空串 / ``None``）按
字段 ``allow_null`` 收口，不再被 falsy 短路放行（会把 ``{}`` 写进库 / 放过 0 等值）。
"""

import pytest
from rest_framework.exceptions import ValidationError

from common.core.fields import (
    ColorField,
    LabeledChoiceField,
    LabeledMultipleChoiceField,
    PhoneField,
    RichTextField,
)


class TestPhoneField:
    def test_representation_empty_value(self):
        """空号码回显空结构。

        历史缺陷：`phonenumbers.parse(None, ...)` 抛的是 TypeError（不在
        NumberParseException 捕获范围内），空值序列化会让整个接口 500。
        """
        field = PhoneField()
        assert field.to_representation(None) == {"code": "+86", "phone": ""}
        assert field.to_representation("") == {"code": "+86", "phone": ""}

    def test_representation_e164(self):
        field = PhoneField()
        # national_number 是 int（phonenumbers 口径）
        assert field.to_representation("+8613800138000") == {"code": "+86", "phone": 13800138000}

    def test_internal_value_normalizes_plain_number(self):
        assert PhoneField().to_internal_value("13800138000") == "+8613800138000"

    def test_internal_value_accepts_code_object(self):
        assert PhoneField().to_internal_value({"code": "+86", "phone": "13800138000"}) == "+8613800138000"

    def test_invalid_value_rejected_instead_of_prefixing_region(self):
        """解析失败不再盲目补 ``+86`` 入库（历史行为会放过 ``+86abc`` 类脏值）。"""
        with pytest.raises(ValidationError):
            PhoneField().to_internal_value("+86abc")

    def test_default_validator_rejects_invalid_number(self):
        with pytest.raises(ValidationError):
            PhoneField().run_validation("+86123")

    def test_validator_can_be_disabled(self):
        assert PhoneField(validate=False).run_validation("+86123") == "+86123"

    def test_explicit_country_restores_legacy_prefix_fallback(self):
        """显式 country + validate=False 才恢复历史「解析失败补地区码」行为。"""
        field = PhoneField(country="CN", validate=False)
        assert field.to_internal_value("not-a-phone") == "+86not-a-phone"
        assert field.run_validation("not-a-phone") == "+86not-a-phone"
        with pytest.raises(ValidationError):
            PhoneField(country="CN").run_validation("not-a-phone")

    def test_empty_dict_follows_allow_blank(self):
        assert PhoneField(allow_blank=True).to_internal_value({"code": "+86", "phone": ""}) == ""

    def test_empty_dict_follows_allow_null(self):
        assert PhoneField(allow_null=True).to_internal_value({"code": "+86", "phone": ""}) is None

    def test_empty_dict_rejected_when_blank_and_null_disallowed(self):
        with pytest.raises(ValidationError):
            PhoneField().to_internal_value({"code": "+86", "phone": ""})

    def test_empty_serializer_validation_respects_allow_blank(self):
        field = PhoneField(allow_blank=True, allow_null=True)
        assert field.run_validation({"code": "+86", "phone": ""}) == ""


class TestColorField:
    @pytest.mark.parametrize("value", ["#409EFF", "#fff", "#409EFF80", "rgb(64, 158, 255)", "red"])
    def test_valid_colors_accepted(self, value):
        assert ColorField().run_validation(value) == value

    @pytest.mark.parametrize("value", ["#zzzzzz", "409EFF", "#409EF", "rgb(1,2,3);"])
    def test_invalid_colors_rejected(self, value):
        with pytest.raises(ValidationError):
            ColorField().run_validation(value)

    def test_blank_allowed(self):
        assert ColorField(allow_blank=True).run_validation("") == ""


class TestRichTextField:
    def test_plain_html_retained(self):
        value = "<p><strong>公告</strong></p>"
        assert RichTextField().run_validation(value) == value

    def test_dangerous_html_sanitized_on_write(self):
        value = '<p onclick="evil()">x</p><script>alert(1)</script>'
        cleaned = RichTextField().run_validation(value)
        assert "script" not in cleaned
        assert "onclick" not in cleaned
        assert "<p>x</p>" in cleaned

    def test_blank_allowed(self):
        assert RichTextField(allow_blank=True).run_validation("") == ""


class TestLabeledChoiceField:
    def test_dict_value_extracted(self):
        field = LabeledChoiceField(choices=[("a", "A")])
        assert field.to_internal_value({"value": "a", "label": "A"}) == "a"

    def test_numeric_zero_is_validated_not_short_circuited(self):
        """0 是合法枚举值：必须走 choices 校验（历史缺陷：falsy 短路直接放过）。"""
        field = LabeledChoiceField(choices=[(0, "未知"), (1, "男")])
        assert field.to_internal_value(0) == 0
        assert field.to_internal_value({"value": 0}) == 0

    def test_numeric_zero_not_in_choices_rejected(self):
        field = LabeledChoiceField(choices=[(1, "男"), (2, "女")])
        with pytest.raises(ValidationError):
            field.to_internal_value(0)

    def test_empty_selection_rejected_when_null_not_allowed(self):
        """前端「未选择」形态必须 400，而不是把 {} / 空值写进库。"""
        field = LabeledChoiceField(choices=[("a", "A")])
        for payload in ({}, {"value": None}, "", "   ", None):
            with pytest.raises(ValidationError):
                field.to_internal_value(payload)

    def test_empty_selection_allowed_when_null_allowed(self):
        field = LabeledChoiceField(choices=[("a", "A")], allow_null=True)
        assert field.to_internal_value({}) is None
        assert field.to_internal_value("") is None
        assert field.to_internal_value({"value": None}) is None

    def test_parenthesized_value_still_parsed(self):
        field = LabeledChoiceField(choices=[("a", "A")])
        assert field.to_internal_value("A(a)") == "a"


class TestLabeledMultipleChoiceField:
    def test_dict_items_extracted_from_list(self):
        field = LabeledMultipleChoiceField(choices=[("a", "A"), ("b", "B")])
        assert field.to_internal_value([{"value": "a"}, {"value": "b"}]) == ["a", "b"]

    def test_placeholder_items_filtered(self):
        """前端默认值形态 [{}] / [{"value": null}] 过滤后等价清空，不写脏值。"""
        field = LabeledMultipleChoiceField(choices=[("a", "A")])
        assert field.to_internal_value([{}]) == []
        assert field.to_internal_value([{"value": None}, {"value": "a"}]) == ["a"]

    def test_empty_list_clears(self):
        field = LabeledMultipleChoiceField(choices=[("a", "A")])
        assert field.to_internal_value([]) == []

    def test_invalid_choice_rejected(self):
        field = LabeledMultipleChoiceField(choices=[("a", "A")])
        with pytest.raises(ValidationError):
            field.to_internal_value(["zzz"])

    def test_null_rejected_when_not_allowed(self):
        field = LabeledMultipleChoiceField(choices=[("a", "A")])
        with pytest.raises(ValidationError):
            field.to_internal_value(None)

    def test_null_allowed_returns_none(self):
        field = LabeledMultipleChoiceField(choices=[("a", "A")], allow_null=True)
        assert field.to_internal_value(None) is None

    def test_malformed_dict_payload_does_not_raise_key_error(self):
        """畸形 payload（dict 输入）不能因 data[0] 抛 KeyError（500）。

        历史缺陷：直接 `isinstance(data[0], dict)` 对 dict 输入抛 KeyError；
        修复后非序列形态原样交给 DRF 校验（校验结果由 DRF 语义决定，本用例只锁定
        「不再 KeyError」这一修复面；DRF 拒绝同样可接受：畸形请求应 400 而非 500）。
        """
        field = LabeledMultipleChoiceField(choices=[("a", "A")])
        try:
            field.run_validation({"a": 1})
        except ValidationError:
            pass
