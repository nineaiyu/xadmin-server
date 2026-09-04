# -*- coding: utf-8 -*-
"""common/core/pagination.py DynamicPageNumber 单元测试。

直接以 DRF Request 驱动动态分页类，验证 page/page_size 参数解析：
合法值切页、非法 size 回退默认、超大 size 按 max_page_size 截断，以及
分页行为（项目未实现 no_page 全量返回参数）。
"""
import pytest
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from common.core.pagination import DynamicPageNumber
from system.models import UserInfo

pytestmark = pytest.mark.django_db

factory = APIRequestFactory()


def make_request(params):
    return Request(factory.get("/api/demo/book", params), authenticators=[])


def make_users(count):
    users = [
        UserInfo.objects.create_user(username=f"user_{i}", password="Test@123456")
        for i in range(count)
    ]
    return [u.pk for u in users]


class TestPageSizeParsing:
    def test_default_page_size(self):
        paginator = DynamicPageNumber()()
        assert paginator.get_page_size(make_request({})) == 20

    def test_valid_page_size(self):
        paginator = DynamicPageNumber()()
        assert paginator.get_page_size(make_request({"size": "5"})) == 5

    @pytest.mark.parametrize("value", ["0", "-3", "abc", "1.5"])
    def test_invalid_page_size_falls_back_to_default(self, value):
        paginator = DynamicPageNumber()()
        assert paginator.get_page_size(make_request({"size": value})) == 20

    def test_oversized_page_size_capped_by_max(self):
        paginator = DynamicPageNumber(max_page_size=10)()
        assert paginator.get_page_size(make_request({"size": "9999"})) == 10

    def test_negative_max_keeps_cap_floor(self):
        # int("-1") 合法，但不小于 0 的常规 size，此处校验不会抛错且取最小值
        paginator = DynamicPageNumber(max_page_size=5)()
        assert paginator.get_page_size(make_request({"size": "7"})) == 5


class TestPaginationBehavior:
    def test_paginate_correct_page_and_size(self):
        pks = make_users(7)
        paginator = DynamicPageNumber(max_page_size=1000)()
        request = make_request({"page": "2", "size": "3"})
        page = paginator.paginate_queryset(UserInfo.objects.order_by("pk"), request)
        assert [item.pk for item in page] == pks[3:6]
        assert paginator.page.paginator.count == 7
        assert paginator.get_paginated_response(page).data["total"] == 7

    def test_size_zero_falls_back_to_default_page(self):
        make_users(7)
        paginator = DynamicPageNumber(max_page_size=1000)()
        request = make_request({"page": "1", "size": "0"})
        page = paginator.paginate_queryset(UserInfo.objects.order_by("pk"), request)
        assert len(page) == 7  # 非法 size 回退默认 20，7 条全部在一页

    def test_capped_page_size_limits_results(self):
        make_users(7)
        paginator = DynamicPageNumber(max_page_size=3)()
        request = make_request({"page": "1", "size": "10"})
        page = paginator.paginate_queryset(UserInfo.objects.order_by("pk"), request)
        assert len(page) == 3

    def test_no_page_param_is_ignored(self):
        """项目未实现 no_page 全量返回参数；请求仍按默认 page_size 分页。"""
        make_users(25)
        paginator = DynamicPageNumber()()
        request = make_request({"page": "1", "no_page": "1"})
        page = paginator.paginate_queryset(UserInfo.objects.order_by("pk"), request)
        assert len(page) == 20  # 默认每页 20，说明 no_page 被忽略，未返回全部 25 条