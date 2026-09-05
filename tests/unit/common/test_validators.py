# -*- coding:utf-8 -*-
"""通用校验器测试（T4.1）。"""
import pytest
from rest_framework import serializers

from common.core.validators import PhoneValidator


class TestPhoneValidator:
    @pytest.mark.parametrize("value", ["13800138000", "15912345678", ""])
    def test_valid_or_empty_passes(self, value):
        assert PhoneValidator()(value) is None

    @pytest.mark.parametrize("value", ["123", "abcdefghi", "1380013800"])
    def test_invalid_phone_raises(self, value):
        with pytest.raises(serializers.ValidationError):
            PhoneValidator()(value)
