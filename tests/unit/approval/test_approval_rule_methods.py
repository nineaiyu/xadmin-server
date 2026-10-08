# -*- coding: utf-8 -*-
"""审批规则 HTTP 方法维度：写入侧校验（白名单 / 大写归一 / 去重 / 空值语义）。"""

import pytest
from rest_framework import serializers

from approval.serializers.approval_rule import ApprovalRuleSerializer


class TestValidateMethods:
    def _validate(self, value):
        return ApprovalRuleSerializer().validate_methods(value)

    def test_empty_values_mean_all_methods(self):
        assert self._validate(None) == []
        assert self._validate("") == []
        assert self._validate([]) == []

    def test_normalized_uppercase_and_deduped(self):
        assert self._validate(["get", "GET", " delete "]) == ["GET", "DELETE"]

    def test_blank_items_filtered(self):
        assert self._validate(["", "  ", "POST"]) == ["POST"]

    def test_unknown_method_rejected(self):
        with pytest.raises(serializers.ValidationError):
            self._validate(["FETCH"])

    def test_non_list_rejected(self):
        with pytest.raises(serializers.ValidationError):
            self._validate("DELETE")

    def test_all_whitelist_methods_accepted(self):
        from approval.models.approval_rule import RULE_METHODS

        assert self._validate(list(RULE_METHODS)) == list(RULE_METHODS)
