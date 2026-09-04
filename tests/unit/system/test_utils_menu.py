# -*- coding: utf-8 -*-
"""system.utils.menu 单元测试（权限名公共前缀、关联模型探测）。"""
import pytest

from demo.models import Book
from system.utils.menu import get_long_str, get_related_models

pytestmark = pytest.mark.django_db


class TestGetLongStr:
    def test_common_prefix(self):
        assert get_long_str(["user-list", "user-detail"]) == "user-"

    def test_no_common_prefix(self):
        assert get_long_str(["abc-list", "xyz-detail"]) == ""

    def test_single_item_is_whole(self):
        assert get_long_str(["user-list"]) == "user-list"

    def test_empty_input(self):
        assert get_long_str([]) == ""


class TestGetRelatedModels:
    def test_book_related_models(self):
        result = get_related_models(Book)
        assert "demo.book" in result
        # 框架根因：排除 Group/Permission 以及 creator/modifier/dept_belong 审计字段
        assert "auth.permission" not in result
        assert "auth.group" not in result

    def test_related_models_include_fk_targets(self):
        result = get_related_models(Book)
        # Book 的 admin/admin2/managers 指向上级模型（system 或其子模型）
        assert any("system." in item for item in result)